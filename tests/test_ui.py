import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import flet as ft
import pytest

from app.panel import Workspace
from app.tools import TOOLS


class VoiceDouble:
    def __init__(self, settings):
        import queue
        self.events, self.state, self.spoken = queue.Queue(), "idle", []
        self.startups, self.listens = 0, 0
    def startup(self): self.startups += 1
    def toggle_listen(self): self.listens += 1
    def speak(self, text): self.spoken.append(text)
    def cancel(self): pass
    def close(self): pass


@pytest.fixture(autouse=True)
def local_voice_double(monkeypatch):
    monkeypatch.setattr("app.panel.VoiceController", VoiceDouble)


class PageDouble:
    """Constructor smoke tests using actual Flet controls, without a display/server."""
    def __init__(self, width=1440, height=900):
        self.width, self.height, self.web = width, height, False
        self.window = SimpleNamespace(width=width, height=height)
        self.overlay, self.controls, self.opened = [], [], []
    def add(self, control):
        self.controls.append(control)
    def run_task(self, fn, *args):
        pass
    def open(self, control):
        self.opened.append(control)
    def close(self, control):
        pass
    def update(self):
        pass


@pytest.mark.parametrize("dark,width,height", [(True, 1440, 900), (False, 1100, 900), (True, 1000, 680)])
def test_all_views_and_tools_construct(dark, width, height):
    with patch.object(ft.Control, "update", lambda self: None):
        app = Workspace(PageDouble(width, height))
        app.cfg["theme_dark"] = dark
        for view in ("home", "tools", "library", "settings", "tasks", "assistant", "memory", "notes"):
            app.navigate(view)
            assert app.root.content is not None
            if view == "assistant":
                assert app.assistant_view is not None
                assert app.assistant_view.content.expand
        for mode in ("read", "write", "graph"):
            app.notes_view.mode(mode)
        app.navigate("tasks")
        for mode in ("monthly", "weekly", "daily"):
            app.calendar_view.mode(mode)
        app.calendar_view.edit()
        app.calendar_view.pick_date()
        app.calendar_view.unscheduled(None)
        app.navigate("home")
        app.navigate("tasks")
        assert app.calendar_mode == "monthly"
        app.navigate("library")
        app.library_view.mode("search")
        app.library_progress = {"phase": "embedding", "completed": 32, "total": 64, "message": "Embedding", "current_file": "book.txt"}
        app.library_view.refresh()
        assert app.library_view.processing_bar.value == .5
        assert "32 / 64" in app.library_view.processing_detail.value
        for tool in TOOLS:
            app.open_tool(tool)
            assert app.root.content is not None


def test_empty_editor_is_saved_on_navigation():
    with patch.object(ft.Control, "update", lambda self: None):
        app = Workspace(PageDouble())
        name = app.vault.create("Clear me", "old body")
        app.open_note(name)
        app.notes_view.mode("write")
        app.notes_view.editor.value = ""
        app.navigate("home")
        assert app.vault.read(name) == ""


def test_note_editor_does_not_overwrite_assistant_changes():
    with patch.object(ft.Control, "update", lambda self: None):
        app = Workspace(PageDouble())
        name = app.vault.create("Conflict note", "Original")
        app.open_note(name)
        app.notes_view.mode("write")
        app.notes_view.editor.value = "My unsaved edit"
        app.vault.write(name, "Assistant edit")
        with pytest.raises(ValueError, match="outside"):
            app.notes_view.save()
        assert app.notes_view.editor.value == "My unsaved edit"
        assert app.vault.read(name) == "Assistant edit"


def test_assistant_busy_and_message_undo_controls_construct():
    with patch.object(ft.Control, "update", lambda self: None):
        app = Workspace(PageDouble())
        app.assistant.messages = [{"role": "user", "text": "Hello"}, {"role": "assistant", "text": "Hi", "sources": []}]
        app.assistant.busy = True
        app.navigate("assistant")
        assert app.assistant_view.input.disabled
        assert app.assistant_view.progress.visible
        assert app.assistant_view.cancel_button.visible


def test_conflicting_draft_can_be_recovered_without_overwriting_saved_note():
    with patch.object(ft.Control, "update", lambda self: None):
        app = Workspace(PageDouble())
        name = app.vault.create("Recovery note", "Original")
        app.open_note(name)
        app.notes_view.mode("write")
        app.notes_view.editor.value = "Unsaved draft"
        app.vault.write(name, "Assistant edit")
        app.notes_view.recover_draft()
        assert app.vault.read(name) == "Assistant edit"
        backups = list((app.vault.root / ".history").glob("editor-conflict-*.md"))
        assert any(path.read_text() == "Unsaved draft" for path in backups)
        app.navigate("assistant")
        assert app.view == "assistant"


@pytest.fixture
def peter_ui(tmp_path, monkeypatch):
    from app import panel
    from app.config import DEFAULTS
    monkeypatch.setattr(panel, "DATA_DIR", tmp_path)
    monkeypatch.setattr(panel, "load_config", lambda: deepcopy(DEFAULTS))
    monkeypatch.setattr(panel, "save_config", lambda _: None)
    monkeypatch.setattr(ft.Control, "update", lambda _: None)
    app = Workspace(PageDouble())
    yield app
    app.close()


def one_poll(app, monkeypatch):
    from app import panel
    async def finish(_):
        app.closed = True
    monkeypatch.setattr(panel.asyncio, "sleep", finish)
    asyncio.run(app.poll())
    app.closed = False


def test_empty_notes_workspace_stays_empty_across_navigation_and_last_deletion(peter_ui, monkeypatch):
    app = peter_ui
    assert app.vault.names() == [] and app.note_name is None
    for mode in ("graph", "read", "write"):
        app.navigate("notes")
        app.notes_view.mode(mode)
        app.navigate("home")
        assert app.vault.names() == []
    app.open_note(app.vault.create("Only note", "A temporary fixture"))
    monkeypatch.setattr(app, "confirm", lambda title, text, action: action())
    app.notes_view.trash()
    assert app.vault.names() == [] and app.note_name is None
    assert app.view == "notes" and app.notes_view.editor is None
    app.assistant_data_changed()
    assert app.vault.names() == []


def test_transcript_uses_existing_assistant_and_keeps_thinking_after_voice_idle(peter_ui, monkeypatch):
    app, requests = peter_ui, []
    app.assistant_model_selected = True
    def send(text):
        requests.append(text)
        app.assistant.busy = True
    monkeypatch.setattr(app.assistant, "send", send)
    app.voice.events.put(dict(type="transcript", text="What is on my calendar?"))
    app.voice.events.put(dict(type="voice_status", state="idle", message="Recording finished"))
    one_poll(app, monkeypatch)
    assert requests == ["What is on my calendar?"]
    assert app.peter_state == "thinking" and app.home_view.state.value == "THINKING"
    assert app.home_view.input.disabled


def test_committed_assistant_answer_is_spoken_and_saved_through_existing_service(peter_ui, monkeypatch):
    app = peter_ui
    app.assistant.busy = True
    app.assistant.events.put(dict(type="answer", text="Your calendar is clear.", sources=[], changes=[], operation_id=None))
    one_poll(app, monkeypatch)
    assert app.voice.spoken == ["Your calendar is clear."]
    assert app.assistant.messages[-1]["text"] == "Your calendar is clear."
    assert app.assistant.path.exists() and not app.assistant.busy
    app.cfg["peter"]["speech_enabled"] = False
    app.assistant.events.put(dict(type="answer", text="A quiet answer.", sources=[], changes=[], operation_id=None))
    one_poll(app, monkeypatch)
    assert app.voice.spoken == ["Your calendar is clear."]


def test_voice_states_switch_client_played_orb_without_animation_polling(peter_ui, monkeypatch):
    app, orb = peter_ui, peter_ui.home_view.orb
    image = orb.orb_image
    from app.views.peter import ORB_STATES
    for state in ("listening", "thinking", "speaking", "idle"):
        app.voice.events.put(dict(type="voice_status", state=state, message=state))
        one_poll(app, monkeypatch)
        assert app.peter_state == orb.state == state
        assert orb.orb_image is image and image.src.endswith(ORB_STATES[state] + '.webp')
    assert not hasattr(orb, 'tick')
    app.cfg['theme_dark'] = False
    orb.set_state('idle')
    assert image.src == 'thinking-orbs/light-breathing.webp'


def test_orb_state_updates_serialize_after_image_has_a_parent(peter_ui):
    orb = peter_ui.home_view.orb
    # Flet Container.image is a decoration field, not a child Image control.
    # Mounting creates parent references that must never enter JSON decoration.
    orb._build_add_commands()
    assert orb.orb_image.parent is orb
    assert orb.image is None
    for state in ('thinking', 'speaking', 'idle'):
        orb.set_state(state)
        orb.before_update()


def test_home_displays_typed_and_transcribed_messages_while_request_is_pending(peter_ui, monkeypatch):
    app = peter_ui
    app.assistant_model_selected = True
    def send(text):
        app.assistant.append(dict(role='user', text=text))
        app.assistant.busy = True
    monkeypatch.setattr(app.assistant, 'send', send)
    app.home_view.input.value = 'A typed message'
    app.home_view.send(None)
    assert app.home_view.conversation.controls[-1].content.controls[1].value == 'A typed message'
    app.assistant.busy = False
    app.voice.events.put(dict(type='transcript', text='A spoken message'))
    one_poll(app, monkeypatch)
    assert app.home_view.conversation.controls[-1].content.controls[1].value == 'A spoken message'
    assert app.home_view.conversation.controls[0].content.controls[1].value == 'A typed message'


def test_startup_preloads_shared_model_once_and_greets_through_voice(peter_ui, monkeypatch):
    app, calls = peter_ui, []
    monkeypatch.setattr(app.model_runtime, "ensure", lambda: calls.append("shared model"))
    asyncio.run(app.start_peter())
    assert app.peter_events.get(timeout=2)["type"] == "model_ready"
    asyncio.run(app.start_peter())
    assert calls == ["shared model"] and app.voice.startups == 1
    assert app.assistant_model_selected


def test_memory_editor_persists_to_assistant_store_and_source_opens_memory(peter_ui, monkeypatch):
    app = peter_ui
    app.navigate("memory")
    memory = app.memory_view
    assert memory.store is app.assistant.memory
    memory.edit()
    dialog = app.page.opened[-1]
    editor, category, source, pinned, temporary, _ = dialog.content.content.controls
    editor.value, category.value, source.value = "Prefer concise replies.", "preferences", "Manual entry"
    pinned.value = True
    dialog.actions[-1].on_click(None)
    entries = app.assistant.memory.list()
    assert len(entries) == 1 and entries[0]["content"] == "Prefer concise replies." and entries[0]["pinned"]
    app.navigate("assistant")
    app.assistant_view.source(dict(kind="memory", id=entries[0]["id"], category="preferences"))
    assert app.view == "memory"
    monkeypatch.setattr(app, "confirm", lambda title, text, action: action())
    app.memory_view.delete(entries[0])
    assert app.assistant.memory.list() == []
