"""Bounded, cached local service health checks, independent of the UI loop."""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import requests

from app import docker_services as docker

STATES = frozenset({"running", "starting", "offline", "error", "unknown"})


def safe_text(value, limit=400):
    """Redact common credentials before upstream errors reach the interface."""
    text = str(value or "")
    text = re.sub(r"(?i)(https?://)[^\s/@:]+:[^\s/@]+@", r"\1[redacted]@", text)
    text = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[redacted]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|password|passwd|token|secret|authorization|cookie)\s*[=:]\s*)[^\s&,;]+",
                  r"\1[redacted]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[redacted]", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)[:limit]


def safe_address(value):
    try:
        parts = urlsplit(str(value or ""))
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return ""
        host = parts.hostname
        if ":" in host:
            host = "[" + host + "]"
        if parts.port:
            host += ":" + str(parts.port)
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except ValueError:
        return ""


def _now():
    return datetime.now(timezone.utc).isoformat()


class ServiceMonitor:
    """snapshot() is always fast; checks run in one bounded background sweep.

    Refresh at most every 15s while visible and every 60s when hidden. No
    autonomous retry loop: an absent frontend generates no ongoing polling.
    Callbacks should return {state, detail, address?, error?} without secrets.
    """

    def __init__(self, model_probe=None, library_probe=None, model_url=None,
                 interval=15.0, hidden_interval=60.0, timeout=3.0):
        self.interval = max(5.0, float(interval))
        self.hidden_interval = max(self.interval, float(hidden_interval))
        self.timeout = max(0.2, min(float(timeout), 8.0))
        self.model_probe, self.library_probe, self.model_url = model_probe, library_probe, model_url
        self._lock = threading.RLock()
        self._visible, self._running, self._closed = True, False, False
        self._last_check = -float("inf")
        self._starting = {}
        titles = {"backend": "NexusDesk", "docker": "Docker", "metube": "MeTube",
                  "stirling_pdf": "Stirling PDF", "model": "Local AI", "library": "AI Library"}
        self._states = {key: {"key": key, "title": title, "state": "unknown", "detail": "Not checked yet",
                              "address": "", "error": "", "checked_at": None, "last_success": None,
                              "actions": []} for key, title in titles.items()}
        self._states["backend"].update(state="running", detail="Local NexusDesk backend is responding",
                                        checked_at=_now(), last_success=_now())

    def set_visible(self, visible):
        with self._lock:
            self._visible = bool(visible)

    def snapshot(self):
        self.refresh()
        with self._lock:
            return [dict(item, actions=list(item["actions"])) for item in self._states.values()]

    def refresh(self, force=False):
        with self._lock:
            delay = self.interval if self._visible else self.hidden_interval
            if self._closed or self._running or (not force and time.monotonic() - self._last_check < delay):
                return False
            self._running, self._last_check = True, time.monotonic()
        threading.Thread(target=self._sweep, name="nexusdesk-service-health", daemon=True).start()
        return True

    def mark_starting(self, key):
        if key not in docker.SERVICES and key != "docker":
            raise ValueError("This service cannot be started by Docker controls.")
        with self._lock:
            self._starting[key] = time.monotonic()
            self._states[key].update(state="starting", detail="Starting; the interface remains available", error="", actions=[])

    def mark_error(self, key, error):
        with self._lock:
            self._starting.pop(key, None)
        self._publish(key, {"state": "error", "error": error, "detail": "Startup failed",
                            "actions": ["start"] if key in docker.SERVICES or key == "docker" else []})

    def stop(self):
        with self._lock:
            self._closed = True

    def _publish(self, key, result):
        if isinstance(result, bool):
            result = {"state": "running" if result else "offline"}
        if not isinstance(result, dict):
            result = {"state": "unknown", "detail": "No health information available"}
        state = result.get("state", "unknown")
        if state not in STATES:
            state = "unknown"
        with self._lock:
            starting = self._starting.get(key)
            if starting is not None:
                if state == "running" or time.monotonic() - starting > 600:
                    self._starting.pop(key, None)
                else:
                    return
            item = self._states[key]
            item.update(state=state, detail=safe_text(result.get("detail")),
                        address=safe_address(result.get("address")), error=safe_text(result.get("error")),
                        checked_at=_now(), actions=[x for x in result.get("actions", []) if x in {"start", "restart", "open"}])
            if state == "running":
                item["last_success"] = item["checked_at"]

    def _http(self, address, json_check=False):
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.get(address, timeout=self.timeout, allow_redirects=False, stream=True)
            with response:
                if 200 <= response.status_code < 300:
                    if json_check:
                        content = next(response.iter_content(65536), b"")
                        if b"UP" not in content:
                            return {"state": "starting", "detail": "Container is running; application is warming up"}
                    return {"state": "running", "detail": "Application is responding"}
                return {"state": "error", "error": f"Health endpoint returned HTTP {response.status_code}"}
        except requests.Timeout:
            return {"state": "offline", "error": "The service did not respond before the timeout"}
        except requests.RequestException:
            return {"state": "offline", "error": "Could not connect to this local service"}
        finally:
            session.close()

    def _docker_probe(self):
        try:
            executable = docker.docker_executable()
            podman = Path(executable).name == "podman"
            result = docker.command(executable, "info", "--format", "{{.Host.OS}}" if podman else "{{.OSType}}", timeout=self.timeout, check=False)
            if result.returncode:
                return {"state": "offline", "detail": "Container engine is unavailable", "actions": ["start"]}
            if result.stdout.strip() != "linux":
                return {"state": "error", "error": "The container engine must support Linux containers"}
            return {"state": "running", "detail": "Rootless Podman is ready" if podman else "Docker Linux engine is ready"}
        except (RuntimeError, OSError):
            return {"state": "offline", "detail": "Podman/Docker is unavailable", "actions": ["start"]}

    def _container_probe(self, key, engine):
        if engine["state"] != "running":
            return {"state": "offline", "detail": "Docker is unavailable", "actions": ["start"]}
        try:
            service = docker.SERVICES[key]
            executable = docker.docker_executable()
            name, info = docker.find_existing(executable, service, timeout=self.timeout)
            if not info:
                return {"state": "offline", "detail": "Container has not been created", "actions": ["start"]}
            url = docker.container_url(info, service)
            state = info.get("State", {})
            if state.get("Restarting"):
                return {"state": "starting", "detail": "Container is restarting", "address": url}
            if not state.get("Running"):
                return {"state": "offline", "detail": "Container is stopped; data is retained", "address": url, "actions": ["start"]}
            endpoint = url + ("api/v1/info/status" if key == "stirling_pdf" else "")
            result = self._http(endpoint, json_check=key == "stirling_pdf")
            if result["state"] == "offline":
                result["state"] = "starting" if state.get("Health", {}).get("Status") == "starting" else "error"
            result.update(address=url, actions=["open", "restart"] if result["state"] == "running" else ["restart"])
            return result
        except (RuntimeError, OSError, ValueError):
            return {"state": "error", "error": "Could not inspect the registered container. Check Docker Desktop.", "actions": ["start"]}

    def _callback_probe(self, callback, default):
        try:
            return callback() if callback else default
        except Exception:
            return {"state": "error", "error": "The service status check failed"}

    def _sweep(self):
        try:
            engine = self._docker_probe()
            self._publish("docker", engine)
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="nexusdesk-health") as pool:
                pending = {key: pool.submit(self._container_probe, key, engine) for key in docker.SERVICES}
                model_default = {"state": "unknown", "detail": "No local model health endpoint configured"}
                model_callback = self.model_probe
                if not model_callback and self.model_url:
                    model_callback = lambda: dict(self._http(self.model_url), address=self.model_url)
                pending["model"] = pool.submit(self._callback_probe, model_callback, model_default)
                pending["library"] = pool.submit(self._callback_probe, self.library_probe,
                    {"state": "unknown", "detail": "Library session status is not available"})
                for key, future in pending.items():
                    self._publish(key, future.result())
            self._publish("backend", {"state": "running", "detail": "Local NexusDesk backend is responding"})
        finally:
            with self._lock:
                self._running = False
