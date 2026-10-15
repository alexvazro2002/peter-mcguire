"""Automatic retries keep the full request and never commit abandoned writes."""
import copy
import json
from queue import Queue
from threading import Event
from types import SimpleNamespace

import pytest

from app.assistant.model_fallback import ToolFallback
from app.assistant.service import AssistantService
from app.integrations import ExternalAccess
from library.models import ModelChoice
from test_integrations import stores
from test_mail_review import Gmail, email


SETTINGS = dict(runtime_provider="lmstudio", context_size=16384, max_tokens=512)
REQUEST = "Create an unscheduled task called Call the dentist."


class Model:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.prompts = []

    def complete(self, messages, **kwargs):
        self.prompts.append(copy.deepcopy(messages))
        reply = next(self.replies)
        if callable(reply):
            reply = reply()
        if isinstance(reply, Exception):
            raise reply
        return dict(content=reply if isinstance(reply, str) else json.dumps(reply))


def runtime(model, ensure=lambda: None):
    return SimpleNamespace(client=model, settings=dict(SETTINGS), ensure=ensure)


def task_reply(title="Call the dentist"):
    return dict(tool_calls=[dict(name="create_task", arguments=dict(title=title, date="", time=""))])


def terminal(service):
    events = []
    while True:
        event = service.events.get(timeout=3)
        events.append(event)
        if event["type"] in {"answer", "error"}:
            return event, events


class RecordingQueue(Queue):
    def __init__(self):
        super().__init__()
        self.recorded = []

    def put(self, event, *args, **kwargs):
        self.recorded.append(copy.deepcopy(event))
        super().put(event, *args, **kwargs)


def service_for(tmp_path, model, *, ensure=lambda: None, external=None):
    vault, calendar, _ = stores(tmp_path)
    return AssistantService(vault, calendar, runtime(model, ensure), tmp_path / "conversation.json", external=external)


def fallback_for(service, model, before=lambda: None):
    calls = []
    def ensure(cancel):
        before()
        calls.append(cancel)
        return runtime(model)
    service.tool_fallback = SimpleNamespace(ensure=ensure, title="Installed instruction model", close=lambda: None)
    return calls


def test_service_replays_full_request_and_conversation_after_primary_only_promises(tmp_path):
    primary = Model(*[dict(answer="I'll create that task.", apply_changes=True)] * 3)
    service = service_for(tmp_path, primary)
    prior = [dict(role="user", text="The appointment is for the dentist, not the doctor."),
             dict(role="assistant", text="Understood, the dentist.")]
    service.messages = copy.deepcopy(prior)
    alternative = Model(task_reply(), dict(answer="Done.", apply_changes=True))
    fallback_calls = fallback_for(service, alternative)
    service.send(REQUEST)
    answer, events = terminal(service)
    assert answer["type"] == "answer" and answer["operation_id"]
    assert fallback_calls == [service.cancel] and len(primary.prompts) == 3
    assert alternative.prompts[0][-1] == dict(role="user", content=REQUEST)
    assert any(m["role"] == "user" and m["content"] == prior[0]["text"] for m in alternative.prompts[0])
    assert any(prior[1]["text"] in m["content"] for m in alternative.prompts[0])
    assert len(service.calendar.entries()) == 1 and service.calendar.entries()[0]["title"] == "Call the dentist"
    record = json.loads(service.history._path(answer["operation_id"]).read_text())
    assert record["request"] == REQUEST and record["status"] == "committed"
    assert len(service.history.recent()) == 1
    assert answer["evidence"]["model_fallback"] == "Installed instruction model"
    assert [e["type"] for e in events].count("answer") == 1
    assert any(e["type"] == "acknowledgement" and "another way" in e["text"] for e in events)
    # Progress speech is not a completed assistant turn or persisted user data.
    persisted = json.loads(service.path.read_text())
    assert persisted == service.messages and len(persisted) == 3
    assert all("Give me a sec" not in row["text"] for row in persisted)


def test_primary_partial_staged_write_is_discarded_before_fallback_and_never_duplicated(tmp_path):
    primary = Model(task_reply("Abandoned partial task"), "invalid JSON", "invalid JSON", "invalid JSON")
    service = service_for(tmp_path, primary)
    alternative = Model(task_reply(), dict(answer="Done.", apply_changes=True))
    def untouched_before_retry():
        assert service.calendar.entries() == [] and service.history.recent() == []
    calls = fallback_for(service, alternative, before=untouched_before_retry)
    service.send(REQUEST)
    answer, _ = terminal(service)
    assert answer["type"] == "answer" and len(calls) == 1
    assert [t["title"] for t in service.calendar.entries()] == ["Call the dentist"]
    assert len(service.history.recent()) == 1
    before_image = json.loads(service.history._path(answer["operation_id"]).read_text())["files"][0]["before"]
    assert json.loads(before_image)["entries"] == []


def test_storage_commit_failure_does_not_switch_models_or_retry_a_write(tmp_path, monkeypatch):
    service = service_for(tmp_path, Model(task_reply(), dict(answer="Done.", apply_changes=True)))
    calls = fallback_for(service, Model())
    def fail_commit(*args, **kwargs):
        raise OSError("Storage is full")
    monkeypatch.setattr(service.history, "commit", fail_commit)
    service.send(REQUEST)
    result, _ = terminal(service)
    assert result["type"] == "error" and "Storage is full" in result["message"]
    assert calls == [] and service.calendar.entries() == []


def test_cancellation_on_protocol_failure_prevents_fallback_and_writes(tmp_path):
    service = service_for(tmp_path, Model())
    def cancel_and_fail():
        service.cancel.set()
        return "invalid JSON"
    primary = Model("invalid JSON", "invalid JSON", cancel_and_fail)
    service.runtime.client = primary
    calls = fallback_for(service, Model())
    service.send(REQUEST)
    result, events = terminal(service)
    assert result["type"] == "error" and "cancelled" in result["message"].lower()
    assert len(primary.prompts) == 3 and calls == [] and service.calendar.entries() == []
    assert not any("another way" in e.get("text", "") for e in events)


def test_write_acknowledgement_is_queued_before_model_work_and_does_not_end_busy_state(tmp_path):
    service = service_for(tmp_path, Model(task_reply(), dict(answer="Done.", apply_changes=True)))
    service.events = RecordingQueue()
    seen = []
    def connecting():
        queued = service.events.recorded
        assert queued[0] == dict(type="acknowledgement", text="Give me a sec.")
        assert service.busy and len(service.messages) == 1 and service.messages[0]["role"] == "user"
        seen.append("connecting")
    service.runtime.ensure = connecting
    service.send(REQUEST)
    result, events = terminal(service)
    assert result["type"] == "answer" and seen == ["connecting"]
    assert events[0]["type"] == "acknowledgement"


def test_large_email_review_acknowledges_before_fetch_and_report_preparation(tmp_path, monkeypatch):
    service = service_for(tmp_path, Model(), external=ExternalAccess(gmail=Gmail([email()])))
    service.events = RecordingQueue()
    original = service.mail_flow.review.prepare
    preparations = []
    def prepare(*args, **kwargs):
        queued = service.events.recorded
        assert queued[0]["type"] == "acknowledgement" and "minute" in queued[0]["text"]
        assert service.busy and [r["role"] for r in service.messages] == ["user"]
        preparations.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(service.mail_flow.review, "prepare", prepare)
    service.send("Peter check my emails from Jose Angel from the past two weeks.")
    result, events = terminal(service)
    assert result["type"] == "answer" and preparations == [True]
    assert events[0]["type"] == "acknowledgement" and "prepared" in result["text"]
    assert len(service.messages) == 1 and service.messages[0]["role"] == "user"


class FakeRuntime:
    def __init__(self, settings):
        self.settings = settings
        self.cancel = None
        self.ensures = 0
        self.closed = False

    def ensure(self):
        self.ensures += 1

    def close(self):
        self.closed = True


def gguf(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"test local model placeholder")
    return str(path)


def registry(monkeypatch, rows):
    calls = []
    def run(self, args, deadline, cancel=None):
        calls.append((args, cancel))
        return json.dumps(rows)
    monkeypatch.setattr("library.lmstudio.LMStudio.run", run)
    return calls


def test_fallback_uses_another_existing_registered_local_chat_model_and_conservative_settings(tmp_path, monkeypatch):
    primary = gguf(tmp_path, "primary.gguf")
    qwen = gguf(tmp_path, "qwen.gguf")
    paddle = gguf(tmp_path, "paddle.gguf")
    choices = [ModelChoice("qwen9b", "Primary model", primary),
               ModelChoice("qwen", "Missing Qwen", str(tmp_path / "missing.gguf")),
               ModelChoice("paddle", "OCR model", paddle, chat=False),
               ModelChoice("qwen", "Compact instruction model", qwen)]
    calls = registry(monkeypatch, [dict(type="llm", modelKey="provider/qwen3.5-4b", trainedForToolUse=True),
                                  dict(type="llm", modelKey="provider/qwen3.5-9b-uncensored-hauhaucs-aggressive")])
    original = dict(SETTINGS, model_id="qwen9b", model_path=primary, api_model="primary-api",
                    lmstudio_model_key="primary-model", context_size=8192, enable_thinking=True)
    fallback = ToolFallback(original, factory=FakeRuntime, choices=lambda _: choices)
    cancel = Event()
    result = fallback.ensure(cancel)
    assert result.settings["model_path"] == qwen and result.settings["model_id"] == "qwen"
    assert result.settings["temperature"] == .1 and result.settings["enable_thinking"] is False
    assert result.settings["context_size"] >= 16384 and result.settings["api_model"] == ""
    assert result.cancel is cancel and result.ensures == 1
    assert fallback.title == "Compact instruction model" and original["api_model"] == "primary-api"
    assert calls == [(["ls", "--json"], cancel)]
    assert fallback.ensure(cancel) is result and result.ensures == 2
    fallback.close()
    assert result.closed


@pytest.mark.parametrize("row", [
    dict(type="llm", modelKey="unrelated/model"),
    dict(type="llm", modelKey="provider/qwen3.5-4b", deviceIdentifier="remote-device"),
    dict(type="embedding", modelKey="provider/qwen3.5-4b"),
    dict(type="llm", modelKey="provider/qwen3.5-4b", trainedForToolUse=False),
])
def test_local_file_alone_does_not_allow_unregistered_remote_or_non_tool_model(tmp_path, monkeypatch, row):
    path = gguf(tmp_path, "qwen.gguf")
    registry(monkeypatch, [row])
    fallback = ToolFallback(dict(SETTINGS, model_path=str(tmp_path / "primary.gguf")),
                            factory=lambda _: pytest.fail("Unsuitable model must not start"),
                            choices=lambda _: [ModelChoice("qwen", "Qwen", path)])
    with pytest.raises(ValueError, match="alternative"):
        fallback.ensure(Event())


def test_cancelled_fallback_does_not_discover_or_load_any_models(monkeypatch):
    monkeypatch.setattr("library.lmstudio.LMStudio.run", lambda *args: pytest.fail("Cancelled request must stop"))
    fallback = ToolFallback(SETTINGS, factory=lambda _: pytest.fail("Cancelled request must stop"))
    cancel = Event(); cancel.set()
    with pytest.raises(ValueError, match="cancelled"):
        fallback.ensure(cancel)


def test_cancellation_during_model_load_is_checked_again_before_retry(tmp_path, monkeypatch):
    path = gguf(tmp_path, "qwen.gguf")
    registry(monkeypatch, [dict(type="llm", modelKey="provider/qwen3.5-4b")])
    class CancelDuringLoad(FakeRuntime):
        def ensure(self):
            self.cancel.set()
    fallback = ToolFallback(SETTINGS, factory=CancelDuringLoad,
                            choices=lambda _: [ModelChoice("qwen", "Qwen", path)])
    cancel = Event()
    with pytest.raises(ValueError, match="cancelled"):
        fallback.ensure(cancel)
    assert cancel.is_set()
