"""Background Assistant requests and local conversation persistence for the Flet UI."""
import json
import queue
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from app.assistant.engine import Engine
from app.assistant.transactions import History
from app.briefing import DailyBriefing
from app.config import atomic_write
from app.memory import MemoryStore
from app.opening import OpenRequests
from app.assistant.mail_flow import EmailReviewFlow, link_request
from app.assistant.workspace_actions import respond as workspace_action
from app.assistant.conversation import social_request, social_answer
from app.assistant.model_fallback import ModelToolError, ToolFallback
from app.assistant.progress import acknowledgement
from app.assistant.email_tasks import respond as email_task
from app.assistant.inference import DeadlineClient
from app.assistant.simple_creation import respond as simple_creation


class AssistantService:
    def __init__(self, vault, calendar, runtime, conversation_path, zone="Europe/London", memory=None, *, external=None):
        self.vault, self.calendar, self.runtime, self.path, self.zone = vault, calendar, runtime, conversation_path, zone
        self.memory = memory or MemoryStore(conversation_path.parent / "personal-memory.json")
        self.external = external
        self.tool_fallback = ToolFallback(getattr(runtime, 'settings', {}))
        self.opening = OpenRequests(vault, zone=zone)
        self.briefing = DailyBriefing(calendar, zone)
        self.email_briefing = None
        self.mail_flow = EmailReviewFlow(conversation_path.parent, external, runtime, zone) if external is not None else None
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

        progress_lock, finished = threading.Lock(), threading.Event()
        acknowledged = False

        def emit(**event):
            if event.get('type') in {'answer', 'error'}:
                with progress_lock:
                    finished.set()
                release = getattr(self.tool_fallback, 'release', None)
                if release:
                    release()
            with progress_lock:
                self.events.put(event)

        def acknowledge(text="I'm working through that now.", *, again=False):
            nonlocal acknowledged
            with progress_lock:
                if finished.is_set() or self.cancel.is_set() or acknowledged and not again:
                    return
                acknowledged = True
                self.events.put(dict(type='acknowledgement', text=text))

        def work():
            started = time.monotonic()
            delayed = threading.Timer(7.0, acknowledge)
            delayed.daemon = True
            initial = acknowledgement(request)
            if initial:
                acknowledge(initial)
            delayed.start()
            try:
                direct = simple_creation(request, self.vault, self.calendar, self.history, self.cancel, self.zone)
                if direct is not None:
                    emit(type='answer', **direct)
                    return
                request_runtime = SimpleNamespace(ensure=getattr(self.runtime, 'ensure', lambda: None), settings=getattr(self.runtime, 'settings', {}),
                    client=DeadlineClient(getattr(self.runtime, 'client', None), self.cancel))
                direct = email_task(request, conversation, self.vault, self.calendar, self.history, self.cancel, self.zone)
                if direct is not None:
                    emit(type='answer', **direct)
                    return
                direct = workspace_action(request, conversation, self.vault, self.calendar, self.history, self.cancel)
                if direct is not None:
                    emit(type='answer', **direct)
                    return
                if self.mail_flow is not None and link_request(request):
                    linked = self.mail_flow.respond(request, conversation, self.cancel, emit, runtime=request_runtime)
                    if linked is not None:
                        emit(type='answer', **linked)
                        return
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
                if self.mail_flow is not None:
                    email = self.mail_flow.respond(request, conversation, self.cancel, emit, runtime=request_runtime)
                    if email is not None:
                        emit(type='answer', **email)
                        return
                quick = self.external.quick_answer(request, self.zone, cancel=self.cancel, conversation=conversation) if self.external is not None else None
                if quick is not None:
                    emit(type="answer", **quick)
                    return
                emit(type="status", message="Connecting to your local model")
                self.runtime.ensure()
                if social_request(request):
                    social = social_answer(request, conversation, request_runtime.client)
                    if social is not None:
                        emit(type='answer', **social)
                        return
                engine = Engine(self.vault, self.calendar, self.history, request_runtime.client,
                                self.runtime.settings, self.zone, external=self.external)
                try:
                    result = engine.run(request, conversation, emit, self.cancel)
                except ModelToolError:
                    if self.cancel.is_set():
                        raise ValueError('Request cancelled. Nothing was saved.')
                    acknowledge("This needs another approach. I'm trying the other local model.", again=True)
                    emit(type='status', message='Retrying with another installed local model')
                    fallback = self.tool_fallback.ensure(self.cancel)
                    engine = Engine(self.vault, self.calendar, self.history, DeadlineClient(fallback.client, self.cancel),
                                    fallback.settings, self.zone, external=self.external)
                    try:
                        result = engine.run(request, conversation, emit, self.cancel)
                    except ModelToolError as error:
                        raise ValueError('Both local models could not complete the action. Nothing was saved. ' + str(error)) from error
                    result.setdefault('evidence', {})['model_fallback'] = self.tool_fallback.title
                result.setdefault('evidence', {})['request_seconds'] = round(time.monotonic()-started, 3)
                emit(type="answer", **result)
            except Exception as error:
                emit(type="error", message=str(error))
            finally:
                finished.set()
                delayed.cancel()
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

    def shutdown(self):
        self.close()
        self.tool_fallback.close()
