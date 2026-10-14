"""Shared local chat transport for the librarian and the workspace Assistant."""
import math
import re

from library.server import validate_url
from library.auth import check_auth, request_options

ASSISTANT_REPLY_SCHEMA = {
    "oneOf": [
        {"type": "object", "properties": {"answer": {"type": "string"}, "apply_changes": {"type": "boolean"},
            "intent": {"enum": ["read", "write", "clarify"]}}, "required": ["answer", "apply_changes"], "additionalProperties": False},
        {"type": "object", "properties": {"tool_calls": {"type": "array", "minItems": 1, "maxItems": 8,
            "items": {"type": "object", "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}},
                      "required": ["name", "arguments"], "additionalProperties": False}},
            "intent": {"enum": ["read", "write", "clarify"]}}, "required": ["tool_calls"], "additionalProperties": False},
    ]
}


class ChatClient:
    def __init__(self, settings):
        self.settings = settings
        self.last_usage = {}

    def complete(self, messages, *, structured=False, max_tokens=None, response_schema=None):
        import requests
        validate_url(self.settings["server_url"])
        payload = {"messages": messages, "temperature": 0.1 if structured else 0.15,
                   "max_tokens": int(max_tokens if max_tokens is not None else self.settings.get("max_tokens", 1800))}
        for name, low, high in (("temperature", 0, 2), ("top_p", 0.01, 1), ("min_p", 0, 1), ("top_k", 0, 200)):
            if name not in self.settings or self.settings[name] is None:
                continue
            value = self.settings[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high or name == "top_k" and int(value) != value:
                raise ValueError("Invalid model sampling setting: " + name)
            payload[name] = int(value) if name == "top_k" else value
        if self.settings.get("api_model"):
            payload["model"] = self.settings["api_model"]
        elif self.settings.get("runtime_provider") == "lmstudio":
            # Library workers share the server in another process; resolve its exact alias too.
            from library.models import model_matches
            models = requests.get(self.settings["server_url"].rstrip("/") + "/models", timeout=5, allow_redirects=False, **request_options(self.settings))
            check_auth(models)
            models.raise_for_status()
            identifiers = [item.get("id", "") for item in models.json().get("data", [])]
            identifier = next((value for value in identifiers if model_matches(self.settings, [value])), None)
            if not identifier:
                raise ValueError("The local server has no matching model. Check the selected model in Settings.")
            self.settings["api_model"] = payload["model"] = identifier
        if "enable_thinking" in self.settings:
            payload["chat_template_kwargs"] = {"enable_thinking": bool(self.settings["enable_thinking"])}
            if self.settings.get("runtime_provider") == "lmstudio" and not self.settings["enable_thinking"]:
                # LM Studio's OpenAI route controls Gemma reasoning separately;
                # chat_template_kwargs alone can be silently ignored there.
                payload["reasoning_effort"] = "none"
        if structured:
            payload["response_format"] = {"type": "json_object"}
            if self.settings.get("runtime_provider") == "lmstudio":
                payload["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "peter_reply", "strict": True, "schema": response_schema or ASSISTANT_REPLY_SCHEMA}}
        url = self.settings["server_url"].rstrip("/") + "/chat/completions"
        def post():
            return requests.post(url, json=payload, timeout=(5, 240), allow_redirects=False, **request_options(self.settings))

        def rejection(response):
            if getattr(response, "status_code", 200) not in {400, 422}:
                return ""
            try:
                return str(response.json().get("error", "")).lower()
            except (ValueError, TypeError, AttributeError):
                return ""

        def unsupported(response, parameter):
            detail = rejection(response)
            return parameter in detail and any(word in detail for word in ("unsupported", "unknown", "unexpected", "not support", "not implemented", "unrecognized", "not one of"))

        response = post()
        if "reasoning_effort" in payload and unsupported(response, "reasoning"):
            payload.pop("reasoning_effort")
            response = post()
        # Retry only an explicitly unsupported option. Engine crashes, context
        # overflow and malformed requests must retain their original failure.
        if structured and unsupported(response, "response_format"):
            if payload["response_format"]["type"] == "json_schema":
                payload["response_format"] = {"type": "json_object"}
                response = post()
            if unsupported(response, "response_format"):
                payload.pop("response_format")
                response = post()
        if "chat_template_kwargs" in payload and unsupported(response, "chat_template_kwargs"):
            payload.pop("chat_template_kwargs")
            response = post()
        detail = rejection(response)
        if any(marker in detail for marker in ("engine protocol predict request failed", "model is unloaded", "no models loaded")):
            raise ValueError("Peter's local model stopped responding. Try again so Peter can reconnect to the installed model.")
        if "context" in detail and any(marker in detail for marker in ("overflow", "exceed", "too long", "maximum")):
            raise ValueError("This request exceeds the local model's context. Ask for a smaller section or shorter request.")
        check_auth(response)
        response.raise_for_status()
        try:
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
            if not isinstance(message, dict):
                raise ValueError("Not a message")
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ValueError("The model server returned an invalid chat response.") from error
        self.last_usage = body.get("usage", {})
        content = re.sub(r"<think>.*?</think>", "", message.get("content") or "", flags=re.S).strip()
        if not content and not message.get("tool_calls"):
            raise ValueError("Model returned no final answer. Increase the output token budget in Settings.")
        return dict(message, content=content, finish_reason=choice.get("finish_reason"))
