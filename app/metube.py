"""Native adapter for the installed MeTube HTTP API (no download engine here).

Verified with MeTube 2026.09.29: GET history, POST add/retry/delete/start.
All requests remain on a registered loopback service; media URLs are passed to
MeTube, whose yt-dlp backend enforces its own extractor and network policy.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import threading
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import requests

from app.service_status import safe_text

VIDEO_QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360", "240", "worst")
FORMATS = {
    "video": {"mp4": VIDEO_QUALITIES, "any": VIDEO_QUALITIES},
    "audio": {"mp3": ("best", "320", "192", "128"), "m4a": ("best", "192", "128"),
              "opus": ("best",), "wav": ("best",), "flac": ("best",)},
}
MEDIA_SUFFIXES = frozenset({".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv", ".3gp", ".ogv",
                            ".mp3", ".m4a", ".opus", ".wav", ".flac", ".ogg", ".aac", ".wma", ".aiff"})


class MeTubeError(RuntimeError):
    pass


class MeTubeClient:
    def __init__(self, url, download_root=None, audio_download_root=None, timeout=5.0):
        parts = urlsplit(str(url))
        if (parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parts.username or parts.password or parts.query or parts.fragment):
            raise ValueError("MeTube must use a local HTTP service address without credentials.")
        self.url = str(url).rstrip("/") + "/"
        self.download_root = Path(download_root).resolve() if download_root else None
        self.audio_download_root = Path(audio_download_root).resolve() if audio_download_root else self.download_root
        self.timeout = max(0.2, min(float(timeout), 15.0))
        self._lock = threading.Lock()
        self._items = {}

    @staticmethod
    def formats(media=None):
        if media is not None and media not in FORMATS:
            raise ValueError("Choose video or audio.")
        values = {key: [{"value": fmt, "label": "Any / best available" if fmt == "any" else fmt.upper(),
                         "qualities": list(qualities)} for fmt, qualities in choices.items()]
                  for key, choices in FORMATS.items()}
        return values[media] if media else values

    def _request(self, endpoint, payload=None, timeout=None):
        session = requests.Session()
        session.trust_env = False
        try:
            method = "GET" if payload is None else "POST"
            with session.request(method, self.url + endpoint, json=payload,
                                 timeout=timeout or self.timeout, allow_redirects=False, stream=True) as response:
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > 8 * 1024 * 1024:
                        raise MeTubeError("MeTube returned more history than can be displayed safely.")
                try:
                    result = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    result = None
                if not 200 <= response.status_code < 300:
                    message = safe_text(result.get("msg", "")) if isinstance(result, dict) else ""
                    raise MeTubeError(message or f"MeTube returned HTTP {response.status_code}. Check its service status.")
                if not isinstance(result, dict):
                    raise MeTubeError("MeTube returned an unsupported response. Check the installed service version.")
                if result.get("status") == "error":
                    raise MeTubeError(safe_text(result.get("msg")) or "MeTube could not complete this action.")
                return result
        except requests.Timeout as error:
            raise MeTubeError("MeTube did not respond in time. Check the queue before submitting again.") from error
        except requests.RequestException as error:
            raise MeTubeError("Could not reach MeTube. Start the service and try again.") from error
        finally:
            session.close()

    @staticmethod
    def _number(value, default=None):
        try:
            number = float(value)
            return number if math.isfinite(number) else default
        except (ValueError, TypeError):
            return default

    def _public_item(self, item, group):
        status = str(item.get("status") or ("finished" if group == "done" else group))
        if status not in {"pending", "queued", "preparing", "downloading", "postprocessing", "finished", "error", "scheduled"}:
            status = "unknown"
        progress = self._number(item.get("percent"))
        if progress is not None:
            progress = max(0.0, min(100.0, progress))
        error = safe_text(item.get("msg") or item.get("error")) if status == "error" else ""
        timestamp = self._number(item.get("timestamp"))
        # MeTube DownloadInfo uses time.time_ns(); tolerate older integrations
        # which supplied milliseconds or seconds and expose seconds consistently.
        if timestamp is not None:
            if timestamp > 1e17:
                timestamp /= 1e9
            elif timestamp > 1e14:
                timestamp /= 1e6
            elif timestamp > 1e11:
                timestamp /= 1e3
        result = {"id": str(item.get("id", "")), "title": safe_text(item.get("title") or "Untitled download", 500),
                  "status": status, "percent": progress, "speed": self._number(item.get("speed")),
                  "eta": self._number(item.get("eta")), "size": self._number(item.get("size")),
                  "timestamp": timestamp, "error": error,
                  "format": safe_text(item.get("format"), 20), "quality": safe_text(item.get("quality"), 20),
                  "media": "audio" if item.get("download_type") == "audio" or item.get("format") in FORMATS["audio"] else "video",
                  "can_retry": group == "done" and status == "error",
                  "can_cancel": group == "queue", "can_start": group == "pending",
                  "can_open": False, "can_open_folder": False}
        # No remote URL, cookie configuration, yt-dlp options, or absolute host
        # paths are forwarded into the browser-facing queue/history objects.
        if group == "done" and status == "finished":
            try:
                path = self._item_path(item)
                result["can_open_folder"] = path.parent.is_dir()
                result["can_open"] = path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
                result["filename"] = path.name
            except (ValueError, OSError):
                pass
        return result

    def snapshot(self):
        raw = self._request("history")
        result = {}
        items = {}
        for group in ("queue", "pending", "done"):
            entries = raw.get(group, [])
            # Older queue serialisers can emit id/info pairs; tolerate those
            # while keeping one stable frontend shape.
            if isinstance(entries, dict):
                entries = list(entries.values())
            if not isinstance(entries, list):
                raise MeTubeError("MeTube history has an unsupported shape.")
            normalized = []
            for item in entries:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    item = item[1]
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    continue
                normalized.append(self._public_item(item, group))
                items[item["id"]] = (dict(item), group)
            if group == "done":
                normalized.sort(key=lambda item: item["timestamp"] or 0, reverse=True)
            result[group] = normalized
        with self._lock:
            self._items = items
        return result

    def submit(self, url, media="video", format="mp4", quality="best"):
        value = str(url or "").strip()
        if len(value) > 8192 or re.search(r"[\x00-\x20]", value):
            raise ValueError("Enter one valid HTTP or HTTPS media URL.")
        try:
            parts = urlsplit(value)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
                raise ValueError
            parts.port  # Reject malformed ports before handing the URL to MeTube.
        except ValueError as error:
            raise ValueError("Enter an HTTP or HTTPS media URL without embedded credentials.") from error
        if media not in FORMATS or format not in FORMATS[media] or quality not in FORMATS[media][format]:
            raise ValueError("Choose an available format and quality for this media type.")
        # Both current and legacy MeTube accept these common formats. The
        # download_type field makes the intent explicit for current versions.
        result = self._request("add", {"url": value, "download_type": media, "codec": "auto", "format": format,
                                       "quality": quality, "auto_start": True}, timeout=45.0)
        return {"status": "ok", "message": safe_text(result.get("msg")) or "Download submitted to MeTube."}

    def _get_item(self, identifier):
        if not isinstance(identifier, str) or not identifier or len(identifier) > 1000:
            raise ValueError("Choose a download from the queue or history.")
        # Refresh before a mutating action so a stale browser cannot cancel a
        # completed item, retry a deleted item, or open a supplied filesystem path.
        self.snapshot()
        with self._lock:
            entry = self._items.get(identifier)
        if not entry:
            raise ValueError("This download is no longer in MeTube's queue or history.")
        return entry

    def retry(self, identifier):
        item, group = self._get_item(identifier)
        if group != "done" or item.get("status") != "error":
            raise ValueError("Only failed downloads can be retried.")
        self._request("retry", {"id": identifier}, timeout=45.0)
        return {"status": "ok", "message": "Retry submitted."}

    def cancel(self, identifier):
        _item, group = self._get_item(identifier)
        if group != "queue":
            raise ValueError("Only active or queued downloads can be cancelled.")
        self._request("delete", {"ids": [identifier], "where": "queue", "delete_files": False})
        return {"status": "ok", "message": "Download cancelled."}

    def start_pending(self, identifier):
        _item, group = self._get_item(identifier)
        if group != "pending":
            raise ValueError("This download is not waiting to start.")
        self._request("start", {"ids": [identifier]})
        return {"status": "ok", "message": "Pending download started."}

    def _item_path(self, item):
        audio = item.get("download_type") == "audio" or item.get("format") in FORMATS["audio"]
        root = self.audio_download_root if audio else self.download_root
        if root is None:
            raise ValueError("This container uses storage that cannot be opened from the host.")
        filename = str(item.get("filename") or "")
        # MeTube returns a path relative to its configured DOWNLOAD_DIR, already
        # including the selected folder. Never append the folder a second time.
        relative = PurePosixPath(filename.replace("\\", "/"))
        if not filename or relative.is_absolute() or any(part in {"..", ".state"} for part in relative.parts) or ":" in filename:
            raise ValueError("The download has no safe local file path.")
        path = root.joinpath(*relative.parts).resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError("The download path is outside the registered downloads folder.")
        return path

    def open_download(self, identifier, folder=False):
        item, group = self._get_item(identifier)
        if group != "done" or item.get("status") != "finished":
            raise ValueError("Only completed downloads can be opened.")
        path = self._item_path(item)
        if folder:
            target = path.parent
            if not target.is_dir():
                raise ValueError("The download folder is no longer available.")
        else:
            target = path
            if path.suffix.lower() not in MEDIA_SUFFIXES or not path.is_file():
                raise ValueError("The media file is no longer available or cannot be opened safely.")
        if os.name == "nt":
            os.startfile(str(target))
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"status": "ok", "message": "Folder opened." if folder else "Media file opened."}
