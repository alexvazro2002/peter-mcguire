"""Read-only Gmail with installed-app OAuth and acknowledged startup inbox sync.

Google account consent happens in the system browser (loopback + state + PKCE).
Only explicitly configured Peter credentials are read. Mail is untrusted data;
no HTML, attachment, or message instruction is executed. Credentials, inbox IDs,
and the pending delivery batch stay in a private local runtime directory.

Protocol references:
https://developers.google.com/identity/protocols/oauth2/native-app
https://developers.google.com/workspace/gmail/api/guides/sync
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import queue
import re
import secrets
import stat
import tempfile
import threading
import time
import webbrowser
from email.header import decode_header
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from app.config import BASE_DIR, DATA_DIR

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
SETUP_MESSAGE = ("Gmail is not connected. Add a Google Cloud Desktop OAuth client JSON with the Gmail API enabled "
                 "in Settings, then choose Connect Gmail. Peter requests read-only inbox access.")


class GmailError(RuntimeError):
    pass


class GmailCancelled(GmailError):
    pass


class GmailAPIError(GmailError):
    def __init__(self, status):
        self.status = status
        message = {401: "Gmail sign-in expired. Choose Connect Gmail again.",
                   403: "Gmail access was refused. Enable the Gmail API and approve read-only access for this account.",
                   429: "Gmail is temporarily rate limited. Try the inbox check again later."}.get(status,
                   f"Gmail returned HTTP {status}. The inbox checkpoint has not advanced.")
        super().__init__(message)


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise GmailCancelled("Gmail operation cancelled.")


def _clean(value, limit=16000):
    # Drop control and direction-override characters while retaining plain text.
    text = str(value or "")
    text = "".join(c for c in text if c in "\n\t" or ord(c) >= 32 and ord(c) != 127
                   and not 0x202A <= ord(c) <= 0x202E and not 0x2066 <= ord(c) <= 0x2069)
    return text[:limit].strip()


class _PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.blocked = [], []

    def handle_starttag(self, tag, attrs):
        if self.blocked or tag in {"head", "script", "style", "iframe", "object", "template", "svg"}:
            self.blocked.append(tag)
        elif tag in {"p", "br", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.blocked:
            if tag in self.blocked:
                index = len(self.blocked) - 1 - self.blocked[::-1].index(tag)
                del self.blocked[index:]
        elif tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.blocked:
            self.parts.append(data)


def _header(value):
    try:
        pieces = [chunk.decode(charset or "utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
                  for chunk, charset in decode_header(str(value or ""))]
    except (LookupError, ValueError):
        pieces = [str(value or "")]
    return _clean("".join(pieces), 1000)


def message_data(message, *, account=None, headers_only=False):
    """Return bounded plain text; never fetch remote resources or attachments."""
    payload = message.get("payload") or {}
    headers = {str(h.get("name", "")).lower(): _header(h.get("value", ""))
               for h in payload.get("headers", []) if isinstance(h, dict)}
    plain, rich, body_limits = [], [], []

    def visit(part, depth=0):
        if depth > 20 or part.get("filename"):
            return
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and mime in {"text/plain", "text/html"}:
            # Bodies are base64url in the Gmail full-message API. Large bodies
            # are truncated locally; attachment IDs are deliberately ignored.
            try:
                encoded = str(data)[:131072]
                if len(str(data)) > 131072:
                    body_limits.append(True)
                decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))[:65536]
                content_type = next((h.get("value", "") for h in part.get("headers", [])
                                     if str(h.get("name", "")).lower() == "content-type"), "")
                charset = re.search(r"charset=[\"']?([A-Za-z0-9_-]+)", content_type, re.I)
                try:
                    text = decoded.decode(charset.group(1) if charset else "utf-8", errors="replace")
                except LookupError:
                    text = decoded.decode("utf-8", errors="replace")
                if mime == "text/plain":
                    plain.append(text)
                else:
                    parser = _PlainHTML(); parser.feed(text); rich.append("".join(parser.parts))
            except (ValueError, TypeError):
                pass
        for child in part.get("parts", [])[:100]:
            if isinstance(child, dict):
                visit(child, depth + 1)

    if not headers_only:
        visit(payload)
    message_id = _message_id(message.get("id", ""))
    try:
        received = int(message.get("internalDate", 0))
    except (ValueError, TypeError):
        received = 0
    return dict(id=message_id, thread_id=_clean(message.get("threadId"), 128),
                sender=headers.get("from", "Unknown sender"), subject=headers.get("subject", "(No subject)"),
                date=headers.get("date", ""), received_ms=received,
                snippet="" if headers_only else _clean(html.unescape(message.get("snippet", "")), 1000),
                body=_clean("\n".join(plain or rich), 16000),
                body_truncated=bool(body_limits) or len("\n".join(plain or rich).strip()) > 16000,
                unread="UNREAD" in message.get("labelIds", []),
                source="gmail", untrusted=True,
                metadata_only=bool(headers_only),
                url="https://mail.google.com/mail/u/?" + urlencode({"authuser": account}) + "#inbox/" + message_id
                    if account else "https://mail.google.com/mail/#inbox/" + message_id)


def _message_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise GmailError("Invalid Gmail message ID.")
    return value


def _private_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".gmail-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _load_json(path, *, private=False):
    path = Path(path)
    if path.is_symlink():
        raise GmailError("Peter's Gmail files must be regular files, not symbolic links.")
    if private and os.name == "posix" and path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise GmailError("Protect Peter's Gmail token file with chmod 600 before using it.")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise GmailError("Peter's Gmail file could not be read. Check its path and JSON format.") from error
    if not isinstance(value, dict):
        raise GmailError("Peter's Gmail file must contain a JSON object.")
    return value


class GmailClient:
    def __init__(self, settings=None, *, root=None, session_factory=requests.Session,
                 clock=time.time, browser_open=webbrowser.open):
        self.settings = dict(settings or {})
        self.root = Path(root or DATA_DIR / ".runtime" / "gmail").expanduser()
        self.client_file = Path(os.environ.get("PETER_GMAIL_CLIENT_FILE") or self.settings.get("client_file")
                                or self.root / "client.json").expanduser()
        self.token_file = Path(os.environ.get("PETER_GMAIL_TOKEN_FILE") or self.settings.get("token_file")
                               or self.root / "token.json").expanduser()
        self.state_file = self.root / "sync.json"
        self._session_factory, self._clock, self._browser_open = session_factory, clock, browser_open
        self._lock = threading.RLock()
        self._token = None

    def _safe_files(self):
        for path in (self.client_file, self.token_file, self.state_file):
            if path.is_symlink():
                raise GmailError("Peter's Gmail files must be regular files, not symbolic links.")
            resolved = path.resolve()
            for project in {BASE_DIR.resolve(), DATA_DIR.resolve()}:
                if resolved.is_relative_to(project) and ".runtime" not in resolved.relative_to(project).parts:
                    raise GmailError("Keep Gmail credentials and sync data outside the app source, or inside its private .runtime/gmail folder.")

    def _client(self):
        self._safe_files()
        if not self.client_file.is_file():
            raise GmailError(SETUP_MESSAGE)
        client = _load_json(self.client_file).get("installed")
        if not isinstance(client, dict) or not str(client.get("client_id", "")).endswith(".apps.googleusercontent.com"):
            raise GmailError("Choose a Google Cloud OAuth client JSON of type Desktop app, with the Gmail API enabled.")
        if client.get("auth_uri", AUTH_URL) not in {AUTH_URL, "https://accounts.google.com/o/oauth2/auth"} or client.get("token_uri", TOKEN_URL) != TOKEN_URL:
            raise GmailError("The OAuth client must use Google's official authorization and token endpoints.")
        return client

    def _credentials(self):
        client = self._client()
        token = self._token or (_load_json(self.token_file, private=True) if self.token_file.is_file() else None)
        if not token or not token.get("account"):
            raise GmailError("Gmail is not signed in. Choose Connect Gmail in Settings.")
        if token.get("client_id") != client["client_id"] or set(str(token.get("scope", "")).split()) != {SCOPE}:
            raise GmailError("Peter needs its own read-only Gmail sign-in. Choose Connect Gmail again.")
        if not isinstance(token.get("account"), str) or "@" not in token["account"]:
            raise GmailError("Peter's Gmail account binding is invalid. Choose Connect Gmail again.")
        try:
            float(token.get("expires_at", 0))
        except (ValueError, TypeError):
            raise GmailError("Peter's Gmail token expiry is invalid. Choose Connect Gmail again.") from None
        self._token = token
        return client, token

    def status(self):
        if not self.settings.get("enabled", True):
            return dict(state="disabled", connected=False, email="", message="Gmail startup checks are disabled.", account="")
        try:
            client = self._client()
            if not self.token_file.is_file() and self._token is None:
                return dict(state="sign_in", connected=False, email="", message="Choose Connect Gmail to approve read-only inbox access.", account="")
            _, token = self._credentials()
            return dict(state="connected", connected=True, email=token["account"], message="Gmail signed in as " + token["account"], account=token["account"])
        except GmailError as error:
            return dict(state="setup_required" if not self.client_file.is_file() else "sign_in",
                        connected=False, email="", message=str(error), account="")

    def _json_request(self, session, method, url, *, cancel=None, **kwargs):
        _check(cancel)
        try:
            response = session.request(method, url, timeout=(5, 20), allow_redirects=False, stream=True, **kwargs)
            try:
                if not 200 <= response.status_code < 300:
                    raise GmailAPIError(response.status_code)
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    _check(cancel); size += len(chunk)
                    if size > 12 * 1024 * 1024:
                        raise GmailError("Gmail returned an unusually large message. The inbox checkpoint has not advanced.")
                    chunks.append(chunk)
                result = json.loads(b"".join(chunks))
            finally:
                response.close()
        except requests.RequestException as error:
            raise GmailError("Gmail could not be reached. Check the internet connection; the inbox checkpoint has not advanced.") from error
        except (ValueError, UnicodeError) as error:
            raise GmailError("Gmail returned invalid data. The inbox checkpoint has not advanced.") from error
        _check(cancel)
        if not isinstance(result, dict):
            raise GmailError("Gmail returned an invalid response.")
        return result

    def _token_exchange(self, session, client, data, *, cancel=None):
        form = dict(client_id=client["client_id"], **data)
        if client.get("client_secret"):
            form["client_secret"] = client["client_secret"]
        try:
            return self._json_request(session, "POST", TOKEN_URL, data=form, cancel=cancel)
        except GmailAPIError as error:
            raise GmailError("Google sign-in could not complete or has expired. Choose Connect Gmail again.") from error

    def _access(self, session, cancel=None, *, force=False):
        client, token = self._credentials()
        if force or not token.get("access_token") or float(token.get("expires_at", 0)) <= self._clock() + 60:
            if not token.get("refresh_token"):
                raise GmailError("Gmail sign-in expired. Choose Connect Gmail again.")
            fresh = self._token_exchange(session, client, dict(grant_type="refresh_token", refresh_token=token["refresh_token"]), cancel=cancel)
            if fresh.get("scope") and set(fresh["scope"].split()) != {SCOPE}:
                raise GmailError("Gmail granted an unexpected scope. Connect again with read-only access.")
            if not fresh.get("access_token"):
                raise GmailError("Google did not return a Gmail access token. Connect again.")
            token = dict(token, access_token=fresh["access_token"], refresh_token=fresh.get("refresh_token", token["refresh_token"]),
                         expires_at=self._clock() + int(fresh.get("expires_in", 3600)))
            _check(cancel); _private_json(self.token_file, token); self._token = token
        return token["access_token"]

    def _api(self, session, path, *, params=None, cancel=None):
        # This surface only implements GET. Sending, labels, deletion and draft
        # mutations are absent and the requested OAuth scope cannot permit them.
        access = self._access(session, cancel)
        try:
            return self._json_request(session, "GET", API_URL + path, params=params,
                                      headers={"Authorization": "Bearer " + access}, cancel=cancel)
        except GmailAPIError as error:
            if error.status != 401:
                raise
            access = self._access(session, cancel, force=True)
            return self._json_request(session, "GET", API_URL + path, params=params,
                                      headers={"Authorization": "Bearer " + access}, cancel=cancel)

    def connect(self, *, cancel=None, on_url=None, budget=180):
        """Authorize in the system browser; call from the controller's worker."""
        with self._lock:
            _check(cancel); client = self._client()
            verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(32)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            result = {}
            class Callback(BaseHTTPRequestHandler):
                def setup(self):
                    super().setup()
                    self.connection.settimeout(1)
                def log_message(self, *args):
                    pass  # OAuth codes and query strings must never enter logs.
                def do_GET(self):
                    parsed = urlsplit(self.path); query = parse_qs(parsed.query)
                    if parsed.path != "/" or len(query.get("state", [])) != 1 or not secrets.compare_digest(query["state"][0], state):
                        self.send_error(400, "Invalid sign-in response"); return
                    if "error" in query:
                        result["error"] = "Google sign-in was declined. Gmail remains disconnected."
                    elif len(query.get("code", [])) == 1:
                        result["code"] = query["code"][0]
                    else:
                        self.send_error(400, "Missing sign-in code"); return
                    body = b"Peter received the sign-in response. You may close this tab and return to Peter."
                    self.send_response(200); self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body)))
                    self.end_headers(); self.wfile.write(body)
            with HTTPServer(("127.0.0.1", 0), Callback) as server:
                server.timeout = 0.25
                redirect = f"http://127.0.0.1:{server.server_port}"
                url = AUTH_URL + "?" + urlencode(dict(client_id=client["client_id"], redirect_uri=redirect,
                    response_type="code", scope=SCOPE, state=state, code_challenge=challenge,
                    code_challenge_method="S256", access_type="offline", prompt="consent", include_granted_scopes="false"))
                if on_url:
                    on_url(url)
                self._browser_open(url)
                deadline = time.monotonic() + max(5, min(600, budget))
                while not result:
                    _check(cancel)
                    if time.monotonic() >= deadline:
                        raise GmailError("Google sign-in timed out. Choose Connect Gmail to try again.")
                    server.handle_request()
            if result.get("error"):
                raise GmailError(result["error"])
            with self._session_factory() as session:
                fresh = self._token_exchange(session, client, dict(code=result["code"], code_verifier=verifier,
                    grant_type="authorization_code", redirect_uri=redirect), cancel=cancel)
                if not fresh.get("refresh_token") or not fresh.get("access_token") or set(str(fresh.get("scope", "")).split()) != {SCOPE}:
                    raise GmailError("Approve Peter's read-only Gmail access in Google sign-in, then try Connect Gmail again.")
                profile = self._json_request(session, "GET", API_URL + "/profile",
                    headers={"Authorization": "Bearer " + fresh["access_token"]}, cancel=cancel)
                account = _clean(profile.get("emailAddress"), 320).lower()
                if not account or "@" not in account:
                    raise GmailError("Google did not identify the signed-in Gmail account.")
                token = dict(access_token=fresh["access_token"], refresh_token=fresh["refresh_token"],
                    expires_at=self._clock() + int(fresh.get("expires_in", 3600)), scope=SCOPE,
                    client_id=client["client_id"], account=account)
                _check(cancel); _private_json(self.token_file, token); self._token = token
            return self.status()

    def _profile(self, session, cancel):
        profile = self._api(session, "/profile", cancel=cancel)
        account = _clean(profile.get("emailAddress"), 320).lower()
        if account != self._credentials()[1]["account"]:
            raise GmailError("The Gmail account changed. Connect Gmail again before checking the inbox.")
        if not str(profile.get("historyId", "")).isdigit():
            raise GmailError("Gmail returned no valid inbox checkpoint.")
        return account, str(profile["historyId"])

    def _ids(self, session, *, query=None, cancel=None, limit=None):
        ids, page, pages = [], None, set()
        while True:
            params = {"labelIds": "INBOX", "maxResults": min(500, limit or 500), "includeSpamTrash": "false"}
            if query:
                params["q"] = query
            if page:
                params["pageToken"] = page
            result = self._api(session, "/messages", params=params, cancel=cancel)
            ids.extend(_message_id(m["id"]) for m in result.get("messages", []))
            ids = list(dict.fromkeys(ids))
            if limit and len(ids) >= limit:
                return ids[:limit]
            page = result.get("nextPageToken")
            if not page:
                return ids
            if page in pages:
                raise GmailError("Gmail repeated an inbox page. The checkpoint has not advanced.")
            pages.add(page)

    def _history(self, session, history_id, cancel):
        ids, pages, page = [], set(), None
        while True:
            params = {"startHistoryId": history_id, "maxResults": 500,
                      "historyTypes": ["messageAdded", "labelAdded"]}
            if page:
                params["pageToken"] = page
            result = self._api(session, "/history", params=params, cancel=cancel)
            for history in result.get("history", []):
                ids.extend(_message_id(item["message"]["id"]) for item in history.get("messagesAdded", []))
                ids.extend(_message_id(item["message"]["id"]) for item in history.get("labelsAdded", [])
                           if "INBOX" in item.get("labelIds", []))
            page = result.get("nextPageToken")
            if not page:
                cursor = str(result.get("historyId", ""))
                if not cursor.isdigit():
                    raise GmailError("Gmail returned no valid history checkpoint.")
                return list(dict.fromkeys(ids)), cursor
            if page in pages:
                raise GmailError("Gmail repeated a history page. The checkpoint has not advanced.")
            pages.add(page)

    def _read(self, session, message_id, cancel, *, headers_only=False):
        params = {"format": "metadata" if headers_only else "full"}
        if headers_only:
            params["metadataHeaders"] = ["From", "Subject", "Date"]
        message = self._api(session, "/messages/" + _message_id(message_id), params=params, cancel=cancel)
        return message, message_data(message, account=self._credentials()[1]["account"], headers_only=headers_only)

    def search(self, query="", *, limit=10, cancel=None):
        if not isinstance(query, str) or len(query) > 1000:
            raise GmailError("Enter a Gmail inbox search of at most 1,000 characters.")
        with self._lock, self._session_factory() as session:
            account, _ = self._profile(session, cancel)
            ids = self._ids(session, query=query.strip(), limit=max(1, min(50, int(limit))), cancel=cancel)
            messages = [self._read(session, message_id, cancel, headers_only=True)[1] for message_id in ids]
            return dict(account=account, messages=messages, source="gmail", untrusted=True)

    def read(self, message_id, *, cancel=None):
        with self._lock, self._session_factory() as session:
            account, _ = self._profile(session, cancel)
            message, data = self._read(session, message_id, cancel)
            if "INBOX" not in message.get("labelIds", []):
                raise GmailError("That message is no longer in the Gmail inbox.")
            return dict(account=account, message=data, source="gmail", untrusted=True)

    def sync_new(self, *, cancel=None, batch_size=50):
        """Return durable inbox headers; fetch a body only through explicit read()."""
        with self._lock, self._session_factory() as session:
            _check(cancel); account, profile_cursor = self._profile(session, cancel)
            state = _load_json(self.state_file, private=True) if self.state_file.is_file() else {"accounts": {}}
            accounts = state.setdefault("accounts", {})
            account_state = accounts.setdefault(account, {})
            if account_state.get("pending"):
                # Older installations may have fetched bodies for a startup
                # digest. Discard those before returning or replaying it.
                pending = account_state["pending"]
                changed = False
                for message in pending.get("messages", []):
                    if message.get("body") or message.get("snippet") or not message.get("metadata_only"):
                        message.update(body="", snippet="", body_truncated=False, metadata_only=True)
                        changed = True
                if changed:
                    _check(cancel); _private_json(self.state_file, state)
                return account_state["pending"]
            backlog = account_state.get("backlog")
            if not backlog:
                known = set(account_state.get("known_ids", []))
                if account_state.get("history_id"):
                    try:
                        candidates, next_cursor = self._history(session, account_state["history_id"], cancel)
                        candidates = [value for value in candidates if value not in known]
                        mode, census = "incremental", []
                    except GmailAPIError as error:
                        if error.status != 404:
                            raise
                        census = self._ids(session, cancel=cancel)
                        candidates = [value for value in census if value not in known]
                        next_cursor, mode = profile_cursor, "recovered"
                else:
                    # Census IDs, not old mail bodies. This permits an exact ID
                    # comparison if Gmail later expires its history cursor.
                    census = self._ids(session, cancel=cancel)
                    days = max(0, min(30, int(self.settings.get("initial_days", 1))))
                    candidates = self._ids(session, query="after:" + str(int(self._clock()) - days * 86400), cancel=cancel) if days else []
                    next_cursor, mode = profile_cursor, "initial"
                backlog = dict(ids=candidates, next_history_id=next_cursor, census=census, mode=mode,
                               started_at=int(self._clock()), previous_session=account_state.get("last_success"))
            ids = backlog["ids"][:max(1, min(100, int(batch_size)))]
            messages = []
            for message_id in ids:
                try:
                    raw, data = self._read(session, message_id, cancel, headers_only=True)
                except GmailAPIError as error:
                    if error.status == 404:
                        continue  # Mail permanently deleted since its history record.
                    raise
                if "INBOX" in raw.get("labelIds", []):
                    messages.append(data)
            messages.sort(key=lambda value: (value["received_ms"], value["id"]))
            remaining = backlog["ids"][len(ids):]
            pending = dict(batch_id=secrets.token_hex(16), account=account, messages=messages,
                           count=len(messages), has_more=bool(remaining), mode=backlog["mode"],
                           first_sync=backlog["mode"] == "initial", previous_session=backlog.get("previous_session"),
                           source="gmail", untrusted=True)
            account_state.update(backlog=dict(backlog, ids=remaining), pending=pending, pending_ids=ids)
            _check(cancel); _private_json(self.state_file, state)
            return pending

    def acknowledge(self, batch_id):
        """Commit only a delivered batch belonging to the active Gmail account."""
        with self._lock:
            account = self._credentials()[1]["account"]
            state = _load_json(self.state_file, private=True)
            value = state.get("accounts", {}).get(account, {})
            pending = value.get("pending")
            if not pending or not secrets.compare_digest(str(pending.get("batch_id", "")), str(batch_id)):
                raise GmailError("This Gmail batch is no longer pending for the connected account.")
            backlog = value["backlog"]
            known = set(value.get("known_ids", [])); known.update(value.pop("pending_ids", []))
            if not backlog["ids"]:
                known.update(backlog.get("census", []))
                value.update(history_id=backlog["next_history_id"], last_success=int(self._clock()))
                value.pop("backlog")
            value["known_ids"] = sorted(known)
            value.pop("pending")
            _private_json(self.state_file, state)
            return bool(backlog["ids"])


class GmailController:
    """One daemon worker; the UI consumes events and acknowledges saved digests."""
    def __init__(self, settings=None, *, root=None, client=None):
        self.client = client or GmailClient(settings, root=root)
        self.events, self._pending = queue.Queue(), queue.Queue()
        self._closed = threading.Event()
        self._cancellations = set()
        self._active, self._lock = False, threading.Lock()
        self._thread = threading.Thread(target=self._work, name="peter-gmail", daemon=True)
        self._thread.start()

    def _submit(self, operation, argument=None):
        with self._lock:
            if self._closed.is_set() or self._active and operation != "acknowledge":
                return False
            self._active = True
            cancel = threading.Event(); self._cancellations.add(cancel)
            self._pending.put((operation, argument, cancel))
            return True

    def startup(self):
        status = self.client.status()
        self.events.put(dict(type="gmail_status", **status))
        return self._submit("sync") if status["state"] == "connected" else False

    def sync(self):
        return self._submit("sync")

    def check(self):
        return self.sync()

    def connect(self, on_url=None):
        return self._submit("connect", on_url)

    def acknowledge(self, batch_id):
        return self._submit("acknowledge", batch_id)

    def _emit(self, cancel, **event):
        if not self._closed.is_set() and not cancel.is_set():
            self.events.put(event)

    def _work(self):
        while not self._closed.is_set():
            try:
                operation, argument, cancel = self._pending.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                _check(cancel)
                if operation == "connect":
                    self._emit(cancel, type="gmail_status", state="signing_in", message="Complete read-only Gmail sign-in in your browser.", account="")
                    def authorize(url):
                        self._emit(cancel, type="gmail_authorize", url=url)
                        if argument:
                            argument(url)
                    status = self.client.connect(cancel=cancel, on_url=authorize)
                    self._emit(cancel, type="gmail_status", **status)
                if operation == "acknowledge":
                    if not self.client.acknowledge(argument):
                        continue
                self._emit(cancel, type="gmail_status", state="checking", message="Checking Gmail inbox…", account="")
                result = self.client.sync_new(cancel=cancel)
                self._emit(cancel, type="gmail_summary", **result)
                self._emit(cancel, type="gmail_status", state="connected", message="Gmail inbox checked for " + result["account"], account=result["account"])
            except GmailCancelled:
                pass
            except (GmailError, OSError, ValueError) as error:
                self._emit(cancel, type="gmail_error", message=str(error))
                self._emit(cancel, type="gmail_status", state="error", message=str(error), account="")
            finally:
                with self._lock:
                    self._cancellations.discard(cancel)
                    self._active = not self._pending.empty()

    def cancel(self):
        with self._lock:
            for cancel in self._cancellations:
                cancel.set()

    def close(self):
        self._closed.set(); self.cancel(); self._thread.join(timeout=1)
