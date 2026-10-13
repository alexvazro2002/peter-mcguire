"""Startup cancellation and ownership checks for the Fedora launcher."""
import signal
import threading

import pytest

from scripts import launch_linux, voice_service


class Child:
    pid = 23456
    returncode = None
    def poll(self): return self.returncode
    def send_signal(self, value): self.returncode = -value
    def wait(self, timeout): return self.returncode


def setup_launcher(monkeypatch, tmp_path):
    from app import config
    settings = {"peter": {"enabled": True, "voice_url": "http://127.0.0.1:3911", "voice_timeout": 5}, "library": {}}
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "load_config", lambda: settings)
    monkeypatch.setattr(launch_linux, "ROOT", tmp_path)
    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda kind, callback: handlers.setdefault(kind, callback))
    return settings, handlers


def test_cancel_during_voice_startup_cleans_owned_group_without_opening_ui(monkeypatch, tmp_path):
    _, handlers = setup_launcher(monkeypatch, tmp_path)
    spawned, signals = [], []
    child = Child()
    def spawn(argv, **kwargs):
        spawned.append((argv, kwargs))
        return child
    monkeypatch.setattr(launch_linux.subprocess, "Popen", spawn)
    def health(url):
        if spawned:
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        return False
    monkeypatch.setattr(launch_linux, "healthy", health)
    def kill_group(pid, value):
        signals.append((pid, value))
        child.returncode = -value
    monkeypatch.setattr(voice_service.os, "killpg", kill_group)
    assert launch_linux.main([]) == 130
    assert len(spawned) == 1 and "voice_service.py" in spawned[0][0][1]
    argv, kwargs = spawned[0]
    assert kwargs["start_new_session"] is True
    assert argv[argv.index("--url") + 1] == "http://127.0.0.1:3911"
    assert signals[0] == (child.pid, signal.SIGTERM)


def test_existing_voice_service_is_never_stopped(monkeypatch, tmp_path):
    setup_launcher(monkeypatch, tmp_path)
    monkeypatch.setattr(launch_linux, "healthy", lambda url: True)
    spawned = []
    child = Child()
    child.returncode = 0
    monkeypatch.setattr(launch_linux.subprocess, "Popen", lambda argv, **kwargs: (spawned.append(argv) or child))
    monkeypatch.setattr(voice_service.os, "killpg", lambda *args: pytest.fail("An already exited child or pre-existing service was stopped"))
    assert launch_linux.main([]) == 0
    assert len(spawned) == 1 and spawned[0][1].endswith("main_flet.py")


def test_disabled_autostart_opens_ui_without_starting_voice(monkeypatch, tmp_path):
    settings, _ = setup_launcher(monkeypatch, tmp_path)
    settings['peter']['autostart'] = False
    monkeypatch.setattr(launch_linux, 'healthy', lambda url: False)
    spawned = []
    child = Child()
    child.returncode = 0
    monkeypatch.setattr(launch_linux.subprocess, 'Popen', lambda argv, **kwargs: (spawned.append(argv) or child))
    assert launch_linux.main([]) == 0
    assert len(spawned) == 1 and spawned[0][1].endswith('main_flet.py')


def test_voice_signal_handler_exists_before_readiness(monkeypatch, tmp_path):
    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda kind, callback: handlers.setdefault(kind, callback))
    def start(*args, cancel=None, **kwargs):
        assert signal.SIGTERM in handlers
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert cancel.is_set()
        raise voice_service.StartupCancelled()
    monkeypatch.setattr(voice_service, "start_service", start)
    assert voice_service.main(["--runtime", str(tmp_path)]) == 130


def test_service_cancellation_after_spawn_stops_only_owned_child(monkeypatch, tmp_path):
    cancel, child = threading.Event(), Child()
    health_calls = []
    def health(*args):
        health_calls.append(args)
        if len(health_calls) == 2:
            cancel.set()
        return None
    monkeypatch.setattr(voice_service, "healthy", health)
    monkeypatch.setattr(voice_service, "runtime_command", lambda *args: (tmp_path, ["python", "backend"]))
    monkeypatch.setattr(voice_service.subprocess, "Popen", lambda *args, **kwargs: child)
    with pytest.raises(voice_service.StartupCancelled):
        voice_service.start_service(tmp_path, root=tmp_path, cancel=cancel)
    assert child.returncode == -signal.SIGTERM


def test_cancelled_service_does_not_spawn_or_adopt_existing_backend(monkeypatch, tmp_path):
    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(voice_service.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Cancelled startup spawned a child"))
    monkeypatch.setattr(voice_service, "healthy", lambda *args: pytest.fail("Cancelled startup probed a service"))
    with pytest.raises(voice_service.StartupCancelled):
        voice_service.start_service(tmp_path, cancel=cancelled)
