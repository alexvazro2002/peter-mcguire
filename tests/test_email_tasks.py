"""Grounded email-to-task execution without inference or a real email account."""
import json
from datetime import datetime
from threading import Event
from zoneinfo import ZoneInfo

import pytest

from app.assistant.email_tasks import plan, respond
from app.assistant.transactions import History
from app.calendar_storage import CalendarStore
from app.storage import Vault


NOW = datetime(2026, 10, 15, 18, tzinfo=ZoneInfo("Europe/London"))
SOURCE = dict(kind="email", id="actual-message", account="me@example.test",
              url="https://mail.google.com/mail/u/0/?authuser=me%40example.test#inbox/actual-message",
              title="Trip planning", sender="A Person <a@example.test>")


@pytest.fixture
def workspace(tmp_path):
    vault = Vault(tmp_path / "notes")
    vault.create("Keep", "# Keep\n\nKeep this content.\n")
    calendar = CalendarStore(tmp_path / "calendar.json")
    history = History(vault, calendar, vault.root / ".history" / "assistant")
    return vault, calendar, history


def conversation(*sources):
    return [dict(role="user", text="Read the email."),
            dict(role="assistant", text="The email discusses a trip.", sources=list(sources or [SOURCE]))]


@pytest.mark.parametrize("utterance", [
    "Can you create a task for today the U8PM to reply to this email and also add a link to it in the task?",
    "can you make a task for today the 8pm to reply to that email and also link",
    "Peter, please add a task to reply to this email today at 20:00 and include a Gmail link.",
    "Schedule a reminder for today at 8 p.m. to respond to the message.",
])
def test_user_dictation_and_normal_requests_save_actual_email_task(utterance, workspace):
    vault, calendar, history = workspace
    keep = vault.read("Keep")
    result = respond(utterance, conversation(), *workspace, now=NOW)
    assert result is not None and result["operation_id"]
    task, = calendar.entries()
    assert task["kind"] == "task" and task["date"] == "2026-10-15" and task["time"] == "20:00"
    assert task["title"] == "Reply to email: Trip planning"
    assert SOURCE["url"] in task["details"]
    assert result["evidence"]["verified"] and result["evidence"]["task_id"] == task["id"]
    assert result["evidence"]["tools"] == ["create_task"]
    record = json.loads(history._path(result["operation_id"]).read_text())
    assert record["status"] == "committed" and [c["kind"] for c in record["files"]] == ["calendar"]
    assert vault.read("Keep") == keep
    history.undo(result["operation_id"])
    assert not calendar.entries()


@pytest.mark.parametrize("phrase,date,time", [
    ("tomorrow at 12am", "2026-10-16", "00:00"),
    ("this Friday at 12pm", "2026-10-16", "12:00"),
    ("Saturday at 3:13am", "2026-10-17", "03:13"),
    ("2026-10-19 at 14:15", "2026-10-19", "14:15"),
])
def test_relative_day_and_time_are_resolved_from_current_local_clock(phrase, date, time):
    assert plan(f"Make a task {phrase} to reply to this email with a link", now=NOW) == dict(date=date, time=time)
    assert plan(f"Make a task {phrase} to reply to this email and add a link", now=NOW) == dict(date=date, time=time)


@pytest.mark.parametrize("utterance", [
    "Don't make a task for today at 8pm to reply to this email.",
    "Can you explain how to create a task for today at 8pm to reply to this email?",
    "Create a task for today at 8pm to reply to this email and create a note called Dump.",
    "Make a task for today at 8pm to reply to this email and delete all tasks.",
    "Make a task for today at 8pm and tomorrow at 9pm to reply to this email.",
    "Make a task for today at 8 to reply to this email.",
    "Make a task for today at 25:00 to reply to this email.",
    "Make a task for today at 13pm to reply to this email.",
    "Make a task for today at 8pm to send this email.",
    "Make a task for today at 8pm to reply to the hotel email.",
    "Make a task for today at 8pm to reply to this email without a link.",
])
def test_extra_instructions_ambiguity_and_negation_continue_without_partial_writes(utterance, workspace):
    before = workspace[1].path.read_bytes()
    assert respond(utterance, conversation(), *workspace, now=NOW) is None
    assert workspace[1].path.read_bytes() == before and workspace[2].recent() == []


def test_repeated_retry_is_idempotent_and_preserves_other_tasks(workspace):
    _, calendar, history = workspace
    other = calendar.save(dict(kind="task", title="Keep appointment", date="2026-10-16", time="09:00"))
    request = "Create a task today at 8pm to reply to this email and add a link."
    first = respond(request, conversation(), *workspace, now=NOW)
    second = respond(request, conversation(), *workspace, now=NOW)
    assert first["evidence"]["task_id"] == second["evidence"]["task_id"]
    assert second["changes"] == [] and second["operation_id"] is None
    assert second["evidence"]["changed"] is False
    assert len(calendar.entries()) == 2 and any(t["id"] == other for t in calendar.entries())
    assert len(history.recent()) == 1


@pytest.mark.parametrize("rows", [
    [dict(role="assistant", text="The email is at https://mail.google.com/#inbox/invented")],
    conversation(SOURCE, dict(SOURCE, id="other", url=SOURCE["url"].replace("actual-message", "other"), title="Another subject")),
    conversation(dict(SOURCE, url="https://attacker.example/inbox/actual-message")),
])
def test_missing_ambiguous_and_unverified_email_targets_ask_instead_of_inventing(rows, workspace):
    result = respond("Make a task today at 8pm to reply to this email and add a link.", rows, *workspace, now=NOW)
    assert result["evidence"]["needs_email_reference"] and "Which email" in result["text"]
    assert not workspace[1].entries() and not workspace[2].recent()


def test_current_single_read_beats_older_multi_message_report(workspace):
    older = dict(SOURCE, id="old", url=SOURCE["url"].replace("actual-message", "old"), title="Older subject")
    rows = conversation(SOURCE, older) + [dict(role="user", text="Read this one."),
                                        dict(role="assistant", text="Actual current text.", sources=[SOURCE])]
    result = respond("Make a task today at 8pm to reply to this email and add a link.", rows, *workspace, now=NOW)
    assert result["evidence"]["email_id"] == SOURCE["id"]
    assert SOURCE["url"] in workspace[1].entries()[0]["details"]


def test_cancelled_request_saves_nothing(workspace):
    cancelled = Event(); cancelled.set()
    with pytest.raises(ValueError, match="cancelled"):
        respond("Create a task today at 8pm to reply to this email.", conversation(), *workspace, cancel=cancelled, now=NOW)
    assert not workspace[1].entries() and not workspace[2].recent()


def test_remind_me_reply_request_creates_task_with_link_without_needing_a_task_noun(workspace):
    result = respond("Can you remind me to reply to this email tomorrow at 8pm?", conversation(), *workspace, now=NOW)
    assert result["evidence"]["verified"] and result["operation_id"]
    task, = workspace[1].entries()
    assert task["date"] == "2026-10-16" and task["time"] == "20:00"
    assert task["title"] == "Reply to email: Trip planning" and SOURCE["url"] in task["details"]


def test_remind_me_compound_request_is_not_partially_executed(workspace):
    result = respond("Remind me to reply to this email tomorrow at 8pm and add a note about the trip.",
                     conversation(), *workspace, now=NOW)
    assert result is None and not workspace[1].entries() and not workspace[2].recent()
