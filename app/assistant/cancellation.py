"""Keep a stopped request from committing, including at the final save boundary."""


class RequestHistory:
    def __init__(self, history, cancel, lock):
        self.history, self.cancel, self.lock = history, cancel, lock
        self.commit_guard = lock
        self.saved = None

    def __getattr__(self, name):
        return getattr(self.history, name)

    def commit(self, changes, summaries, request="", **kwargs):
        # Stop and commit share the same lock. Either Stop wins and no writes
        # begin, or the complete verified operation remains available to undo.
        with self.lock:
            if self.cancel.is_set():
                raise ValueError("Request cancelled. Nothing was saved.")
            operation_id = self.history.commit(changes, summaries, request, **kwargs)
            if operation_id:
                self.saved = dict(operation_id=operation_id, changes=list(summaries))
            return operation_id
