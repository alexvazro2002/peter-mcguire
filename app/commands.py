"""Deterministic command registry. Commands describe allowlisted UI actions only."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from rapidfuzz.fuzz import WRatio

from app.config import atomic_write
from app.tools import TOOLS


class CommandRegistry:
    def __init__(self, path: Path, vault, calendar):
        self.path, self.vault, self.calendar = path, vault, calendar
        self.lock = threading.RLock()
        self.static = []
        for key, title, icon in [("home", "Today's Focus", "home"), ("calendar", "Calendar", "calendar"),
                                 ("tools", "Tools", "tools"), ("notes", "Notes", "notes"),
                                 ("graph", "Notes graph", "graph"), ("library", "AI Library", "library"),
                                 ("assistant", "Assistant", "assistant"), ("memory", "Personal Memory", "memory"),
                                 ("settings", "Settings", "settings")]:
            self.register(f"page:{key}", title, "Pages", f"Open {title}", icon, "route", {"route": key})
        self.register("create:note", "Create a new note", "Create", "Save a new local Markdown note", "plus", "new_note", {})
        self.register("create:task", "Create a new task", "Create", "Add a scheduled or unscheduled task", "plus", "new_task", {})
        for tool in TOOLS:
            route = "metube" if tool.key == "metube" else "service" if tool.key == "stirling_pdf" else "tool"
            self.register(f"tool:{tool.key}", tool.title, "Automations" if tool.category == "Automation" else "Tools",
                          tool.description + (" Configure and run locally." if tool.key not in {"metube", "stirling_pdf"} else " Start or open the local service."),
                          "tools", "route", {"route": route, "id": tool.key})

    def register(self, identity, title, group, description, icon, action, payload):
        if any(c["id"] == identity for c in self.static):
            raise ValueError("Duplicate command ID")
        self.static.append(dict(id=identity, title=title, group=group, description=description,
                                icon=icon, action=action, payload=payload))

    def preferences(self):
        if not self.path.exists():
            return {"recent": [], "pinned": []}
        if self.path.is_symlink():
            raise ValueError("Command preferences must be a local file.")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(not isinstance(data.get(key), list) or
                any(not isinstance(item, str) or len(item) > 150 for item in data[key]) for key in ("recent", "pinned")):
            raise ValueError("Command preferences are invalid; the file was preserved.")
        return data

    def _dynamic(self, query):
        notes = self.vault.search_notes(query, limit=100) if query else self.vault.search_notes(limit=30)
        for note in notes:
            context = " · ".join(part for part in (note["project"], note["folder"], ", ".join(note["tags"])) if part)
            yield dict(id="note:" + note["id"], title=note["title"], group="Notes", description=context,
                       icon="notes", action="route", payload={"route": "notes", "id": note["id"]})
        for task in self.calendar.entries():
            if task.get("done"):
                continue
            yield dict(id="task:" + task["id"], title=task["title"], group="Tasks",
                       description=(task.get("date") or "Unscheduled") + " · " + (task.get("time") or "All day"),
                       icon="calendar", action="task", payload={"id": task["id"]})

    def resolve(self, identity):
        command = next((c for c in self.static if c["id"] == identity), None)
        if command:
            return dict(command)
        kind, _, target = identity.partition(":")
        if kind == "note":
            name = self.vault.resolve_id(target)
            if name:
                return dict(id=identity, title=name, group="Notes", description="Open note", icon="notes",
                            action="route", payload={"route": "notes", "id": target})
        if kind == "task":
            task = next((t for t in self.calendar.entries() if t["id"] == target), None)
            if task:
                return dict(id=identity, title=task["title"], group="Tasks", description=task.get("date") or "Unscheduled",
                            icon="calendar", action="task", payload={"id": target})
        raise ValueError("This command is no longer available.")

    def search(self, query="", limit=60):
        query = query.strip().casefold()[:200]
        with self.lock:
            preferences = self.preferences()
        if not query:
            result, seen = [], set()
            for group, identities in [("Pinned", preferences["pinned"]), ("Recent", preferences["recent"])]:
                for identity in identities:
                    try:
                        command = self.resolve(identity)
                    except ValueError:
                        continue
                    if identity not in seen:
                        result.append(dict(command, group=group, pinned=identity in preferences["pinned"]))
                        seen.add(identity)
            result.extend(dict(c, pinned=c["id"] in preferences["pinned"]) for c in self.static if c["id"] not in seen)
            return result[:limit]
        ranked = []
        for command in [*self.static, *self._dynamic(query)]:
            title = command["title"].casefold()
            haystack = title + " " + command["description"].casefold()
            score = 120 if title == query else 110 if title.startswith(query) else 100 if query in haystack else WRatio(query, haystack)
            if score >= 50:
                ranked.append((score, dict(command, pinned=command["id"] in preferences["pinned"])))
        ranked.sort(key=lambda row: (-row[0], row[1]["title"].casefold()))
        return [command for _, command in ranked[:limit]]

    def record(self, identity, *, pin=None):
        command = self.resolve(identity)
        with self.lock:
            preferences = self.preferences()
            if pin is None:
                preferences["recent"] = [identity, *[c for c in preferences["recent"] if c != identity]][:20]
            else:
                preferences["pinned"] = [c for c in preferences["pinned"] if c != identity]
                if pin:
                    preferences["pinned"].append(identity)
                preferences["pinned"] = preferences["pinned"][:40]
            atomic_write(self.path, json.dumps(preferences, indent=2) + "\n")
        return command
