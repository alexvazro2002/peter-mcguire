"""Execute narrow, explicit workspace operations without asking a model to act.

The calendar is the authority for both selection and completion. A conversation
can narrow the user's own last deletion request, but assistant prose never grants
permission or supplies task IDs, dates, or counts.
"""
from __future__ import annotations

import re

from app.assistant.transactions import Transaction


_ACTION = r"(?:delete|remove|clear|wipe)(?:\s+(?:out|away))?"
_PREFIX = (r"(?:peter\s+)?(?:please\s+)?(?:"
           r"(?:can|could|would|will)\s+you\s+|"
           r"i\s+(?:want|need|would\s+like)\s+you\s+to\s+)?(?:please\s+)?")
_DETERMINERS = r"(?:(?:all|every|the|my|those|these|remaining|of|six|\d+)\s+)*"
_UNDATED = r"(?:unscheduled|unschedule|undated|no\s+date|no\s+due\s+date)"
_TASKS = (rf"(?:{_UNDATED}\s+tasks|tasks\s+(?:"
          r"with\s+no\s+(?:due\s+)?dates?|"
          r"without\s+(?:(?:a|any)\s+)?(?:due\s+)?dates?|"
          r"that\s+are\s+not\s+scheduled|"
          r"that\s+have\s+no\s+(?:due\s+)?dates?))")
_TAIL = r"(?:\s+(?:please|sir|thanks|thank\s+you|for\s+me|now))*"
_NEGATED = re.compile(r"\b(?:never|don'?t|do\s+not|shouldn'?t|wouldn'?t|can'?t|except|unless|excluding|keep|leave)\b")
_DIRECT = re.compile(rf"^{_PREFIX}{_ACTION}\s+(?:(?:just|only)\s+)?{_DETERMINERS}{_TASKS}{_TAIL}$")
_COUNT_THEN_DELETE = re.compile(
    rf"^{_PREFIX}(?:there\s+(?:should\s+be|are)|i\s+have)\s+"
    rf"{_DETERMINERS}{_TASKS}\s+(?:please\s+)?{_ACTION}\s+"
    rf"(?:all\s+of\s+)?(?:those|them)(?:\s+all)?{_TAIL}$")
_CONTEXTUAL = re.compile(
    rf"^{_PREFIX}(?:now\s+)?(?:(?:{_ACTION}\s+)|(?:just\s+)|(?:only\s+))"
    rf"(?:(?:just|only)\s+)?{_DETERMINERS}{_UNDATED}\s+(?:ones|tasks){_TAIL}$")
_PREVIOUS_DELETE = re.compile(
    rf"^{_PREFIX}{_ACTION}\s+{_DETERMINERS}(?:(?:dated|scheduled)\s+)?tasks{_TAIL}$")


def _phrase(request):
    if not isinstance(request, str):
        return ""
    value = request.casefold().replace("\u2019", "'")
    value = re.sub(r"[-–—]", " ", value)
    value = re.sub(r"[^\w'\s]", " ", value)
    return " ".join(value.split())


def _previous_task_deletion(conversation):
    """Only the immediately previous user turn can supply a missing task noun."""
    previous = next((row for row in reversed(conversation or [])
                     if isinstance(row, dict) and row.get("role") == "user"), None)
    text = _phrase(previous.get("text", "")) if previous else ""
    return bool(text and not _NEGATED.search(text) and
                (_DIRECT.fullmatch(text) or _COUNT_THEN_DELETE.fullmatch(text)
                 or _PREVIOUS_DELETE.fullmatch(text)))


def handles(request, conversation=()):
    """Return true only for a whole-category, unqualified undated-task deletion."""
    text = _phrase(request)
    if not text or _NEGATED.search(text):
        return False
    if _DIRECT.fullmatch(text) or _COUNT_THEN_DELETE.fullmatch(text):
        return True
    return bool(_CONTEXTUAL.fullmatch(text) and _previous_task_deletion(conversation))


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise ValueError("Request cancelled.")


def respond(request, conversation, vault, calendar, history, cancel=None):
    """Delete only live undated tasks; return a verified, undoable app result.

    Unrecognised or qualified requests return None for the normal assistant.
    Cancellation and concurrent changes raise before anything can be committed.
    """
    if not handles(request, conversation):
        return None
    _cancelled(cancel)
    history.recover()
    _cancelled(cancel)
    tx = Transaction(vault, calendar, history)
    deleted = [dict(item) for item in tx.calendar_data["entries"]
               if item.get("kind") == "task" and not item.get("date")]
    _cancelled(cancel)
    count = len(deleted)
    deleted_ids = {item["id"] for item in deleted}
    evidence = dict(tools=["delete_unscheduled_tasks"],
                    workspace_action="delete_unscheduled_tasks",
                    verified=True, deleted_count=count,
                    deleted_task_ids=[item["id"] for item in deleted],
                    selection=dict(kind="task", date=None))
    if not count:
        return dict(text="You have no unscheduled tasks to delete.",
                    changes=[], operation_id=None, sources=[], evidence=evidence)
    tx.calendar_data["entries"] = [item for item in tx.calendar_data["entries"]
                                   if item["id"] not in deleted_ids]
    summary = f"Deleted {count} unscheduled {'task' if count == 1 else 'tasks'} (recoverable)."
    tx.changed(summary)
    _cancelled(cancel)
    operation_id = tx.commit(request)
    # History.commit checks the written bytes against the complete after-image.
    # Report only that verified operation, never a model-generated promise.
    return dict(text=f"Deleted {count} unscheduled {'task' if count == 1 else 'tasks'}. You can undo this.",
                changes=list(tx.summaries), operation_id=operation_id,
                sources=[], evidence=evidence)
