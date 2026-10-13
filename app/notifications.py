"""Optional ntfy publishing off the UI thread; answer contents are private by default."""
from __future__ import annotations

import os
import queue
import re
import threading
from urllib.parse import urlparse


def topic_url(settings):
    server = (settings.get("server_url") or "").strip().rstrip("/")
    url = urlparse(server)
    if (url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})
            or not url.hostname or url.username or url.password or url.query or url.fragment):
        raise ValueError("Use an HTTPS ntfy server, or a local HTTP server. Credentials belong in NEXUS_NTFY_TOKEN.")
    if not re.fullmatch(r"[-_A-Za-z0-9]{1,64}", settings.get("topic", "")):
        raise ValueError("ntfy topic must contain 1-64 letters, numbers, underscores or dashes.")
    return server + "/" + settings["topic"]


def publish(settings, section, answer):
    import requests
    url = topic_url(settings)
    title = "Nexus Desk - " + section
    message = answer[:800] if settings.get("include_answer") else section + " has finished answering. Open Nexus Desk to read the response."
    headers = {"Title": title, "Content-Type": "text/plain; charset=utf-8", "Priority": "default"}
    token = os.environ.get("NEXUS_NTFY_TOKEN", "")
    if token:
        headers["Authorization"] = "Bearer " + token
    response = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=(3, 5), allow_redirects=False)
    if 300 <= response.status_code < 400:
        raise ValueError("ntfy redirects are not followed. Set the final server URL in Settings.")
    response.raise_for_status()


class Notifier:
    def __init__(self):
        self.jobs, self.events = queue.Queue(maxsize=32), queue.Queue()
        self.closed = False
        self.thread = threading.Thread(target=self._work, daemon=True)
        self.thread.start()

    def send(self, settings, section, answer):
        if not settings.get("enabled") or self.closed:
            return
        try:
            self.jobs.put_nowait((dict(settings), section, answer))
        except queue.Full:
            self.events.put("Notification queue is full. Your AI answer was still saved.")

    def _work(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            try:
                publish(*job)
            except Exception:
                # Do not leak topic, credentials or answer text through error logs.
                self.events.put("ntfy notification could not be delivered. Check server, topic and authorization in Settings. Your AI answer is unaffected.")

    def close(self):
        self.closed = True
        while True:
            try:
                self.jobs.get_nowait()
            except queue.Empty:
                break
        self.jobs.put_nowait(None)
