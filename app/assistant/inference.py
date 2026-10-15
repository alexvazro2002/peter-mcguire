"""Bound Peter's local model work without limiting document Library jobs."""
import time
import requests

from app.assistant.model_fallback import ModelToolError
from library.chat import ChatClient


class DeadlineClient:
    def __init__(self, client, cancel, *, seconds=45, max_calls=8, clock=time.monotonic):
        self.client, self.cancel, self.seconds = client, cancel, seconds
        self.clock, self.deadline, self.calls, self.max_calls = clock, None, 0, max_calls

    def __getattr__(self, name):
        return getattr(self.client, name)

    def complete(self, *args, **kwargs):
        if self.cancel.is_set():
            raise ValueError('Request cancelled. Nothing was saved.')
        if self.deadline is None:
            self.deadline = self.clock() + self.seconds
        if self.clock() >= self.deadline or self.calls >= self.max_calls:
            raise ModelToolError('The local model took too long to finish. No changes were saved.')
        self.calls += 1
        try:
            if isinstance(self.client, ChatClient):
                kwargs.update(deadline=self.deadline, cancel=self.cancel)
            result = self.client.complete(*args, **kwargs)
        except (requests.Timeout, TimeoutError) as error:
            if self.cancel.is_set():
                raise ValueError('Request cancelled. Nothing was saved.') from error
            raise ModelToolError('The local model took too long to finish. No changes were saved.') from error
        if self.cancel.is_set():
            raise ValueError('Request cancelled. Nothing was saved.')
        if self.clock() >= self.deadline:
            raise ModelToolError('The local model took too long to finish. No changes were saved.')
        return result
