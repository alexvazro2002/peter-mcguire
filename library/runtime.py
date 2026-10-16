"""One UI-owned model server shared by Library workers and Assistant requests."""
import threading
import time

from library.chat import ChatClient
from library.server import ModelServer


class _Cancellation:
    def __init__(self, *events):
        self.events = events

    def is_set(self):
        return any(event.is_set() for event in self.events)

    def wait(self, timeout):
        until = time.monotonic() + timeout
        while not self.is_set():
            remaining = until - time.monotonic()
            if remaining <= 0:
                return False
            self.events[-1].wait(min(.05, remaining))
        return True


class ModelRuntime:
    def __init__(self, settings):
        self.settings = dict(settings)
        self.lock, self.closed = threading.RLock(), False
        self.cancel = threading.Event()
        self.server = ModelServer(self.settings, lambda _: None)
        self.client = ChatClient(self.settings)

    def ensure(self, cancel=None):
        token = _Cancellation(self.cancel, cancel) if cancel is not None else self.cancel
        while not self.lock.acquire(timeout=.05):
            if token.is_set():
                raise ValueError("Model startup cancelled.")
        try:
            if self.closed:
                raise ValueError("The model runtime is closed.")
            if token.is_set():
                raise ValueError("Model startup cancelled.")
            try:
                self.server.ensure(token)
            except Exception:
                self.server.close()
                self.server.process = None
                raise
        finally:
            self.lock.release()

    def start(self, report):
        def run():
            try:
                self.ensure()
            except Exception as error:
                report(str(error))
        threading.Thread(target=run, daemon=True).start()

    def close(self, wait=False):
        self.closed = True
        self.cancel.set()
        def stop():
            with self.lock:
                self.server.close()
        thread = threading.Thread(target=stop, daemon=False)
        thread.start()
        if wait:
            thread.join()
