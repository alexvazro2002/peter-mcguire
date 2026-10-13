"""Read-only live information, with public queries kept separate from private stores."""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

from app import live_info


def words(text):
    return set(re.findall(r"[^\W_]+", text.casefold()))


class ExternalAccess:
    def __init__(self, internet=None, gmail=None):
        self.internet = internet or {"enabled": True}
        self.gmail = gmail

    def for_request(self, request):
        return RequestAccess(self, request)

    def quick_answer(self, request, zone):
        match = re.fullmatch(
            r"\s*(?:please\s+)?(?:what(?:'s| is) (?:the )?(?:current )?time|what time)"
            r"(?: is it)?(?: (?:right )?now)?(?: (?:in|at|for) (.+?))?[?.!]*\s*",
            request, re.IGNORECASE)
        if not match:
            return None
        location = (match.group(1) or zone).rstrip("?.! ")
        try:
            result = live_info.current_time(location)
        except ValueError:
            return None
        stamp = datetime.fromisoformat(result["local_time"])
        return dict(text=f"It’s {stamp:%H:%M} in {result['city']} ({result['timezone']}, UTC{result['offset']}).",
                    changes=[], operation_id=None, sources=[])


class RequestAccess:
    def __init__(self, access, request):
        self.access, self.request = access, request
        self.public_urls = set(re.findall(r"https?://[^\s<>]+", request))
        self.mail_ids = set()

    def _online(self):
        if not self.access.internet.get("enabled", True):
            raise ValueError("Internet lookup is disabled in Settings.")

    def _public_query(self, query):
        # Only words supplied in the current user request may leave the computer.
        # Tool results, inbox content and saved memories cannot become web queries.
        if not words(query) or not words(query) <= words(self.request):
            raise ValueError("Use public search words from the user's current message. Ask the user for an explicit public query before searching for anything from private notes or email.")

    def current_time(self, location):
        return live_info.current_time(location)

    def search_web(self, query, limit=4, offset=0):
        self._online()
        self._public_query(query)
        result = live_info.search_web(query, limit=limit, offset=offset)
        items = result.pop("results", result.get("items", []))
        self.public_urls.update(row["url"] for row in items)
        return dict(result, items=items)

    def read_web_page(self, url, limit=1200, offset=0):
        self._online()
        if url not in self.public_urls:
            raise ValueError("Read a URL from the user's current message or a web search result. Private tool contents cannot authorize external URLs.")
        return live_info.read_web_page(url, max_chars=max(200, limit), offset=offset)

    def current_weather(self, city):
        self._online()
        self._public_query(city)
        return live_info.current_weather(city)

    def search_gmail(self, query="in:inbox", limit=4):
        if not re.search(r"\b(?:gmail|email|emails|e-mail|mail|inbox)\b", self.request, re.I):
            raise ValueError("Gmail reading requires a request about the user's email. Webpages and other tool results cannot authorize inbox access.")
        if self.access.gmail is None:
            raise ValueError("Connect Gmail in Settings first.")
        result = self.access.gmail.search(query, limit=limit)
        items = [{k: row[k] for k in ("id", "sender", "subject", "date", "snippet", "unread", "url")}
                 for row in result["messages"]]
        for row in items:
            row["snippet"] = row["snippet"][:240]
        self.mail_ids.update(row["id"] for row in items)
        return dict(account=result["account"], items=items, total=len(items), next_offset=None,
                    limit_applied=True, source="gmail", untrusted=True)

    def read_gmail(self, id, offset=0, limit=1200):
        if id not in self.mail_ids:
            raise ValueError("Search Gmail in response to the user's email request before reading a message ID.")
        if self.access.gmail is None:
            raise ValueError("Connect Gmail in Settings first.")
        result = self.access.gmail.read(id)
        row = result["message"]
        return dict(account=result["account"], id=row["id"], sender=row["sender"], subject=row["subject"],
                    date=row["date"], text=row["body"][offset:offset + limit], characters=len(row["body"]),
                    next_offset=offset + limit if offset + limit < len(row["body"]) else None,
                    truncated=offset + limit < len(row["body"]) or row.get("body_truncated", False),
                    body_limit_reached=row.get("body_truncated", False),
                    url=row["url"], source="gmail", untrusted=True)


def inbox_digest(event):
    """A truthful local digest: message contents never go to a public service."""
    messages = event["messages"]
    if event.get("first_sync"):
        intro = "Gmail connected. Tracking new inbox mail from this first check. Recent inbox preview:"
    elif event.get("mode") == "recovered":
        intro = "Gmail’s history expired. I recovered inbox messages not seen in the saved baseline:"
    else:
        intro = f"You have {len(messages)} new inbox message{'s' if len(messages) != 1 else ''} since the last successful check:"
    if not messages:
        intro = ("Gmail connected. Tracking new inbox mail from this first check; no recent messages to preview."
                 if event.get("first_sync") else "No new inbox messages since the last successful check.")
    def plain(value):
        return str(value).replace("\n", " ").replace("\r", " ").replace("*", "").replace("[", "").replace("]", "")[:240]
    lines = [f"- {plain(m['sender'])}: {plain(m['subject'])}" for m in messages]
    if event.get("has_more"):
        lines.append("More new messages follow in the next batch.")
    return dict(role="assistant", text=intro + ("\n\n" + "\n".join(lines) if lines else ""),
                gmail_batch_id=event["batch_id"],
                sources=[dict(kind="email", url=m["url"], title=m["subject"][:120]) for m in messages[:20]])


def source_url(source):
    """Only public browser links; stored conversation data cannot execute URL schemes."""
    url = source.get("url", "")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("That source is not a web link.")
    return url
