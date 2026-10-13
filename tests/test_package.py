"""Source packages must stay safe even when built inside a populated workspace."""
import zipfile

import pytest

from scripts.package import build_package


def write(root, relative, content="private-data"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_package_keeps_source_docs_and_launcher_excludes_personal_runtime_and_models(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    included = ["README.md", "ASSISTANT.md", "PERSONAL_MEMORY.md", "main_flet.py",
                "start-linux.sh", "requirements.txt", "app/assistant/engine.py",
                "library/lmstudio.py", "scripts/launch_linux.py", "tests/test_runtime.py",
                "web/src/notes.ts", "web/notes.css", "assets/peter.svg"]
    for relative in included:
        write(root, relative, "source or project documentation")
    excluded = ["config.json", "config.legacy.json", "calendar.json", "assistant-conversation.json",
                "personal-memory.json", "notes/Credentials.md", "notes/.history/note.md",
                "notes/.trash/note.md", "tasks/Weekly Tasks.md", "services/stirling_pdf/configs/credential-encryption.key",
                "services/stirling_pdf/configs/settings.yml", "brick-backups/README.md",
                "projects/vendor/README.md", "projects/vendor/main.py", ".venv/lib/module.py",
                "logs/session.log", "previews/screenshot.svg", "assets/screenshot.png",
                "ai_models/model.gguf", "library/model.gguf", "app/model.bin", "library/metadata.pkl", "assets/screenshot.webp",
                "app/.env", "app/.env.example", "app/secrets.py", "app/credentials.py",
                "app/personal-memory.json", "app/notes/copied_note.py", "app/.history/restore.py",
                "scripts/launch_linux.py.bak", "web/dist/index.html", "web/node_modules/vendor/index.ts",
                "unrecognized-personal-document.md"]
    for relative in excluded:
        write(root, relative)
    write(root, "app/linked.py").unlink()
    (root / "app/linked.py").symlink_to(tmp_path / "outside-secret.py")
    write(tmp_path, "outside-secret.py")
    (root / "app/linked-directory").symlink_to(root / "projects", target_is_directory=True)
    output = tmp_path / "NexusDesk.zip"
    build_package(root, output)
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"NexusDesk/" + relative for relative in included}
        assert all(b"private-data" not in archive.read(name) for name in archive.namelist())
        assert archive.testzip() is None


def test_package_output_cannot_overwrite_source(tmp_path):
    source = write(tmp_path, "main_flet.py", "preserve the source")
    with pytest.raises(ValueError, match="overwrite"):
        build_package(tmp_path, source)
    assert source.read_text() == "preserve the source"
