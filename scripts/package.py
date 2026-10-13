"""Package explicitly approved source/documents, never a copy of a live workspace."""
import argparse
import hashlib
import os
import tempfile
import zipfile
from pathlib import Path


ROOT_FILES = {
    ".gitignore", "README.md", "ASSISTANT.md", "PERSONAL_MEMORY.md", "VALIDATION.md",
    "UPDATE_2_3.md", "CHANGES.md", "DOCKER_BRICKS.md", "PETER_LINUX.md", "LINUX.md", "LINUX_PETER.md", "PETER_VALIDATION.md",
    "LICENSE", "LICENSE.txt", "LICENSE.md", "NOTICE", "START_HERE.txt",
    "main_flet.py", "library_cli.py", "start-linux.sh", "install-linux.sh", "install-voice.sh",
    "START.bat", "START_LIBRARY_AI.bat", "INSTALL.bat",
    "requirements.txt", "requirements-dev.txt", "requirements-ai.txt", "requirements-automation.txt", "requirements-library.txt",
    "package.json", "package-lock.json", "tsconfig.json", "eslint.config.mjs", "pytest.ini",
    "scripts/thinking_orbs/package.json", "scripts/thinking_orbs/pnpm-lock.yaml",
    "assets/thinking-orbs/LICENSE.txt", "assets/thinking-orbs/provenance.json",
}
SOURCE_TREES = {
    "app": {".py"}, "library": {".py"}, "tools": {".py"}, "scripts": {".py", ".sh", ".mjs"},
    "tests": {".py"}, "web": {".ts", ".css", ".html"}, "assets": {".svg", ".desktop", ".webp"}, "docs": {".md"},
}
ORB_ASSETS = {f"assets/thinking-orbs/{theme}-{state}.webp"
              for theme in ("dark", "light") for state in ("breathing", "listening", "working", "composing", "error")}
EXCLUDED_DIRS = {".git", ".venv", "venv", ".runtime", "runtime", "__pycache__", ".pytest_cache",
                 "node_modules", "dist", "build", "notes", "tasks", "services", "logs", "models",
                 "ai_models", "projects", "previews", "screenshots", "brick-backups", "backups",
                 "migration-backups", ".history", ".trash", "_KNOWLEDGE_BASE"}
PRIVATE_NAMES = {"config.json", "config.legacy.json", "calendar.json", "assistant-conversation.json",
                 "personal-memory.json", "command-preferences.json", ".netrc", ".npmrc", ".git-credentials"}


def private_name(name):
    name = name.casefold()
    return (name in PRIVATE_NAMES or name == ".env" or name.startswith(".env.") or
            any(marker in name for marker in ("credential", "secret", "api-token")) or
            name.endswith((".bak", ".log", ".lock")) or name.startswith(".nexus-"))


def source_files(root):
    root = Path(root).resolve()
    files = []
    for name in sorted(ROOT_FILES):
        path = root / name
        if path.is_file() and not path.is_symlink():
            files.append(path)
    for tree, suffixes in SOURCE_TREES.items():
        base = root / tree
        if not base.is_dir() or base.is_symlink():
            continue
        for directory, dirs, names in os.walk(base, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name not in EXCLUDED_DIRS and not name.startswith(".")
                             and not (Path(directory) / name).is_symlink())
            for name in sorted(names):
                path = Path(directory) / name
                if path.suffix.casefold() == '.webp' and path.relative_to(root).as_posix() not in ORB_ASSETS:
                    continue
                if path.is_file() and not path.is_symlink() and path.suffix.casefold() in suffixes and not private_name(name):
                    files.append(path)
    return sorted(set(files), key=lambda path: path.relative_to(root).as_posix())


def build_package(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    files = source_files(root)
    if output in files:
        raise ValueError("The package output would overwrite a project source file.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".nexus-package-", suffix=".zip", dir=output.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in files:
                archive.write(path, Path("NexusDesk") / path.relative_to(root))
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise RuntimeError("ZIP integrity check failed.")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files = build_package(root, args.output)
    print(f"ZIP ready: {args.output}\nFiles: {len(files)}\nBytes: {args.output.stat().st_size}")
    print("SHA256: " + hashlib.sha256(args.output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
