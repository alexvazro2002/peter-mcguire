"""Validated local personal memories shared by the dashboard and local assistant.

Only user-provided facts belong here. This store never imports environment variables,
chat transcripts, configuration or prompts. See PERSONAL_MEMORY.md for the API.
"""
from __future__ import annotations

import json
import re
import threading
import unicodedata
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR, atomic_write

LOCK = threading.RLock()
CATEGORIES = {
    "personal": "Personal details", "preferences": "Preferences", "work": "Work",
    "projects": "Projects", "devices": "Devices and technical setup",
    "decisions": "Decisions", "goals": "Goals", "temporary": "Temporary context",
}
FIELDS = {"id", "content", "category", "created_at", "updated_at", "source", "temporary", "pinned"}
EDITABLE = {"content", "category", "source", "temporary", "pinned"}
MAX_MEMORIES = 20000
SENSITIVE = re.compile(
    r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----|"
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{12,}|"
    r"AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{12,})\b|"
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"\b(?:api[ _-]?key|password|passwd|passphrase|client[ _-]?secret|"
    r"access[ _-]?token|refresh[ _-]?token|auth(?:entication|orization)?[ _-]?token|"
    r"secret[ _-]?key|token)\b[\"']?\s*(?::|=|\bis\b)\s*[\"']?\S+|"
    r"https?://[^\s/:]+:[^\s/@]+@|"
    r"(?:<\|(?:system|im_start|start_header_id)\|>|"
    r"\b(?:hidden[ _-]?)?(?:system[ _-]?prompt|developer[ _-]?instructions)\s*[:=])",
    re.I,
)


def _text(value, label, maximum, *, required=False):
    if not isinstance(value, str):
        raise ValueError(f"Memory {label} must be text.")
    value = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if (required and not value) or len(value) > maximum:
        raise ValueError(f"Memory {label} must contain {'1' if required else '0'}–{maximum:,} characters.")
    if any(unicodedata.category(ch) in {"Cc", "Cf"} and ch not in "\n\t" for ch in value):
        raise ValueError(f"Memory {label} contains unsupported control characters.")
    if SENSITIVE.search(value):
        # Never echo rejected content into the dashboard, logs or model tool output.
        raise ValueError("Credentials, authentication tokens and hidden prompts cannot be stored in Personal Memory.")
    if re.search(r"<\s*/?\s*(?:script|iframe|object|embed|style)\b", value, re.I):
        raise ValueError("Personal memories must be plain text, without embedded active content.")
    return value


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("Invalid memory ID.")
    return value


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Memory dates must be ISO timestamps including a timezone.") from None
    return result


def validate_document(data):
    """Validate persisted/journal data before any entry can be shown to a user/model."""
    if (not isinstance(data, dict) or set(data) != {"schema_version", "memories"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1
            or not isinstance(data["memories"], list) or len(data["memories"]) > MAX_MEMORIES):
        raise ValueError("Personal Memory has an unsupported or invalid data format. Its file was preserved.")
    seen = set()
    for entry in data["memories"]:
        if not isinstance(entry, dict) or set(entry) != FIELDS:
            raise ValueError("Personal Memory contains an invalid entry. Its file was preserved.")
        memory_id = _id(entry["id"])
        if memory_id in seen:
            raise ValueError("Personal Memory contains duplicate IDs. Its file was preserved.")
        seen.add(memory_id)
        _text(entry["content"], "content", 4000, required=True)
        _text(entry["source"], "source", 300)
        if not isinstance(entry["category"], str) or entry["category"] not in CATEGORIES:
            raise ValueError("Choose a valid memory category.")
        if type(entry["temporary"]) is not bool or type(entry["pinned"]) is not bool:
            raise ValueError("Temporary and pinned memory settings must be boolean.")
        if _timestamp(entry["updated_at"]) < _timestamp(entry["created_at"]):
            raise ValueError("A memory cannot be updated before it was created.")
    return data


def stage_save(data, values, memory_id=None):
    """Apply a validated add/patch to a caller-owned snapshot; never writes a file."""
    if not isinstance(values, dict) or not values or set(values) - EDITABLE:
        raise ValueError("Supply valid memory fields: content, category, source, temporary and pinned.")
    validate_document(data)
    now = datetime.now(timezone.utc).isoformat()
    if memory_id is None:
        if len(data["memories"]) >= MAX_MEMORIES:
            raise ValueError("Personal Memory is full. Export or remove entries before adding more.")
        entry = dict(id=uuid.uuid4().hex, content="", category="personal", source="Manual entry",
                     temporary=False, pinned=False, created_at=now, updated_at=now)
    else:
        _id(memory_id)
        entry = next((deepcopy(m) for m in data["memories"] if m["id"] == memory_id), None)
        if entry is None:
            raise ValueError("Memory not found. Refresh and try again.")
    before = deepcopy(entry)
    entry.update(values)
    entry["content"] = _text(entry["content"], "content", 4000, required=True)
    entry["source"] = _text(entry["source"], "source", 300)
    if entry != before:
        entry["updated_at"] = now
    validate_document(dict(schema_version=1, memories=[entry]))
    if memory_id is None:
        duplicate = next((m for m in data["memories"] if m["category"] == entry["category"]
                          and m["content"].casefold() == entry["content"].casefold()), None)
        if duplicate is not None:
            raise ValueError("This memory already exists in that category. Edit the existing entry instead.")
        data["memories"].append(entry)
    else:
        data["memories"] = [entry if m["id"] == memory_id else m for m in data["memories"]]
    return deepcopy(entry)


def search_entries(data, query="", category="", temporary=None):
    validate_document(data)
    if not isinstance(query, str) or len(query) > 1000:
        raise ValueError("Use a search query of at most 1,000 characters.")
    if not isinstance(category, str) or category and category not in CATEGORIES:
        raise ValueError("Choose a valid memory category.")
    if temporary is not None and type(temporary) is not bool:
        raise ValueError("Temporary filter must be boolean.")
    words = query.casefold().split()
    entries = [m for m in data["memories"] if (not category or m["category"] == category)
               and (temporary is None or m["temporary"] == temporary)
               and all(word in (m["content"] + " " + m["source"] + " " + CATEGORIES[m["category"]]).casefold()
                       for word in words)]
    entries.sort(key=lambda m: m["updated_at"], reverse=True)
    entries.sort(key=lambda m: not m["pinned"])
    return deepcopy(entries)


class MemoryStore:
    """Local JSON store. Delete/clear require confirmed=True from a real UI action."""

    def __init__(self, path: Path = DATA_DIR / "personal-memory.json"):
        self.path = Path(path).absolute()

    def raw(self):
        if self.path.is_symlink():
            raise ValueError("Linked memory storage files are not allowed.")
        if not self.path.exists():
            return None
        if self.path.stat().st_size > 100_000_000:
            raise ValueError("Personal Memory is too large to load safely. Its file was preserved.")
        return self.path.read_text(encoding="utf-8-sig")

    def read(self):
        with LOCK:
            raw = self.raw()
            try:
                data = json.loads(raw) if raw is not None else dict(schema_version=1, memories=[])
            except (ValueError, UnicodeError):
                raise ValueError("Personal Memory could not be read. Its file was preserved.") from None
            return validate_document(data)

    def _write(self, data):
        validate_document(data)
        self.raw()  # Refuse a storage symlink, including one replaced since loading.
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def restore(self, raw):
        """Internal journal recovery only; dashboard/model callers use save/delete."""
        with LOCK:
            if raw is None:
                self.raw()
                self.path.unlink(missing_ok=True)
            else:
                validate_document(json.loads(raw))
                self.raw()
                atomic_write(self.path, raw)

    def list(self, query="", category="", temporary=None):
        return search_entries(self.read(), query, category, temporary)

    def get(self, memory_id):
        _id(memory_id)
        for entry in self.read()["memories"]:
            if entry["id"] == memory_id:
                return deepcopy(entry)
        raise ValueError("Memory not found. Refresh and try again.")

    def save(self, values, memory_id=None):
        with LOCK:
            data = self.read()
            entry = stage_save(data, values, memory_id)
            self._write(data)
            return entry

    def delete(self, memory_id, *, confirmed=False):
        if confirmed is not True:
            raise ValueError("Confirm deletion before removing this memory.")
        with LOCK:
            _id(memory_id)
            data = self.read()
            if not any(m["id"] == memory_id for m in data["memories"]):
                raise ValueError("Memory not found. Refresh and try again.")
            data["memories"] = [m for m in data["memories"] if m["id"] != memory_id]
            self._write(data)

    def clear_temporary(self, *, confirmed=False):
        if confirmed is not True:
            raise ValueError("Confirm before clearing temporary memories.")
        with LOCK:
            data = self.read()
            count = sum(m["temporary"] for m in data["memories"])
            if count:
                data["memories"] = [m for m in data["memories"] if not m["temporary"]]
                self._write(data)
            return count

    def export_json(self):
        return json.dumps(self.read(), ensure_ascii=False, indent=2) + "\n"

    def relevant(self, request, *, limit=8, max_chars=6000):
        """Small deterministic context: pinned facts then relevant word matches."""
        terms = set(re.findall(r"\w{3,}", request.casefold())) - {"the", "and", "for", "that", "with", "what", "you", "your", "can", "have"}
        scored = []
        for entry in self.read()["memories"]:
            words = set(re.findall(r"\w{3,}", (entry["content"] + " " + CATEGORIES[entry["category"]]).casefold()))
            score = len(terms & words)
            if entry["pinned"] or score:
                scored.append((entry["pinned"], score, entry["updated_at"], entry))
        scored.sort(key=lambda row: row[:3], reverse=True)
        result, size = [], 0
        for *_, entry in scored:
            cost = len(json.dumps(entry, ensure_ascii=False))
            if size + cost > max_chars:
                continue
            result.append(deepcopy(entry))
            size += cost
            if len(result) >= limit:
                break
        return result
