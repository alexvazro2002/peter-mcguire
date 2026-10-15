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
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_INLINE_ATTACHMENT_BYTES = 4 * 1024 * 1024


class GmailError(RuntimeError):
    pass


class GmailCancelled(GmailError):
    pass


class GmailAPIError(GmailError):
    def __init__(self, status, reason=""):
        self.status = status
        self.reason = reason if reason in {'SERVICE_DISABLED', 'accessNotConfigured', 'ACCESS_TOKEN_SCOPE_INSUFFICIENT', 'insufficientPermissions'} else ''
        message = {401: "Gmail sign-in expired. Choose Connect Gmail again.",
                   403: "Gmail access was refused. Enable the Gmail API and approve read-only access for this account.",
                   429: "Gmail is temporarily rate limited. Try the inbox check again later."}.get(status,
                   f"Gmail returned HTTP {status}. The inbox checkpoint has not advanced.")
        if self.reason in {'SERVICE_DISABLED', 'accessNotConfigured'}:
            message = "The Gmail API is disabled for Peter's Google Cloud project. Enable Gmail API there, then choose Connect Gmail again."
        elif self.reason in {'ACCESS_TOKEN_SCOPE_INSUFFICIENT', 'insufficientPermissions'}:
            message = "Google did not grant Gmail reading access. Choose Connect Gmail and approve only read-only access."
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


def _header(value, limit=1000):
    try:
        pieces = [chunk.decode(charset or "utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
                  for chunk, charset in decode_header(str(value or ""))]
    except (LookupError, ValueError):
        pieces = [str(value or "")]
    return _clean("".join(pieces), limit)


def message_data(message, *, account=None, headers_only=False):
    """Return bounded text and an honest MIME inventory, without fetching files."""
    raw_payload = message.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    raw_headers = payload.get("headers", [])
    participant_headers_complete = recipient_headers_complete = isinstance(raw_payload, dict) and isinstance(raw_headers, list)
    headers = {}
    for row in raw_headers if isinstance(raw_headers, list) else []:
        if not isinstance(row, dict):
            participant_headers_complete = recipient_headers_complete = False
            continue
        name = str(row.get("name", "")).lower()
        limit = 16000 if name in {"from", "to", "cc"} else 1000
        value = _header(row.get("value", ""), limit + 1)
        if name in {"from", "to", "cc"} and headers.get(name):
            value = headers[name] + ", " + value
        if len(value) > limit:
            if name in {"from", "cc"}:
                participant_headers_complete = False
            if name in {"to", "cc"}:
                recipient_headers_complete = False
        headers[name] = value[:limit]
    plain, rich, body_limits, attachments = [], [], [], []
    inventory_complete, parts_seen, inline_bytes = not headers_only and isinstance(raw_payload, dict), 0, 0
    body_unavailable = False

    def visit(part, depth=0, inside_file=False):
        nonlocal inventory_complete, parts_seen, inline_bytes, body_unavailable
        parts_seen += 1
        if depth > 20 or parts_seen > 1000 or not isinstance(part, dict):
            inventory_complete = False
            return
        mime = _clean(part.get("mimeType", ""), 200).lower()
        body = part.get("body") or {}
        if not isinstance(body, dict):
            body = {}; inventory_complete = False
        data, attachment_id = body.get("data"), body.get("attachmentId")
        filename = _header(part.get("filename", ""))
        disposition = next((str(h.get("value", "")) for h in part.get("headers", [])
                            if isinstance(h, dict) and str(h.get("name", "")).lower() == "content-disposition"), "")
        is_file = bool(filename or re.match(r"attachment(?:\s|;|$)", disposition, re.I)
                       or mime and not mime.startswith("multipart/") and mime not in {"text/plain", "text/html"}
                       and (data or attachment_id))
        if is_file:
            if len(attachments) >= 100:
                inventory_complete = False
            else:
                try:
                    size = max(0, int(body.get("size", 0)))
                except (ValueError, TypeError):
                    size = 0; inventory_complete = False
                item = dict(part_id=_clean(part.get("partId", ""), 128), filename=filename,
                            mime_type=mime, size=size, attachment_id="", inline_data="",
                            inline_data_truncated=False)
                if attachment_id:
                    try:
                        item["attachment_id"] = _attachment_id(attachment_id)
                    except GmailError:
                        inventory_complete = False
                if data:
                    # Keep inline file bytes available for the private review
                    # cache; never confuse a truncated representation with a
                    # complete downloadable attachment.
                    room = MAX_INLINE_ATTACHMENT_BYTES - inline_bytes
                    if isinstance(data, str) and len(data) <= (room + 2) // 3 * 4:
                        try:
                            decoded = _attachment_bytes(data, room)
                            item["inline_data"] = data
                            inline_bytes += len(decoded)
                        except GmailError:
                            item["inline_data_truncated"] = True
                    else:
                        item["inline_data_truncated"] = True
                attachments.append(item)
        elif not inside_file and mime in {"text/plain", "text/html"} and attachment_id and not data:
            body_unavailable = True; body_limits.append(True)
        if not is_file and not inside_file and data and mime in {"text/plain", "text/html"}:
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
        children = part.get("parts", [])
        if not isinstance(children, list):
            inventory_complete = False; children = []
        if len(children) > 100:
            inventory_complete = False
        for child in children[:100]:
            if isinstance(child, dict):
                visit(child, depth + 1, inside_file=inside_file or is_file)
            else:
                inventory_complete = False

    if not headers_only:
        visit(payload)
    message_id = _message_id(message.get("id", ""))
    try:
        received = int(message.get("internalDate", 0))
    except (ValueError, TypeError):
        received = 0
    view = next((name.casefold() for name in ('INBOX', 'SPAM', 'TRASH') if name in message.get('labelIds', [])), 'all')
    return dict(id=message_id, thread_id=_clean(message.get("threadId"), 128),
                sender=headers.get("from", "Unknown sender"), to=headers.get("to", ""), cc=headers.get("cc", ""),
                participant_headers_complete=participant_headers_complete,
                recipient_headers_complete=recipient_headers_complete,
                account=account or "", subject=headers.get("subject", "(No subject)"),
                date=headers.get("date", ""), received_ms=received,
                snippet="" if headers_only else _clean(html.unescape(message.get("snippet", "")), 1000),
                body=_clean("\n".join(plain or rich), 16000),
                body_truncated=bool(body_limits) or len("\n".join(plain or rich).strip()) > 16000,
                body_unavailable=body_unavailable,
                attachments=attachments, attachment_inventory_complete=inventory_complete,
                attachment_inventory_truncated=not headers_only and not inventory_complete,
                unread="UNREAD" in message.get("labelIds", []),
                folders=[name for name in ("INBOX", "SPAM", "TRASH", "SENT", "DRAFT") if name in message.get("labelIds", [])],
                source="gmail", untrusted=True,
                metadata_only=bool(headers_only),
                url="https://mail.google.com/mail/u/?" + urlencode({"authuser": account}) + '#' + view + '/' + message_id
                    if account else 'https://mail.google.com/mail/#' + view + '/' + message_id)


def _message_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise GmailError("Invalid Gmail message ID.")
    return value


def _attachment_id(value):
    # Attachment IDs are longer opaque base64url identifiers, unlike the short
    # hexadecimal message ID. Do not allow path/query syntax into API URLs.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", value):
        raise GmailError("Invalid Gmail attachment ID.")
    return value


def _attachment_bytes(data, max_bytes):
    if not isinstance(data, str) or len(data) > ((max_bytes + 2) // 3) * 4:
        raise GmailError("The Gmail attachment exceeds the allowed download size.")
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]*={0,2}", data) or len(data.rstrip("=")) % 4 == 1:
            raise ValueError("Invalid base64url")
        decoded = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as error:
        raise GmailError("Gmail returned invalid attachment data.") from error
    if len(decoded) > max_bytes:
        raise GmailError("The Gmail attachment exceeds the allowed download size.")
    return decoded


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
        self._profile_totals = {}

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

    def _json_request(self, session, method, url, *, cancel=None, max_response_bytes=12 * 1024 * 1024, **kwargs):
        _check(cancel)
        try:
            response = session.request(method, url, timeout=(5, 20), allow_redirects=False, stream=True, **kwargs)
            try:
                if not 200 <= response.status_code < 300:
                    error_data, size = [], 0
                    for chunk in response.iter_content(8192):
                        _check(cancel); size += len(chunk)
                        if size > 65536:
                            break
                        error_data.append(chunk)
                    reason = ''
                    try:
                        detail = json.loads(b''.join(error_data)).get('error', {})
                        if isinstance(detail, dict):
                            reasons = [row.get('reason', '') for row in detail.get('errors', []) + detail.get('details', []) if isinstance(row, dict)]
                            reason = next((value for value in reasons if value in {'SERVICE_DISABLED', 'accessNotConfigured', 'ACCESS_TOKEN_SCOPE_INSUFFICIENT', 'insufficientPermissions'}), '')
                    except (ValueError, AttributeError, TypeError):
                        pass
                    raise GmailAPIError(response.status_code, reason)
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    _check(cancel); size += len(chunk)
                    if size > max_response_bytes:
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

    def _api(self, session, path, *, params=None, cancel=None, max_response_bytes=12 * 1024 * 1024):
        # This surface only implements GET. Sending, labels, deletion and draft
        # mutations are absent and the requested OAuth scope cannot permit them.
        access = self._access(session, cancel)
        try:
            return self._json_request(session, "GET", API_URL + path, params=params,
                                      headers={"Authorization": "Bearer " + access}, cancel=cancel,
                                      max_response_bytes=max_response_bytes)
        except GmailAPIError as error:
            if error.status != 401:
                raise
            access = self._access(session, cancel, force=True)
            return self._json_request(session, "GET", API_URL + path, params=params,
                                      headers={"Authorization": "Bearer " + access}, cancel=cancel,
                                      max_response_bytes=max_response_bytes)

    def connect(self, *, cancel=None, on_url=None, budget=180):
        """Authorize in the system browser; call from the controller's worker."""
        with self._lock:
            _check(cancel); client = self._client()
            verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(32)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            result = {}
            owner = self
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
                        try:
                            result['status'] = owner._finish_sign_in(client, query['code'][0], verifier, redirect, cancel)
                        except (GmailError, OSError, ValueError) as error:
                            result['error'] = str(error) if isinstance(error, GmailError) else 'Peter could not save the Gmail connection. Check the local runtime folder.'
                    else:
                        self.send_error(400, "Missing sign-in code"); return
                    body = ("Peter is connected to Gmail with read-only access. You may close this tab and return to Peter."
                            if result.get('status') else "Peter could not connect to Gmail. " + result.get('error', 'Try Connect Gmail again.')).encode('utf-8')
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
            return result['status']

    def _finish_sign_in(self, client, code, verifier, redirect, cancel):
        """Verify Gmail and persist its account-bound token before browser success."""
        with self._session_factory() as session:
            fresh = self._token_exchange(session, client, dict(code=code, code_verifier=verifier,
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
        self._profile_totals = {name: profile[name] for name in ('messagesTotal', 'threadsTotal')
                                if type(profile.get(name)) is int and profile[name] >= 0}
        return account, str(profile["historyId"])

    def _ids(self, session, *, query=None, cancel=None, limit=None, scope="inbox"):
        if scope not in {"inbox", "all"}:
            raise GmailError("Choose inbox or all mail for the Gmail search scope.")
        ids, page, pages = [], None, set()
        while True:
            params = {"maxResults": min(500, limit or 500), "includeSpamTrash": "true" if scope == "all" else "false"}
            if scope == "inbox":
                params["labelIds"] = "INBOX"
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
            params["metadataHeaders"] = ["From", "To", "Cc", "Subject", "Date"]
        message = self._api(session, "/messages/" + _message_id(message_id), params=params, cancel=cancel)
        return message, message_data(message, account=self._credentials()[1]["account"], headers_only=headers_only)

    def search(self, query="", *, limit=10, cancel=None, scope="inbox"):
        if not isinstance(query, str) or len(query) > 1000:
            raise GmailError("Enter a Gmail inbox search of at most 1,000 characters.")
        with self._lock, self._session_factory() as session:
            account, _ = self._profile(session, cancel)
            ids = self._ids(session, query=query.strip(), limit=max(1, min(50, int(limit))), cancel=cancel, scope=scope)
            messages = [self._read(session, message_id, cancel, headers_only=True)[1] for message_id in ids]
            messages.sort(key=lambda row: row['received_ms'], reverse=True)
            return dict(account=account, messages=messages, scope=scope,
                        account_messages_total=self._profile_totals.get('messagesTotal'), source="gmail", untrusted=True)

    def list_headers(self, query, *, limit=None, cancel=None, scope="all"):
        """Enumerate a complete requested set of received headers, including all API pages.

        A finite limit means the user's requested recent count. None exhausts a
        bounded date query; this operation never fetches bodies or changes mail.
        """
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise GmailError('A subject list needs a bounded Gmail query.')
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000):
            raise GmailError('Choose between 1 and 1,000 recent email subjects.')
        if limit is None and not re.search(r'\bafter:\d+', query):
            raise GmailError('Choose a date range before listing every email subject.')
        with self._lock, self._session_factory() as session:
            account, _ = self._profile(session, cancel)
            ids = self._ids(session, query=query.strip(), limit=limit or 0, cancel=cancel, scope=scope)
            messages = [self._read(session, message_id, cancel, headers_only=True)[1] for message_id in ids]
            messages.sort(key=lambda row: row['received_ms'], reverse=True)
            _check(cancel)
            return dict(account=account, messages=messages, scope=scope, complete=True, metadata_only=True,
                        source='gmail', untrusted=True)

    def latest(self, *, scope="all", cancel=None):
        """Newest received mail, including archived mail, without an invented date."""
        return self.search("-in:sent -in:drafts", limit=1, scope=scope, cancel=cancel)

    def read(self, message_id, *, cancel=None):
        with self._lock, self._session_factory() as session:
            account, _ = self._profile(session, cancel)
            message, data = self._read(session, message_id, cancel)
            return dict(account=account, message=data, source="gmail", untrusted=True)

    def read_attachment(self, message_id, attachment_id, *, max_bytes=MAX_ATTACHMENT_BYTES, cancel=None):
        """Fetch account-bound bytes with read-only GET; callers own safe storage."""
        message_id, attachment_id = _message_id(message_id), _attachment_id(attachment_id)
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_ATTACHMENT_BYTES:
            raise GmailError("Choose an attachment size limit between 1 byte and 20 MiB.")
        with self._lock, self._session_factory() as session:
            self._profile(session, cancel)
            result = self._api(session, "/messages/" + message_id + "/attachments/" + attachment_id,
                               cancel=cancel, max_response_bytes=((max_bytes + 2) // 3) * 4 + 65536)
            size = result.get("size")
            if type(size) is not int or size < 0:
                raise GmailError("Gmail returned an invalid attachment size.")
            if size > max_bytes:
                raise GmailError("The Gmail attachment exceeds the allowed download size.")
            decoded = _attachment_bytes(result.get("data"), max_bytes)
            if len(decoded) != size:
                raise GmailError("Gmail returned incomplete attachment data.")
            _check(cancel)
            return decoded

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
