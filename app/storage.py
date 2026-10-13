"""Safe Markdown vault operations, links, and lossless checklist editing."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import atomic_write
from app.note_links import LOCK as NOTE_LOCK, NoteIndex, canonical_id, parse_links

WIKI = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
CODE = re.compile(r"(```.*?```|~~~.*?~~~|`[^`\n]*`)", re.S)
TASK = re.compile(r"^(\s*[-*+]\s+\[)([ xX])(\]\s*)(.*)$")
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}


def validate_name(name: str) -> str:
    name = name.strip()
    if (not name or len(name) > 180 or name in {".", ".."} or
        re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((".", " ")) or
        name.split(".")[0].upper() in RESERVED):
        raise ValueError("Use a valid note title without slashes or reserved filename characters.")
    return name


def transform_links(text: str, callback) -> str:
    return "".join(part if i % 2 else WIKI.sub(callback, part)
                   for i, part in enumerate(CODE.split(text)))


def links(text: str) -> list[str]:
    return list(dict.fromkeys(link["target"] for link in parse_links(text)))


def link_references(text: str, target: str) -> list[dict]:
    """Keep source lines and aliases for graph edges, ignoring links in code blocks."""
    return [reference for reference in parse_links(text) if reference["target"] == target]


class Vault:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._note_index = NoteIndex(self.root)

    def path(self, name: str) -> Path:
        path = self.root / (validate_name(name) + ".md")
        if path.is_symlink():
            raise ValueError("Linked files outside the vault are not editable.")
        return path

    def names(self) -> list[str]:
        return sorted((p.stem for p in self.root.glob("*.md") if not p.is_symlink()), key=str.casefold)

    def read(self, name: str) -> str:
        path = self.path(name)
        return path.read_text(encoding="utf-8-sig") if path.exists() else ""

    def write(self, name: str, content: str) -> None:
        with NOTE_LOCK:
            path = self.path(name)
            self._note_index.refresh()
            atomic_write(path, content)
            self._note_index.record_write(name)

    def create(self, name: str, content: str | None = None) -> str:
        with NOTE_LOCK:
            name = validate_name(name)
            if any(n.casefold() == name.casefold() for n in self.names()):
                raise ValueError("A note with that title already exists.")
            self._note_index.refresh()
            atomic_write(self.path(name), content if content is not None else f"# {name}\n\n")
            self._note_index.record_write(name, fresh=True)
            return name

    def rename(self, old: str, new: str) -> None:
        with NOTE_LOCK:
            self._rename(old, new)

    def _rename(self, old: str, new: str) -> None:
        new = validate_name(new)
        if old == new:
            return
        self._note_index.refresh()
        if old not in self._note_index._titles:
            raise ValueError("The note no longer exists.")
        if any(n.casefold() == new.casefold() for n in self.names() if n != old):
            raise ValueError("A note with that title already exists.")
        # Preflight and back up all affected files, then update wiki-link targets.
        affected = {}
        for name in self.names():
            text = self.read(name)
            def replace(match):
                raw = match.group(0)
                return raw.replace(match.group(1), new, 1) if match.group(1).strip() == old else raw
            updated = transform_links(text, replace)
            if updated != text:
                affected[name] = (text, updated)
        backup = self.root / ".history" / f"rename-{time.time_ns()}"
        backup.mkdir(parents=True)
        atomic_write(backup / f"{old}.md", self.read(old))
        for name, (before, _) in affected.items():
            atomic_write(backup / f"{name}.md", before)
        self.path(old).rename(self.path(new))
        self._note_index.renamed(old, new)
        for name, (_, updated) in affected.items():
            self.write(new if name == old else name, updated)

    def duplicate(self, name: str) -> str:
        new, i = f"{name} copy", 2
        while new.casefold() in {n.casefold() for n in self.names()}:
            new, i = f"{name} copy {i}", i + 1
        return self.create(new, self.read(name))

    def trash(self, name: str) -> Path:
        with NOTE_LOCK:
            self._note_index.refresh()
            dest = self.root / ".trash" / f"{time.time_ns()}-{validate_name(name)}.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.path(name).replace(dest)
            self._note_index.deleted(name)
            return dest

    def backlinks(self, name: str) -> list[str]:
        return list(dict.fromkeys(reference["source"] for reference in self.backlinks_details(name)))

    def graph(self) -> tuple[list[str], list[tuple[str, str]]]:
        nodes, directed = self.directed_graph()
        edges = [(edge["source"], edge["target"]) for edge in directed]
        return [node["title"] for node in nodes], list(dict.fromkeys(tuple(sorted(edge)) for edge in edges if edge[0] != edge[1]))

    def note_id(self, name: str) -> str:
        with NOTE_LOCK:
            return self._note_index.refresh().metadata(name)["id"]

    def resolve_id(self, identity: str) -> str | None:
        with NOTE_LOCK:
            if not canonical_id(identity):
                return None
            return self._note_index.refresh().resolve(identity)

    def resolve_note(self, reference: str) -> str | None:
        """Resolve a permanent ID or an unambiguous legacy title."""
        with NOTE_LOCK:
            return self._note_index.refresh().resolve(reference)

    def metadata(self, name: str) -> dict:
        with NOTE_LOCK:
            return self._note_index.refresh().metadata(name)

    def note_metadata(self) -> list[dict]:
        with NOTE_LOCK:
            index = self._note_index.refresh()
            return [index.metadata(name) for name in sorted(index._titles, key=str.casefold)]

    def mark_opened(self, name: str) -> None:
        with NOTE_LOCK:
            index = self._note_index.refresh()
            if name in index._titles:
                index._titles[name]["last_opened"] = time.time()
                index._save()

    def link_occurrences(self, name: str | None = None, *, text: str | None = None) -> list[dict]:
        with NOTE_LOCK:
            return self._note_index.refresh().occurrences(name, text=text)

    def backlinks_details(self, name: str) -> list[dict]:
        with NOTE_LOCK:
            index = self._note_index.refresh()
            _, incoming = index.relations()
            return sorted(incoming.get(name, []), key=lambda ref: (ref["source"].casefold(), ref["start"]))

    def directed_graph(self) -> tuple[list[dict], list[dict]]:
        """One directed edge per source/destination; repeated text links stay in backlinks."""
        with NOTE_LOCK:
            index = self._note_index.refresh()
            outgoing, _ = index.relations()
            edges, seen = [], set()
            for source, references in outgoing.items():
                for reference in references:
                    target = reference["target_name"]
                    if target and (source, target) not in seen:
                        seen.add((source, target))
                        edges.append(dict(source=source, target=target, source_id=index._titles[source]["id"],
                                          target_id=index._titles[target]["id"]))
            return [index.metadata(name) for name in sorted(index._titles, key=str.casefold)], edges

    def search_notes(self, query: str = "", *, limit=50, exclude=None, recent=None) -> list[dict]:
        with NOTE_LOCK:
            return self._note_index.refresh().search(query, limit=limit, exclude=exclude, recent=recent)


@dataclass(frozen=True)
class ChecklistItem:
    line: int
    text: str
    done: bool


class Checklist:
    """Line IDs preserve duplicate task titles, headings, comments, and order."""
    def __init__(self, path: Path):
        self.path = path

    def raw(self) -> str:
        return self.path.read_text(encoding="utf-8-sig") if self.path.exists() else "# Tasks\n\n"

    def items(self) -> list[ChecklistItem]:
        return [ChecklistItem(i, m.group(4), m.group(2).lower() == "x")
                for i, line in enumerate(self.raw().splitlines()) if (m := TASK.match(line))]

    def update(self, line_id: int, *, text: str | None = None, done: bool | None = None,
               delete: bool = False) -> None:
        lines = self.raw().splitlines()
        if not 0 <= line_id < len(lines) or not (match := TASK.match(lines[line_id])):
            raise ValueError("Task changed on disk. Refresh and try again.")
        if delete:
            history = self.path.parent / ".history" / f"{time.time_ns()}-{self.path.name}"
            atomic_write(history, self.raw())
            lines.pop(line_id)
        else:
            body = match.group(4) if text is None else text.strip()
            if not body or "\n" in body:
                raise ValueError("Enter a non-empty, single-line task.")
            status = match.group(2) if done is None else ("x" if done else " ")
            lines[line_id] = match.group(1) + status + match.group(3) + body
        atomic_write(self.path, "\n".join(lines) + "\n")

    def add(self, text: str) -> None:
        text = text.strip()
        if not text or "\n" in text:
            raise ValueError("Enter a non-empty, single-line task.")
        atomic_write(self.path, self.raw().rstrip() + f"\n- [ ] {text}\n")
