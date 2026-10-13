"""Permanent note identities and lossless Markdown links, without modifying note bodies.

The small JSON sidecar contains identities and usage only. Markdown remains the source
of truth; parsed links/metadata are cached in memory and refreshed by file signature.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import unquote

from app.config import atomic_write

LOCK = threading.RLock()
CODE = re.compile(r"(```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)|`[^`\n]*`)", re.S)
WIKI = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
ID_DESTINATION = re.compile(r"\(\s*nexus-note:([^\s)]+)\s*\)")


def canonical_id(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _unescape(value: str) -> str:
    return re.sub(r"\\([\\\[\]])", r"\1", value)


def make_link(words: str, destination_id: str) -> str:
    """Encode an arbitrary display label without changing its rendered text."""
    destination_id = canonical_id(destination_id)
    if not destination_id:
        raise ValueError("Choose an existing note with a permanent ID.")
    if not words:
        raise ValueError("Select some text to link first.")
    label = words.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    return f"[{label}](nexus-note:{destination_id})"


def parse_links(text: str) -> list[dict]:
    """Return ordered links with exact Unicode source offsets; code is excluded.

    ID destinations are deliberately retained even when invalid or deleted, allowing
    viewers to show broken links and still offer change/remove actions.
    """
    plain = CODE.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)
    if plain.startswith("---\n") or plain.startswith("---\r\n"):
        front = re.match(r"\A---\r?\n.*?\r?\n(?:---|\.\.\.)\s*(?:\r?\n|$)", plain, re.S)
        if front:
            plain = " " * front.end() + plain[front.end():]
    result, index = [], 0
    while index < len(plain):
        start = plain.find("[", index)
        if start < 0:
            break
        index = start + 1
        # Ignore image syntax and escaped brackets.
        slash = start - 1
        while slash >= 0 and plain[slash] == "\\":
            slash -= 1
        if (start - slash - 1) % 2 or (start and plain[start - 1] == "!"):
            continue
        wiki = WIKI.match(plain, start)
        if wiki:
            words_group = 2 if wiki.group(2) is not None else 1
            occurrence = dict(start=start, end=wiki.end(), label_start=wiki.start(words_group),
                              label_end=wiki.end(words_group), words=wiki.group(words_group),
                              target=wiki.group(1).strip(), target_id=None, kind="wiki")
        else:
            end, depth = start + 1, 1
            while end < len(plain) and depth:
                if plain[end] == "\\":
                    end += 2
                    continue
                if plain[end] == "[":
                    depth += 1
                elif plain[end] == "]":
                    depth -= 1
                end += 1
            destination = ID_DESTINATION.match(plain, end) if not depth else None
            if not destination:
                continue
            target = unquote(destination.group(1))
            occurrence = dict(start=start, end=destination.end(), label_start=start + 1,
                              label_end=end - 1, words=_unescape(text[start + 1:end - 1]),
                              target=target, target_id=canonical_id(target), kind="id")
        position = occurrence["start"]
        line_start, line_end = text.rfind("\n", 0, position) + 1, text.find("\n", occurrence["end"])
        if line_end < 0:
            line_end = len(text)
        # Keep the selected phrase in the excerpt even on a long source line.
        before = text[max(line_start, position - 70):position]
        after = text[occurrence["end"]:min(line_end, occurrence["end"] + 90)]
        occurrence.update(line=text.count("\n", 0, position) + 1,
                          excerpt=(before + occurrence["words"] + after).strip(),
                          text=text[line_start:line_end].strip())
        result.append(occurrence)
        index = occurrence["end"]
    return result


def replace_link(text: str, start: int, end: int, destination_id: str) -> str:
    """Create/change one link, refusing partially overlapping existing links."""
    if not 0 <= start < end <= len(text):
        raise ValueError("The selected text changed. Select it again.")
    overlapping = [link for link in parse_links(text) if link["start"] < end and link["end"] > start]
    if overlapping:
        if len(overlapping) != 1:
            raise ValueError("Select one link at a time.")
        link = overlapping[0]
        if not (link["start"] <= start <= link["label_start"] and link["label_end"] <= end <= link["end"]
                or link["label_start"] <= start < end <= link["label_end"]):
            raise ValueError("Select just the linked text before changing its destination.")
        start, end, words = link["start"], link["end"], link["words"]
    else:
        words = text[start:end]
    return text[:start] + make_link(words, destination_id) + text[end:]


def remove_link(text: str, start: int, end: int) -> str:
    for link in parse_links(text):
        if link["start"] <= start < end <= link["end"]:
            return text[:link["start"]] + link["words"] + text[link["end"]:]
    raise ValueError("The selected link changed. Select it again.")


def extract_metadata(text: str) -> dict:
    """Read simple safe frontmatter; no YAML constructors or external resources."""
    result = {"project": "", "tags": []}
    front = re.match(r"\A\ufeff?---\r?\n(.*?)\r?\n(?:---|\.\.\.)(?:\r?\n|$)", text, re.S)
    if not front:
        return result
    collecting_tags = False
    for line in front.group(1).splitlines():
        item = re.match(r"^\s+-\s+(.+?)\s*$", line)
        if collecting_tags and item:
            result["tags"].append(item.group(1).strip("\"' "))
            continue
        field = re.match(r"^([A-Za-z_]+)\s*:\s*(.*?)\s*$", line)
        if not field:
            continue
        key, value = field.group(1).casefold(), field.group(2)
        collecting_tags = key in {"tags", "tag"}
        if key == "project":
            result["project"] = value.strip("\"' ")[:180]
        elif collecting_tags:
            result["tags"].extend(part.strip("\"' #") for part in value.strip("[]").split(",") if part.strip())
    result["tags"] = list(dict.fromkeys(tag[:80] for tag in result["tags"] if tag))[:100]
    return result


class NoteIndex:
    """Persistent, non-invasive identity map with incremental link indexing."""
    def __init__(self, root: Path):
        self.root = root
        self.path = root / ".nexus" / "notes.json"
        self.records: dict[str, dict] = {}
        self.cache: dict[str, dict] = {}
        self.signature = None
        self.loaded = False
        self.revision = 0
        self._relations_revision = -1
        self._outgoing = {}
        self._incoming = {}
        self._titles = {}

    def _signature(self):
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError("The note identity index must be a local file, not a symbolic link.")
        if not self.path.exists():
            return None
        stat = self.path.stat()
        return stat.st_mtime_ns, stat.st_size

    def _load(self):
        signature = self._signature()
        if self.loaded and signature == self.signature:
            return
        records = {}
        if signature:
            try:
                document = json.loads(self.path.read_text(encoding="utf-8"))
                if document.get("version") != 1 or not isinstance(document.get("notes"), list):
                    raise ValueError("Unsupported note identity index")
                titles = set()
                for record in document["notes"]:
                    identity = canonical_id(record.get("id"))
                    title = record.get("title")
                    if not identity or identity in records or not isinstance(title, str) or not title:
                        raise ValueError("Duplicate or invalid note identity")
                    if not record.get("deleted"):
                        if title.casefold() in titles:
                            raise ValueError("Ambiguous note title in identity index")
                        titles.add(title.casefold())
                    records[identity] = dict(record, id=identity)
            except (OSError, ValueError, TypeError, AttributeError) as error:
                raise ValueError("The note identity index is invalid. Its original file and all notes have been preserved; repair .nexus/notes.json before editing links.") from error
        self.records, self.signature, self.loaded = records, signature, True
        self.cache.clear()
        self.revision += 1

    def _save(self):
        self._signature()
        atomic_write(self.path, json.dumps({"version": 1, "notes": list(self.records.values())},
                                         indent=2, ensure_ascii=False) + "\n")
        self.signature = self._signature()

    @staticmethod
    def _file_signature(path):
        stat = path.stat()
        return (stat.st_mtime_ns, stat.st_size, stat.st_ino), f"{stat.st_dev}:{stat.st_ino}"

    def _parse(self, path: Path):
        signature, file_id = self._file_signature(path)
        cached = self.cache.get(path.stem)
        if cached and cached["signature"] == signature:
            return cached
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        cached = dict(signature=signature, file_id=file_id, digest=hashlib.sha256(raw).hexdigest(),
                      links=parse_links(text), metadata=extract_metadata(text))
        self.cache[path.stem] = cached
        self.revision += 1
        return cached

    def refresh(self):
        with LOCK:
            self._load()
            paths = sorted((p for p in self.root.glob("*.md") if not p.is_symlink()), key=lambda p: p.stem.casefold())
            if len({p.stem.casefold() for p in paths}) != len(paths):
                raise ValueError("Two notes have the same title ignoring case. Rename one before creating links.")
            active = {record["title"]: record for record in self.records.values() if not record.get("deleted")}
            present = {p.stem for p in paths}
            missing = [record for title, record in active.items() if title not in present]
            unknown = [path for path in paths if path.stem not in active]
            parsed_unknown = {path.stem: self._parse(path) for path in unknown}
            digest_counts = Counter(item["digest"] for item in parsed_unknown.values())
            file_counts = Counter(item["file_id"] for item in parsed_unknown.values())
            changed = False
            for path in paths:
                name, parsed = path.stem, self._parse(path)
                record = active.get(name)
                if record is None:
                    # Only reconcile uniquely identifiable renames from this scan.
                    matches = [item for item in missing if item.get("file_id") == parsed["file_id"]]
                    if len(matches) != 1 or file_counts[parsed["file_id"]] != 1:
                        matches = [item for item in missing if item.get("digest") == parsed["digest"]]
                        if len(matches) != 1 or digest_counts[parsed["digest"]] != 1:
                            matches = []
                    record = matches[0] if matches else dict(id=str(uuid.uuid4()), last_opened=0)
                    if matches:
                        missing.remove(record)
                    record.update(title=name, deleted=False)
                    self.records[record["id"]] = record
                    changed = True
                values = dict(file_id=parsed["file_id"], digest=parsed["digest"])
                if any(record.get(key) != value for key, value in values.items()):
                    record.update(values)
                    changed = True
            for record in missing:
                record.update(deleted=True, deleted_at=time.time())
                changed = True
            self.cache = {name: item for name, item in self.cache.items() if name in present}
            if changed:
                self.revision += 1
                self._save()
            self._titles = {record["title"]: record for record in self.records.values() if not record.get("deleted")}
            return self

    def record_write(self, name: str, *, fresh=False, restore=True):
        """Called with LOCK after writing one file, avoiding a full vault rescan."""
        parsed = self._parse(self.root / f"{name}.md")
        record = self._titles.get(name)
        if fresh:
            record = None
        if record is None and restore and not fresh:
            candidates = [r for r in self.records.values() if r.get("deleted") and r["title"] == name
                          and r.get("digest") == parsed["digest"]]
            if len(candidates) == 1:
                record = candidates[0]
        if record is None:
            record = dict(id=str(uuid.uuid4()), last_opened=0)
        record.update(title=name, deleted=False, digest=parsed["digest"], file_id=parsed["file_id"])
        record.pop("deleted_at", None)
        self.records[record["id"]] = record
        self._titles[name] = record
        self.revision += 1
        self._save()

    def renamed(self, old: str, new: str):
        record = self._titles.pop(old)
        record["title"] = new
        self._titles[new] = record
        self.cache.pop(old, None)
        self.record_write(new)

    def deleted(self, name: str):
        record = self._titles.pop(name)
        record.update(deleted=True, deleted_at=time.time())
        self.cache.pop(name, None)
        self.revision += 1
        self._save()

    def metadata(self, name: str):
        record = self._titles.get(name)
        if not record:
            raise ValueError("The destination note no longer exists.")
        return dict(self.cache[name]["metadata"], id=record["id"], title=name,
                    folder=self.root.name, last_opened=record.get("last_opened", 0))

    def resolve(self, reference: str):
        identity = canonical_id(reference)
        if identity:
            record = self.records.get(identity)
            return record["title"] if record and not record.get("deleted") else None
        # Legacy titles remain supported; new links always use permanent IDs.
        if reference in self._titles:
            return reference
        matches = [name for name in self._titles if name.casefold() == reference.casefold()]
        return matches[0] if len(matches) == 1 else None

    def occurrences(self, name: str | None = None, *, text: str | None = None):
        parsed = parse_links(text) if text is not None else self.cache.get(name, {}).get("links", [])
        result = []
        for occurrence in parsed:
            target = (self.resolve(occurrence["target_id"]) if occurrence["target_id"] else None) if occurrence["kind"] == "id" else self.resolve(occurrence["target"])
            identity = self._titles[target]["id"] if target else occurrence["target_id"]
            result.append(dict(occurrence, target_name=target, target_id=identity, broken=target is None))
        return result

    def relations(self):
        if self._relations_revision != self.revision:
            self._outgoing, self._incoming = {}, {}
            for name in self._titles:
                outgoing = self.occurrences(name)
                self._outgoing[name] = outgoing
                for occurrence in outgoing:
                    if not occurrence["broken"]:
                        self._incoming.setdefault(occurrence["target_name"], []).append(
                            dict(occurrence, source=name, source_id=self._titles[name]["id"],
                                 source_metadata=self.metadata(name)))
            self._relations_revision = self.revision
        return self._outgoing, self._incoming

    def search(self, query="", *, limit=50, exclude=None, recent=None):
        _, incoming = self.relations()
        recent = recent or {}
        query = query.strip().casefold()
        ranked = []
        for name in self._titles:
            if name == exclude or self._titles[name]["id"] == exclude:
                continue
            metadata = self.metadata(name)
            haystack = " ".join([name, metadata["project"], metadata["folder"], *metadata["tags"]]).casefold()
            if query:
                if query in name.casefold():
                    score = 110 + (20 if name.casefold().startswith(query) else 0)
                elif query in haystack:
                    score = 100
                else:
                    score = max(SequenceMatcher(None, query, name.casefold()).ratio(),
                                max((SequenceMatcher(None, query, word).ratio() for word in haystack.split()), default=0)) * 100
                    if score < 45:
                        continue
            else:
                score = 0
            count = len(incoming.get(name, []))
            last_opened = max(metadata["last_opened"], recent.get(name, 0))
            # Small usage bonuses keep textual relevance dominant.
            score += min(count, 10) * .5 + (4 if last_opened and time.time() - last_opened < 604800 else 0)
            ranked.append((score, last_opened, count, name.casefold(), dict(metadata, link_count=count)))
        ranked.sort(key=lambda row: (-row[0], -row[1], -row[2], row[3]))
        return [row[-1] for row in ranked[:max(0, int(limit))]]
