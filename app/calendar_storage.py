"""Local calendar; imports old checklists once without changing their Markdown."""
import calendar
import json
import re
import time
import uuid
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from app.config import atomic_write
from app.storage import Checklist


def month_days(day):
    first = day.replace(day=1)
    start = first - timedelta(days=first.weekday())
    return [start + timedelta(days=i) for i in range(42)]


def shift_month(day, offset):
    year, month = divmod(day.year * 12 + day.month - 1 + offset, 12)
    return day.replace(year=year, month=month + 1, day=min(day.day, calendar.monthrange(year, month + 1)[1]))


class CalendarStore:
    def __init__(self, path: Path, legacy_files=()):
        self.path = path
        if not path.exists():
            data = {"schema_version": 1, "entries": []}
            for label, file in legacy_files:
                for task in Checklist(file).items():
                    data["entries"].append({"id": uuid.uuid4().hex, "title": task.text[:240], "kind": "task",
                        "date": None, "time": None, "details": task.text if len(task.text) > 240 else "", "note": "", "done": task.done, "origin": label})
            self.write(data)
        self.read()

    @staticmethod
    def validate(item):
        title = item.get("title", "")
        if not isinstance(title, str) or not title.strip() or len(title) > 240:
            raise ValueError("Enter a title between 1 and 240 characters.")
        if item.get("kind") not in {"task", "note"}:
            raise ValueError("Choose Task or Note.")
        if item.get("date"):
            value = item["date"]
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("Use YYYY-MM-DD for the date, or leave it empty for Unscheduled.")
            date.fromisoformat(value)
        if item.get("time") and (not isinstance(item["time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", item["time"])):
            raise ValueError("Use 24-hour HH:MM, or leave the time empty for All day.")
        if item.get("time") and not item.get("date"):
            raise ValueError("Choose a date before setting a time.")
        if not isinstance(item.get("details", ""), str) or not isinstance(item.get("note", ""), str):
            raise ValueError("Details and note links must be text.")

    def read(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("entries"), list):
            raise ValueError("calendar.json is invalid. Restore a backup before editing.")
        ids = set()
        for item in data["entries"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in ids:
                raise ValueError("Calendar entries have invalid or duplicate IDs.")
            self.validate(item)
            ids.add(item["id"])
        return data

    def write(self, data):
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def entries(self, on=None, *, unscheduled=False, query="", show_done=True):
        items = self.read()["entries"]
        if on is not None:
            items = [e for e in items if e.get("date") == on.isoformat()]
        if unscheduled:
            items = [e for e in items if not e.get("date")]
        q = query.casefold().strip()
        items = [e for e in items if (show_done or not e.get("done")) and q in (e["title"] + " " + e.get("details", "") + " " + e.get("note", "")).casefold()]
        return sorted(items, key=lambda e: (e.get("date") or "9999", e.get("time") or "", e.get("done", False), e["title"].casefold()))

    def save(self, values, entry_id=None):
        data = self.read()
        position = None
        if entry_id:
            position = next((i for i, e in enumerate(data["entries"]) if e["id"] == entry_id), None)
            if position is None:
                raise ValueError("This item was removed. Reopen the calendar.")
        item = dict(deepcopy(data["entries"][position]) if position is not None else {}, **deepcopy(values))
        item["title"] = str(item.get("title", "")).strip()
        item["date"], item["time"] = item.get("date") or None, item.get("time") or None
        item["id"] = entry_id or uuid.uuid4().hex
        item["done"] = bool(item.get("done")) if item.get("kind") == "task" else False
        self.validate(item)
        if entry_id:
            item.setdefault("origin", data["entries"][position].get("origin", ""))
            data["entries"][position] = item
        else:
            data["entries"].append(item)
        self.write(data)
        return item["id"]

    def complete(self, entry_id, done):
        item = next((e for e in self.entries() if e["id"] == entry_id), None)
        if not item or item["kind"] != "task":
            raise ValueError("Task no longer exists.")
        self.save(dict(item, done=done), entry_id)

    def delete(self, entry_id):
        data = self.read()
        remaining = [e for e in data["entries"] if e["id"] != entry_id]
        if len(remaining) == len(data["entries"]):
            raise ValueError("Item no longer exists.")
        atomic_write(self.path.parent / ".history" / f"calendar-{time.time_ns()}.json", self.path.read_text(encoding="utf-8"))
        data["entries"] = remaining
        self.write(data)
