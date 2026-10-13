"""Local API authentication comes only from the process environment."""
import os
from pathlib import Path


def request_options(settings=None):
    token = os.environ.get("NEXUS_MODEL_TOKEN", "").strip()
    if not token:
        configured = os.environ.get("NEXUS_MODEL_TOKEN_FILE", "") or (settings or {}).get("model_token_file", "")
        path = Path(configured).expanduser() if configured else Path.home() / ".config/lmstudio-agent/api-token"
        if configured or path.is_file():
            try:
                token = path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError) as error:
                raise ValueError("The local model token file could not be read. Check NEXUS_MODEL_TOKEN_FILE or Settings.") from error
    if not token:
        return {}
    if len(token) > 8192 or any(char in token for char in "\r\n\x00"):
        raise ValueError("NEXUS_MODEL_TOKEN contains invalid characters.")
    return {"headers": {"Authorization": "Bearer " + token}}


def check_auth(response):
    if getattr(response, "status_code", 200) in {401, 403}:
        raise RuntimeError("The local model API requires authentication. Set NEXUS_MODEL_TOKEN or NEXUS_MODEL_TOKEN_FILE to an authorized LM Studio token; Peter never stores it in project data.")
