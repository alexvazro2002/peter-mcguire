"""Discover existing GGUF weights and describe their intended use, without downloads."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import BASE_DIR


@dataclass(frozen=True)
class ModelChoice:
    key: str
    title: str
    path: str
    projector: str = ""
    chat: bool = True
    recommendation: str = ""


PROFILES = (
    ("qwen", "Qwen3.5-4B", "Qwen3.5-4B.Q4_K_S.gguf", "",
     "Recommended for Assistant actions and Library Q&A. General text, code and tool use.", True),
    ("gemma", "Gemma 4 E4B", "gemma-4-E4B-it-Q4_K_M.gguf", "mmproj-gemma-4-E4B-it-BF16.gguf",
     "Alternative for general chat, writing and code questions. Compare quality and speed on your hardware.", True),
    ("qwen9b", "Qwen3.5 9B HauhauCS Aggressive", "Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf", "",
     "Local text chat and Assistant actions. Uses the installed LM Studio Q4_K_M weights with thinking disabled.", True),
    ("paddle", "PaddleOCR-VL 1.6", "PaddleOCR-VL-1.6-GGUF.gguf", "PaddleOCR-VL-1.6-GGUF-mmproj.gguf",
     "Document-image OCR and layout extraction, not a general Assistant/Library text-chat model. Image OCR is not wired into this chat.", False),
)


def model_choices(settings, roots=None):
    roots = roots or [BASE_DIR / "ai_models", BASE_DIR / "projects" / "ai_models",
                      BASE_DIR.parent / "ai_models", BASE_DIR.parent / "projects" / "ai_models",
                      Path.home() / "Desktop" / "NexusDesk" / "ai_models", Path.home() / ".lmstudio" / "models"]
    configured = Path(settings["model_path"]).expanduser() if settings.get("model_path") else None
    if configured and configured.is_file():
        roots = [configured.parent, *roots]
    found = []
    for root in dict.fromkeys(Path(r) for r in roots):
        if root.is_dir():
            found.extend(p for p in root.rglob("*.gguf") if p.is_file())
    choices = []
    for key, title, filename, projector, recommendation, chat in PROFILES:
        weight = next((p for p in found if p.name.casefold() == filename.casefold()), None)
        if weight is None and key == "qwen9b":
            continue
        if weight is None:
            folder = {"qwen9b": "Qwen3.5-9B-Uncensored-HauhauCS-Aggressive", "qwen": "Qwen3.5-4B", "gemma": "gemma-4-E4B", "paddle": "PaddleOCR-VL"}[key]
            weight = Path(roots[0]) / folder / filename
        mmproj = weight.parent / projector if projector else None
        choices.append(ModelChoice(key, title, str(weight), str(mmproj) if mmproj else "", chat, recommendation))
    if configured and configured.is_file() and "mmproj" not in configured.name.casefold() and all(Path(c.path).resolve() != configured.resolve() for c in choices):
        choices.append(ModelChoice("custom", configured.stem, str(configured), settings.get("projector_path", ""),
                                   recommendation="Your configured local GGUF. Verify that it supports instruction following and tools."))
    choices.append(ModelChoice("external", "Current local server / LM Studio", "", recommendation=
                               "Use the model already loaded in your local server. This option cannot switch its weights for you."))
    return choices


def selected_settings(settings, choice):
    if not choice.chat:
        raise ValueError("PaddleOCR is a document-image OCR specialist, not a general text-chat Assistant. Choose Qwen or Gemma.")
    if choice.path and "mmproj" in Path(choice.path).name.casefold():
        raise ValueError("Select the model weights, not the mmproj vision projector.")
    if choice.key != "external" and not Path(choice.path).is_file():
        raise ValueError("Model file not found: " + choice.path + ". Set its location in Settings, or move ai_models beside main_flet.py.")
    result = dict(settings, model_id=choice.key, model_path=choice.path, projector_path="", external_model=choice.key == "external",
                  lmstudio_model_key="", api_model="")
    if choice.key == "qwen9b":
        result.update(temperature=0.35, top_p=0.8, top_k=20, min_p=0.0, enable_thinking=False)
    elif choice.key in {"qwen", "gemma"}:
        for name in ("temperature", "top_p", "top_k", "min_p"):
            result.pop(name, None)
    # Text-only chat does not need to allocate the vision projector.
    return result


def model_matches(settings, identifiers):
    if settings.get("external_model") or not settings.get("model_id"):
        return True
    expected = Path(settings.get("model_path", "")).stem.casefold()
    profile = settings["model_id"]
    markers = {"qwen": "qwen3.5-4b", "qwen9b": "qwen3.5-9b-uncensored-hauhaucs-aggressive", "gemma": "gemma-4-e4b"}
    marker = markers.get(profile, expected)
    alias = settings.get("api_model", "")
    return any((expected and expected in str(value).casefold()) or (marker and marker in str(value).casefold())
               or (alias and str(value) == alias) for value in identifiers)
