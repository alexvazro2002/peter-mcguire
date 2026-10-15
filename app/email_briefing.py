"""Sender-first inbox offers with consent tied to the current visible question.

The queue contains headers, never message bodies. Only a direct affirmative
reply to an active offer calls Gmail.read; mail contents remain plain data.
"""
from __future__ import annotations

import re
import threading
from email.utils import parseaddr
from urllib.parse import urlsplit

from app.gmail import GmailError, _check, _clean, _message_id


def _single_line(value, limit):
    return " ".join(_clean(value, limit).split())[:limit]


def sender_name(value):
    """Prefer a header's display name, then its address, without interpreting it."""
    cleaned = _single_line(value, 1000)
    name, address = parseaddr(cleaned)
    return _single_line(name or (address if "@" in address else "") or cleaned or "an unknown sender", 120)


def header_message(message, account=""):
    """A safe persisted queue entry; ignore body, snippet and arbitrary extra keys."""
    message_id = _message_id(message.get("id"))
    url = str(message.get("url", ""))
    try:
        parsed = urlsplit(url)
        safe_url = (parsed.scheme == "https" and parsed.hostname == "mail.google.com"
                    and not parsed.username and not parsed.password and parsed.port in {None, 443})
    except ValueError:
        safe_url = False
    if not safe_url:
        url = ""
    try:
        received = max(0, int(message.get("received_ms", 0)))
    except (TypeError, ValueError):
        received = 0
    return dict(id=message_id, account=_single_line(account or message.get("account", ""), 320).lower(),
                sender=_single_line(message.get("sender", "Unknown sender"), 1000),
                subject=_single_line(message.get("subject", "(No subject)"), 240),
                date=_single_line(message.get("date", ""), 240), received_ms=received,
                unread=bool(message.get("unread", False)), url=url,
                metadata_only=True, source="gmail", untrusted=True)


def _answer(text, **extra):
    return dict(text=text, changes=[], operation_id=None, sources=[], **extra)


def _choice(request):
    # Full matches keep "yes, but first ..." and other substantive requests
    # from accidentally granting consent to the previous email question.
    text = str(request or "").casefold().strip()
    text = re.sub(r"[.!?,;]+", " ", text)
    text = " ".join(text.split())
    text = re.sub(r"(?:\s+(?:peter|sir))$", "", text)
    yes = {
        "yes", "yeah", "yep", "yes please", "yes please do", "yes read it", "yes please read it",
        "yes read it please", "yes read it for me", "yes please read it for me",
        "sure", "sure please", "sure read it", "sure please read it", "of course",
        "yes of course", "certainly", "absolutely", "affirmative", "yes go ahead", "please go ahead",
        "okay", "ok", "okay read it", "ok read it", "go ahead", "please do",
        "read it", "read it please", "please read it", "read the email",
        "please read the email", "read it for me", "please read it for me", "read it to me",
        "please read it to me", "do so", "please do so",
    }
    no = {
        "no", "no thanks", "no thank you", "no please", "not now", "later",
        "skip", "skip it", "skip this", "skip that", "skip this email",
        "skip this one", "next", "next one", "next email", "leave it",
        "leave it for now", "no leave it", "no skip it", "no not now",
    }
    return "read" if text in yes else "skip" if text in no else None


class EmailBriefing:
    """Local conversation state; the UI decides when the next offer is shown."""

    def __init__(self, client):
        self.client = client
        self.pending = []
        self.awaiting = None
        self._seen = set()
        self._staged = {}
        self._lock = threading.RLock()
        self._offer_generation = 0

    def extend(self, messages, account=None):
        with self._lock:
            added = 0
            for message in messages:
                if not isinstance(message, dict):
                    continue
                try:
                    entry = header_message(message, account or "")
                except (GmailError, ValueError):
                    continue
                key = (entry["account"], entry["id"])
                if key in self._seen:
                    continue
                self._seen.add(key)
                self.pending.append(entry)
                added += 1
            return added

    add = extend

    def offer_next(self):
        # Gmail.status only checks Peter's local configuration and token file.
        # Inactive accounts remain queued but their senders are not announced.
        status = self.client.status()
        account = str(status.get("account") or status.get("email") or "").lower()
        with self._lock:
            self._offer_generation += 1
            self.awaiting = None
            if not status.get("connected") or not account:
                return None
            entry = next((row for row in self.pending if row["account"] == account), None)
            if entry is None:
                return None
            self.awaiting = dict(entry)
            return _answer(
                f"You have a new email from {sender_name(self.awaiting['sender'])}, "
                "would you like me to read it?",
                email_offer=dict(self.awaiting), untrusted=True)

    def expire(self):
        """An unrelated request ends consent context, but retains the queued mail."""
        with self._lock:
            self.awaiting = None
            self._offer_generation += 1

    def _handled(self):
        entry = self.awaiting
        key = (entry["account"], entry["id"])
        self.pending = [row for row in self.pending
                        if (row["account"], row["id"]) != key]
        self._staged[key] = dict(entry)
        self.awaiting = None
        self._offer_generation += 1
        return dict(account=entry["account"], id=entry["id"])

    def commit_handled(self, marker):
        """Confirm only after the UI has persisted its read or decline answer.

        Until this commit, export_state keeps the header pending. An unrelated
        incoming Gmail batch cannot durably remove a response not yet delivered.
        """
        if not isinstance(marker, dict):
            raise ValueError("An email delivery marker must identify its account and message.")
        key = (_single_line(marker.get("account", ""), 320).lower(), _message_id(marker.get("id")))
        with self._lock:
            return self._staged.pop(key, None) is not None

    def respond(self, request, cancel=None):
        with self._lock:
            if self.awaiting is None:
                return None
            choice = _choice(request)
            if choice is None:
                self.expire()
                return None
            _check(cancel)
            if choice == "skip":
                return _answer("Of course. I'll leave that email for now.",
                               email_handled=self._handled(), email_briefing_next=True)
            entry = dict(self.awaiting)
            generation = self._offer_generation
        # A queued message cannot grant access to a newly connected account.
        if entry["account"]:
            status = self.client.status()
            current_account = str(status.get("account") or status.get("email") or "").lower()
            if not status.get("connected") or current_account != entry["account"]:
                self.expire()
                raise GmailError("The Gmail account changed. Check the inbox again before reading that email.")
        result = self.client.read(entry["id"], cancel=cancel)
        _check(cancel)
        if entry["account"] and str(result.get("account", "")).lower() != entry["account"]:
            self.expire()
            raise GmailError("The Gmail account changed before the email could be read.")
        message = result["message"]
        if _message_id(message.get("id")) != entry["id"]:
            raise GmailError("Gmail returned a different message. The queued email has not been read.")
        subject = _single_line(message.get("subject", entry["subject"]), 240) or "(No subject)"
        body = _clean(message.get("body", ""), 16000)
        text = f"Subject: {subject}.\n\n"
        if body:
            text += body[:6000]
            if len(body) > 6000 or message.get("body_truncated"):
                text += "\n\nThis is a partial reading. Open the email to read the remainder."
        else:
            text += "This message has no readable text body. Open the email to view any attachments."
        safe_message = header_message(message, result.get("account", ""))
        with self._lock:
            _check(cancel)
            if generation != self._offer_generation:
                raise GmailError("That email question is no longer active. Ask Peter to offer it again before reading.")
            answer = _answer(text, email_handled=self._handled(), email_briefing_next=True,
                             untrusted=True, email_content=True)
        if safe_message["url"]:
            answer["sources"] = [dict(kind="email", title=subject[:120], url=safe_message["url"])]
        return answer

    def export_state(self):
        """Header-only state suitable for the app's existing private persistence."""
        with self._lock:
            return dict(pending=[dict(row) for row in self._staged.values()] + [dict(row) for row in self.pending],
                        awaiting=dict(self.awaiting) if self.awaiting else None,
                        seen=[dict(account=account, id=message_id) for account, message_id in sorted(self._seen)])

    def restore_state(self, state):
        if not isinstance(state, dict) or not isinstance(state.get("pending", []), list) or not isinstance(state.get("seen", []), list):
            raise ValueError("Saved email prompt state must contain lists of pending headers and known IDs.")
        pending, seen = [], set()
        try:
            for entry in state.get("pending", []):
                if not isinstance(entry, dict):
                    raise ValueError("Saved email queue contains an invalid header entry.")
                row = header_message(entry)
                key = (row["account"], row["id"])
                if key not in seen:
                    pending.append(row); seen.add(key)
            for entry in state.get("seen", []):
                if not isinstance(entry, dict):
                    raise ValueError("Saved email queue contains an invalid known message ID.")
                seen.add((_single_line(entry.get("account", ""), 320).lower(), _message_id(entry.get("id"))))
        except GmailError as error:
            raise ValueError("Saved email queue contains an invalid message ID.") from error
        with self._lock:
            self.pending, self.awaiting, self._seen = pending, None, seen
            self._staged = {}
            self._offer_generation += 1
        # A restarted session asks again; stale consent never survives a restart.
