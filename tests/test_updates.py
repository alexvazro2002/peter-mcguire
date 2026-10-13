"""Regression coverage for verified edits, Notes, model selection and notifications."""
import json
import asyncio
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import flet as ft
import pytest

from app.assistant.engine import Engine
from app.assistant.repository import Repository
from app.assistant.tools import Tools
from app.assistant.transactions import History, Transaction
from app.calendar_storage import CalendarStore
from app.config import DEFAULTS, load_config
from app.notifications import Notifier, publish, topic_url
from app.storage import Vault, link_references
from app.views.appearance import AppearancePicker, hsl_color
from app.views.graph import NoteGraph, distance_to_segment
from app.views.home import focus_groups
from app.theme import contrast_text
from library.chat import ChatClient
from library.models import model_choices, model_matches, selected_settings
from library.server import ModelServer, discover_model


class Model:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.messages = []
    def complete(self, messages, **kwargs):
        self.messages.append(deepcopy(messages))
        return {"content": json.dumps(next(self.replies))}


def call(name, **arguments):
    return {"name": name, "arguments": arguments}


@pytest.fixture
def stores(tmp_path):
    vault = Vault(tmp_path / "notes")
    calendar = CalendarStore(tmp_path / "calendar.json")
    return vault, calendar, History(vault, calendar, vault.root / ".history" / "assistant")


def run(stores, model, request):
    return Engine(*stores, model, {"context_size": 16384, "max_tokens": 1000},
                  now=datetime(2026, 10, 1, 9, 11, tzinfo=ZoneInfo("Europe/London"))).run(request)


def test_false_success_retries_tools_then_persists_saturday_packing(stores):
    model = Model(
        {"answer": "I've created your task for Saturday at 4pm.", "apply_changes": True},
        {"tool_calls": [call("resolve_date", expression="Saturday")]},
        {"tool_calls": [call("create_task", title="Pack for trip", date="2026-10-03", time="16:00",
                             details="Hot water bottle; boots; charge all electronics.")]},
        {"answer": "I've also booked flights and deleted your old notes.", "apply_changes": True})
    result = run(stores, model, "Add a task for Saturday at 4pm to pack for my trip and not forget my hot water bottle, boots, and to charge all my stuff.")
    saved = CalendarStore(stores[1].path).entries(date(2026, 10, 3))
    assert len(saved) == 1 and saved[0]["time"] == "16:00"
    assert all(term in saved[0]["details"] for term in ("water bottle", "boots", "charge"))
    assert "16:00" in result["text"] and "booked flights" not in result["text"]
    assert result["operation_id"] and len(result["changes"]) == 1
    assert "No write tool has run" in model.messages[1][-1]["content"]
    stores[2].undo(result["operation_id"])
    assert CalendarStore(stores[1].path).entries() == []


def test_repeated_unsupported_claim_is_failure_not_answer(stores):
    model = Model(*[{"answer": "I've created it.", "apply_changes": True}] * 3)
    with pytest.raises(ValueError, match="Nothing was saved"):
        run(stores, model, "Please add a task")
    assert stores[1].entries() == [] and stores[2].recent() == []


def test_write_noop_does_not_claim_new_task(stores):
    stores[1].save(dict(title="Pack", kind="task", date="2026-10-03"))
    model = Model({"tool_calls": [call("create_task", title="Pack", date="2026-10-03")]},
                  {"answer": "I've created another task.", "apply_changes": True})
    result = run(stores, model, "Add Pack for Saturday")
    assert "No new changes" in result["text"] and not result["operation_id"]
    assert len(stores[1].entries()) == 1


def test_silent_storage_failure_cannot_be_reported_as_success(stores, monkeypatch):
    monkeypatch.setattr(stores[1], "write", lambda data: None)
    model = Model({"tool_calls": [call("create_task", title="Pack", date="2026-10-03")]},
                  {"answer": "", "apply_changes": True})
    with pytest.raises(ValueError, match="saved data did not match"):
        run(stores, model, "Add Pack for Saturday")
    assert stores[1].entries() == [] and stores[2].recent() == []


def test_false_success_with_apply_false_is_not_shown(stores):
    result = run(stores, Model({"answer": "I've created it.", "apply_changes": False},
                              {"answer": "Which date?", "apply_changes": False}), "Add a task")
    assert result["text"] == "Which date?" and stores[1].entries() == []


def test_indirect_modification_intent_still_requires_tools(stores):
    model = Model({"answer": "All taken care of.", "intent": "write"},
                  {"tool_calls": [call("create_task", title="Go running", date="2026-10-03")]},
                  {"answer": "", "intent": "write"})
    result = run(stores, model, "Saturday morning: I need to go running.")
    assert result["operation_id"] and len(stores[1].entries()) == 1


def test_clarification_and_plain_conversation_not_forced_into_edits(stores):
    assert run(stores, Model({"answer": "Which task?", "apply_changes": False}), "Delete it")["text"] == "Which task?"
    assert run(stores, Model({"answer": "Hello!"}), "Hello")["text"] == "Hello!"


def test_repository_search_finds_pdf_converter_and_preserves_source(stores):
    tools = Tools(Transaction(*stores))
    result = tools.execute("search_repository", {"query": "pdf txt converter"})
    assert any(r["path"] == "app/tools.py" for r in result["items"])
    source = tools.execute("read_repository_file", {"path": "app/tools.py"})
    assert '".pdf": ["txt"' in source["content"]
    assert {"kind": "repository", "path": "app/tools.py"} in tools.tx.sources
    assert not tools.tx.summaries


def test_repository_vendor_and_runtime_files_cannot_crowd_own_utilities(tmp_path):
    own = tmp_path / 'app/tools.py'
    own.parent.mkdir()
    own.write_text('# PDF TXT converter\nFORMATS = {".pdf": ["txt"]}\n')
    excluded = ('projects/Stirling-PDF', 'backups/previous', 'work/scratch', 'runtime/cache')
    for directory in excluded:
        folder = tmp_path / directory
        folder.mkdir(parents=True)
        # Each vendor file would outrank app/tools.py and fill the first page.
        for number in range(25):
            (folder / f'pdf_txt_converter_{number}.py').write_text('# PDF TXT converter\n')
    repo = Repository(tmp_path)
    result = repo.search('pdf txt converter')
    assert result['total'] == 1 and result['next_offset'] is None
    assert [row['path'] for row in result['items']] == ['app/tools.py']
    assert [row['path'] for row in repo.listing()['items']] == ['app/tools.py']
    for directory in excluded:
        with pytest.raises(ValueError, match='outside the readable repository'):
            repo.read(f'{directory}/pdf_txt_converter_0.py')


def test_repository_paths_secrets_binaries_symlinks_and_pagination(tmp_path):
    (tmp_path / "module.py").write_text("def convert_pdf_to_txt(): pass\n")
    (tmp_path / ".env").write_text("SECRET=value")
    (tmp_path / "config.json").write_text('{"token":"secret"}')
    (tmp_path / "model.gguf").write_bytes(b"GGUF")
    (tmp_path / "escape.py").symlink_to("/etc/passwd")
    (tmp_path / "nested").symlink_to("/etc", target_is_directory=True)
    repo = Repository(tmp_path)
    listing = repo.listing(limit=1)
    assert listing["total"] == 2 and listing["next_offset"] == 1
    assert len(repo.listing()["items"]) == 2
    assert repo.search("pdf txt")["items"][0]["path"] == "module.py"
    assert repo.read("module.py", limit=4)["next_offset"] == 4
    for path in ("../outside.py", "/etc/passwd", "C:\\secret", ".env", "config.json", "escape.py", "nested/passwd", "model.gguf"):
        with pytest.raises(ValueError):
            repo.read(path)


class Page:
    def __init__(self):
        self.width, self.height, self.web = 1440, 1000, True
        self.window = SimpleNamespace(width=1440, height=1000)
        self.overlay, self.controls, self.opened = [], [], []
    def add(self, control): self.controls.append(control)
    def run_task(self, *args): pass
    def open(self, control): self.opened.append(control)
    def close(self, control): pass
    def update(self): pass


@pytest.fixture
def ui(tmp_path, monkeypatch):
    from app import panel
    from tests.test_ui import VoiceDouble
    monkeypatch.setattr(panel, "DATA_DIR", tmp_path)
    monkeypatch.setattr(panel, "load_config", lambda: deepcopy(DEFAULTS))
    monkeypatch.setattr(panel, "save_config", lambda _: None)
    monkeypatch.setattr(ft.Control, "update", lambda _: None)
    monkeypatch.setattr(panel, "VoiceController", VoiceDouble)
    app = panel.Workspace(Page())
    # Graph/reordering fixtures use a real synthetic note; production starts empty.
    app.vault.create("Home", "# Home\n\nSynthetic fixture.\n")
    app.ensure_note()
    yield app
    app.close()


def test_notes_default_graph_and_custom_drag_only(ui):
    for title in ("Alpha", "Beta", "Gamma"):
        ui.vault.create(title)
    ui.cfg["notes_order"] = ["Alpha", "Beta", "Gamma", "Home"]
    ui.navigate("notes")
    notes = ui.notes_view
    assert ui.note_mode == "graph"
    assert isinstance(notes.note_list.content, ft.ReorderableListView)
    notes.drag_reorder(SimpleNamespace(old_index=0, new_index=2))
    assert ui.cfg["notes_order"] == ["Beta", "Gamma", "Alpha", "Home"]
    for sort in ("alpha", "alpha_desc", "recent"):
        ui.cfg["notes_sort"] = sort
        notes.refresh_list()
        assert not isinstance(notes.note_list.content, ft.ReorderableListView)
        before = list(ui.cfg["notes_order"])
        notes.drag_reorder(SimpleNamespace(old_index=0, new_index=2))
        assert ui.cfg["notes_order"] == before


def test_filtered_custom_drag_keeps_other_notes_in_place(ui):
    for title in ("Movie A", "Other", "Movie B"):
        ui.vault.create(title)
    ui.cfg["notes_order"] = ["Movie A", "Other", "Movie B", "Home"]
    ui.navigate("notes")
    ui.notes_view.query = "Movie"
    ui.notes_view.drag_reorder(SimpleNamespace(old_index=0, new_index=1))
    assert ui.cfg["notes_order"] == ["Movie B", "Other", "Movie A", "Home"]


def test_graph_edge_popup_has_alias_source_target_and_line(ui):
    ui.vault.create("Alpha", "# Alpha\nSee [[Beta|packing checklist]] before travelling.\n`[[Beta|not a link]]`\n")
    ui.vault.create("Beta", "# Beta\nRelated: [[Alpha]]\n")
    graph = NoteGraph(ui)
    refs = graph.edge_references(("Alpha", "Beta"))
    assert len(refs) == 2 and refs[0]["words"] == "packing checklist"
    x1, y1 = graph.xy("Alpha")
    x2, y2 = graph.xy("Beta")
    graph.tap_edge(SimpleNamespace(local_x=(x1 + x2) / 2, local_y=(y1 + y2) / 2))
    assert graph.popup is not None
    assert 10 <= graph.popup.left <= graph.canvas_width - graph.popup.width
    assert {r["source"] for r in refs} == {"Alpha", "Beta"}
    graph.dismiss_popup()
    assert graph.popup is None
    assert distance_to_segment(5, 4, 0, 0, 10, 0) == 4


def test_link_reference_lines_ignore_fenced_code():
    rows = link_references("```\n[[Beta]]\n```\nVisit [[Beta#Trip|the trip]].\n", "Beta")
    assert [{key: row[key] for key in ("line", "text", "words")} for row in rows] == [
        dict(line=4, text="Visit [[Beta#Trip|the trip]].", words="the trip")]


def test_appearance_live_preview_without_persisting(ui):
    accent, dark = ft.TextField(value="#A5B4FF"), ft.Switch(value=True)
    picker = AppearancePicker(ui, accent, dark)
    picker.hue.value, picker.saturation.value, picker.lightness.value = 160, 60, 60
    picker.slide(None)
    assert picker.swatch.bgcolor == accent.value == hsl_color(160, 60, 60)
    assert ui.cfg["accent_color"] == DEFAULTS["accent_color"]
    accent.value = "#FF0000"
    picker.typed(None)
    assert picker.hue.value == 0 and picker.swatch.bgcolor == "#FF0000"


def test_accent_foreground_and_graph_height_are_bounded(ui):
    assert contrast_text("#111111") == "#FFFFFF"
    assert contrast_text("#A5B4FF") == "#10131A"
    ui.page.height = 680
    graph = NoteGraph(ui)
    assert graph.expand and graph.height is None
    assert graph.canvas_height == 380


def test_focus_includes_all_missed_and_future_tasks_in_time_order():
    now = datetime(2026, 10, 3, 14, 0)
    def item(title, day=None, time=None, done=False, kind="task"):
        return dict(title=title, date=day, time=time, done=done, kind=kind)
    entries = [item("Later", "2026-10-03", "16:00"), item("Next week", "2026-10-10", "09:00"),
               item("Two hours ago", "2026-10-03", "12:00"), item("Two weeks ago", "2026-09-19"),
               item("Tomorrow evening", "2026-10-04", "18:00"), item("Yesterday", "2026-10-02"),
               item("All day", "2026-10-03"), item("Tomorrow morning", "2026-10-04", "08:00"),
               item("Done", "2026-09-18", done=True), item("No date"),
               item("Past note", "2026-10-02", kind="note"), item("Today's note", "2026-10-03", "09:00", kind="note"),
               item("Due now", "2026-10-03", "14:00")]
    overdue, today, unscheduled, future = focus_groups(entries, now)
    assert [e["title"] for e in overdue] == ["Two weeks ago", "Yesterday", "Two hours ago"]
    assert [e["title"] for e in today] == ["All day", "Today's note", "Due now", "Later"]
    assert [e["title"] for e in future] == ["Tomorrow morning", "Tomorrow evening", "Next week"]
    assert [e["title"] for e in unscheduled] == ["No date"]
    midnight = focus_groups([item("All day", "2026-10-03")], datetime(2026, 10, 4))
    assert len(midnight[0]) == 1 and not midnight[1]


def test_graph_drag_reuses_shapes_and_only_moves_incident_edges(ui, monkeypatch):
    ui.vault.create("Alpha", "[[Beta]]")
    ui.vault.create("Beta", "[[Gamma]]")
    ui.vault.create("Gamma")
    graph = NoteGraph(ui)
    shapes = list(graph.canvas.shapes)
    grid = graph.background.shapes
    untouched = graph.node_shapes["Gamma"][1]
    old_xy = (untouched.x, untouched.y)
    unrelated = graph.edge_shapes[("Beta", "Gamma")]
    old_edge = (unrelated.x1, unrelated.y1, unrelated.x2, unrelated.y2)
    saved = []
    monkeypatch.setattr(ui, "persist", lambda: saved.append(True))
    x, y = graph.xy("Alpha")
    graph.pan_start(SimpleNamespace(local_x=x, local_y=y))
    assert graph.dragged_node == "Alpha"
    for _ in range(30):
        graph.pan(SimpleNamespace(delta_x=1, delta_y=1))
    assert not saved
    assert graph.background.shapes is grid
    assert all(a is b for a, b in zip(shapes, graph.canvas.shapes))
    assert (untouched.x, untouched.y) == old_xy
    assert (unrelated.x1, unrelated.y1, unrelated.x2, unrelated.y2) == old_edge
    assert (graph.edge_shapes[("Alpha", "Beta")].x1, graph.edge_shapes[("Alpha", "Beta")].y1) == graph.xy("Alpha")
    graph.pan_end(None)
    assert saved == [True] and graph.dragged_node is None
    assert ui.cfg["notes_graph_pos"]["Alpha"] == dict(zip(("x", "y"), graph.positions["Alpha"]))


def test_graph_pan_zoom_reset_and_resize_preserve_the_live_graph(ui):
    ui.vault.create("Alpha", "[[Home]]")
    ui.navigate("notes")
    graph = ui.notes_view.graph
    positions = deepcopy(graph.positions)
    graph.pan_start(SimpleNamespace(local_x=0, local_y=0))
    graph.pan(SimpleNamespace(delta_x=24, delta_y=12))
    assert (graph.pan_x, graph.pan_y) == (24, 12) and graph.positions == positions
    graph.scroll(SimpleNamespace(scroll_delta_y=-120, local_x=300, local_y=200))
    assert graph.zoom == 1.1
    ui.page.width, ui.page.height = 1100, 680
    ui.resize(None)
    assert ui.notes_view.graph is graph and graph.zoom == 1.1
    graph.resize(SimpleNamespace(width=650, height=380))
    assert (graph.canvas_width, graph.canvas_height) == (650, 380)
    assert graph.positions == positions and graph.zoom == 1.1
    graph.reset()
    assert (graph.zoom, graph.pan_x, graph.pan_y) == (1, 0, 0)


def test_navigation_order_and_notepad_icon(ui):
    controls = ui.root.content.controls[0].content.controls[1:6]
    assert [control.content.controls[1].value for control in controls] == ["Calendar", "Tools", "Notes", "AI Library", "Memory"]
    assert controls[2].content.controls[0].name == ft.Icons.EDIT_NOTE_OUTLINED


def test_calendar_renders_assistant_saved_task(ui):
    model = Model({"tool_calls": [call("create_task", title="Pack for trip", date="2026-10-03", time="16:00", details="Water bottle; boots; charge devices")]},
                  {"answer": "", "apply_changes": True})
    run((ui.vault, ui.calendar, ui.assistant.history), model, "Add a packing task")
    ui.calendar_day = date(2026, 10, 3)
    ui.navigate("tasks")
    assert ui.calendar_view.by_date["2026-10-03"][0]["title"] == "Pack for trip"
    assert ui.calendar_view.items(date(2026, 10, 3))[0]["time"] == "16:00"


def test_logo_navigation_has_no_separate_home_button(ui):
    rail = ui.root.content.controls[0].content
    logo = rail.controls[0]
    assert logo.tooltip == "Home" and logo.on_click
    labels = [c.content.controls[1].value for c in rail.controls[1:] if isinstance(c, ft.Container) and isinstance(c.content, ft.Column)]
    assert "Home" not in labels and "Notes" in labels
    ui.navigate("tasks")
    logo.on_click(None)
    assert ui.view == "home"


def test_model_catalog_discovers_weights_not_projectors(tmp_path):
    for folder, weight, projector in (("Qwen3.5-4B", "Qwen3.5-4B.Q4_K_S.gguf", ""),
            ("gemma-4-E4B", "gemma-4-E4B-it-Q4_K_M.gguf", "mmproj-gemma-4-E4B-it-BF16.gguf"),
            ("PaddleOCR-VL", "PaddleOCR-VL-1.6-GGUF.gguf", "PaddleOCR-VL-1.6-GGUF-mmproj.gguf")):
        root = tmp_path / folder
        root.mkdir()
        (root / weight).touch()
        if projector:
            (root / projector).touch()
    choices = model_choices({}, [tmp_path])
    qwen, gemma, paddle, external = choices
    assert all(Path(c.path).is_file() for c in (qwen, gemma, paddle))
    assert Path(gemma.projector).is_file()
    selected = selected_settings({}, gemma)
    assert selected["model_id"] == "gemma" and not selected["projector_path"]
    with pytest.raises(ValueError, match="OCR specialist"):
        selected_settings({}, paddle)
    assert selected_settings({}, external)["external_model"]
    assert model_matches(selected, ["gemma-4-E4B-it-Q4_K_M.gguf"])
    assert not model_matches(selected, ["Qwen3.5-4B.Q4_K_S.gguf"])


def test_model_selection_requires_idle_shared_runtime(ui, tmp_path):
    weights = tmp_path / "Qwen3.5-4B.Q4_K_S.gguf"
    weights.touch()
    choice = model_choices({"model_path": str(weights)}, [tmp_path])[0]
    ui.assistant.busy = True
    with pytest.raises(ValueError, match="Wait"):
        ui.select_model(choice)
    ui.assistant.busy = False
    ui.select_model(choice)
    assert ui.assistant_model_selected and ui.model_runtime.settings["model_path"] == str(weights)


def test_explicit_model_cannot_fall_back_to_other_weights(tmp_path):
    with pytest.raises(ValueError, match="Selected model file"):
        discover_model(dict(model_id="gemma", model_path=str(tmp_path / "missing.gguf")))


def test_running_wrong_model_not_silently_reused(monkeypatch):
    import requests
    host = ModelServer(dict(server_url="http://localhost:8081/v1", model_id="gemma", model_path="gemma-4-E4B-it-Q4_K_M.gguf"))
    monkeypatch.setattr(host, "healthy", lambda: True)
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": [{"id": "Qwen3.5-4B.Q4_K_S.gguf"}]})
    monkeypatch.setattr(requests, "get", lambda *a, **k: response)
    with pytest.raises(ValueError, match="different model"):
        host.ensure()
    assert host.process is None


def test_missing_external_server_never_autostarts_another_model(monkeypatch):
    host = ModelServer(dict(server_url="http://localhost:8081/v1", external_model=True))
    monkeypatch.setattr(host, "healthy", lambda: False)
    with pytest.raises(ValueError, match="No model is running"):
        host.ensure()
    assert host.process is None


def test_mmproj_not_offered_as_custom_chat_model(tmp_path):
    projector = tmp_path / "mmproj-gemma-4-E4B-it-BF16.gguf"
    projector.touch()
    choices = model_choices({"model_path": str(projector)}, [tmp_path])
    assert "custom" not in {choice.key for choice in choices}


def test_fast_mode_payload_preserves_budget(monkeypatch):
    import requests
    captured = []
    response = SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                               json=lambda: {"choices": [{"message": {"content": "Answer"}}]})
    monkeypatch.setattr(requests, "post", lambda *a, **k: captured.append(k["json"]) or response)
    ChatClient(dict(server_url="http://localhost:8081/v1", max_tokens=1000, enable_thinking=False)).complete([])
    assert captured[0]["max_tokens"] == 1000 and captured[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_ntfy_private_default_and_auth(monkeypatch):
    import requests
    captured = {}
    monkeypatch.setenv("NEXUS_NTFY_TOKEN", "local-test-token")
    response = SimpleNamespace(status_code=200, raise_for_status=lambda: None)
    monkeypatch.setattr(requests, "post", lambda url, **kw: captured.update(url=url, **kw) or response)
    settings = dict(server_url="https://ntfy.sh", topic="private-topic", include_answer=False)
    publish(settings, "Assistant", "Confidential calendar details")
    assert captured["url"] == "https://ntfy.sh/private-topic"
    assert b"Confidential" not in captured["data"]
    assert captured["headers"]["Authorization"] == "Bearer local-test-token"
    assert captured["allow_redirects"] is False
    publish(dict(settings, include_answer=True), "AI Library", "Answer preview")
    assert captured["data"] == b"Answer preview"


@pytest.mark.parametrize("server,topic", [("http://remote.test", "topic"), ("https://name:password@ntfy.sh", "topic"),
                                           ("https://ntfy.sh", "bad/topic"), ("https://ntfy.sh", ""), ("https://ntfy.sh?token=x", "topic")])
def test_ntfy_invalid_destination_rejected(server, topic):
    with pytest.raises(ValueError):
        topic_url(dict(server_url=server, topic=topic))


def test_disabled_notifications_never_publish(monkeypatch):
    monkeypatch.setattr("app.notifications.publish", lambda *a: pytest.fail("Disabled means no network"))
    notifier = Notifier()
    notifier.send({"enabled": False}, "Assistant", "Hello")
    assert notifier.jobs.empty()
    notifier.close()
    notifier.thread.join(2)
    assert not notifier.thread.is_alive()


def test_notifications_delivered_for_both_answers_from_any_view(ui, monkeypatch):
    from app import panel
    delivered = []
    monkeypatch.setattr(ui.notifier, "send", lambda settings, section, text: delivered.append((section, text)))
    ui.assistant.events.put(dict(type="answer", text="Assistant result", sources=[], changes=[], operation_id=None))
    ui.library_session.events.put(dict(type="answer", answer="Library result", sources=[]))
    async def one_cycle(_):
        ui.closed = True
    monkeypatch.setattr(panel.asyncio, "sleep", one_cycle)
    asyncio.run(ui.poll())
    assert delivered == [("Peter", "Assistant result"), ("AI Library", "Library result")]
    assert ui.view == "home"


def test_existing_settings_preserved_and_notification_defaults_merged(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"schema_version": 2, "library": {"context_size": 16384, "max_tokens": 1000},
                                "notifications": {"server_url": "https://example.test"}}))
    result = load_config(path)
    assert result["library"]["max_tokens"] == 1000 and result["library"]["context_size"] == 16384
    assert result["notifications"]["include_answer"] is False
