"""Bounded, read-only access to project source, with no shell or file-write tools."""
from __future__ import annotations

import os
import re
from pathlib import Path

from app.config import BASE_DIR

EXCLUDED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
                 ".history", ".trash", "logs", "migration-backups", "ai_models", "models",
                 "notes", "tasks", "services", "brick-backups", "previews", ".runtime", "voice-cache",
                 "projects", "backups", "work", "runtime", "build", "dist", "screenshots",
                 "_KNOWLEDGE_BASE", ".mypy_cache", ".ruff_cache"}
PRIVATE_FILES = {"config.json", "config.legacy.json", "calendar.json", "assistant-conversation.json",
                 "credentials.md", "credentials.json", "secrets.json", "personal-memory.json"}
TEXT_TYPES = {".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg",
              ".bat", ".ps1", ".sh", ".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx",
              ".html", ".css", ".sql", ".xml", ".csv", ".svg"}
MAX_BYTES = 1_000_000


def private(path):
    name = path.name.casefold()
    return (name in PRIVATE_FILES or name == ".env" or name.startswith(".env.") or
            path.suffix.casefold() in {".pem", ".key", ".p12", ".pfx"})


class Repository:
    def __init__(self, root=BASE_DIR):
        self.root = Path(root).resolve()

    def files(self):
        results = []
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".venv")
                             and not (Path(directory) / d).is_symlink())
            for name in sorted(files):
                path = Path(directory) / name
                if not path.is_symlink() and not private(path):
                    results.append(path)
        return sorted(results, key=lambda p: p.relative_to(self.root).as_posix().casefold())

    def readable(self, path):
        return (path.suffix.casefold() in TEXT_TYPES or path.name in {"LICENSE", "Dockerfile", ".gitignore"}) and path.stat().st_size <= MAX_BYTES

    def resolve(self, relative):
        if not relative or "\\" in relative or ":" in relative or "\x00" in relative:
            raise ValueError("Use a repository-relative path with forward slashes.")
        part = Path(relative)
        if part.is_absolute() or ".." in part.parts or any(p in EXCLUDED_DIRS or p.startswith(".venv") for p in part.parts):
            raise ValueError("That path is outside the readable repository.")
        path = self.root / part
        if any((self.root.joinpath(*part.parts[:i])).is_symlink() for i in range(1, len(part.parts) + 1)):
            raise ValueError("Repository symlinks are not followed.")
        if not path.is_file() or not path.resolve().is_relative_to(self.root) or private(path):
            raise ValueError("File is missing or private. Runtime data is available through workspace tools.")
        if not self.readable(path):
            raise ValueError("This is a binary or oversized file; only bounded text/source files can be read.")
        return path

    def read(self, path, offset=0, limit=5000):
        target = self.resolve(path)
        content = target.read_text(encoding="utf-8-sig")
        if "\x00" in content:
            raise ValueError("Binary files cannot be read as source.")
        return dict(path=target.relative_to(self.root).as_posix(), content=content[offset:offset + limit],
                    characters=len(content), next_offset=offset + limit if offset + limit < len(content) else None)

    def listing(self, query="", offset=0, limit=20):
        rows = [dict(path=p.relative_to(self.root).as_posix(), readable=self.readable(p))
                for p in self.files() if query.casefold() in p.relative_to(self.root).as_posix().casefold()]
        return dict(items=rows[offset:offset + limit], total=len(rows),
                    next_offset=offset + limit if offset + limit < len(rows) else None,
                    excluded="Model binaries, dependency/cache folders, secrets, runtime config, and symlinks. App data uses workspace tools.")

    def search(self, query, offset=0, limit=20):
        terms = set(re.findall(r"[^\W_]+", query.casefold()))
        if not terms:
            raise ValueError("Enter words, a symbol name, or a file path to search.")
        rows = []
        for path in self.files():
            if not self.readable(path):
                continue
            try:
                content = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError):
                continue
            if "\x00" in content:
                continue
            name = path.relative_to(self.root).as_posix()
            matches = []
            for line_no, line in enumerate(content.splitlines(), 1):
                hits = len(terms & set(re.findall(r"[^\W_]+", line.casefold())))
                if hits:
                    matches.append((hits, line_no, line[:400]))
            path_hits = len(terms & set(re.findall(r"[^\W_]+", name.casefold())))
            if matches or path_hits:
                matches.sort(key=lambda m: (-m[0], m[1]))
                score = max((m[0] for m in matches), default=0) + path_hits
                rows.append(dict(path=name, score=score, matches=[dict(line=m[1], text=m[2]) for m in matches[:4]]))
        rows.sort(key=lambda r: (-r["score"], r["path"]))
        return dict(items=rows[offset:offset + limit], total=len(rows),
                    next_offset=offset + limit if offset + limit < len(rows) else None)
