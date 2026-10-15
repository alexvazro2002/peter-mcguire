"""A second installed LM Studio model for requests abandoned before commit."""
from pathlib import Path
import time

from library.models import model_choices, selected_settings
from library.runtime import ModelRuntime
from library.lmstudio import LMStudio, matches, parse_models


class ModelToolError(ValueError):
    """The model failed its tool protocol; all changes are still uncommitted."""


class ToolFallback:
    def __init__(self, settings, *, factory=ModelRuntime, choices=model_choices):
        self.settings = dict(settings)
        self.factory, self.choices, self.runtime = factory, choices, None
        self.title = ''

    def ensure(self, cancel):
        if cancel.is_set():
            raise ValueError('Request cancelled. Nothing was saved.')
        if self.settings.get('runtime_provider') != 'lmstudio':
            raise ValueError('No alternative LM Studio model is configured. Nothing was saved.')
        if self.runtime is None:
            installed = parse_models(LMStudio(self.settings).run(
                ['ls', '--json'], time.monotonic() + 15, cancel))
            current = Path(self.settings.get('model_path') or '/nonexistent').expanduser().resolve()
            available = [choice for choice in self.choices(self.settings)
                         if choice.key in {'qwen', 'gemma', 'qwen9b'} and choice.chat
                         and Path(choice.path).is_file() and Path(choice.path).expanduser().resolve() != current]
            # Keep the normal personality model. Use a compact instruction model
            # only for this retry, and leave LM Studio's existing models loaded.
            order = {'qwen': 0, 'gemma': 1, 'qwen9b': 2}
            available = [choice for choice in available if any(
                model.get('trainedForToolUse') is not False and matches(model, selected_settings(self.settings, choice))
                for model in installed)]
            available.sort(key=lambda choice: order[choice.key])
            if not available:
                raise ValueError('No alternative installed LM Studio chat model is available. Nothing was saved.')
            choice = available[0]
            candidate = selected_settings(self.settings, choice)
            candidate.update(enable_thinking=False, temperature=.1,
                             context_size=max(16384, int(candidate.get('context_size', 16384))))
            self.runtime, self.title = self.factory(candidate), choice.title
        self.runtime.cancel = cancel
        self.runtime.ensure()
        if cancel.is_set():
            raise ValueError('Request cancelled. Nothing was saved.')
        return self.runtime

    def close(self):
        if self.runtime is not None:
            self.runtime.close()
