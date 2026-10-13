import json
from pathlib import Path
import pytest

from scripts.clean_notes import clean
from scripts.install_desktop import desktop_entry
from scripts.launch_linux import diagnose


def test_cleanup_purges_recoverable_notes_preserves_code_calendar_tasks_memory(tmp_path):
    vault = tmp_path / 'notes'
    for name in ('private.md', '.trash/deleted.md', '.history/undo.json', '.notes-index.json'):
        file = vault / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('private note content')
    backup = tmp_path / 'migration-backups/previous/notes/private.md'
    backup.parent.mkdir(parents=True)
    backup.write_text('private note content')
    (tmp_path / 'README.md').write_text('Project documentation')
    (tmp_path / 'personal-memory.json').write_text('personal memory retained')
    (tmp_path / 'config.json').write_text(json.dumps(dict(vault_path='', notes_order=['private'], library={'gpu_layers':0})))
    (tmp_path / 'assistant-conversation.json').write_text('[{"text":"quoted private note"}]')
    (tmp_path / 'calendar.json').write_text(json.dumps(dict(schema_version=1, entries=[
        dict(id='a', kind='task', title='Keep task', note='private', details='Keep details'),
        dict(id='b', kind='note', title='Remove calendar note')
    ])))
    report = clean(tmp_path)
    assert report['files_to_remove'] == 5 and (vault / 'private.md').exists()
    report = clean(tmp_path, apply=True)
    assert report['remaining_notes'] == 0 and not list(vault.iterdir())
    assert not backup.exists()
    assert (tmp_path / 'README.md').read_text() == 'Project documentation'
    assert (tmp_path / 'personal-memory.json').read_text() == 'personal memory retained'
    assert json.loads((tmp_path / 'assistant-conversation.json').read_text()) == []
    calendar = json.loads((tmp_path / 'calendar.json').read_text())
    assert calendar['entries'] == [dict(id='a',kind='task',title='Keep task',note='',details='Keep details')]
    assert json.loads((tmp_path / 'config.json').read_text())['library']['gpu_layers'] == 0


def test_cleanup_rejects_project_overlap_and_linked_vault(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps({'vault_path':str(tmp_path)}))
    with pytest.raises(ValueError, match='overlap'):
        clean(tmp_path, True)
    (tmp_path / 'notes').mkdir()
    (tmp_path / 'linked').symlink_to(tmp_path / 'notes', target_is_directory=True)
    (tmp_path / 'config.json').write_text(json.dumps({'vault_path':str(tmp_path/'linked')}))
    with pytest.raises(ValueError, match='linked'):
        clean(tmp_path, True)


def test_desktop_launcher_quotes_spaces_and_does_not_embed_secrets():
    entry = desktop_entry(Path('/tmp/Peter Workspace'))
    assert 'Exec=/bin/bash "/tmp/Peter Workspace/start-linux.sh"' in entry
    assert 'Terminal=false' in entry and 'Peter (McGuire)' in entry
    assert 'Exec=/bin/bash "/tmp/Peter %% Workspace/start-linux.sh"' in desktop_entry(Path('/tmp/Peter % Workspace'))


def test_diagnosis_reports_missing_runtime_without_claiming_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.launch_linux.healthy',lambda _:False)
    config = dict(peter={'voice_url':'http://127.0.0.1:3900'},library={'server_url':'http://127.0.0.1:8081/v1','model_path':str(tmp_path/'missing.gguf')})
    report = diagnose(config, tmp_path)
    assert report['voice_ready'] is False
    assert report['model_path_exists'] is False
    assert report['voice_backend_exists'] is False
