"""Application shell and lifecycle; views, storage, and AI are independent modules."""
import asyncio
import queue
import time
import subprocess
import sys
import threading
from datetime import date, datetime
from pathlib import Path
import flet as ft

from app.config import BASE_DIR, DATA_DIR, ai_python, atomic_write, load_config, save_config
from app.processes import Session
from app.calendar_storage import CalendarStore
from app.storage import Vault
from app.assistant.service import AssistantService
from app.notifications import Notifier
from app.voice import VoiceController
from app.gmail import GmailController
from app.integrations import ExternalAccess, inbox_digest
from app.theme import button, field, palette
from app.views.library import LibraryView
from app.views.assistant import AssistantView
from app.views.notes import EditorConflict, NotesView
from app.views.tasks import TasksView
from app.views.memory import MemoryView
from app.views.workspace import DOCKER_TOOLS, build_home, build_settings, build_tool, build_tools, run_tool
from library.paths import resolve_source
from library.runtime import ModelRuntime
from library.models import selected_settings
from app.views.model_picker import choose_model


class Workspace:
    def __init__(self, page, *, embedded=False):
        self.embedded = embedded
        self.shell_commands, self.shell_events = queue.Queue(), queue.Queue()
        self.page, self.cfg = page, load_config()
        self.c = palette(self.cfg)
        self.view, self.notes_view, self.library_view = "home", None, None
        self.active_tool, self.console_control = None, None
        self.tool_search, self.tool_title, self.tool_logs, self.tool_values = "", "", [], {}
        self.tool_category = "All"
        self.tool_session, self.library_session = Session(), Session()
        self.library_status, self.library_ready, self.library_busy = "Not connected", False, False
        self.library_documents, self.library_chunks = 0, 0
        self.library_source, self.library_messages, self.library_logs = "", [], []
        self.library_progress = None
        self.library_sources, self.library_results = [], []
        self.library_mode, self.library_filter = "ask", ""
        self.library_search_query, self.library_search_seconds = "", 0
        self.closed = False
        self.notifier = Notifier()
        self.assistant_model_selected = False
        self.calendar_day, self.calendar_mode, self.calendar_view = date.today(), "monthly", None
        self.note_name, self.note_mode, self.note_draft = self.cfg.get("last_note", "Home"), "graph", ""
        vault_path = self.cfg.get("vault_path")
        if vault_path and not Path(vault_path).expanduser().is_dir():
            vault_path = None
        self.vault = Vault(Path(vault_path).expanduser() if vault_path else DATA_DIR / "notes")
        self.calendar = CalendarStore(DATA_DIR / "calendar.json", [("Personal checklist", self.vault.root / "Tasks.md"),
                                                                   ("Weekly checklist", DATA_DIR / "tasks" / "Weekly Tasks.md")])
        self.ensure_note()
        self.model_runtime = ModelRuntime(self.cfg["library"])
        self.gmail = GmailController(self.cfg["gmail"])
        self.gmail_status, self.gmail_status_control = self.gmail.client.status()["message"], None
        self.external = ExternalAccess(self.cfg["internet"], self.gmail.client)
        self.assistant = AssistantService(self.vault, self.calendar, self.model_runtime,
                                          DATA_DIR / "assistant-conversation.json", self.cfg["assistant_timezone"], external=self.external)
        self.assistant_view, self.assistant_draft = None, ""
        self.assistant_status = "Ready" if not self.assistant.warning else "Needs attention"
        self.home_view, self.memory_view = None, None
        self.peter_state, self.peter_status = "idle", "Tap the bubble to speak, or type below."
        self.peter_model_status = "Local model connects when needed"
        self.peter_events, self.peter_started = queue.Queue(), False
        self.voice = VoiceController(self.cfg.get("peter", {}))
        self.root = ft.Container(expand=True)
        self.picker = ft.FilePicker(on_result=self.pick_result)
        self.pick_callback, self.export_content = None, None
        page.overlay.append(self.picker)
        page.title, page.padding, page.spacing = "Peter McGuire · NexusDesk", 0, 0
        page.window.width = self.cfg.get("window_width", 1440)
        page.window.height = self.cfg.get("window_height", 900)
        page.window.min_width, page.window.min_height = 1000, 680
        page.window.prevent_close = True
        page.window.on_event = self.window_event
        page.on_keyboard_event, page.on_disconnect = self.keyboard, self.disconnect
        page.on_resize = self.resize
        page.add(self.root)
        self.render()
        page.run_task(self.poll)
        page.run_task(self.start_peter)
        if self.cfg["gmail"].get("check_on_startup", True):
            self.gmail.startup()
        if self.cfg.get("config_warning"):
            self.notify("Configuration is invalid; the original file was preserved. Repair or rename config.json.", True)

    def ensure_note(self):
        names = self.vault.names()
        if not names:
            self.note_name, self.note_draft = None, ""
            return
        if self.note_name not in names:
            self.note_name = "Home" if "Home" in names else names[0]
        self.note_draft = self.vault.read(self.note_name)

    async def start_peter(self):
        """Preload the existing shared runtime and start local voice off the UI thread."""
        settings = self.cfg.get("peter", {})
        if self.peter_started or not settings.get("autostart", True):
            return
        self.peter_started = True
        self.assistant_model_selected = True
        self.peter_model_status = "Loading the shared local model…"
        if self.view == "home" and self.home_view:
            self.home_view.refresh()
        if settings.get("enabled", True):
            self.voice.startup()
        runtime = self.model_runtime
        def load():
            try:
                runtime.ensure()
                if runtime is self.model_runtime:
                    self.peter_events.put(dict(type="model_ready"))
            except Exception as error:
                if runtime is self.model_runtime:
                    self.peter_events.put(dict(type="model_error", message=str(error)))
        threading.Thread(target=load, name="peter-model-startup", daemon=True).start()

    def new_note(self):
        self.prompt("Create a note", "Note title", "", lambda name: self.open_note(self.vault.create(name)))

    def submit_peter(self, text):
        """Voice and Home text both enter the original Assistant tool/transaction loop."""
        if not self.assistant_model_selected:
            choose_model(self, "Peter", lambda: self.submit_peter(text))
            return False
        try:
            self.voice.cancel()
            self.assistant.send(text)
            self.assistant_draft = ""
            self.assistant_status = "Connecting to your local model"
            self.peter_state, self.peter_status = "thinking", self.assistant_status
            if self.view == "home" and self.home_view:
                self.home_view.refresh()
            if self.view == "assistant" and self.assistant_view:
                self.assistant_view.input.value = ""
                self.assistant_view.refresh_messages()
                self.assistant_view.refresh()
            return True
        except (OSError, ValueError) as error:
            self.notify(str(error), True)
            return False

    def toggle_peter_listen(self):
        if not self.cfg.get("peter", {}).get("enabled", True):
            self.notify("Peter voice is disabled in Settings.")
        elif self.assistant.busy:
            self.notify("Peter is finishing the current request.")
        else:
            self.voice.toggle_listen()

    def cancel_peter(self):
        self.voice.cancel()
        self.assistant.close()
        self.peter_state = "thinking" if self.assistant.busy else "idle"
        self.peter_status = "Cancelling current request…" if self.assistant.busy else "Ready when you are."
        if self.home_view and self.view == "home":
            self.home_view.refresh()

    def reset_voice(self):
        self.voice.close()
        self.voice = VoiceController(self.cfg.get("peter", {}))
        self.peter_state, self.peter_status = "idle", "Ready when you are."

    def persist(self, raise_error=False):
        try:
            save_config(self.cfg)
        except (OSError, ValueError) as error:
            if raise_error:
                raise
            self.notify(str(error), True)

    def notify(self, message, error=False):
        self.page.open(ft.SnackBar(ft.Text(message, color=self.c["danger"] if error else self.c["text"]), bgcolor=self.c["raised"]))

    def prompt(self, title, hint, value, callback):
        tf = field(self.c, label=hint, value=value, width=500, autofocus=True)
        dialog = ft.AlertDialog(title=ft.Text(title, color=self.c["text"]), content=tf, bgcolor=self.c["surface"])
        def submit(_):
            try:
                callback(tf.value or "")
                self.page.close(dialog)
            except (OSError, ValueError) as error:
                tf.error_text = str(error)
                tf.update()
        tf.on_submit = submit
        dialog.actions = [ft.TextButton("Cancel", on_click=lambda e: self.page.close(dialog)), ft.TextButton("Save", on_click=submit)]
        self.page.open(dialog)

    def confirm(self, title, text, callback):
        dialog = ft.AlertDialog(title=ft.Text(title, color=self.c["text"]), content=ft.Text(text, color=self.c["muted"]), bgcolor=self.c["surface"])
        def accept(_):
            self.page.close(dialog)
            try:
                callback()
            except (OSError, ValueError) as error:
                self.notify(str(error), True)
        dialog.actions = [ft.TextButton("Cancel", on_click=lambda e: self.page.close(dialog)), ft.TextButton("Continue", on_click=accept)]
        self.page.open(dialog)

    def pick(self, control, directory=True, callback=None):
        if self.embedded:
            def work():
                result = subprocess.run([sys.executable, "-m", "app.native_dialogs", "directory" if directory else "file"],
                    capture_output=True, text=True, encoding="utf-8", cwd=BASE_DIR,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                path = result.stdout.strip()
                if result.returncode:
                    self.shell_events.put(dict(type="error", message="The native file picker could not open. Enter the path manually."))
                elif path:
                    self.shell_commands.put(dict(action="picked", control=control, callback=callback, path=path))
            threading.Thread(target=work, daemon=True).start()
            return
        if self.page.web:
            self.notify("Enter the directory or file path manually in this browser preview. Native Browse works in the desktop app.")
            return
        self.pick_callback = callback or (lambda path: setattr(control, "value", path) or control.update())
        if directory:
            self.picker.get_directory_path(dialog_title="Choose directory")
        else:
            self.picker.pick_files(allow_multiple=False, dialog_title="Choose file")

    def pick_result(self, event):
        path = event.path or (event.files[0].path if event.files else None)
        if path and self.export_content is not None:
            try:
                atomic_write(Path(path), self.export_content)
                self.notify("File exported.")
            except OSError as error:
                self.notify(str(error), True)
        elif path and self.pick_callback:
            self.pick_callback(path)
        self.export_content, self.pick_callback = None, None

    def export_note(self, name):
        content = self.vault.read(name)
        if self.notes_view and self.notes_view.editor is not None and name == self.note_name:
            content = self.notes_view.editor.value or ""
        self.export(content, name + ".md")

    def export_log(self):
        self.export("\n".join(self.tool_logs), "workspace-log.txt")

    def export(self, content, filename):
        if self.embedded:
            self.shell_events.put(dict(type="download", filename=filename, content=content))
            return
        if self.page.web:
            self.notify("Export uses the native desktop save dialog.")
            return
        self.export_content = content
        self.picker.save_file(file_name=filename)

    def navigate(self, view):
        if self.embedded and view in {"home", "notes", "tools"}:
            self.shell_events.put(dict(type="route", route=view))
            return
        try:
            if self.notes_view:
                self.notes_view.save()
            self.notes_view = None
            if view == "tasks":
                self.calendar_mode = "monthly"
            elif view == "notes":
                self.note_mode = "graph"
                self.ensure_note()
            self.view = view
            self.render()
            if view == "assistant" and not self.assistant_model_selected and not self.assistant.busy:
                choose_model(self, "Peter", lambda: None)
        except EditorConflict as error:
            def reload():
                self.notes_view.recover_draft()
                self.navigate(view)
                self.notify("Your unsaved draft was backed up in the vault's .history folder.")
            self.confirm("Note changed outside the editor", str(error) + " Reload the saved version? Your draft will be backed up first.", reload)
        except (ValueError, OSError) as error:
            self.notify("Could not save the note: " + str(error), True)

    def open_tool(self, tool):
        if tool.key in DOCKER_TOOLS and self.tool_session.running:
            self.notify("Another tool is still running. Wait for it to finish or cancel it first.")
            return
        self.active_tool = tool
        self.navigate("tool")
        if self.view == "tool" and tool.key in DOCKER_TOOLS:
            run_tool(self, tool)

    def open_calendar(self, day=None, mode="daily"):
        self.calendar_day = day or date.today()
        self.navigate("tasks")
        if self.view == "tasks":
            self.calendar_view.mode(mode)

    def open_unscheduled(self):
        self.open_calendar(mode="monthly")
        if self.view == "tasks":
            self.calendar_view.unscheduled(None)

    def open_note(self, name):
        if self.embedded:
            if name not in self.vault.names():
                self.notify("This note no longer exists.", True)
                return
            self.shell_events.put(dict(type="route", route="notes", id=self.vault.note_id(name)))
            return
        try:
            if self.notes_view:
                self.notes_view.save()
            if name not in self.vault.names():
                self.vault.create(name)
            self.note_name, self.note_draft = name, self.vault.read(name)
            self.cfg["last_note"] = name
            self.cfg["notes_last_used"][name] = time.time()
            self.persist()
            self.note_mode = "read"
            self.notes_view, self.view = None, "notes"
            self.render()
        except (OSError, ValueError) as error:
            self.notify(str(error), True)

    def start_library(self, source, rebuild=False, model_selected=False):
        if self.library_session.running:
            self.notify("Stop the current AI session before choosing another source or updating its index.", True)
            return
        if not model_selected:
            choose_model(self, "AI Library", lambda: self.start_library(source, rebuild, model_selected=True))
            return
        try:
            paths = resolve_source(source or "")
            self.library_ready, self.library_busy = False, True
            self.library_status, self.library_source = "Preparing library…", str(paths.source)
            self.library_progress = {"phase": "preparing", "total": None, "completed": 0, "message": "Preparing your documents"}
            self.library_sources, self.library_results, self.library_filter = [], [], ""
            self.library_logs, self.library_messages = [], []
            self.cfg["library"]["last_source"] = str(paths.source)
            self.persist(raise_error=True)
            command = [ai_python(self.cfg), "-u", "-m", "library.worker", "--source", str(paths.source), "--external-model"]
            if rebuild:
                command.append("--rebuild")
            self.library_session = Session()
            self.library_session.start(command, protocol=True)
            session = self.library_session
            def model_failed(message):
                session.events.put({"type": "error", "message": message})
                session.stop()
            self.model_runtime.start(model_failed)
            self.cfg["usage"]["library_ai"] = self.cfg["usage"].get("library_ai", 0) + 1
            self.persist()
            self.view = "library"
            self.render()
        except (OSError, ValueError) as error:
            self.library_progress = None
            self.library_busy = False
            self.library_status = "Not connected"
            self.notify(str(error), True)

    def stop_library(self):
        self.library_session.stop()
        self.library_ready, self.library_busy = False, False
        self.library_progress = None
        self.library_status = "Stopping…" if self.library_session.running else "Not connected"
        if self.library_view and self.view == "library":
            self.library_view.refresh()

    def stop_tool(self):
        self.tool_session.stop()
        self.notify("Stopping the tool…")

    def assistant_data_changed(self):
        if self.notes_view and self.notes_view.editor is not None:
            self.notes_view.refresh_list()
            self.notes_view.note_list.update()
            return
        self.ensure_note()
        if self.view in {"home", "notes", "tasks", "memory"}:
            self.render()

    def reset_assistant(self):
        self.assistant.close()
        self.model_runtime.close(wait=True)
        self.model_runtime = ModelRuntime(self.cfg["library"])
        self.assistant = AssistantService(self.vault, self.calendar, self.model_runtime,
                                          DATA_DIR / "assistant-conversation.json", self.cfg["assistant_timezone"], external=self.external)
        self.assistant_status = "Ready" if not self.assistant.warning else "Needs attention"
        self.peter_model_status = "Local model connects when needed"

    def configure_integrations(self):
        self.gmail.close()
        self.gmail = GmailController(self.cfg["gmail"])
        self.external = ExternalAccess(self.cfg["internet"], self.gmail.client)
        self.assistant.external = self.external
        self.gmail_status = self.gmail.client.status()["message"]

    def connect_gmail(self, client_file):
        try:
            if self.assistant.busy:
                raise ValueError("Wait for Peter's current answer before connecting Gmail.")
            self.cfg["gmail"]["client_file"] = str(client_file or "").strip().strip('"')
            self.persist(raise_error=True)
            self.configure_integrations()
            if not self.gmail.connect():
                raise ValueError("A Gmail operation is already running.")
        except (OSError, ValueError) as error:
            self.notify(str(error), True)

    def check_gmail(self):
        if not self.gmail.check():
            self.notify("Gmail is already checking or signing in.")

    def select_model(self, choice):
        if self.assistant.busy:
            raise ValueError("Wait for Peter's current answer before switching models.")
        candidate = selected_settings(self.cfg["library"], choice)
        current = self.model_runtime.settings
        changed = any(candidate.get(key) != current.get(key) for key in ("model_path", "model_id", "external_model"))
        if changed and self.library_session.running:
            raise ValueError("Stop AI Library before switching the shared model. Both sections use the same running model.")
        if changed:
            previous = self.cfg["library"]
            self.cfg["library"] = candidate
            try:
                self.persist(raise_error=True)
            except (ValueError, OSError):
                self.cfg["library"] = previous
                raise
            self.reset_assistant()
        self.assistant_model_selected = True
        if self.view == "assistant":
            self.assistant_view.refresh()

    def ai_answer_ready(self, section, text):
        self.notify(section + " answer is ready.")
        self.notifier.send(self.cfg["notifications"], section, text)

    async def poll(self):
        while not self.closed:
            while True:
                try:
                    command = self.shell_commands.get_nowait()
                except queue.Empty:
                    break
                try:
                    action = command.get("action")
                    if action == "navigate":
                        self.navigate(command["view"])
                        if command.get("day") and self.calendar_view:
                            self.calendar_view.open_day(date.fromisoformat(command["day"]))
                    elif action == "tool":
                        from app.tools import TOOLS
                        self.open_tool(next(t for t in TOOLS if t.key == command["key"]))
                    elif action == "picked":
                        if command.get("callback"):
                            command["callback"](command["path"])
                        else:
                            command["control"].value = command["path"]
                            command["control"].update()
                    elif action == "theme":
                        self.cfg["theme_dark"] = command["dark"]
                        self.render()
                except (ValueError, OSError, StopIteration) as error:
                    self.notify(str(error), True)
            changed, chat_changed, console_changed, home_changed = False, False, False, False
            peter_changed = False
            assistant_changed = False
            while True:
                try:
                    event = self.gmail.events.get_nowait()
                except queue.Empty:
                    break
                if event["type"] in {"gmail_status", "gmail_error"}:
                    self.gmail_status = event.get("message", "")
                    if self.gmail_status_control and self.view == "settings":
                        self.gmail_status_control.value = self.gmail_status
                        self.gmail_status_control.update()
                    if event["type"] == "gmail_error":
                        self.notify("Gmail: " + self.gmail_status, True)
                elif event["type"] == "gmail_summary":
                    try:
                        # A crash after saving but before acknowledgment replays
                        # the same batch; retain one visible copy and retry ack.
                        if not any(m.get("gmail_batch_id") == event["batch_id"] for m in self.assistant.messages):
                            self.assistant.append(inbox_digest(event))
                        self.gmail.acknowledge(event["batch_id"])
                        assistant_changed = peter_changed = True
                        if event["count"] and self.cfg["peter"].get("enabled", True) and self.cfg["peter"].get("speech_enabled", True) and not self.assistant.busy:
                            intro = "Gmail connected. I've listed recent inbox messages." if event.get("first_sync") else f"You have {event['count']} new inbox messages. I've listed them in the window."
                            self.voice.speak(intro)
                        if self.assistant_view and self.view == "assistant":
                            self.assistant_view.refresh_messages()
                    except (OSError, ValueError) as error:
                        self.notify("Gmail digest could not be saved; it will be retried next check: " + str(error), True)
                elif event["type"] == "gmail_authorize":
                    self.notify("Complete Gmail's read-only sign-in in your browser.")
            while True:
                try:
                    event = self.peter_events.get_nowait()
                except queue.Empty:
                    break
                peter_changed = True
                if event["type"] == "model_ready":
                    self.peter_model_status = "Local model ready · Shared with AI Library"
                else:
                    self.peter_model_status = event["message"]
                    self.peter_state = "error"
                    self.peter_status = "Local model startup: " + event["message"]
                    self.notify("Local model startup: " + event["message"], True)
            while True:
                try:
                    event = self.voice.events.get_nowait()
                except queue.Empty:
                    break
                peter_changed = True
                if event["type"] == "transcript":
                    self.submit_peter(event["text"])
                elif event["type"] == "voice_status":
                    self.peter_status = event.get("message", "")
                    self.peter_state = "thinking" if self.assistant.busy else event.get("state", "idle")
                elif event["type"] == "voice_error":
                    self.peter_state, self.peter_status = "error", event["message"]
                    self.notify(event["message"], True)
            while True:
                try:
                    self.notify(self.notifier.events.get_nowait(), True)
                except queue.Empty:
                    break
            while True:
                try:
                    event = self.assistant.events.get_nowait()
                except queue.Empty:
                    break
                assistant_changed = True
                peter_changed = True
                if event["type"] == "status":
                    self.assistant_status = event["message"]
                    self.peter_state = "thinking"
                elif event["type"] in {"answer", "error"}:
                    self.assistant.busy = False
                    self.assistant_status = "Ready" if event["type"] == "answer" else "Request failed"
                    self.peter_state = "idle" if event["type"] == "answer" else "error"
                    self.peter_status = "Ready when you are." if event["type"] == "answer" else event.get("message", "Request failed")
                    message = dict(role="assistant", text=event.get("text", "**Could not complete the request:** " + event.get("message", "")))
                    if event["type"] == "answer":
                        message.update({k: event.get(k) for k in ("sources", "changes", "operation_id")})
                    try:
                        self.assistant.append(message)
                    except (OSError, ValueError) as error:
                        self.notify("Could not save chat history: " + str(error), True)
                    if event.get("operation_id"):
                        self.assistant_data_changed()
                    if event["type"] == "answer":
                        self.peter_model_status = "Local model ready · Shared with AI Library"
                        if self.cfg.get("peter", {}).get("enabled", True) and self.cfg.get("peter", {}).get("speech_enabled", True):
                            self.voice.speak(event["text"])
                        self.ai_answer_ready("Peter", event["text"])
                    if self.assistant_view and self.view == "assistant":
                        self.assistant_view.refresh_messages()
            for session, library in [(self.library_session, True), (self.tool_session, False)]:
                while True:
                    try:
                        event = session.events.get_nowait()
                    except queue.Empty:
                        break
                    kind = event["type"]
                    if not library:
                        self.tool_logs.append(event.get("message", f"Process exited with code {event.get('code')}."))
                        self.tool_logs = self.tool_logs[-2500:]
                        console_changed = True
                        if kind == "exit" and self.view == "tool":
                            self.render()
                        continue
                    changed = True
                    if kind in {"ready", "error", "exit", "closed"}:
                        home_changed = True
                    if kind == "log":
                        self.library_logs.append(event.get("message", ""))
                        self.library_logs = self.library_logs[-200:]
                    elif kind == "progress":
                        self.library_progress = event
                        self.library_status = event.get("message", "Processing documents…")
                    elif kind == "ready":
                        self.library_ready, self.library_busy, self.library_status = True, False, "Connected"
                        self.library_progress = None
                        self.library_sources = event.get("sources", [])
                        self.library_documents, self.library_chunks = event.get("documents", 0), event.get("chunks", 0)
                    elif kind == "thinking":
                        self.library_busy, self.library_status = True, event.get("message", "Reading evidence…")
                    elif kind == "results":
                        self.library_results = event["results"]
                        self.library_search_query, self.library_search_seconds = event["query"], event["seconds"]
                        self.library_busy, self.library_status, chat_changed = False, "Connected", True
                    elif kind == "answer":
                        self.library_messages.append({"role": "assistant", "text": event["answer"], "sources": event["sources"]})
                        self.library_busy, self.library_status, chat_changed = False, "Connected", True
                        self.ai_answer_ready("AI Library", event["answer"])
                    elif kind == "error":
                        self.library_progress = None
                        self.library_logs.append(event["message"])
                        self.library_messages.append({"role": "assistant", "text": "**Session error:** " + event["message"]})
                        self.library_busy, chat_changed = False, True
                        self.library_status = "Connected" if self.library_ready else "Could not launch"
                    elif kind in {"exit", "closed"}:
                        self.library_progress = None
                        self.library_ready, self.library_busy = False, False
                        if self.library_status != "Could not launch":
                            self.library_status = "Disconnected"
            try:
                if self.view == "home" and datetime.now().replace(second=0, microsecond=0) != self.home_focus_minute:
                    home_changed = True
                if assistant_changed and self.assistant_view and self.view == "assistant":
                    self.assistant_view.refresh()
                if home_changed and self.view == "home":
                    self.render()
                if self.view == "home" and self.home_view:
                    if peter_changed:
                        self.home_view.refresh()
                    self.home_view.expire_conversation()
                if changed and self.library_view and self.view == "library":
                    if chat_changed:
                        self.library_view.refresh_messages()
                    self.library_view.refresh()
                if console_changed and self.console_control and self.view == "tool":
                    self.console_control.value = "\n".join(self.tool_logs[-600:])
                    self.console_control.update()
            except (AssertionError, RuntimeError):
                pass  # A control may have been replaced during navigation.
            await asyncio.sleep(0.15)

    def keyboard(self, event):
        if self.embedded and (event.ctrl or event.meta) and event.key.lower() == "k":
            self.shell_events.put(dict(type="commands"))
            return
        if (event.ctrl or event.meta) and event.key.lower() in {" ", "space"}:
            self.toggle_peter_listen()
            return
        if self.view == "notes" and self.notes_view and (event.ctrl or event.meta):
            if event.key.lower() == "s":
                self.notes_view.save()
                self.notify("Note saved.")
            elif event.key.lower() == "l":
                self.notes_view.insert_link()

    def resize(self, _):
        if self.view == "tasks" and self.calendar_view:
            self.calendar_view.refresh(True)
        elif self.view == "notes" and self.notes_view and self.note_mode == "graph":
            # The canvas follows its layout via on_resize; keep the graph and viewport alive.
            pass
        elif self.view not in {"notes", "tool", "library", "settings", "assistant", "memory"}:
            self.render()

    def disconnect(self, _):
        self.close()

    def close(self):
        if self.notes_view:
            self.notes_view.save()
        self.closed = True
        self.library_session.stop()
        self.tool_session.stop()
        self.assistant.close()
        self.gmail.close()
        self.voice.close()
        self.notifier.close()
        self.model_runtime.close()
        self.cfg.update(window_width=self.page.window.width, window_height=self.page.window.height)
        self.persist()

    def window_event(self, event):
        if event.data == "close":
            try:
                self.close()
                self.page.window.destroy()
            except (ValueError, OSError) as error:
                self.notify("Save failed: " + str(error), True)

    def render(self):
        self.c = palette(self.cfg)
        c = self.c
        self.root.bgcolor = c["bg"]
        self.page.bgcolor = c["bg"]
        self.page.theme_mode = ft.ThemeMode.DARK if self.cfg["theme_dark"] else ft.ThemeMode.LIGHT
        self.page.theme = ft.Theme(font_family="DejaVu Sans", color_scheme_seed=c["accent"], use_material3=True)
        if self.view == "notes":
            self.notes_view = NotesView(self)
            content = self.notes_view.content
        elif self.view == "library":
            self.library_view = LibraryView(self)
            content = self.library_view.content
        elif self.view == "assistant":
            self.assistant_view = AssistantView(self)
            content = self.assistant_view.content
        elif self.view == "tasks":
            self.calendar_view = TasksView(self)
            content = self.calendar_view.content
        elif self.view == "memory":
            self.memory_view = MemoryView(self)
            content = self.memory_view.content
        elif self.view == "tool":
            content = build_tool(self)
        elif self.view == "tools":
            content = build_tools(self)
        elif self.view == "settings":
            content = build_settings(self)
        else:
            content = build_home(self)
        if self.embedded:
            self.root.content = ft.Container(content, expand=True, padding=16)
            self.page.update()
            return
        nav = []
        for key, title, icon in [("tasks", "Calendar", ft.Icons.CALENDAR_MONTH_OUTLINED),
                                 ("tools", "Tools", ft.Icons.GRID_VIEW_ROUNDED),
                                 ("notes", "Notes", ft.Icons.EDIT_NOTE_OUTLINED),
                                 ("library", "AI Library", ft.Icons.AUTO_AWESOME_OUTLINED),
                                 ("memory", "Memory", ft.Icons.PSYCHOLOGY_OUTLINED)]:
            selected = self.view == key or self.view == "tool" and key == "tools"
            nav.append(ft.Container(ft.Column([ft.Icon(icon, size=22, color=c["accent"] if selected else c["muted"]),
                                               ft.Text(title, size=10, color=c["text"] if selected else c["muted"], text_align=ft.TextAlign.CENTER)],
                                              spacing=7, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                                    padding=ft.padding.symmetric(vertical=12), width=70, border_radius=13,
                                    bgcolor=c["raised"] if selected else None, on_click=lambda e, k=key: self.navigate(k), ink=True))
        rail = ft.Container(ft.Column([
            ft.Container(ft.Text("P", size=25, weight=ft.FontWeight.W_400, color=c["cyan"]), alignment=ft.alignment.center,
                         width=44, height=44, border_radius=22, bgcolor=c["raised"], border=ft.border.all(1, c["border"]),
                         margin=ft.margin.only(bottom=12),
                         on_click=lambda e: self.navigate("home"), ink=True, tooltip="Home"),
            *nav, ft.Container(expand=True),
            ft.IconButton(ft.Icons.SETTINGS_OUTLINED, tooltip="Settings", icon_color=c["accent"] if self.view == "settings" else c["muted"],
                          on_click=lambda e: self.navigate("settings")),
            ft.Container(ft.Text("P", size=11, color=c["muted"]), width=34, height=34, bgcolor=c["raised"], border_radius=17,
                         alignment=ft.alignment.center, tooltip="Peter McGuire")],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, expand=True, spacing=7), bgcolor=c["sidebar"],
            width=94, padding=ft.padding.symmetric(horizontal=10, vertical=20), border=ft.border.only(right=ft.BorderSide(1, c["border"])))
        header = ft.Row([ft.Text("PETER", size=11, color=c["text"], weight=ft.FontWeight.W_600),
                         ft.Text("/", color=c["dim"]), ft.Text("NEXUSDESK", size=10, color=c["dim"]), ft.Container(expand=True),
                         ft.Icon(ft.Icons.CIRCLE, size=6, color=c["success"]), ft.Text("LOCAL", size=10, color=c["muted"])], spacing=12)
        self.root.content = ft.Row([rail, ft.Container(ft.Column([header, ft.Divider(height=1, color=c["border"]), content],
                                                               expand=True, spacing=23), expand=True,
                                                       padding=ft.padding.symmetric(horizontal=28, vertical=22))],
                                   expand=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        self.page.update()


def main(page: ft.Page):
    Workspace(page)
