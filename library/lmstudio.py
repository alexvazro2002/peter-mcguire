"""Use installed LM Studio and existing local weights; never download or unload models."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def discover_cli(settings):
    configured = settings.get("lmstudio_cli", "")
    candidates = [configured, shutil.which("lms"), Path.home() / ".lmstudio/bin/lms",
                  Path.home() / ".local/bin/lms"]
    for item in candidates:
        if item and Path(item).is_file() and (os.name == "nt" or os.access(item, os.X_OK)):
            return str(item)
    raise ValueError("LM Studio CLI was not found. Install LM Studio's lms CLI or select a llama-server in Settings.")


def parse_models(output):
    # Some CLI builds print a daemon wakeup before their JSON.
    decoder = json.JSONDecoder()
    for offset, char in enumerate(output):
        if char != "[":
            continue
        try:
            result, end = decoder.raw_decode(output[offset:])
            if not output[offset + end:].strip() and isinstance(result, list) and all(isinstance(m, dict) for m in result):
                return result
        except ValueError:
            continue
    raise ValueError("LM Studio returned no valid model list. See logs/model-server.log.")


def matches(model, settings):
    if model.get("deviceIdentifier") or model.get("type") != "llm":
        return False  # Never select an LM Link remote device or an embedding model.
    configured = settings.get("lmstudio_model_key", "")
    keys = [str(model.get(k, "")) for k in ("modelKey", "identifier", "selectedVariant", "indexedModelIdentifier")]
    keys.extend(str(k) for k in model.get("variants", []))
    if configured:
        return configured.casefold() in {k.casefold() for k in keys}
    profile = settings.get("model_id", "gemma")
    marker = {"gemma": "gemma-4-e4b", "qwen": "qwen3.5-4b", "qwen9b": "qwen3.5-9b-uncensored-hauhaucs-aggressive"}.get(profile)
    if marker:
        return any(marker in k.casefold() for k in keys)
    path = settings.get("model_path", "")
    return bool(path) and any(Path(path).stem.casefold() in k.casefold() for k in keys)


class LMStudio:
    def __init__(self, settings, log=lambda _: None):
        self.settings, self.log, self.command_process = settings, log, None

    def run(self, args, deadline, cancel=None):
        if cancel and cancel.is_set():
            raise ValueError("Model startup cancelled.")
        from app.config import DATA_DIR
        logs = DATA_DIR / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen([discover_cli(self.settings), *args], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
        self.command_process = process
        output = ""
        try:
            while True:
                if cancel and cancel.is_set():
                    raise ValueError("Model startup cancelled.")
                if time.monotonic() >= deadline:
                    raise TimeoutError("LM Studio startup timed out. See logs/model-server.log.")
                try:
                    output, _ = process.communicate(timeout=min(.5, max(.01, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    pass
            with (logs / "model-server.log").open("a", encoding="utf-8") as handle:
                handle.write("lms " + " ".join(args) + "\n" + output[-16000:] + "\n")
            if process.returncode:
                raise RuntimeError("LM Studio command failed: lms " + " ".join(args[:2]) + ". See logs/model-server.log.")
            return output
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
            self.command_process = None

    def resolve_endpoint(self, deadline, cancel=None):
        from library.server import validate_url
        _, port = validate_url(self.settings["server_url"])
        status_output = self.run(["server", "status", "--json"], deadline, cancel)
        try:
            status = json.loads(status_output[status_output.index("{"):])
        except (ValueError, AttributeError):
            raise ValueError("LM Studio returned invalid server status. See logs/model-server.log.")
        if status.get("running") and isinstance(status.get("port"), int) and 0 < status["port"] < 65536:
            port = status["port"]
            self.settings["server_url"] = f"http://127.0.0.1:{port}/v1"
        return port

    def ensure(self, healthy, cancel=None):
        deadline = time.monotonic() + max(10, float(self.settings.get("startup_timeout", 180)))
        port = self.resolve_endpoint(deadline, cancel)
        loaded = parse_models(self.run(["ps", "--json"], deadline, cancel))
        selected = next((m for m in loaded if matches(m, self.settings)), None)
        if selected is None:
            available = parse_models(self.run(["ls", "--json"], deadline, cancel))
            selected = next((m for m in available if matches(m, self.settings)), None)
            if selected is None:
                raise ValueError("Your selected model is not installed in LM Studio. Peter needs an existing local Gemma 4 E4B or the model chosen in Settings; no weights were downloaded.")
            # lms load accepts modelKey; selectedVariant is metadata used for
            # matching quantization and is not a CLI load key in current builds.
            key = selected.get("modelKey") or selected.get("path")
            if not isinstance(key, str) or not key or key.startswith("-") or len(key) > 1000:
                raise ValueError("LM Studio returned an invalid model key.")
            self.settings["lmstudio_model_key"] = selected.get("selectedVariant") or key
            alias = "peter-" + self.settings.get("model_id", "local")
            args = ["load", key, "--identifier", alias, "--context-length",
                    str(int(self.settings.get("context_size", 8192))), "--yes"]
            # LM Studio's automatic placement is the default. Respect explicit full/CPU offload.
            layers = int(self.settings.get("gpu_layers", 0))
            args += ["--gpu", "max" if layers == -1 else "off"] if layers in {0, -1} else []
            self.log("Loading your existing " + str(selected.get("displayName") or key) + " in LM Studio…")
            self.run(args, deadline, cancel)
            loaded = parse_models(self.run(["ps", "--json"], deadline, cancel))
            selected = next((m for m in loaded if matches(m, self.settings)), None)
            if selected is None:
                raise RuntimeError("LM Studio did not confirm the selected model was loaded.")
        identifier = selected.get("identifier")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("LM Studio did not report an API identifier for the loaded model.")
        self.settings["api_model"] = identifier
        self.settings["lmstudio_model_key"] = selected.get("selectedVariant") or selected.get("modelKey")
        context = selected.get("contextLength")
        if isinstance(context, int) and context > 0:
            self.settings["context_size"] = min(int(self.settings.get("context_size", context)), context)
        if not healthy():
            self.log("Starting the LM Studio local API…")
            self.run(["server", "start", "--port", str(port), "--bind", "127.0.0.1"], deadline, cancel)
        while time.monotonic() < deadline:
            if cancel and cancel.is_set():
                raise ValueError("Model startup cancelled.")
            if healthy():
                import requests
                from library.models import model_matches
                from library.auth import request_options
                response = requests.get(self.settings["server_url"].rstrip("/") + "/models", timeout=3, allow_redirects=False, **request_options(self.settings))
                response.raise_for_status()
                identifiers = [item.get("id", "") for item in response.json().get("data", [])]
                if identifier not in identifiers or not model_matches(self.settings, [identifier]):
                    raise ValueError("LM Studio's API does not advertise the selected loaded model. Check its local server port in Settings.")
                self.log("Peter is connected to " + identifier + ". LM Studio stays running when NexusDesk closes.")
                return
            if cancel:
                cancel.wait(.5)
            else:
                time.sleep(.5)
        raise TimeoutError("LM Studio loaded the model but its local API is not ready. See logs/model-server.log.")
