"""Deterministic destructive selection, truthful completion and durable undo."""
import json
from threading import Event

import pytest

from app.assistant.transactions import ConflictError, History
from app.assistant.workspace_actions import handles, respond
from app.calendar_storage import CalendarStore
from app.storage import Vault


@pytest.fixture
def workspace(tmp_path):
    vault = Vault(tmp_path / "notes")
    vault.create("Keep", "# Keep\n\nUntouched.\n")
    calendar = CalendarStore(tmp_path / "calendar.json")
    history = History(vault, calendar, vault.root / ".history" / "assistant")
    return vault, calendar, history


def add(calendar, title, **patch):
    values = dict(title=title, kind="task", date=None, time=None,
                  done=False, details="Original details", note="Keep")
    values.update(patch)
    return calendar.save(values)


@pytest.mark.parametrize("utterance", [
    "Can you delete all my unscheduled tasks?",
    "Peter, please remove all of my undated tasks.",
    "Clear my unscheduled tasks, please.",
    "I want you to delete all the tasks with no date.",
    "Could you wipe out all my tasks without a date?",
    "Delete the tasks that are not scheduled.",
    "There should be 6 unscheduled tasks, delete those.",
    "There are 6 undated tasks. Please remove them all.",
    "Delete all the no-date tasks for me now.",
])
def test_explicit_delete_all_variants(utterance):
    assert handles(utterance)


@pytest.mark.parametrize("utterance", [
    "Do not delete all my unscheduled tasks.",
    "Please don't delete my unscheduled tasks.",
    "Why did you delete all my unscheduled tasks?",
    "Tell me how to delete all my unscheduled tasks.",
    "Write a note saying delete all my unscheduled tasks.",
    "Delete all unscheduled tasks except the invoice.",
    "Delete all my unscheduled tasks and my scheduled tasks.",
    "Delete all my unscheduled tasks then add a note.",
    "Delete all my unscheduled tasks and emails.",
    "Delete my unscheduled tasks from last Monday.",
    "Delete one unscheduled task.",
    "Delete all my tasks without a time.",
    "Delete all my tasks with a date.",
    "Delete all my tasks that have dates.",
    "Delete all my tasks without no date.",
    "Delete all my tasks.",
    "Do I have any unscheduled tasks?",
    "Yes, do it.",
    "Now just the unscheduled ones.",
])
def test_ambiguous_qualified_or_non_action_requests_are_not_executed(utterance, workspace):
    _, calendar, _ = workspace
    add(calendar, "Must stay")
    before = calendar.path.read_bytes()
    assert not handles(utterance)
    assert respond(utterance, [], *workspace) is None
    assert calendar.path.read_bytes() == before


def test_live_whole_calendar_selection_preserves_dated_tasks_and_all_notes(workspace):
    vault, calendar, history = workspace
    deleted = [add(calendar, f"Undated {index}", done=index % 2 == 0) for index in range(6)]
    preserved = [add(calendar, "All day", date="2026-10-16"),
                 add(calendar, "At a time", date="2026-10-17", time="03:13"),
                 add(calendar, "Undated day note", kind="note"),
                 add(calendar, "Dated day note", kind="note", date="2026-10-16")]
    before = calendar.read()
    keep_text = vault.read("Keep")
    # Neither an assistant's fabricated count nor a preview limit controls IDs.
    conversation = [dict(role="assistant", text="You have three tasks, all dated tomorrow.")]
    result = respond("Can you delete all my unscheduled tasks?", conversation, *workspace)
    assert {row["id"] for row in calendar.entries()} == set(preserved)
    assert calendar.read()["entries"] == [item for item in before["entries"] if item["id"] in preserved]
    assert result["operation_id"]
    assert result["evidence"]["deleted_count"] == 6
    assert set(result["evidence"]["deleted_task_ids"]) == set(deleted)
    assert "Deleted 6 unscheduled tasks" in result["text"]
    assert len(result["changes"]) == 1
    assert vault.read("Keep") == keep_text
    record = json.loads(history._path(result["operation_id"]).read_text())
    assert record["status"] == "committed"
    assert len(record["files"]) == 1 and record["files"][0]["kind"] == "calendar"
    history.undo(result["operation_id"])
    assert calendar.read() == before


@pytest.mark.parametrize("utterance", [
    "Now just the unscheduled ones.",
    "Now delete only the unscheduled tasks.",
    "Just the undated tasks, please.",
])
def test_contextual_narrowing_uses_last_user_scope_and_keeps_dated_tasks(utterance, workspace):
    _, calendar, _ = workspace
    add(calendar, "Undated")
    scheduled = add(calendar, "Dated", date="2026-10-16")
    conversation = [dict(role="user", text="Delete all my tasks."),
                    dict(role="assistant", text="I will delete your dated tasks as well.")]
    assert handles(utterance, conversation)
    result = respond(utterance, conversation, *workspace)
    assert [item["id"] for item in calendar.entries()] == [scheduled]
    assert result["evidence"]["deleted_count"] == 1


@pytest.mark.parametrize("conversation", [
    [dict(role="assistant", text="Shall I delete all your tasks?")],
    [dict(role="user", text="Delete all my tasks."), dict(role="user", text="What's the weather?")],
    [dict(role="user", text="Do not delete all my tasks.")],
    [dict(role="user", text="Tell me how to delete all my tasks.")],
    [dict(role="user", text="Delete the word tasks from my note.")],
])
def test_context_cannot_be_authorised_by_assistant_or_unrelated_or_negated_turn(conversation, workspace):
    _, calendar, _ = workspace
    add(calendar, "Must stay")
    assert respond("Now just the unscheduled ones.", conversation, *workspace) is None
    assert len(calendar.entries()) == 1


def test_live_count_wins_over_users_estimate_and_completed_are_included(workspace):
    _, calendar, _ = workspace
    for index in range(9):
        add(calendar, f"Real task {index}", done=True)
    result = respond("There should be 6 unscheduled tasks, delete those.", [], *workspace)
    assert not calendar.entries()
    assert result["evidence"]["deleted_count"] == 9
    assert "9 unscheduled tasks" in result["text"]


def test_noop_is_truthful_and_does_not_create_an_undo_operation(workspace):
    _, calendar, history = workspace
    add(calendar, "Dated", date="2026-10-16")
    before = calendar.path.read_bytes()
    result = respond("Delete all unscheduled tasks.", [], *workspace)
    assert result["operation_id"] is None and result["changes"] == []
    assert result["evidence"]["deleted_count"] == 0
    assert "no unscheduled tasks" in result["text"]
    assert calendar.path.read_bytes() == before
    assert history.recent() == []


def test_cancel_before_selection_saves_nothing(workspace):
    _, calendar, history = workspace
    add(calendar, "Must stay")
    before = calendar.path.read_bytes()
    cancel = Event()
    cancel.set()
    with pytest.raises(ValueError, match="cancelled"):
        respond("Delete all unscheduled tasks.", [], *workspace, cancel=cancel)
    assert calendar.path.read_bytes() == before
    assert history.recent() == []


def test_cancel_during_read_saves_nothing(workspace, monkeypatch):
    _, calendar, history = workspace
    add(calendar, "Must stay")
    before = calendar.path.read_bytes()
    cancel = Event()
    read = calendar.read
    def cancel_read():
        result = read()
        cancel.set()
        return result
    monkeypatch.setattr(calendar, "read", cancel_read)
    with pytest.raises(ValueError, match="cancelled"):
        respond("Delete all unscheduled tasks.", [], *workspace, cancel=cancel)
    assert calendar.path.read_bytes() == before
    assert history.recent() == []


def test_concurrent_calendar_edit_is_preserved_without_partial_deletion(workspace, monkeypatch):
    _, calendar, history = workspace
    undated = add(calendar, "Must stay")
    # Transaction's precondition must reject changes between selection and commit.
    from app.assistant import workspace_actions
    original_cancelled = workspace_actions._cancelled
    calls = 0
    def concurrent_edit(cancel):
        nonlocal calls
        calls += 1
        if calls == 4:
            add(calendar, "Concurrent addition", date="2026-10-16")
        return original_cancelled(cancel)
    monkeypatch.setattr(workspace_actions, "_cancelled", concurrent_edit)
    with pytest.raises(ConflictError, match="calendar changed"):
        respond("Delete all unscheduled tasks.", [], *workspace)
    assert undated in {item["id"] for item in calendar.entries()}
    assert len(calendar.entries()) == 2 and history.recent() == []
