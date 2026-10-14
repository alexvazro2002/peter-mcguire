"""Background Assistant requests and local conversation persistence for the Flet UI."""
import json
import queue
import threading
from datetime import datetime, timezone

from app.assistant.engine import Engine
from app.assistant.transactions import History
from app.briefing import DailyBriefing
from app.config import atomic_write
from app.memory import MemoryStore
from app.opening import OpenRequests


class AssistantService:
    def __init__(self, vault, calendar, runtime, conversation_path, zone="Europe/London", memory=None, *, external=None):
        self.vault, self.calendar, self.runtime, self.path, self.zone = vault, calendar, runtime, conversation_path, zone
        self.memory = memory or MemoryStore(conversation_path.parent / "personal-memory.json")
        self.external = external
        self.opening = OpenRequests(vault)
        self.briefing = DailyBriefing(calendar, zone)
        self.email_briefing = None
        self.history = History(vault, calendar, vault.root / ".history" / "assistant", memory=self.memory)
        self.events, self.cancel, self.lock = queue.Queue(), threading.Event(), threading.RLock()
        self.busy, self.messages, self.warning, self.storage_error = False, [], "", ""
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, list) or any(not isinstance(m, dict) or m.get("role") not in {"user", "assistant"} or not isinstance(m.get("text"), str) for m in data):
                    raise ValueError("Conversation must be an array of chat messages.")
                self.messages = data[-200:]
            except (OSError, ValueError) as error:
                self.storage_error = "Conversation could not be read; its file was preserved: " + str(error)
                self.warning = self.storage_error
        try:
            self.history.recover()
        except (OSError, ValueError, RuntimeError) as error:
            self.warning = "Assistant recovery needs attention: " + str(error)

    def persist(self):
        if self.storage_error:
            raise ValueError(self.storage_error)
        atomic_write(self.path, json.dumps(self.messages[-200:], ensure_ascii=False, indent=2) + "\n")

    def append(self, message):
        with self.lock:
            message.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
            previous = self.messages
            self.messages = [*previous, message][-200:]
            try:
                self.persist()
            except (OSError, ValueError):
                self.messages = previous
                raise

    def send(self, request):
        with self.lock:
            if self.busy:
                raise ValueError("Wait for the current Assistant request to finish.")
            if self.warning:
                raise ValueError(self.warning)
            if not isinstance(request, str) or not request.strip() or len(request) > 8000:
                raise ValueError("Enter a message between 1 and 8,000 characters.")
            conversation = list(self.messages)
            self.append(dict(role="user", text=request))
            self.cancel.clear()
            self.busy = True

        def emit(**event):
            self.events.put(event)

        def work():
            try:
                opened = self.opening.respond(request, conversation, self.external, self.cancel)
                if opened is not None:
                    emit(type="answer", **opened)
                    return
                briefing = self.briefing.respond(request, cancel=self.cancel)
                if briefing is not None:
                    emit(type="answer", **briefing)
                    return
                if self.email_briefing is not None:
                    email = self.email_briefing.respond(request, cancel=self.cancel)
                    if email is not None:
                        emit(type="answer", **email)
                        return
                quick = self.external.quick_answer(request, self.zone, cancel=self.cancel) if self.external is not None else None
                if quick is not None:
                    emit(type="answer", **quick)
                    return
                emit(type="status", message="Connecting to your local model")
                self.runtime.ensure()
                engine = Engine(self.vault, self.calendar, self.history, self.runtime.client,
                                self.runtime.settings, self.zone, external=self.external)
                result = engine.run(request, conversation, emit, self.cancel)
                emit(type="answer", **result)
            except Exception as error:
                emit(type="error", message=str(error))
        threading.Thread(target=work, daemon=True).start()

    def undo(self, operation_id):
        with self.lock:
            if self.busy:
                raise ValueError("Wait for the current request before undoing changes.")
            result = self.history.undo(operation_id)
            self.append(dict(role="assistant", text="Undid that operation. All affected workspace data and personal memories were restored.", operation_id=result))
            return result

    def clear(self):
        if self.busy:
            raise ValueError("Wait for the current request before clearing chat.")
        self.messages = []
        self.persist()

    def close(self):
        self.cancel.set()
