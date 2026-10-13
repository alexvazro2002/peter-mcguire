import threading
import time
from types import SimpleNamespace

import pytest

from app import docker_services as docker
from app.service_status import ServiceMonitor, safe_address, safe_text


def finish(monitor):
    deadline = time.monotonic() + 4
    while monitor._running and time.monotonic() < deadline:
        time.sleep(.01)
    assert not monitor._running
    return {item["key"]: item for item in monitor.snapshot()}


def test_nonblocking_sweep_throttles_and_preserves_last_success(monkeypatch):
    monitor = ServiceMonitor(model_probe=lambda: {"state": "running"}, library_probe=lambda: {"state": "offline"})
    gate = threading.Event()
    calls = []

    def engine():
        calls.append(1)
        gate.wait(2)
        return {"state": "running"}

    monkeypatch.setattr(monitor, "_docker_probe", engine)
    monkeypatch.setattr(monitor, "_container_probe", lambda key, engine: {"state": "running"})
    began = time.monotonic()
    assert monitor.snapshot()[0]["state"] == "running"
    assert time.monotonic() - began < .2
    for _ in range(20):
        monitor.snapshot()
    gate.set()
    result = finish(monitor)
    assert len(calls) == 1 and result["metube"]["last_success"]
    success = result["metube"]["last_success"]
    monitor._publish("metube", {"state": "offline", "error": "connection refused"})
    assert finish(monitor)["metube"]["last_success"] == success
    monitor.set_visible(False)
    monitor._last_check = time.monotonic() - 20
    assert monitor.refresh() is False
    monitor.stop()
    assert monitor.refresh(force=True) is False


def test_starting_error_unknown_states_and_redaction():
    monitor = ServiceMonitor()
    monitor.mark_starting("metube")
    monitor._publish("metube", {"state": "offline"})
    assert monitor._states["metube"]["state"] == "starting"
    monitor.mark_error("metube", "API token=secret-token failed")
    item = monitor._states["metube"]
    assert item["state"] == "error" and "secret-token" not in item["error"]
    monitor._publish("model", {"state": "mystery", "address": "http://user:secret@127.0.0.1:8081/health?token=bad"})
    assert monitor._states["model"]["state"] == "unknown"
    assert monitor._states["model"]["address"] == "http://127.0.0.1:8081/health"
    with pytest.raises(ValueError):
        monitor.mark_starting("anything")


def test_container_online_offline_starting_and_error(monkeypatch):
    monitor = ServiceMonitor()
    monkeypatch.setattr(docker, "docker_executable", lambda: "docker")
    info = {"State": {"Running": False}, "HostConfig": {"PortBindings": {"8081/tcp": [{"HostPort": "8082"}]}}}
    monkeypatch.setattr(docker, "find_existing", lambda *args, **kwargs: ("metube", info))
    engine = {"state": "running"}
    assert monitor._container_probe("metube", engine)["state"] == "offline"
    info["State"].update(Running=True, Restarting=True)
    assert monitor._container_probe("metube", engine)["state"] == "starting"
    info["State"]["Restarting"] = False
    monkeypatch.setattr(monitor, "_http", lambda *args, **kwargs: {"state": "running"})
    assert monitor._container_probe("metube", engine)["actions"] == ["open", "restart"]
    monkeypatch.setattr(monitor, "_http", lambda *args, **kwargs: {"state": "offline", "error": "No response"})
    assert monitor._container_probe("metube", engine)["state"] == "error"
    info["State"]["Health"] = {"Status": "starting"}
    assert monitor._container_probe("metube", engine)["state"] == "starting"
    assert monitor._container_probe("metube", {"state": "offline"})["state"] == "offline"


def test_docker_engine_status_and_model_library_callbacks(monkeypatch):
    monitor = ServiceMonitor()
    monkeypatch.setattr(docker, "docker_executable", lambda: "docker")
    monkeypatch.setattr(docker, "command", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="linux"))
    assert monitor._docker_probe()["state"] == "running"
    monkeypatch.setattr(docker, "command", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""))
    assert monitor._docker_probe()["state"] == "offline"
    assert monitor._callback_probe(lambda: True, {}) is True

    def failing():
        raise RuntimeError("password=do-not-show")

    assert monitor._callback_probe(failing, {}) == {"state": "error", "error": "The service status check failed"}


def test_safe_error_text_does_not_expose_credentials():
    raw = "http://user:secret@localhost/ password=private api_key=topsecret Bearer abc.def.ghi sk-abcdefghijklmnop"
    safe = safe_text(raw)
    for value in ("secret@", "private", "topsecret", "abc.def.ghi", "sk-abcdefghijklmnop"):
        assert value not in safe
    assert safe_address("file:///etc/passwd") == ""
