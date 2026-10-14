"""Portable configuration. The application never depends on its working directory."""
from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", str(BASE_DIR))).expanduser().resolve()
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULTS = {
    "schema_version": 2, "theme_dark": True, "accent_color": "#84D9EF",
    "window_width": 1440, "window_height": 900, "view_mode": "grid",
    "tool_order": "alphabetical", "usage": {}, "notes_order": [],
    "notes_pinned": [], "notes_sort": "custom", "notes_last_used": {},
    "notes_graph_pos": {}, "vault_path": "", "last_note": "",
    "assistant_timezone": "Europe/London",
    "internet": {"enabled": True},
    "gmail": {"client_file": "", "check_on_startup": True, "initial_days": 1},
    "notifications": {"enabled": False, "server_url": "https://ntfy.sh", "topic": "", "include_answer": False},
    "peter": {
        "enabled": True, "autostart": True, "speech_enabled": True, "startup_greeting": True,
        "hands_free": True,
        "greeting": "Systems are ready. At your service.",
        "voice_url": "http://127.0.0.1:3900", "voice_model": "kokoro-onnx", "voice_id": "bm_george",
        "voice_speed": 1.15, "voice_pitch": 0.8, "british_voice_path": "",
        "asr_model": "whisper-1", "record_seconds": 30, "voice_timeout": 120, "voice_language": "en",
        "voice_runtime": "",
    },
    "library": {
        "default_source": "",
        "last_source": "", "ai_python": "",
        "embedding_model": "all-MiniLM-L6-v2", "offline_models": True,
        "model_path": "",
        "model_id": "gemma", "external_model": False, "projector_path": "", "enable_thinking": False,
        "runtime_provider": "lmstudio", "lmstudio_model_key": "", "lmstudio_cli": "", "api_model": "",
        "model_token_file": "",
        "server_executable": "", "server_url": "http://127.0.0.1:1234/v1",
        "context_size": 8192, "gpu_layers": -1, "startup_timeout": 180,
        "max_tokens": 384,
    },
}


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False,
                            prefix=".nexus-", suffix=".tmp") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        tmp = Path(handle.name)
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def load_config(path: Path = CONFIG_PATH) -> dict:
    config = deepcopy(DEFAULTS)
    candidate = path if path.exists() else DATA_DIR / "config.legacy.json"
    if candidate.exists():
        try:
            old = json.loads(candidate.read_text(encoding="utf-8-sig"))
            if not isinstance(old, dict):
                raise ValueError("Configuration must be an object")
            config.update({k: v for k, v in old.items() if k in DEFAULTS and k not in {"library", "notifications", "peter", "internet", "gmail"}})
            if old.get("schema_version") != 2:
                config["accent_color"] = DEFAULTS["accent_color"]
            if isinstance(old.get("library"), dict):
                config["library"].update(old["library"])
            if isinstance(old.get("notifications"), dict):
                config["notifications"].update({k: v for k, v in old["notifications"].items() if k in DEFAULTS["notifications"]})
            if isinstance(old.get("peter"), dict):
                config["peter"].update({k: v for k, v in old["peter"].items() if k in DEFAULTS["peter"]})
            for section in ("internet", "gmail"):
                if isinstance(old.get(section), dict):
                    config[section].update({k: v for k, v in old[section].items() if k in DEFAULTS[section]})
        except (OSError, ValueError) as error:
            # Keep the malformed file untouched; do not silently overwrite it.
            config["config_warning"] = str(error)
    for key in ("usage", "notes_last_used", "notes_graph_pos"):
        if not isinstance(config[key], dict):
            config[key] = {}
    for key in ("notes_order", "notes_pinned"):
        if not isinstance(config[key], list):
            config[key] = []
    if config.get("accent_color") == "#9C8CFF":
        config["accent_color"] = DEFAULTS["accent_color"]
    if os.name != "nt":
        # Imported Windows paths are meaningless on Fedora; retain all portable settings.
        for key in ("model_path", "projector_path", "server_executable", "ai_python", "default_source", "last_source"):
            value = config["library"].get(key, "")
            if isinstance(value, str) and (len(value) > 2 and value[1:3] in {":\\", ":/"} or value.startswith("\\\\")):
                config["library"][key] = ""
        value = config.get("vault_path", "")
        if isinstance(value, str) and len(value) > 2 and value[1:3] in {":\\", ":/"}:
            config["vault_path"] = ""
    if not config["library"].get("model_path") and not config["library"].get("external_model"):
        from library.models import model_choices
        choice = next((c for c in model_choices(config["library"]) if c.key == config["library"].get("model_id") and Path(c.path).is_file()), None)
        if choice:
            config["library"]["model_path"] = choice.path
    return config


def save_config(config: dict, path: Path = CONFIG_PATH) -> None:
    if config.get("config_warning"):
        raise ValueError("config.json is invalid. Repair or rename it before saving settings.")
    atomic_write(path, json.dumps(config, indent=2, ensure_ascii=False) + "\n")


def ai_python(config: dict) -> str:
    configured = config["library"].get("ai_python", "")
    return configured if configured and Path(configured).is_file() else sys.executable
