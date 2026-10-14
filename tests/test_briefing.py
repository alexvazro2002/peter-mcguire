from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace

import pytest

from app.assistant.service import AssistantService
from app.briefing import DailyBriefing
from app.calendar_storage import CalendarStore
from app.storage import Vault


@pytest.mark.parametrize("hour,minute,period", [(0, 0, "morning"), (11, 59, "morning"),
    (12, 0, "afternoon"), (17, 59, "afternoon"), (18, 0, "evening"), (23, 59, "evening")])
def test_start_greeting_uses_local_period(hour, minute, period):
    briefing = DailyBriefing(None)
    assert briefing.start(datetime(2026, 10, 14, hour, minute)) == f"Good {period}, sir. Ready for today?"
    assert briefing.awaiting_ready


def test_greeting_converts_aware_clock_into_london():
    briefing = DailyBriefing(None)
    assert briefing.start(datetime(2026, 10, 14, 11, 30, tzinfo=timezone.utc)) == "Good afternoon, sir. Ready for today?"


def test_ready_briefing_includes_all_today_items_and_pending_tasks_without_edits(tmp_path):
    calendar = CalendarStore(tmp_path / "calendar.json")
    for item in [
        dict(title="Workshop", kind="note", date="2026-10-14", time="14:00"),
        dict(title="Bank appointment", kind="note", date="2026-10-14", time="09:00"),
        dict(title="Submit report", kind="task", date="2026-10-14", time="15:30"),
        dict(title="Morning call", kind="task", date="2026-10-14", time="08:00"),
        dict(title="Read notes", kind="task", date="2026-10-14"),
        dict(title="Finished task", kind="task", date="2026-10-14", done=True),
        dict(title="Overdue invoice", kind="task", date="2026-10-12"),
        dict(title="Old finished task", kind="task", date="2026-10-12", done=True),
        dict(title="Old appointment", kind="note", date="2026-10-12"),
        dict(title="Tomorrow", kind="task", date="2026-10-15"),
        dict(title="Unscheduled", kind="task"),
    ]:
        calendar.save(item)
    before = calendar.path.read_bytes()
    briefing = DailyBriefing(calendar)
    briefing.start(datetime(2026, 10, 14, 10))
    result = briefing.respond("Yes, please, Peter.", now=datetime(2026, 10, 14, 10))
    text = result["text"]
    assert result["briefing_complete"] is True
    assert result["briefing_counts"] == dict(calendar=2, today_tasks=3, overdue=1)
    assert text.index("Bank appointment") < text.index("Workshop")
    assert "08:00: Morning call (overdue)" in text
    assert "All day: Read notes" in text
    assert "Overdue invoice (due 12 October 2026)" in text
    for omitted in ["Finished task", "Old finished task", "Old appointment", "Tomorrow", "Unscheduled"]:
        assert omitted not in text
    assert calendar.path.read_bytes() == before
    assert not briefing.awaiting_ready
    assert briefing.respond("yes", now=datetime(2026, 10, 14, 10)) is None


def test_agenda_uses_local_date_across_midnight(tmp_path):
    calendar = CalendarStore(tmp_path / "calendar.json")
    calendar.save(dict(title="Today's appointment", kind="note", date="2026-10-14"))
    calendar.save(dict(title="Yesterday's appointment", kind="note", date="2026-10-13"))
    briefing = DailyBriefing(calendar)
    clock = datetime(2026, 10, 13, 23, 30, tzinfo=timezone.utc)
    briefing.start(clock)
    result = briefing.respond("I'm ready", now=clock)
    assert result["briefing_date"] == "2026-10-14"
    assert "Today's appointment" in result["text"]
    assert "Yesterday's appointment" not in result["text"]


@pytest.mark.parametrize("reply", ["yes", "YEP!", "Sure, Peter.", "I’m ready.", "Let's begin", "Of course.",
    "yes sir", "yes peter", "yes i am", "sure thing", "sounds good", "i am", "i'm good", "i am good",
    "let's get started", "let's start", "all set", "i am ready peter"])
def test_standalone_positive_reply_starts_briefing(reply, tmp_path):
    briefing = DailyBriefing(CalendarStore(tmp_path / "calendar.json"))
    briefing.start()
    result = briefing.respond(reply)
    assert result["briefing_complete"]
    assert "no pending tasks" in result["text"]


@pytest.mark.parametrize("reply", ["No", "Not now.", "Later, Peter", "No thank you", "leave it for now"])
def test_negative_readiness_reply_defers_without_reading_calendar(reply):
    calendar = SimpleNamespace(entries=lambda: pytest.fail("A declined briefing must not read the calendar"))
    briefing = DailyBriefing(calendar)
    briefing.start()
    assert briefing.respond(reply)["briefing_declined"]
    assert briefing.respond("yes") is None


@pytest.mark.parametrize("reply", ["yes but don't read any emails", "yes, add a 2pm meeting",
    "sure, delete tomorrow's task", "What time is it?", "yes 2", "yes 你好"])
def test_mixed_or_unrelated_request_supersedes_readiness(reply):
    calendar = SimpleNamespace(entries=lambda: pytest.fail("Substantive requests need normal handling"))
    briefing = DailyBriefing(calendar)
    briefing.start()
    assert briefing.respond(reply) is None
    assert not briefing.awaiting_ready
    assert briefing.respond("yes") is None


def test_cancelled_readiness_reply_cannot_emit_agenda_or_retrigger():
    calendar = SimpleNamespace(entries=lambda: pytest.fail("Cancelled request must not read the calendar"))
    briefing = DailyBriefing(calendar)
    briefing.start()
    cancel = Event()
    cancel.set()
    with pytest.raises(ValueError, match="cancelled"):
        briefing.respond("yes", cancel=cancel)
    cancel.clear()
    assert briefing.respond("yes", cancel=cancel) is None


def test_cancellation_during_calendar_read_suppresses_answer():
    cancel = Event()
    def read():
        cancel.set()
        return []
    briefing = DailyBriefing(SimpleNamespace(entries=read))
    briefing.start()
    with pytest.raises(ValueError, match="cancelled"):
        briefing.respond("yes", cancel=cancel)


def test_service_ready_reply_skips_model_and_email_responder(tmp_path):
    calendar = CalendarStore(tmp_path / "calendar.json")
    runtime = SimpleNamespace(ensure=lambda: pytest.fail("Readiness must work without loading the model"))
    service = AssistantService(Vault(tmp_path / "notes"), calendar, runtime, tmp_path / "chat.json")
    service.email_briefing = SimpleNamespace(respond=lambda *a, **k: pytest.fail("Readiness comes before email confirmation"))
    service.briefing.start()
    service.send("Yes please")
    event = service.events.get(timeout=5)
    assert event["type"] == "answer" and event["briefing_complete"]
    assert service.messages[-1]["text"] == "Yes please"


def test_service_email_reply_runs_before_general_model(tmp_path):
    runtime = SimpleNamespace(ensure=lambda: pytest.fail("Email confirmation must not use the model"))
    service = AssistantService(Vault(tmp_path / "notes"), CalendarStore(tmp_path / "calendar.json"),
        runtime, tmp_path / "chat.json")
    seen = []
    def respond(request, cancel=None):
        seen.append((request, cancel))
        return dict(text="A confirmed email body", changes=[], operation_id=None, sources=[])
    service.email_briefing = SimpleNamespace(respond=respond)
    service.send("yes")
    event = service.events.get(timeout=5)
    assert event["type"] == "answer" and event["text"] == "A confirmed email body"
    assert seen == [("yes", service.cancel)]
