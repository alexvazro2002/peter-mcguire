import json
import queue
import threading
from types import SimpleNamespace

import pytest

from app.assistant.service import AssistantService
from app.assistant.transactions import History
from app.calendar_storage import CalendarStore
from app.storage import Vault
from library.chat import ChatClient
from library.runtime import ModelRuntime


def test_shared_transport_retries_only_unsupported_json_mode(monkeypatch):
    import requests
    captured = []
    class Response:
        def __init__(self, status): self.status_code = status
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": '<think>private</think>{"answer":"Hello"}'}}]}
    def post(url, **kwargs):
        captured.append((url, dict(kwargs["json"])))
        return Response(400 if len(captured) == 1 else 200)
    monkeypatch.setattr(requests, "post", post)
    client = ChatClient({"server_url": "http://127.0.0.1:8081/v1", "max_tokens": 1800})
    result = client.complete([{"role": "user", "content": "Hello"}], structured=True)
    assert result["content"] == '{"answer":"Hello"}'
    assert "response_format" in captured[0][1] and "response_format" not in captured[1][1]
    assert captured[0][0] == captured[1][0] == "http://127.0.0.1:8081/v1/chat/completions"


def test_invalid_chat_response_is_reported(monkeypatch):
    import requests
    response = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"bad": "response"})
    monkeypatch.setattr(requests, "post", lambda *a, **k: response)
    with pytest.raises(ValueError, match="invalid chat response"):
        ChatClient({"server_url": "http://localhost:8081/v1"}).complete([])


def test_chat_budget_override_preserves_truncation_and_usage(monkeypatch):
    import requests
    captured = []
    body = {"choices": [{"message": {"content": '{"answer":"unfinished'}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 700, "completion_tokens": 384}}
    response = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: body)
    monkeypatch.setattr(requests, "post", lambda *a, **kw: captured.append(kw["json"]) or response)
    client = ChatClient({"server_url": "http://localhost:8081/v1", "max_tokens": 1800})
    result = client.complete([], structured=True, max_tokens=384)
    assert captured[0]["max_tokens"] == 384
    assert result["finish_reason"] == "length" and client.last_usage == body["usage"]


def test_lmstudio_fast_mode_disables_reasoning_with_specific_fallback(monkeypatch):
    import requests
    captured = []
    class Response:
        def __init__(self, rejected=False): self.status_code = 400 if rejected else 200
        def raise_for_status(self): pass
        def json(self):
            if self.status_code == 400:
                return {"error": {"message": "Unsupported parameter: reasoning_effort"}}
            return {"choices": [{"message": {"content": '{"answer":"Hello"}'}}]}
    def post(*args, **kwargs):
        captured.append(dict(kwargs["json"]))
        return Response(rejected=len(captured) == 1)
    monkeypatch.setattr(requests, "post", post)
    client = ChatClient({"server_url": "http://localhost:8081/v1", "api_model": "test",
                         "runtime_provider": "lmstudio", "enable_thinking": False})
    client.complete([], structured=True)
    assert captured[0]["reasoning_effort"] == "none"
    assert "reasoning_effort" not in captured[1]
    assert captured[0]["response_format"] == captured[1]["response_format"]
    assert captured[1]["response_format"]["type"] == "json_schema"
    assert captured[1]["chat_template_kwargs"] == {"enable_thinking": False}


def test_model_runtime_serializes_startup_and_closes_owned_server(monkeypatch):
    from library import runtime
    calls, active = [], threading.Event()
    class Server:
        def __init__(self, *a): self.process = None
        def ensure(self, cancel):
            assert not active.is_set()
            active.set()
            calls.append("ensure")
            active.clear()
        def close(self): calls.append("close")
    monkeypatch.setattr(runtime, "ModelServer", Server)
    host = ModelRuntime({"server_url": "http://127.0.0.1:8081/v1"})
    threads = [threading.Thread(target=host.ensure) for _ in range(4)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    host.close(wait=True)
    assert calls == ["ensure"] * 4 + ["close"]
    with pytest.raises(ValueError, match="closed"):
        host.ensure()


def test_conversation_persistence_readonly_background_request(tmp_path):
    vault, calendar = Vault(tmp_path / "notes"), CalendarStore(tmp_path / "calendar.json")
    vault.create("Running", "Run with Alex on Friday.")
    ready = threading.Event()
    class Client:
        calls = 0
        def complete(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                result = {"tool_calls": [{"name": "search_notes", "arguments": {"query": "Alex"}}]}
            else:
                result = {"answer": "You planned to run with Alex on Friday in Running.", "apply_changes": True}
                ready.set()
            return {"content": json.dumps(result)}
    runtime = SimpleNamespace(ensure=lambda: None, client=Client(), settings={"context_size": 16384, "max_tokens": 1800})
    path = tmp_path / "chat.json"
    service = AssistantService(vault, calendar, runtime, path)
    service.send("What did I plan with Alex?")
    with pytest.raises(ValueError, match="current"):
        service.send("Another message")
    assert ready.wait(5)
    while True:
        event = service.events.get(timeout=5)
        if event["type"] == "answer": break
    assert "Alex" in event["text"] and not event["operation_id"]
    service.busy = False
    service.append(dict(role="assistant", text=event["text"], sources=event["sources"]))
    reloaded = AssistantService(vault, calendar, runtime, path)
    assert len(reloaded.messages) == 2
    assert reloaded.messages[-1]["sources"] == [{"kind": "note", "name": "Running"}]
    reloaded.clear()
    assert json.loads(path.read_text()) == []
    assert vault.read("Running") == "Run with Alex on Friday."


def test_invalid_conversation_file_is_preserved(tmp_path):
    path = tmp_path / "chat.json"
    path.write_text("not JSON")
    service = AssistantService(Vault(tmp_path / "notes"), CalendarStore(tmp_path / "calendar.json"), None, path)
    assert service.warning
    with pytest.raises(ValueError):
        service.send("Hello")
    assert path.read_text() == "not JSON"


def test_external_model_server_is_reused_without_becoming_owned(monkeypatch):
    from library.server import ModelServer
    host = ModelRuntime({"server_url": "http://127.0.0.1:8081/v1"})
    monkeypatch.setattr(ModelServer, "healthy", lambda self: True)
    host.ensure()
    assert host.server.process is None
    host.close(wait=True)
    assert host.server.process is None


def test_remote_model_endpoint_is_rejected_before_request(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("No remote request should be made"))
    client = ChatClient({"server_url": "https://example.com/v1"})
    with pytest.raises(ValueError, match="local"):
        client.complete([])


def test_sampling_settings_reach_local_model(monkeypatch):
    import requests
    captured=[]
    response = SimpleNamespace(status_code=200, raise_for_status=lambda: None,
        json=lambda: {'choices':[{'message':{'content':'{"answer":"Ready"}'}}]})
    monkeypatch.setattr(requests,'post',lambda *a, **kw: captured.append(kw['json']) or response)
    ChatClient(dict(server_url='http://localhost:8081/v1', temperature=.35, top_p=.8, top_k=20, min_p=0)).complete([], structured=True)
    assert {k:captured[0][k] for k in ('temperature','top_p','top_k','min_p')} == dict(temperature=.35,top_p=.8,top_k=20,min_p=0)


@pytest.mark.parametrize('name,value', [('temperature',float('nan')),('temperature',True),('top_k',3.5),('top_p',0),('min_p',2)])
def test_invalid_sampling_settings_fail_before_request(name,value,monkeypatch):
    import requests
    monkeypatch.setattr(requests,'post',lambda *a, **kw: pytest.fail('Invalid sampling parameters must not reach inference'))
    with pytest.raises(ValueError,match='sampling setting'):
        ChatClient(dict(server_url='http://localhost:8081/v1',**{name:value})).complete([])
