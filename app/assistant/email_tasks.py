"""Save a clearly requested email reply task using a verified conversation source.

This deliberately covers a small, complete action. Requests with extra work or
unclear scheduling continue to the general tool-capable assistant instead of
silently losing a clause. Email prose cannot supply an action or a target URL.
"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.assistant.dates import WEEKDAYS, local_now, resolve_date
from app.assistant.mail_flow import select_reference
from app.assistant.tools import Tools
from app.assistant.transactions import Transaction
from app.integrations import request_text


_HEAD = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+|"
    r"i\s+(?:want|need|would\s+like)\s+you\s+to\s+)?(?:please\s+)?"
    r"(?:(?:create|make|add|schedule)\s+(?:me\s+)?(?:a\s+|the\s+)?(?:task|reminder)|remind\s+me)\b",
    re.I,
)
_REPLY = re.compile(r"\b(?:to\s+)?(?:reply|respond)\s+to\s+(?:(?:this|that|the)\s+)?(?:email|message)\b", re.I)
_DATE = re.compile(r"\b(?:today|tomorrow|yesterday|(?:(?:this|next|last|upcoming)\s+)?(?:"
                   + "|".join(WEEKDAYS) + r")|\d{4}-\d{2}-\d{2})\b", re.I)
_TIME = re.compile(
    r"(?<![\w:.])(?:u\s*)?(?P<hour12>\d{1,2})(?:(?::|\.|\s+)(?P<minute12>\d{2}))?"
    r"\s*(?P<meridiem>[ap]\.?m\.?)(?!\w)|"
    r"(?<![\w:.])(?P<hour24>\d{1,2}):(?P<minute24>\d{2})(?![\w:]|\.\d)", re.I,
)
_LINK = re.compile(
    r"\b(?:(?:and|with)\s+)?(?:also\s+)?(?:(?:add|include|attach|put|save)\s+)?"
    r"(?:(?:a|the|its)\s+)?(?:(?:direct|gmail|email)\s+)?link"
    r"(?:\s+to\s+(?:(?:this|that|the)\s+)?(?:it|email|message))?"
    r"(?:\s+in\s+(?:(?:this|that|the)\s+)?(?:task|reminder))?\b", re.I,
)
_NEGATED = re.compile(r"\b(?:never|don'?t|do\s+not|shouldn'?t|wouldn'?t|can'?t|unless|except|instead)\b", re.I)
_LEAD_IN = re.compile(r"^\s*(?:and|also|then|now|well|oh|actually)\b[\s,.:]+", re.I)


def _command_text(request):
    # Conversational lead-ins precede Peter's name in ordinary speech. Remove
    # filler only at the start; never discard a previous instruction or clause.
    return request_text(_LEAD_IN.sub("", request, count=1))


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise ValueError("Request cancelled.")


def plan(request, zone="Europe/London", now=None):
    """Return a fully understood date/time plan, or None without executing it."""
    if not isinstance(request, str):
        return None
    text = _command_text(request)
    if _NEGATED.search(text):
        return None
    head = _HEAD.match(text)
    if not head:
        return None
    rest = text[head.end():]
    replies = list(_REPLY.finditer(rest))
    dates = list(_DATE.finditer(rest))
    times = list(_TIME.finditer(rest))
    if len(replies) != 1 or len(dates) != 1 or len(times) != 1:
        return None
    clock = times[0]
    if clock["hour12"]:
        hour, minute = int(clock["hour12"]), int(clock["minute12"] or 0)
        if not 1 <= hour <= 12 or minute > 59:
            return None
        hour = hour % 12 + (12 if clock["meridiem"].lower().startswith("p") else 0)
    else:
        hour, minute = int(clock["hour24"]), int(clock["minute24"])
        if hour > 23 or minute > 59:
            return None
    # Remove only recognised clauses. Anything else is work we must not drop.
    remainder = _REPLY.sub(" ", rest)
    remainder = _DATE.sub(" ", remainder)
    remainder = _TIME.sub(" ", remainder)
    remainder = _LINK.sub(" ", remainder)
    remainder = re.sub(r"[,.!?;]", " ", remainder)
    if any(word.lower() not in {"for", "on", "at", "the", "to", "and", "also", "please", "sir", "me"}
           for word in remainder.split()):
        return None
    base = now or local_now(zone)
    if isinstance(base, datetime) and base.tzinfo is not None:
        base = base.astimezone(ZoneInfo(zone))
    try:
        day = resolve_date(dates[0][0], base.date().isoformat(), zone)["date"]
    except (TypeError, ValueError):
        return None
    return dict(date=day, time=f"{hour:02}:{minute:02}")


def respond(request, conversation, vault, calendar, history, cancel=None,
            zone="Europe/London", now=None):
    """Create one undoable task with its real email link; never send an email."""
    schedule = plan(request, zone, now)
    if schedule is None:
        return None
    _cancelled(cancel)
    ref = select_reference(request, conversation)
    if ref is None:
        return dict(text="Which email should I link to that reply task?",
                    sources=[], changes=[], operation_id=None,
                    evidence=dict(workspace_action="email_reply_task", verified=False,
                                  needs_email_reference=True))
    # Source metadata is evidence, not instructions. Keep the full subject in
    # details when it exceeds the calendar's title limit.
    subject = " ".join(str(ref.get("subject") or "Untitled email").split())
    title = ("Reply to email: " + subject)[:240]
    details = f"Reply to: {subject}\n\nGmail: {ref['url']}"
    history.recover()
    _cancelled(cancel)
    tx = Transaction(vault, calendar, history)
    tools = Tools(tx, zone=zone, today=schedule["date"])
    created = tools.execute("create_task", dict(title=title, details=details, note="", **schedule))
    _cancelled(cancel)
    operation_id = tx.commit(request)
    source = dict(kind="email", id=ref["id"], account=ref["account"],
                  url=ref["url"], title=subject)
    verb = "I've scheduled" if created["changed"] else "You already have"
    text = f"{verb} a task to reply to “{subject}” for {schedule['date']} at {schedule['time']}, with the Gmail link."
    return dict(text=text, sources=[source, *tx.sources], changes=list(tx.summaries),
                operation_id=operation_id,
                evidence=dict(tools=["create_task"], workspace_action="email_reply_task",
                              verified=True, task_id=created["id"], changed=created["changed"],
                              email_id=ref["id"], email_account=ref["account"], **schedule))
