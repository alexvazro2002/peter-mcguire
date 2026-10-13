"""Lossless editing of ordinary Markdown lists, not a separate list database."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz.fuzz import ratio

MARKER = re.compile(r"^(\s*)([-*+]\s+|\d+[.)]\s+)(?:\[([ xX])\]\s+)?(.+)$")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
META = re.compile(r"\s*<!-- nexus-assistant: (\{.*?\}) -->\s*$")


def canonical(text):
    text = META.sub("", text)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[\[([^]|]+)(?:\|([^]]+))?\]\]", lambda m: m[2] or m[1], text)
    text = unicodedata.normalize("NFKC", text).casefold()
    text = " ".join(re.findall(r"\w+", text))
    return re.sub(r"^(?:the|a|an) ", "", text)


@dataclass
class ListItem:
    line: int
    end: int
    text: str
    raw: list[str]
    prefix: str
    metadata: dict


class MarkdownList:
    def __init__(self, text, section=None):
        self.lines = text.splitlines(keepends=True)
        self.start, self.end = 0, len(self.lines)
        headings = [(i, m[1], len(line) - len(line.lstrip("#"))) for i, line in enumerate(self.lines)
                    if (m := HEADING.match(line.strip()))]
        if section:
            matches = [h for h in headings if h[1].casefold() == section.casefold()]
            if len(matches) != 1:
                raise ValueError("Find a unique existing list heading first.")
            at, _, level = matches[0]
            self.start = at + 1
            self.end = next((i for i, _, depth in headings if i > at and depth <= level), len(self.lines))
        candidates, fenced = [], False
        for i in range(self.start, self.end):
            line = self.lines[i].rstrip("\r\n")
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            if not fenced and (m := MARKER.match(line)):
                candidates.append((i, m))
        if not section:
            groups = {next((h[1] for h in reversed(headings) if h[0] < i), "") for i, _ in candidates}
            if len(groups) > 1:
                raise ValueError("This note has lists under several headings. Specify a section from read_note.")
        indent = min((len(m[1]) for _, m in candidates), default=0)
        top = [(i, m) for i, m in candidates if len(m[1]) == indent]
        self.items, self.plain = [], False
        for i, m in top:
            end = i + 1
            # Keep nested bullets and indented continuation lines attached to their parent.
            while end < self.end and self.lines[end].strip() and len(self.lines[end]) - len(self.lines[end].lstrip()) > indent:
                end += 1
            body, metadata = m[4], {}
            if meta := META.search(body):
                try:
                    metadata = json.loads(meta[1])
                except ValueError:
                    pass
                body = body[:meta.start()]
            metadata = {k: v for k, v in metadata.items() if k in {"date", "activity"} and isinstance(v, str)}
            prefix = m[1] + m[2] + (f"[{m[3]}] " if m[3] is not None else "")
            self.items.append(ListItem(i, end, body.strip(), self.lines[i:end], prefix, metadata))
        if not self.items:
            groups, group = [], []
            for i in range(self.start, self.end):
                raw = self.lines[i].strip()
                body = META.sub("", raw)
                if (body and len(body) <= 200 and not body.startswith(("#", "<", "```", "~~~")) and
                        not body.endswith((".", ":", ";", "!", "?")) and ":" not in body):
                    group.append((i, body))
                else:
                    if len(group) >= 2 or len(group) == 1 and META.search(self.lines[group[0][0]]):
                        groups.append(group)
                    group = []
            if len(group) >= 2 or len(group) == 1 and META.search(self.lines[group[0][0]]):
                groups.append(group)
            if len(groups) == 1:
                self.plain = True
                for i, body in groups[0]:
                    metadata = {}
                    if meta := META.search(self.lines[i].strip()):
                        try:
                            metadata = json.loads(meta[1])
                        except ValueError:
                            pass
                    self.items.append(ListItem(i, i + 1, body, [self.lines[i]], "", metadata))
            elif groups:
                raise ValueError("Several plain-text blocks could be lists. Specify a heading or edit the note explicitly.")

    def resolve(self, text):
        matches = [item for item in self.items if canonical(item.text) == canonical(text)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError("Several items match this title. Read/edit the note to disambiguate them.")
        ranked = sorted(((ratio(canonical(text), canonical(item.text)), item) for item in self.items),
                        key=lambda pair: pair[0], reverse=True)
        if ranked and ranked[0][0] >= 92 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 8):
            return ranked[0][1]
        return None

    def _rewrite(self, order):
        if not self.items:
            return "".join(self.lines)
        numbered = bool(re.match(r"\s*\d+[.)]", self.items[0].prefix))
        replacements = []
        for i, item in enumerate(order, 1):
            raw = list(item.raw)
            if numbered:
                raw[0] = re.sub(r"^(\s*)\d+([.)])", lambda m: f"{m[1]}{i}{m[2]}", raw[0])
            if raw and not raw[-1].endswith(("\n", "\r")):
                raw[-1] += "\n"
            replacements.extend(raw)
        covered = {i for item in self.items for i in range(item.line, item.end)}
        output = []
        for i, line in enumerate(self.lines):
            if i == self.items[0].line:
                output.extend(replacements)
            if i not in covered:
                output.append(line)
        # Only contiguous list blocks can be safely reordered without relocating prose.
        between = range(self.items[0].line, self.items[-1].end)
        if any(i not in covered and self.lines[i].strip() for i in between):
            raise ValueError("This list contains intervening prose. Edit a specific section or the note instead.")
        return "".join(output)

    def add(self, text, *, position="alphabetical", metadata=None):
        if self.resolve(text):
            return "".join(self.lines), False
        prefix = self.items[0].prefix if self.items else "- "
        prefix = re.sub(r"\[[xX]\]", "[ ]", prefix)
        suffix = " <!-- nexus-assistant: " + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + " -->" if metadata else ""
        raw = [prefix + text + suffix + "\n"]
        item = ListItem(-1, -1, text, raw, prefix, metadata or {})
        order = list(self.items)
        if position == "top":
            order.insert(0, item)
        elif position == "bottom":
            order.append(item)
        else:
            order.append(item)
            order.sort(key=lambda i: canonical(i.text))
        if self.items:
            return self._rewrite(order), True
        before, after = "".join(self.lines[:self.end]), "".join(self.lines[self.end:])
        return before.rstrip("\r\n") + "\n\n" + raw[0] + ("\n" if after else "") + after, True

    def remove(self, text):
        item = self.resolve(text)
        if item is None:
            return "".join(self.lines), None
        return "".join(self.lines[:item.line] + self.lines[item.end:]), item

    def move(self, text, position):
        item = self.resolve(text)
        if item is None:
            raise ValueError("Item not found. Read the list and select an existing title.")
        order = [i for i in self.items if i is not item]
        target = 0 if position == "top" else len(order) if position == "bottom" else int(position)
        if not 0 <= target <= len(order):
            raise ValueError("Position is outside this list (indexes start at 0).")
        order.insert(target, item)
        return self._rewrite(order), item
