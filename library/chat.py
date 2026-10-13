"""Shared local chat transport for the librarian and the workspace Assistant."""
import re

from library.server import validate_url
from library.auth import check_auth, request_options


class ChatClient:
    def __init__(self, settings):
        self.settings = settings
        self.last_usage = {}

    def complete(self, messages, *, structured=False, max_tokens=None):
        import requests
        validate_url(self.settings["server_url"])
        payload = {"messages": messages, "temperature": 0.1 if structured else 0.15,
                   "max_tokens": int(max_tokens if max_tokens is not None else self.settings.get("max_tokens", 1800))}
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
        url = self.settings["server_url"].rstrip("/") + "/chat/completions"
        response = requests.post(url, json=payload, timeout=(5, 240), allow_redirects=False, **request_options(self.settings))
        if getattr(response, "status_code", 200) in {400, 422} and "reasoning_effort" in payload:
            try:
                error = response.json().get("error", "")
                error = str(error).lower()
            except (ValueError, TypeError, AttributeError):
                error = ""
            if "reasoning_effort" in error or "reasoning" in error and any(word in error for word in ("unsupported", "unknown", "unexpected")):
                payload.pop("reasoning_effort")
                response = requests.post(url, json=payload, timeout=(5, 240), allow_redirects=False, **request_options(self.settings))
        # Some existing local servers lack response_format; the prompt still defines JSON.
        if structured and getattr(response, "status_code", 200) in {400, 422}:
            payload.pop("response_format")
            response = requests.post(url, json=payload, timeout=(5, 240), allow_redirects=False, **request_options(self.settings))
        if getattr(response, "status_code", 200) in {400, 422} and "chat_template_kwargs" in payload:
            payload.pop("chat_template_kwargs")
            response = requests.post(url, json=payload, timeout=(5, 240), allow_redirects=False, **request_options(self.settings))
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
