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
    def startup(self, text=None):
        self.startups += 1
        self.greeting = text
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
    assert len(app.home_view.conversation.controls) == 1
    assert [message['text'] for message in app.assistant.messages] == ['A typed message', 'A spoken message']


def test_home_messages_fade_after_twenty_idle_seconds_once(peter_ui, monkeypatch):
    from app.views import home
    app, clock = peter_ui, [100.0]
    monkeypatch.setattr(home.clock, 'monotonic', lambda: clock[0])
    app.assistant.append(dict(role='user', text='Current request'))
    app.home_view.refresh_conversation()
    clock[0] = 102.0
    app.assistant.append(dict(role='assistant', text='Current reply'))
    app.home_view.refresh_conversation()
    view = app.home_view
    assert view.conversation_frame.opacity == 1 and len(view.conversation.controls) == 2
    assert not view.expire_conversation(now=121.9)
    app.assistant.busy = True
    assert not view.expire_conversation(now=130.0)
    app.assistant.busy = False
    app.peter_state = 'speaking'
    assert not view.expire_conversation(now=130.0)
    app.peter_state = 'idle'
    assert not view.expire_conversation(now=149.9)
    assert view.expire_conversation(now=150.0)
    assert view.conversation_frame.opacity == 0 and view.conversation_frame.animate_opacity.duration == 300
    assert not view.expire_conversation(now=160.0)
    assert len(app.assistant.messages) == 2  # Fading never deletes stored messages.
    clock[0] = 170.0
    app.assistant.append(dict(role='user', text='Next request'))
    view.refresh_conversation()
    assert view.conversation_frame.opacity == 1 and len(view.conversation.controls) == 1
    assert view.conversation.controls[0].content.controls[1].value == 'Next request'


def test_home_history_toggle_survives_navigation_and_does_not_expire(peter_ui):
    from app.views.home import build_home
    app = peter_ui
    app.assistant.messages = [dict(role='user' if n % 2 == 0 else 'assistant', text=f'Message {n}') for n in range(200)]
    del app.home_conversation_state
    build_home(app)
    view = app.home_view
    assert view.conversation.controls == [] and view.conversation_frame.opacity == 0
    assert view.history_toggle.text == 'Show history'
    view.toggle_history(None)
    assert view.history_toggle.text == 'Hide' and len(view.conversation.controls) == 200
    assert view.conversation_frame.opacity == 1 and not view.conversation.auto_scroll
    assert not view.expire_conversation(now=100000)
    app.navigate('tools')
    app.navigate('home')
    assert app.home_view.history_toggle.text == 'Hide' and len(app.home_view.conversation.controls) == 200
    app.home_view.toggle_history(None)
    assert app.home_view.history_toggle.text == 'Show history' and app.home_view.conversation_frame.opacity == 0
    assert len(app.assistant.messages) == 200
    app.assistant.messages.append(dict(role='user', text='New message'))
    app.home_view.refresh_conversation()
    assert app.home_view.conversation_frame.opacity == 1
    assert app.home_view.conversation.controls[0].content.controls[1].value == 'New message'


def test_home_source_and_undo_actions_work_without_conversation_tab(peter_ui):
    from app.assistant.transactions import Transaction
    from app.assistant.tools import Tools
    app = peter_ui
    transaction = Transaction(app.vault, app.calendar, app.assistant.history)
    tools = Tools(transaction)
    tools.execute('create_note', dict(name='Home action check', content='Verified.'))
    operation = transaction.commit('Synthetic Home undo check')
    app.assistant.append(dict(role='assistant', text='Saved.', sources=[dict(kind='note', name='Home action check')], operation_id=operation))
    app.home_view.refresh_conversation()
    message = app.home_view.conversation.controls[-1].content.controls
    source_button, undo_button = message[2].controls[0], message[3]
    assert not undo_button.disabled
    source_button.on_click(None)
    assert app.view == 'notes' and app.note_name == 'Home action check'
    app.navigate('home')
    app.home_view.undo(operation)
    assert 'Home action check' not in app.vault.names()
    assert app.assistant.messages[-1]['text'].startswith('Undid that operation.')


def test_sidebar_has_one_home_entry_and_no_duplicate_peter_conversation(peter_ui):
    rail = peter_ui.root.content.controls[0].content
    labels = [item.content.controls[1].value for item in rail.controls
              if isinstance(item, ft.Container) and isinstance(item.content, ft.Column)
              and len(item.content.controls) > 1 and isinstance(item.content.controls[1], ft.Text)]
    assert labels == ['Calendar', 'Tools', 'Notes', 'AI Library', 'Memory']
    assert rail.controls[0].tooltip == 'Home'


def test_startup_preloads_shared_model_once_and_greets_through_voice(peter_ui, monkeypatch):
    app, calls = peter_ui, []
    monkeypatch.setattr(app.model_runtime, "ensure", lambda: calls.append("shared model"))
    asyncio.run(app.start_peter())
    assert app.peter_events.get(timeout=2)["type"] == "model_ready"
    asyncio.run(app.start_peter())
    assert calls == ["shared model"] and app.voice.startups == 1
    assert app.assistant_model_selected
    assert app.voice.greeting.endswith(", sir. Ready for today?")
    assert app.assistant.messages[-1]["text"] == app.voice.greeting
    assert app.assistant.briefing.awaiting_ready


def test_startup_greets_and_accepts_ready_without_model_preload(peter_ui, monkeypatch):
    app = peter_ui
    app.cfg['peter']['autostart'] = False
    monkeypatch.setattr(app.model_runtime, 'ensure', lambda: pytest.fail('Readiness must not load the model'))
    asyncio.run(app.start_peter())
    assert app.voice.startups == 1 and not app.assistant_model_selected
    assert app.submit_peter('Yes, please.')
    event = app.assistant.events.get(timeout=2)
    assert event['type'] == 'answer' and event['briefing_complete']
    assert 'Tasks pending today:' in event['text'] or 'no pending tasks' in event['text']


def queued_email():
    return dict(id='a1', sender='Ada Example <ada@example.test>', subject='A test update',
                url='https://mail.google.com/mail/u/0/#inbox/a1', received_ms=1)


def test_startup_mail_is_saved_without_interrupting_readiness(peter_ui, monkeypatch):
    app, acknowledgements = peter_ui, []
    app.assistant.briefing.start()
    monkeypatch.setattr(app.gmail, 'acknowledge', acknowledgements.append)
    app.receive_email_batch(dict(messages=[queued_email()], account='test@example.test', batch_id='batch1'))
    assert acknowledgements == ['batch1'] and app.email_state_path.exists()
    assert not app.assistant.messages and not app.voice.spoken
    assert not app.offer_next_email()
    saved = app.email_state_path.read_text()
    assert 'Ada Example' in saved and 'body' not in saved


def test_email_offer_waits_for_calendar_speech_then_preserves_exact_prompt(peter_ui, monkeypatch):
    app = peter_ui
    monkeypatch.setattr(app.gmail.client, 'status', lambda: dict(connected=True, account='test@example.test'))
    app.email_briefing.add([queued_email()], account='test@example.test')
    app.assistant.events.put(dict(type='answer', text='Your calendar is clear.',
        sources=[], changes=[], operation_id=None, briefing_complete=True))
    def speak(text):
        app.voice.spoken.append(text)
        app.voice.state = 'thinking'
    monkeypatch.setattr(app.voice, 'speak', speak)
    one_poll(app, monkeypatch)
    assert app.voice.spoken == ['Your calendar is clear.']
    assert app.email_prompt_pending and app.email_briefing.awaiting is None
    app.voice.state = 'idle'
    one_poll(app, monkeypatch)
    prompt = 'You have a new email from Ada Example, would you like me to read it for you, sir?'
    assert app.voice.spoken[-1] == prompt
    assert app.assistant.messages[-1]['text'] == prompt
    assert app.assistant.messages[-1]['email_offer']['id'] == 'a1'


def test_email_headers_are_not_acknowledged_when_queue_save_fails(peter_ui, monkeypatch):
    app, acknowledgements = peter_ui, []
    monkeypatch.setattr(app.gmail, 'acknowledge', acknowledgements.append)
    def fail():
        raise OSError('Disk full')
    monkeypatch.setattr(app, 'save_email_briefing', fail)
    with pytest.raises(OSError, match='Disk full'):
        app.receive_email_batch(dict(messages=[queued_email()], account='test@example.test', batch_id='batch1'))
    assert not acknowledgements


def test_email_reading_flags_remain_in_saved_history(peter_ui, monkeypatch):
    app = peter_ui
    app.assistant.events.put(dict(type='answer', text='Subject: Test.\n\nQuoted email contents.',
        sources=[], changes=[], operation_id=None, untrusted=True, email_content=True,
        email_handled=dict(account='test@example.test', id='a1')))
    one_poll(app, monkeypatch)
    assert app.assistant.messages[-1]['email_content']
    assert app.assistant.messages[-1]['untrusted']


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
