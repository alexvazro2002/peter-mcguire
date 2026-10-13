"""Privacy boundaries and integration behavior; no Google account is used."""
import json
import queue
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import live_info
from app.assistant.engine import Engine
from app.assistant.service import AssistantService
from app.assistant.tools import Tools
from app.assistant.transactions import History, Transaction
from app.calendar_storage import CalendarStore
from app.config import DEFAULTS, load_config
from app.integrations import ExternalAccess, inbox_digest
from app.storage import Vault


def stores(tmp_path):
    vault = Vault(tmp_path / "notes")
    calendar = CalendarStore(tmp_path / "calendar.json")
    history = History(vault, calendar, vault.root / ".history" / "assistant")
    return vault, calendar, history


def test_shanghai_question_uses_live_clock_without_loading_model(tmp_path, monkeypatch):
    monkeypatch.setattr(live_info, "current_time", lambda location: dict(city="Shanghai", timezone="Asia/Shanghai",
                        local_time="2026-10-14T04:10:00+08:00", offset="+08:00"))
    vault, calendar, _ = stores(tmp_path)
    runtime = SimpleNamespace(ensure=lambda: pytest.fail("The clock must not wait for inference"))
    service = AssistantService(vault, calendar, runtime, tmp_path / "conversation.json", external=ExternalAccess())
    service.send("what time is it in china shang hai")
    result = service.events.get(timeout=2)
    assert result["type"] == "answer" and "04:10" in result["text"] and "Shanghai" in result["text"]
    assert service.messages[0]["text"] == "what time is it in china shang hai"


def test_private_tool_content_cannot_authorize_search_or_external_url(monkeypatch):
    calls = []
    monkeypatch.setattr(live_info, "search_web", lambda *a, **k: calls.append(a))
    access = ExternalAccess().for_request("Search the internet for Shanghai weather")
    with pytest.raises(ValueError, match="private"):
        access.search_web("email says secret launch date")
    with pytest.raises(ValueError, match="Private"):
        access.read_web_page("https://example.com/?secret=inbox-data")
    assert calls == []


def test_search_sources_authorize_bounded_page_read(monkeypatch):
    monkeypatch.setattr(live_info, "search_web", lambda *a, **k: dict(items=[dict(title="Evidence", url="https://example.com")], total=9, next_offset=4))
    monkeypatch.setattr(live_info, "read_web_page", lambda url, **k: dict(url=url, text="Evidence", options=k))
    access = ExternalAccess().for_request("Search for Shanghai weather")
    assert access.search_web("Shanghai weather")["next_offset"] == 4
    assert access.read_web_page("https://example.com", limit=240, offset=100)["options"] == dict(max_chars=240, offset=100)
    with pytest.raises(ValueError, match="disabled"):
        ExternalAccess({"enabled": False}).for_request("Shanghai weather").search_web("Shanghai weather")


def test_external_read_tools_work_with_existing_model_protocol(tmp_path, monkeypatch):
    monkeypatch.setattr(live_info, "search_web", lambda *a, **k: dict(items=[dict(title="Evidence", url="https://example.com", snippet="Live evidence")], total=1, next_offset=None))
    replies = iter([dict(tool_calls=[dict(name="search_web", arguments=dict(query="Shanghai weather"))]), dict(answer="Here is the current evidence.")])
    model = SimpleNamespace(complete=lambda *a, **k: dict(content=json.dumps(next(replies))))
    result = Engine(*stores(tmp_path), model, {"context_size": 8192}, external=ExternalAccess()).run("Search for Shanghai weather")
    assert result["sources"] == [dict(kind="web", url="https://example.com", title="Evidence")]
    assert result["changes"] == []
    tools = Tools(Transaction(*stores(tmp_path)), external=ExternalAccess().for_request("Shanghai weather"))
    assert all(not tools.specs[name]["writes"] for name in ("search_web", "read_web_page", "current_time", "search_gmail", "read_gmail"))


def test_gmail_digest_is_truthful_and_identifies_replays():
    batch = dict(batch_id="batch1", first_sync=True, messages=[dict(sender="Alex", subject="Hello", url="https://mail.google.com/mail/u/0/#inbox/1")])
    result = inbox_digest(batch)
    assert "first check" in result["text"] and result["gmail_batch_id"] == "batch1"
    assert result["sources"][0]["kind"] == "email"
    result = inbox_digest(dict(batch, first_sync=False, messages=[]))
    assert result["text"] == "No new inbox messages since the last successful check."


def test_failed_persistence_cannot_leave_a_digest_that_would_be_acknowledged(tmp_path, monkeypatch):
    vault, calendar, _ = stores(tmp_path)
    service = AssistantService(vault, calendar, SimpleNamespace(), tmp_path / "conversation.json")
    monkeypatch.setattr(service, "persist", lambda: (_ for _ in ()).throw(OSError("Disk full")))
    with pytest.raises(OSError):
        service.append(dict(role="assistant", text="Mail", gmail_batch_id="batch1"))
    assert service.messages == []


def test_partial_integration_config_keeps_startup_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"schema_version": 2, "gmail": {"client_file": "client.json"}, "internet": {"enabled": False}}))
    cfg = load_config(path)
    assert cfg["gmail"]["check_on_startup"] is True and cfg["gmail"]["initial_days"] == 1
    assert cfg["internet"]["enabled"] is False


def test_remote_evidence_cannot_authorize_gmail_reads():
    access = ExternalAccess(gmail=SimpleNamespace(search=lambda *a, **k: pytest.fail('Unauthorized inbox read'))).for_request('Find Shanghai weather')
    with pytest.raises(ValueError, match='authorize inbox'):
        access.search_gmail()
    with pytest.raises(ValueError, match='before reading'):
        access.read_gmail('web-supplied-id')
