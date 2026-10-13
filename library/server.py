"""Reuse a local OpenAI-compatible server or manage a single owned llama.cpp process."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse
from library.auth import check_auth, request_options


def validate_url(value: str) -> tuple[str, int]:
    url = urlparse(value)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("AI Library accepts local HTTP servers only (localhost or 127.0.0.1).")
    if url.username or url.password or url.query or url.fragment or url.path.rstrip("/") != "/v1":
        raise ValueError("Use a local base URL such as http://127.0.0.1:8081/v1.")
    return url.hostname, url.port or 80


def discover_executable(settings: dict) -> Path:
    configured = settings.get("server_executable", "").strip()
    if configured:
        if not Path(configured).is_file():
            raise ValueError(f"llama-server executable not found: {configured}")
        return Path(configured)
    found = shutil.which("llama-server") or shutil.which("llama-server.exe")
    if found:
        return Path(found)
    backends = Path.home() / ".lmstudio" / "extensions" / "backends"
    candidates = sorted([*backends.glob("**/llama-server"), *backends.glob("**/llama-server.exe")],
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise ValueError("No llama-server executable found. Set its path in Settings, or start LM Studio "
                         "with your existing model and local server on port 8081.")
    return candidates[0]


def discover_model(settings: dict) -> Path:
    configured = settings.get("model_path", "").strip()
    if settings.get("model_id") and configured and not Path(configured).is_file():
        raise ValueError("Selected model file was not found: " + configured)
    candidates = [Path(configured)] if configured else []
    from library.models import model_choices
    candidates += [Path(choice.path) for choice in model_choices(settings) if choice.chat and choice.path]
    for model in candidates:
        if model.is_file() and "mmproj" not in model.name.casefold():
            return model
    raise ValueError("Model was not found. Set the path to your existing GGUF in Settings.")


class ModelServer:
    def __init__(self, settings: dict, log=print):
        self.settings, self.log, self.process = settings, log, None
        self.log_handle = None

    def healthy(self) -> bool:
        import requests
        try:
            result = requests.get(self.settings["server_url"].rstrip("/") + "/models", timeout=2, allow_redirects=False, **request_options(self.settings))
            check_auth(result)
            return result.ok and bool(result.json().get("data"))
        except (requests.RequestException, ValueError, AttributeError):
            return False

    def ensure(self, cancel=None) -> None:
        host, port = validate_url(self.settings["server_url"])
        if self.settings.get("runtime_provider") == "lmstudio" and not self.settings.get("external_model"):
            from library.lmstudio import LMStudio
            LMStudio(self.settings, self.log).ensure(self.healthy, cancel)
            return
        if self.healthy():
            if self.settings.get("model_id") and not self.settings.get("external_model"):
                import requests
                from library.models import model_matches
                result = requests.get(self.settings["server_url"].rstrip("/") + "/models", timeout=5, allow_redirects=False, **request_options(self.settings))
                result.raise_for_status()
                identifiers = [item.get("id", "") for item in result.json().get("data", [])]
                if not model_matches(self.settings, identifiers):
                    raise ValueError("A different model is already running at this local server. Load the selected model in LM Studio, "
                                     "stop that external server, or select 'Current local server / LM Studio'.")
                self.settings["api_model"] = next(value for value in identifiers if model_matches(self.settings, [value]))
            self.log("Reusing the existing local model server. It will not be stopped by this app.")
            return
        if self.settings.get("external_model"):
            raise ValueError("No model is running at the configured local server. Start it in LM Studio or select an existing GGUF.")
        executable, model = discover_executable(self.settings), discover_model(self.settings)
        env = os.environ.copy()
        vendor = executable.parent.parent / "vendor"
        dll_dirs = [str(executable.parent)] + [str(p) for p in vendor.glob("*") if p.is_dir()]
        env["PATH"] = os.pathsep.join(dll_dirs + [env.get("PATH", "")])
        from app.config import DATA_DIR
        logs = DATA_DIR / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        self.log_handle = (logs / "model-server.log").open("a", encoding="utf-8")
        command = [str(executable), "-m", str(model), "--host", host, "--port", str(port),
                   "-c", str(int(self.settings.get("context_size", 16384))),
                   "-ngl", str(int(self.settings.get("gpu_layers", 0)))]
        self.log("Starting " + model.name + " locally. GPU layers can be set in Settings.")
        self.process = subprocess.Popen(command, env=env, stdout=self.log_handle, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = time.monotonic() + float(self.settings.get("startup_timeout", 180))
        while time.monotonic() < deadline:
            if cancel and cancel.is_set():
                raise ValueError("Model startup cancelled.")
            if self.process.poll() is not None:
                raise RuntimeError("Model server exited. See logs/model-server.log (model/backend/DLL compatibility).")
            if self.healthy():
                import requests
                response = requests.get(self.settings["server_url"].rstrip("/") + "/models", timeout=3, allow_redirects=False, **request_options(self.settings))
                identifiers = [item.get("id", "") for item in response.json().get("data", [])]
                from library.models import model_matches
                selected = next((value for value in identifiers if model_matches(self.settings, [value])), None)
                if self.settings.get("model_id") and not selected:
                    raise ValueError("The launched local server did not advertise the selected model.")
                self.settings["api_model"] = selected or (identifiers[0] if identifiers else "")
                self.log("Local model server is ready.")
                return
            time.sleep(0.5)
        raise TimeoutError("Model startup timed out. Check logs/model-server.log or start LM Studio manually.")

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.log_handle:
            self.log_handle.close()
