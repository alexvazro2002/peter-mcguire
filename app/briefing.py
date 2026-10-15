"""A session's readiness prompt and a truthful local calendar briefing."""
from __future__ import annotations

import re
import threading
from datetime import date, datetime, time
from zoneinfo import ZoneInfo


_AFFIRMATIVE = {
    "yes", "yes please", "yes sir", "yes i am", "yes i'm ready", "yes i am ready",
    "yes let's begin", "yes let's go", "yes let's do it", "yes go ahead",
    "yeah", "yeah please", "yep", "yep please", "sure", "sure please",
    "absolutely", "certainly", "of course", "sure thing", "sounds good",
    "ready", "i'm ready", "i am ready", "i am", "i'm good", "i am good", "all set",
    "i'm ready for today", "i am ready for today", "let's begin", "let's go",
    "let's do it", "let's get started", "let's start", "go ahead", "please do", "okay", "ok", "alright",
}
_NEGATIVE = {
    "no", "no thanks", "no thank you", "not now", "no not now", "not yet",
    "later", "maybe later", "leave it for now", "skip it", "skip this",
}


def _response_phrase(request):
    """Only a complete, simple response can authorise a readiness briefing."""
    if not isinstance(request, str):
        return ""
    phrase = request.casefold().replace("\u2019", "'")
    phrase = re.sub(r"[^\w'\s]", " ", phrase)
    phrase = " ".join(phrase.split())
    # A vocative does not change a simple answer; other instructions do.
    phrase = re.sub(r"^peter\s+|\s+peter$", "", phrase).strip()
    return phrase


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise ValueError("Request cancelled.")


class DailyBriefing:
    """Readiness is session state, independent of model conversation context."""

    def __init__(self, calendar, zone="Europe/London"):
        self.calendar = calendar
        self.zone = ZoneInfo(zone)
        self.awaiting_ready = False
        self.lock = threading.RLock()

    def _now(self, now=None):
        current = now if now is not None else datetime.now(self.zone)
        if current.tzinfo is None:
            current = current.replace(tzinfo=self.zone)
        return current.astimezone(self.zone)

    def start(self, now=None):
        current = self._now(now)
        period = "morning" if current.hour < 12 else "afternoon" if current.hour < 18 else "evening"
        with self.lock:
            self.awaiting_ready = True
        return f"Good {period}. Shall we see what today has in store?"

    def respond(self, request, cancel=None, now=None):
        with self.lock:
            if not self.awaiting_ready:
                return None
            # The prompt applies to the next reply only. A new request supersedes it.
            self.awaiting_ready = False
        _cancelled(cancel)
        phrase = _response_phrase(request)
        if phrase in _NEGATIVE:
            return dict(text="Of course. We'll leave it for now.", sources=[],
                        changes=[], operation_id=None, briefing_declined=True)
        if phrase not in _AFFIRMATIVE:
            return None
        return self.agenda(cancel=cancel, now=now)

    def agenda(self, cancel=None, now=None):
        """Read all saved entries once, without inventing dates or modifying tasks."""
        _cancelled(cancel)
        current = self._now(now)
        today = current.date()
        day_string = today.isoformat()
        entries = self.calendar.entries()
        calendar_items, today_tasks, overdue = [], [], []
        for item in entries:
            _cancelled(cancel)
            scheduled = item.get("date")
            if item.get("kind") == "task":
                if item.get("done"):
                    continue
                if scheduled == day_string:
                    today_tasks.append(item)
                elif scheduled and scheduled < day_string:
                    overdue.append(item)
            elif scheduled == day_string:
                calendar_items.append(item)

        ordering = lambda item: (item.get("date") or "", item.get("time") or "", item["title"].casefold())
        calendar_items.sort(key=ordering)
        today_tasks.sort(key=ordering)
        overdue.sort(key=ordering)
        lines = [f"Here's the day ahead. {today.strftime('%A')}, {today.day} {today.strftime('%B')}."]
        if calendar_items:
            lines.extend(["", "Today's calendar:"])
            lines.extend(self._item_text(item) for item in calendar_items)
        else:
            lines.extend(["", "Your calendar has no other items scheduled today."])
        if today_tasks:
            lines.extend(["", "Tasks pending today:"])
            lines.extend(self._item_text(item, current=current) for item in today_tasks)
        else:
            lines.extend(["", "You have no pending tasks scheduled for today."])
        if overdue:
            lines.extend(["", "Overdue tasks still pending:"])
            lines.extend(self._item_text(item, overdue=True) for item in overdue)
        lines.extend(["", "That's the day in order. I'll leave the schedule as it stands."])
        _cancelled(cancel)
        return dict(text="\n".join(lines), changes=[], operation_id=None,
                    sources=[dict(kind="calendar", date=day_string)],
                    briefing_complete=True, briefing_date=day_string,
                    briefing_counts=dict(calendar=len(calendar_items), today_tasks=len(today_tasks), overdue=len(overdue)))

    @staticmethod
    def _item_text(item, *, current=None, overdue=False):
        title = " ".join(item["title"].split())
        clock = item.get("time")
        label = f"{clock}: {title}" if clock else f"All day: {title}"
        if overdue:
            day = date.fromisoformat(item["date"])
            label += f" (due {day.day} {day.strftime('%B %Y')})"
        elif current is not None and clock and time.fromisoformat(clock) < current.time().replace(tzinfo=None):
            label += " (overdue)"
        return "- " + label
