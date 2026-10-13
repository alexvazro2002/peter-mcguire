"""Permanently empty the configured notes vault and its managed recovery copies.

No note-content backup is created: this command is for explicitly requested deletion.
It never reads or deletes project documentation, task Markdown, memories or models.
Run --apply only after inspecting the dry-run report and closing NexusDesk.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import tempfile


def plan(root):
    root = Path(root).resolve()
    config_path = root / 'config.json'
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    vault = Path(config.get('vault_path') or root / 'notes').expanduser()
    if vault.is_symlink():
        raise ValueError('Resolve and review a linked vault explicitly before deleting it.')
    vault = vault.resolve()
    if vault == root or root.is_relative_to(vault) or vault == Path.home():
        raise ValueError('The configured vault overlaps the project or home directory.')
    targets = list(vault.iterdir()) if vault.is_dir() else []
    # Older app-managed migration/brick backups can contain whole note vaults.
    for managed in ('migration-backups', 'brick-backups'):
        backup = root / managed
        if backup.is_dir() and not backup.is_symlink():
            for candidate in backup.rglob('notes'):
                if candidate.is_dir() and not candidate.is_symlink():
                    targets.append(candidate)
    return root, config, vault, targets


def write_json(path, value):
    with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.cleanup-', delete=False, encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write('\n')
        temporary = Path(handle.name)
    temporary.replace(path)


def clean(root, apply=False):
    root, config, vault, targets = plan(root)
    count = sum(1 if p.is_file() or p.is_symlink() else sum(x.is_file() or x.is_symlink() for x in p.rglob('*')) for p in targets)
    report = dict(applied=apply, vault=str(vault), files_to_remove=count,
                  documentation_preserved=True, tasks_preserved=True)
    if not apply:
        return report
    for target in targets:
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)
    vault.mkdir(parents=True, exist_ok=True)
    # Drop stale titles/layout metadata without changing unrelated settings.
    configs = [root / 'config.json', root / 'config.legacy.json']
    configs.extend((root / 'brick-backups').rglob('config*.json'))
    for path in configs:
        if path.is_file() and not path.is_symlink():
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            data.update(notes_order=[], notes_pinned=[], notes_last_used={}, notes_graph_pos={}, last_note='')
            write_json(path, data)
    # Past chats may quote deleted notes; clearing them prevents resurfacing this content.
    conversation = root / 'assistant-conversation.json'
    if conversation.is_file() and not conversation.is_symlink():
        write_json(conversation, [])
    calendar = root / 'calendar.json'
    if calendar.is_file() and not calendar.is_symlink():
        data = json.loads(calendar.read_text())
        note_count = sum(item.get('kind') == 'note' for item in data['entries'])
        data['entries'] = [dict(item, note='') for item in data['entries'] if item.get('kind') != 'note']
        write_json(calendar, data)
        report['calendar_notes_removed'] = note_count
    report['remaining_notes'] = len(list(vault.glob('*.md')))
    report['past_conversation_cleared'] = True
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(clean(args.root, args.apply), indent=2))
