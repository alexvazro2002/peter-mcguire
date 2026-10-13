"""Controlled tools and model protocol tests; no actual model weights are required."""
import json
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.assistant.dates import resolve_date
from app.assistant.engine import Engine, parse_reply
from app.assistant.lists import MarkdownList
from app.assistant.tools import Tools
from app.assistant.transactions import ConflictError, History, Transaction
from app.calendar_storage import CalendarStore
from app.storage import Vault


@pytest.fixture
def workspace(tmp_path):
    vault = Vault(tmp_path / "notes")
    vault.create("Movies I wanna watch", "# Movies I wanna watch\n\nKeep these alphabetically.\n\n- Arrival\n- The Peanut Butter Falcon\n- Zodiac\n")
    vault.create("Films I've seen", "# Films I've seen\n\n- Moon\n")
    calendar = CalendarStore(tmp_path / "calendar.json")
    history = History(vault, calendar, vault.root / ".history" / "assistant")
    return vault, calendar, history


def tools_for(workspace):
    vault, calendar, history = workspace
    tx = Transaction(vault, calendar, history)
    return tx, Tools(tx, today="2026-10-01")


def call(tool_name, **arguments):
    return dict(name=tool_name, arguments=arguments)


class ScriptedModel:
    """Represents model-selected tool calls, not an intent parser in the application."""
    def __init__(self, *replies):
        self.replies, self.messages = iter(replies), []
    def complete(self, messages, **kwargs):
        self.messages.append(list(messages))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return dict(content=json.dumps(reply))


def engine_for(workspace, model):
    return Engine(*workspace, model, {"context_size": 16384, "max_tokens": 1800},
                  now=datetime(2026, 10, 1, 4, 45, tzinfo=ZoneInfo("Europe/London")))


def test_conversational_history_uses_json_without_changing_workspace(workspace):
    vault, calendar, history = workspace
    before = {name: vault.read(name) for name in vault.names()}
    previous_answer = 'I can help with your notes. Say "hello" whenever you like.\nReady when you are.'
    conversation = [{"role": "user", "text": "What can you help with?"},
                    {"role": "assistant", "text": previous_answer}]
    model = ScriptedModel({"answer": "Hello. How can I help?", "apply_changes": True})

    result = engine_for(workspace, model).run("Hello", conversation)

    sent = model.messages[0]
    assert sent[1] == {"role": "user", "content": "What can you help with?"}
    assert parse_reply(sent[2]) == {"answer": previous_answer, "apply_changes": True}
    assert sent[3] == {"role": "user", "content": "Hello"}
    assert result["text"] == "Hello. How can I help?"
    assert result["changes"] == [] and result["operation_id"] is None
    assert {name: vault.read(name) for name in vault.names()} == before
    assert calendar.entries() == [] and history.recent() == []


def test_quick_sequential_requests_bound_large_old_history(workspace):
    conversation = []
    for n in range(20):
        model = ScriptedModel({"answer": "Ready.", "apply_changes": True})
        engine = Engine(*workspace, model, {"context_size": 8192, "max_tokens": 1800})
        result = engine.run("Hello again.", conversation)
        sent = model.messages[0]
        assert sum(len(m["content"]) for m in sent) < 13000
        assert sent[0]["role"] == "system" and sent[-1]["content"] == "Hello again."
        assert "describe_tools" in sent[0]["content"]
        assert '"mode"' not in sent[0]["content"]  # Detailed edit schemas are fetched only when needed.
        assert result["operation_id"] is None
        conversation += [{"role": "user", "text": "Earlier topic " + "x" * 5000},
                         {"role": "assistant", "text": "Earlier answer " + "y" * 5000}]


def test_describe_tools_retains_validated_schemas_and_paging(workspace):
    tx, tools = tools_for(workspace)
    schema = tools.execute("describe_tools", {"names": ["create_task", "read_note"]})
    assert schema["tools"] == [tools.specs["create_task"], tools.specs["read_note"]]
    with pytest.raises(ValueError):
        tools.execute("describe_tools", {"names": ["not_a_tool"]})
    with pytest.raises(ValueError):
        tools.execute("create_task", {"title": "Test", "date": "", "unknown": True})
    assert tx.summaries == []
    workspace[0].create("Long note", "a" * 10000)
    tools = Tools(Transaction(*workspace), page_chars=300, page_items=2)
    result = tools.execute("read_note", {"name": "Long note", "limit": 8000})
    assert len(result["content"]) == 300 and result["next_offset"] == 300
    assert result["characters"] == 10000


def test_history_eviction_keeps_current_request_and_pending_write_evidence(workspace):
    request = "Create a note titled Context check with text Verified. " + "Please keep this request intact. " * 120
    model = ScriptedModel({"tool_calls": [call("create_note", name="Context check", content="Verified.")]},
                          {"answer": "", "apply_changes": True})
    conversation = [{"role": role, "text": "old " + "z" * 5000}
                    for role in ["user", "assistant"] * 10]
    engine = Engine(*workspace, model, {"context_size": 8192, "max_tokens": 1800})
    result = engine.run(request, conversation)
    final_messages = model.messages[-1]
    assert final_messages[1]["role"] == "user"
    assert any(m["role"] == "user" and m["content"] == request for m in final_messages)
    assert any('"name": "create_note"' in m["content"] for m in final_messages if m["role"] == "assistant")
    assert any('"changed": true' in m["content"] for m in final_messages)
    assert workspace[0].read("Context check") == "Verified."
    workspace[2].undo(result["operation_id"])
    assert "Context check" not in workspace[0].names()


def test_truncated_json_retry_keeps_staged_writes_until_valid_final(workspace):
    class TruncatedClient:
        def __init__(self): self.messages, self.budgets = [], []
        def complete(self, messages, **kwargs):
            self.messages.append(list(messages))
            self.budgets.append(kwargs["max_tokens"])
            if len(self.messages) == 1:
                return {"content": json.dumps({"tool_calls": [call("create_note", name="Retry check", content="Verified.")]})}
            if len(self.messages) == 2:
                return {"content": '{"answer":"cut', "finish_reason": "length"}
            return {"content": '{"answer":"","apply_changes":true}'}
    client = TruncatedClient()
    result = Engine(*workspace, client, {"context_size": 8192, "max_tokens": 1800}).run("Create Retry check.")
    assert client.budgets == [384, 768, 1536]
    assert workspace[0].read("Retry check") == "Verified."
    assert len(workspace[2].recent()) == 1
    assert any('"changed": true' in m["content"] for m in client.messages[-1])
    workspace[2].undo(result["operation_id"])
    assert "Retry check" not in workspace[0].names()


def test_missing_task_date_retry_publishes_schema_without_inventing_schedule(workspace):
    model = ScriptedModel({"tool_calls": [call("create_task", title="Unscheduled check")]},
                          {"tool_calls": [call("create_task", title="Unscheduled check", date="", time="")]},
                          {"answer": "", "apply_changes": True})
    result = Engine(*workspace, model, {"context_size": 8192, "max_tokens": 384}).run(
        "Create an unscheduled task titled Unscheduled check. Do not assign a date or time.")
    assert '"exact_schema"' in model.messages[1][-1]["content"]
    task = workspace[1].entries()[0]
    assert task["date"] is None and task["time"] is None
    workspace[2].undo(result["operation_id"])
    assert workspace[1].entries() == []


def test_prior_change_question_uses_live_evidence_without_false_write_retry(workspace):
    task_id = workspace[1].save({"title": "Existing check", "kind": "task", "date": None})
    model = ScriptedModel({"tool_calls": [call("search_tasks", query="Existing check")]},
                          {"answer": "I created a task titled Existing check, with no date assigned.", "apply_changes": True})
    result = engine_for(workspace, model).run("What task did you just create?")
    assert len(model.messages) == 2 and "Existing check" in result["text"]
    assert result["operation_id"] is None and result["changes"] == []
    assert workspace[1].entries()[0]["id"] == task_id and workspace[2].recent() == []


def test_prior_change_question_cannot_claim_success_from_empty_search(workspace):
    model = ScriptedModel({"tool_calls": [call("search_tasks", query="Missing check")]},
                          {"answer": "I created Missing check.", "apply_changes": True},
                          {"answer": "There is no matching task.", "apply_changes": False})
    result = engine_for(workspace, model).run("What task did you just create?")
    assert len(model.messages) == 3 and result["text"] == "There is no matching task."
    assert workspace[1].entries() == [] and workspace[2].recent() == []


def test_bounded_result_keeps_truthful_pagination_and_marks_omissions():
    from app.assistant.engine import bounded_result
    result = {"items": [{"name": str(n), "excerpt": "x" * 300} for n in range(10)],
              "total": 30, "next_offset": 15}
    bounded = bounded_result(result, {"offset": 5}, limit=900)
    assert 0 < len(bounded["items"]) < 10
    assert bounded["total"] == 30 and bounded["next_offset"] == 5 + len(bounded["items"])
    omitted = bounded_result({"content": "y" * 10000}, {}, limit=900)
    assert omitted["result_omitted"] and "content" not in omitted


@pytest.mark.parametrize("phrase,anchor,expected", [
    ("this Sunday", "2026-10-01", "2026-10-04"), ("yesterday", "2026-10-01", "2026-09-30"),
    ("last Friday", "2026-10-01", "2026-09-25"), ("tomorrow", "2026-12-31", "2027-01-01"),
    ("this Sunday", "2026-10-04", "2026-10-04"), ("next Sunday", "2026-10-04", "2026-10-11"),
    ("last Sunday", "2026-10-04", "2026-09-27"), ("01 Oct", "2026-10-01", "2026-10-01"),
    ("3 weeks ago", "2026-10-01", "2026-09-10"), ("29 February 2024", "2026-10-01", "2024-02-29")])
def test_relative_dates(phrase, anchor, expected):
    assert resolve_date(phrase, anchor)["date"] == expected


def test_ambiguous_numeric_date_requires_clarification():
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_date("01/10", "2026-10-01")


def test_example_multi_intent_model_tool_loop_and_single_undo(workspace):
    vault, calendar, history = workspace
    before = {n: vault.read(n) for n in vault.names()}
    model = ScriptedModel(
        {"tool_calls": [call("find_list", query="movies watchlist"), call("find_list", query="films seen"),
                        call("resolve_date", expression="01 Oct"), call("resolve_date", expression="this Sunday", anchor="2026-10-01"),
                        call("resolve_date", expression="yesterday", anchor="2026-10-01")]},
        {"tool_calls": [call("read_list", name="Movies I wanna watch"), call("read_list", name="Films I've seen")]},
        {"tool_calls": [call("create_calendar_note", title="Stayed late 2 hours because Sonny came sick.", date="2026-10-01",
                             metadata={"activity": "work_late", "people": ["Sonny"], "duration_minutes": 120}),
                        call("create_task", title="Watch Interstellar", date="2026-10-04"),
                        call("add_list_item", name="Movies I wanna watch", item="Interstellar"),
                        call("transfer_list_item", source="Movies I wanna watch", target="Films I've seen",
                             item="Peanut Butter Falcon", date="2026-09-30", activity="watch")]},
        {"answer": "", "apply_changes": True})
    request = "01 Oct: today I had to stay late for 2 hours because Sonny came sick. I want to watch Interstellar this Sunday. I watched The Peanut Butter Falcon yesterday."
    result = engine_for(workspace, model).run(request)
    assert len(result["changes"]) == 5
    assert result["operation_id"]
    assert [i.text for i in MarkdownList(vault.read("Movies I wanna watch")).items] == ["Arrival", "Interstellar", "Zodiac"]
    watched = MarkdownList(vault.read("Films I've seen")).resolve("Peanut Butter Falcon")
    assert watched.text == "The Peanut Butter Falcon"
    assert watched.metadata == {"date": "2026-09-30", "activity": "watch"}
    assert [(e["kind"], e["date"]) for e in calendar.entries()] == [("note", "2026-10-01"), ("task", "2026-10-04")]
    assert len(history.recent()) == 1
    assert "Available tools" in model.messages[0][0]["content"]
    history.undo(result["operation_id"])
    assert {n: vault.read(n) for n in vault.names()} == before
    assert calendar.entries() == []


def test_unrelated_book_list_and_arbitrary_note_task_tools(workspace):
    vault, calendar, _ = workspace
    vault.create("Books to read", "# Books\n- Solaris\n- Dune\n")
    vault.create("Books finished", "# Finished\n")
    tx, tools = tools_for(workspace)
    tools.execute("transfer_list_item", dict(source="Books to read", target="Books finished", item="Dune", date="2026-09-25", activity="read"))
    tools.execute("create_note", dict(name="Running", content="# Running\nEasy pace."))
    tools.execute("edit_note", dict(name="Running", mode="replace", old_text="Easy pace.", text="Steady pace."))
    task = tools.execute("create_task", dict(title="Call Alex", date="2026-10-02"))
    tools.execute("edit_task", dict(id=task["id"], patch={"date": "2026-10-03", "details": "Discuss the project"}))
    tools.execute("complete_task", dict(id=task["id"]))
    assert tools.execute("count_history", dict(activity="read", weekday="friday"))["count"] == 1
    tx.commit("Read Dune and call Alex")
    assert "Dune" not in vault.read("Books to read")
    assert "Dune" in vault.read("Books finished")
    assert vault.read("Running") == "# Running\nSteady pace."
    assert calendar.entries()[0]["done"] is True
    assert calendar.entries()[0]["date"] == "2026-10-03"


def test_read_only_history_question_queries_live_data_and_does_not_write(workspace):
    vault, calendar, history = workspace
    calendar.save(dict(title="Stayed late 2 hours", kind="note", date="2026-09-25", metadata={"activity": "work_late"}))
    before = calendar.path.read_bytes()
    model = ScriptedModel({"tool_calls": [call("search_history", query="stayed late")]},
                          {"answer": "You last stayed late on 25 September 2026, for 2 hours.", "apply_changes": True})
    result = engine_for(workspace, model).run("When did I last have to stay late?")
    assert "25 September" in result["text"]
    assert result["operation_id"] is None
    assert calendar.path.read_bytes() == before
    assert history.recent() == []
    assert "2026-09-25" in model.messages[-1][-1]["content"]


def test_counts_scan_all_records_not_only_first_page_and_report_undated(workspace):
    vault, calendar, _ = workspace
    for i in range(70):
        calendar.save(dict(title=f"Watched film {i}", kind="note", date="2026-09-25", metadata={"activity": "watch", "subjects": [f"Film {i}"]}))
    tx, tools = tools_for(workspace)
    search = tools.execute("search_history", dict(activity="watch", limit=5))
    assert search["dated_matches"] == 70 and len(search["items"]) == 5 and search["next_offset"] == 5
    count = tools.execute("count_history", dict(activity="watch", start="2026-09-10", end="2026-10-01", weekday="friday"))
    assert count["count"] == 70 and count["by_weekday"]["friday"] == 70
    assert tools.execute("count_history", dict(query="Moon"))["undated_matches"] == 1


def test_dated_markdown_headings_and_undated_lines(workspace):
    vault, _, _ = workspace
    vault.create("Journal", "# Journal\n\n## 2026-09-25\nStayed late with Sonny.\n\n## Other\nWas sick sometime.\n")
    _, tools = tools_for(workspace)
    assert tools.execute("search_history", dict(query="Sonny"))["items"][0]["date"] == "2026-09-25"
    assert tools.execute("search_history", dict(query="sick"))["undated_matches"] == 1


def test_alias_duplicate_and_reorder(workspace):
    vault, _, _ = workspace
    tx, tools = tools_for(workspace)
    assert not tools.execute("add_list_item", dict(name="Movies I wanna watch", item="Peanut Butter Falcon"))["changed"]
    tools.execute("move_list_item", dict(name="Movies I wanna watch", item="Zodiac", position="top"))
    tx.commit("Move Zodiac to top")
    assert MarkdownList(vault.read("Movies I wanna watch")).items[0].text == "Zodiac"
    assert "Keep these alphabetically." in vault.read("Movies I wanna watch")


@pytest.mark.parametrize("name,args", [
    ("run_shell", {"command": "ls"}), ("create_note", {"name": "../outside", "content": "bad"}),
    ("create_task", {"title": "Task", "date": "2026-10-01", "filename": "outside"}),
    ("create_calendar_note", {"title": "Event", "date": "2026-02-30"}),
    ("search_notes", {"query": "", "limit": True}),
    ("create_task", {"title": "Task", "date": "", "metadata": {"path": "/tmp/x"}})])
def test_tool_validation_and_path_boundaries(workspace, name, args):
    tx, tools = tools_for(workspace)
    with pytest.raises((ValueError, TypeError)):
        tools.execute(name, args)
    assert not tx.summaries


def test_destructive_actions_restore_and_undo_refuses_later_edits(workspace):
    vault, calendar, history = workspace
    task_id = calendar.save(dict(title="Call Alex", kind="task", date="2026-10-01"))
    before = vault.read("Movies I wanna watch")
    tx, tools = tools_for(workspace)
    tools.execute("delete_note", dict(name="Movies I wanna watch"))
    tools.execute("delete_task", dict(id=task_id))
    op = tx.commit("Delete watchlist and task")
    assert "Movies I wanna watch" not in vault.names() and not calendar.entries()
    assert list((vault.root / ".trash").glob("*.md"))
    history.undo(op)
    assert vault.read("Movies I wanna watch") == before and calendar.entries()[0]["id"] == task_id
    tx, tools = tools_for(workspace)
    tools.execute("add_list_item", dict(name="Movies I wanna watch", item="Interstellar"))
    op = tx.commit("Add Interstellar")
    vault.write("Movies I wanna watch", "User's later edit\n")
    with pytest.raises(ConflictError):
        history.undo(op)
    assert vault.read("Movies I wanna watch") == "User's later edit\n"


def test_optimistic_conflict_prevents_all_changes(workspace):
    vault, calendar, _ = workspace
    tx, tools = tools_for(workspace)
    tools.execute("add_list_item", dict(name="Movies I wanna watch", item="Interstellar"))
    tools.execute("create_task", dict(title="Watch Interstellar", date="2026-10-04"))
    vault.write("Movies I wanna watch", "New edit")
    with pytest.raises(ConflictError):
        tx.commit("Add movie and task")
    assert vault.read("Movies I wanna watch") == "New edit"
    assert not calendar.entries()


def test_disk_failure_rolls_back_prior_note_writes(workspace, monkeypatch):
    vault, calendar, history = workspace
    before = vault.read("Movies I wanna watch")
    tx, tools = tools_for(workspace)
    tools.execute("add_list_item", dict(name="Movies I wanna watch", item="Interstellar"))
    tools.execute("create_task", dict(title="Watch Interstellar", date="2026-10-04"))
    def fail(_):
        raise OSError("Disk full")
    monkeypatch.setattr(calendar, "write", fail)
    with pytest.raises(OSError):
        tx.commit("Add movie and task")
    assert vault.read("Movies I wanna watch") == before
    assert not calendar.entries() and not history.recent()
    record = json.loads(next(history.root.glob("*.json")).read_text())
    assert record["status"] == "rolled_back"


def test_interrupted_operation_recovers_on_restart(workspace):
    vault, calendar, history = workspace
    before = vault.read("Movies I wanna watch")
    record = dict(id="a" * 32, created_at="2026-10-01", vault=str(vault.root), calendar=str(calendar.path.resolve()),
                  status="prepared", summaries=[], files=[dict(kind="note", name="Movies I wanna watch", before=before, after="Partial edit")])
    history._save(record)
    vault.write("Movies I wanna watch", "Partial edit")
    history.recover()
    assert vault.read("Movies I wanna watch") == before
    assert history._read(record["id"])["status"] == "rolled_back"


def test_failed_tool_discards_whole_plan_and_clarification_saves_nothing(workspace):
    vault, calendar, _ = workspace
    before = vault.read("Movies I wanna watch")
    model = ScriptedModel({"tool_calls": [call("add_list_item", name="Movies I wanna watch", item="Interstellar"),
                                         call("create_task", title="Watch", date="not a date")]},
                          {"answer": "Which date did you mean?", "apply_changes": False})
    result = engine_for(workspace, model).run("Add this and schedule it on some date")
    assert result["operation_id"] is None
    assert vault.read("Movies I wanna watch") == before and not calendar.entries()
    assert "all_staged_writes_discarded" in model.messages[-1][-1]["content"]


def test_model_failure_or_cancel_does_not_save_staged_changes(workspace):
    vault, _, _ = workspace
    before = vault.read("Movies I wanna watch")
    model = ScriptedModel({"tool_calls": [call("add_list_item", name="Movies I wanna watch", item="Interstellar")]}, RuntimeError("Model offline"))
    with pytest.raises(RuntimeError):
        engine_for(workspace, model).run("Add Interstellar")
    assert vault.read("Movies I wanna watch") == before
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(ValueError, match="cancelled"):
        engine_for(workspace, model).run("Add Interstellar", cancel=cancelled)


def test_malformed_model_reply_can_retry_without_writes(workspace):
    model = ScriptedModel(["bad reply"], {"answer": "Please specify a note.", "apply_changes": False})
    assert "specify" in engine_for(workspace, model).run("Edit something")["text"]
    assert workspace[2].recent() == []


def test_native_tool_response_compatibility():
    response = {"tool_calls": [{"function": {"name": "search_notes", "arguments": '{"query":"Sonny"}'}}]}
    assert parse_reply(response)["tool_calls"][0]["arguments"] == {"query": "Sonny"}


def test_history_does_not_count_planned_tasks_as_completed_activities(workspace):
    _, calendar, _ = workspace
    calendar.save(dict(title="Watch Interstellar", kind="task", date="2026-09-25", metadata={"activity": "watch"}))
    _, tools = tools_for(workspace)
    assert tools.execute("count_history", dict(activity="watch"))["count"] == 0
    assert tools.execute("count_history", dict(activity="watch", include_tasks=True))["count"] == 1


def test_crosslinked_activity_deduplicates_but_distinct_calendar_occurrences_do_not(workspace):
    vault, calendar, _ = workspace
    calendar.save(dict(title="Watched Moon", kind="note", date="2026-09-25", time="13:00", metadata={"activity": "watch", "subjects": ["Moon"]}))
    calendar.save(dict(title="Watched Moon again", kind="note", date="2026-09-25", time="20:00", metadata={"activity": "watch", "subjects": ["Moon"]}))
    vault.write("Films I've seen", '# Seen\n- Moon <!-- nexus-assistant: {"date":"2026-09-25","activity":"watch"} -->\n')
    _, tools = tools_for(workspace)
    assert tools.execute("count_history", dict(activity="watch"))["count"] == 2


def test_list_sections_nested_lines_and_final_line_without_newline():
    before = "# Reading\n\n## Fiction\n- Dune\n  - Paperback\n- Solaris\n\n## Other\nKeep this.\n"
    listing = MarkdownList(before, "Fiction")
    after, _ = listing.add("Arrival")
    assert "- Arrival\n- Dune\n  - Paperback\n- Solaris" in after
    assert "## Other\nKeep this.\n" in after
    after, _ = MarkdownList("# Watchlist\n- Arrival").add("Zodiac")
    assert "- Arrival\n- Zodiac\n" in after


def test_plain_text_lists_keep_their_format_and_prose():
    before = "# Watchlist\n\nKeep these alphabetically.\n\nArrival\nThe Peanut Butter Falcon\nZodiac\n"
    listing = MarkdownList(before)
    assert listing.plain
    after, added = listing.add("Interstellar")
    assert added and "Arrival\nInterstellar\nThe Peanut Butter Falcon\nZodiac" in after
    assert "Keep these alphabetically." in after
    after, matched = MarkdownList(after).remove("Peanut Butter Falcon")
    assert matched.text == "The Peanut Butter Falcon" and "Falcon" not in after
    assert "- " not in after


def test_calendar_api_preserves_assistant_metadata_on_manual_edits(workspace):
    _, calendar, _ = workspace
    id = calendar.save(dict(title="Watched Moon", kind="note", date="2026-09-25", metadata={"activity": "watch", "subjects": ["Moon"]}))
    calendar.save(dict(title="Watched Moon with Alex", kind="note", date="2026-09-25"), id)
    item = calendar.entries()[0]
    assert item["metadata"] == {"activity": "watch", "subjects": ["Moon"]}
    assert item["title"] == "Watched Moon with Alex"


def test_source_links_only_include_returned_evidence(workspace):
    vault, _, _ = workspace
    vault.create("Unrelated", "Nothing about running here")
    tx, tools = tools_for(workspace)
    tools.execute("search_notes", dict(query="Zodiac"))
    assert tx.sources == [{"kind": "note", "name": "Movies I wanna watch"}]


def test_new_checklist_item_is_unchecked_even_when_first_item_is_complete():
    after, _ = MarkdownList("# Shopping\n- [x] Apples\n").add("Milk")
    assert "- [x] Apples" in after and "- [ ] Milk" in after
