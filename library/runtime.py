"""One UI-owned model server shared by Library workers and Assistant requests."""
import threading

from library.chat import ChatClient
from library.server import ModelServer


class ModelRuntime:
    def __init__(self, settings):
        self.settings = dict(settings)
        self.lock, self.closed = threading.RLock(), False
        self.cancel = threading.Event()
        self.server = ModelServer(self.settings, lambda _: None)
        self.client = ChatClient(self.settings)

    def ensure(self):
        with self.lock:
            if self.closed:
                raise ValueError("The model runtime is closed.")
            try:
                self.server.ensure(self.cancel)
            except Exception:
                self.server.close()
                self.server.process = None
                raise

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
