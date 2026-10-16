"""Optimistic workspace transactions with durable before/after images and safe undo."""
from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

from app.config import atomic_write
from app.memory import LOCK, validate_document
from app.storage import validate_name


class ConflictError(ValueError):
    pass


class History:
    def __init__(self, vault, calendar, root: Path, memory=None):
        self.vault, self.calendar, self.root = vault, calendar, root
        self.memory = memory

    def _path(self, operation_id):
        if not isinstance(operation_id, str) or len(operation_id) != 32 or any(c not in "0123456789abcdef" for c in operation_id):
            raise ValueError("Invalid operation ID.")
        path = self.root / f"{operation_id}.json"
        if path.is_symlink():
            raise ValueError("History links are not allowed.")
        return path

    def _save(self, record):
        atomic_write(self._path(record["id"]), json.dumps(record, ensure_ascii=False, indent=2) + "\n")

    def _read(self, operation_id):
        record = json.loads(self._path(operation_id).read_text(encoding="utf-8"))
        if (record.get("vault") != str(self.vault.root) or
                record.get("calendar") != str(self.calendar.path.resolve())):
            raise ConflictError("This operation belongs to another vault. Switch back before undoing it.")
        for change in record["files"]:
            if change["kind"] == "note":
                validate_name(change["name"])
                self.vault.path(change["name"])
            elif change["kind"] == "memory":
                if self.memory is None or record.get("memory") != str(self.memory.path):
                    raise ConflictError("This operation belongs to another Personal Memory store.")
                for key in ("before", "after"):
                    if change[key] is not None:
                        validate_document(json.loads(change[key]))
            elif change["kind"] != "calendar":
                raise ValueError("Invalid history target.")
        return record

    def _current(self, change):
        if change["kind"] == "memory":
            return self.memory.raw()
        path = self.vault.path(change["name"]) if change["kind"] == "note" else self.calendar.path
        if path.is_symlink():
            raise ValueError("Linked storage files are not editable.")
        return path.read_text(encoding="utf-8-sig") if path.exists() else None

    def _apply(self, change, content):
        if change["kind"] == "memory":
            self.memory.restore(content)
        elif change["kind"] == "note":
            if content is None:
                if self.vault.path(change["name"]).exists():
                    self.vault.trash(change["name"])
            else:
                self.vault.write(change["name"], content)
        else:
            if content is None:
                raise ValueError("The calendar cannot be removed.")
            self.calendar.write(json.loads(content))

    def commit(self, changes, summaries, request="", *, undo_of=None):
        if not changes:
            return None
        with LOCK:
            for change in changes:
                if self._current(change) != change["before"]:
                    raise ConflictError("Workspace data changed while the Assistant was working. Nothing was saved; try again.")
            record = {"id": uuid.uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
                      "vault": str(self.vault.root), "calendar": str(self.calendar.path.resolve()),
                      "status": "prepared", "request": request, "summaries": summaries,
                      "files": changes, "undo_of": undo_of}
            if self.memory is not None:
                record["memory"] = str(self.memory.path)
            # Write-ahead journal makes interrupted multi-file operations recoverable.
            self._save(record)
            try:
                for change in changes:
                    self._apply(change, change["after"])
                    if self._current(change) != change["after"]:
                        raise ValueError("The saved data did not match the requested change. The operation will be rolled back.")
                record["status"] = "committed"
                self._save(record)
            except Exception:
                try:
                    for change in reversed(changes):
                        current = self._current(change)
                        if current not in (change["before"], change["after"]):
                            raise ConflictError("A file was edited during rollback. Its journal was preserved.")
                        if current != change["before"]:
                            self._apply(change, change["before"])
                    record["status"] = "rolled_back"
                    self._save(record)
                except Exception as recovery_error:
                    record["status"] = "recovery_required"
                    self._save(record)
                    raise RuntimeError("Rollback needs attention. Stop editing and restart; the before-images remain in .history/assistant.") from recovery_error
                raise
            return record["id"]

    def recent(self, limit=20):
        if not self.root.exists():
            return []
        records = [self._read(path.stem) for path in self.root.glob("*.json")
                   if not path.is_symlink()]
        records = [r for r in records if r["status"] in {"committed", "undone"}]
        records.sort(key=lambda r: r["created_at"], reverse=True)
        return [{k: r.get(k) for k in ("id", "created_at", "summaries", "status", "undo_of")} for r in records[:limit]]

    def undo(self, operation_id):
        with LOCK:
            record = self._read(operation_id)
            if record["status"] != "committed":
                raise ValueError("This operation has already been undone or was not committed.")
            reverse = [dict(c, before=c["after"], after=c["before"]) for c in record["files"]]
            result = self.commit(reverse, ["Undid the previous Assistant operation."], undo_of=operation_id)
            record["status"] = "undone"
            self._save(record)
            return result

    def recover(self):
        """Only restore interrupted writes if no later external edit would be lost."""
        with LOCK:
            if not self.root.exists():
                return
            records = [self._read(p.stem) for p in self.root.glob("*.json") if not p.is_symlink()]
            undone = {r.get("undo_of") for r in records if r["status"] == "committed" and r.get("undo_of")}
            for record in records:
                if record["id"] in undone and record["status"] == "committed":
                    record["status"] = "undone"
                    self._save(record)
                if record["status"] not in {"prepared", "recovery_required"}:
                    continue
                for change in record["files"]:
                    if self._current(change) not in (change["before"], change["after"]):
                        raise ConflictError("Interrupted Assistant operation conflicts with a later edit. Restore its backup manually before using Assistant.")
                for change in reversed(record["files"]):
                    if self._current(change) != change["before"]:
                        self._apply(change, change["before"])
                record["status"] = "rolled_back"
                self._save(record)


class Transaction:
    def __init__(self, vault, calendar, history):
        self.vault, self.calendar, self.history = vault, calendar, history
        self.original_names = vault.names()
        self.before, self.notes, self.summaries, self.sources = {}, {}, [], []
        if calendar.path.is_symlink():
            raise ValueError("Linked calendar files are not editable.")
        self.calendar_before = calendar.path.read_text(encoding="utf-8-sig")
        self.calendar_data = calendar.read()
        self.memory = history.memory
        with LOCK:
            self.memory_before = self.memory.raw() if self.memory else None
            self.memory_data = self.memory.read() if self.memory else None

    def names(self):
        return sorted((set(self.original_names) | set(self.notes)) - {n for n, t in self.notes.items() if t is None}, key=str.casefold)

    def read_note(self, name):
        validate_name(name)
        if name not in self.names():
            raise ValueError("Note not found. Search for its exact title first.")
        if name not in self.before:
            self.before[name] = self.vault.read(name) if name in self.original_names else None
            self.notes.setdefault(name, self.before[name])
        return self.notes[name]

    def source(self, kind, **values):
        if kind == "calendar" and values.get("date"):
            values.pop("id", None)
        source = dict(kind=kind, **values)
        if source not in self.sources:
            self.sources.append(source)

    def write_note(self, name, content, *, create=False):
        validate_name(name)
        if create:
            if any(n.casefold() == name.casefold() for n in self.names()):
                raise ValueError("A note with this title already exists.")
            self.before.setdefault(name, None)
        else:
            self.read_note(name)
        self.notes[name] = content
        self.source("note", name=name)

    def delete_note(self, name):
        self.read_note(name)
        self.notes[name] = None

    def changed(self, summary):
        self.summaries.append(summary)

    def commit(self, request):
        # Keep the same request-lock -> storage-lock order as Stop and Undo.
        with getattr(self.history, 'commit_guard', nullcontext()), LOCK:
            if self.vault.names() != self.original_names:
                raise ConflictError("The vault changed while Assistant was working. Nothing was saved; try again.")
            for name, content in self.before.items():
                path = self.vault.path(name)
                current = path.read_text(encoding="utf-8-sig") if path.exists() else None
                if current != content:
                    raise ConflictError(f"'{name}' changed during this request. Nothing was saved; try again.")
            if self.calendar.path.read_text(encoding="utf-8-sig") != self.calendar_before:
                raise ConflictError("The calendar changed during this request. Nothing was saved; try again.")
            changes = [dict(kind="note", name=name, before=self.before[name], after=text)
                       for name, text in self.notes.items() if text != self.before[name]]
            if self.calendar_data != json.loads(self.calendar_before):
                after = json.dumps(self.calendar_data, ensure_ascii=False, indent=2) + "\n"
                changes.append(dict(kind="calendar", before=self.calendar_before, after=after))
            if self.memory is not None:
                if self.memory.raw() != self.memory_before:
                    raise ConflictError("Personal Memory changed during this request. Nothing was saved; try again.")
                original = json.loads(self.memory_before) if self.memory_before is not None else dict(schema_version=1, memories=[])
                if self.memory_data != original:
                    validate_document(self.memory_data)
                    after = json.dumps(self.memory_data, ensure_ascii=False, indent=2) + "\n"
                    changes.append(dict(kind="memory", before=self.memory_before, after=after))
            return self.history.commit(changes, self.summaries, request)
