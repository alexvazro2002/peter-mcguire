import json
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.metube import MeTubeClient, MeTubeError


@pytest.fixture
def metube_server(tmp_path):
    state = {"queue": [], "pending": [], "done": [], "requests": [], "failure": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, payload, code=200):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if state["failure"]:
                return self.reply({"msg": "backend token=super-secret is unavailable"}, 503)
            self.reply({key: deepcopy(state[key]) for key in ("queue", "pending", "done")})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.path, body))
            if self.path == "/add":
                state["queue"].append({"id": "download-1", "title": "Test clip", "status": "queued",
                                       "format": body["format"], "download_type": body["download_type"],
                                       "quality": body["quality"], "url": body["url"]})
            elif self.path == "/retry":
                item = next(item for item in state["done"] if item["id"] == body["id"])
                state["done"].remove(item)
                state["queue"].append(dict(item, status="queued", error=None, msg=None))
            elif self.path == "/delete":
                assert body["where"] == "queue" and body["delete_files"] is False
                state["queue"] = [item for item in state["queue"] if item["id"] not in body["ids"]]
            elif self.path == "/start":
                item = state["pending"].pop()
                state["queue"].append(dict(item, status="queued"))
            self.reply({"status": "ok"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = MeTubeClient(f"http://127.0.0.1:{server.server_port}", tmp_path)
    yield client, state, tmp_path
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_real_http_submission_progress_completion_and_failure_retry_cancel(metube_server):
    client, state, directory = metube_server
    assert client.snapshot() == {"queue": [], "pending": [], "done": []}
    assert client.submit("https://example.com/test-media.mp4")["status"] == "ok"
    submitted = state["requests"][-1]
    assert submitted[0] == "/add" and submitted[1]["auto_start"] is True
    assert client.snapshot()["queue"][0]["status"] == "queued"
    state["queue"][0].update(status="downloading", percent=25.5, speed=240000, eta=17)
    progress = client.snapshot()["queue"][0]
    assert progress["percent"] == 25.5 and progress["eta"] == 17 and progress["can_cancel"]
    (directory / "clip.mp4").write_bytes(b"fixture data, no downloaded media")
    completed = state["queue"].pop()
    state["done"].append(dict(completed, status="finished", filename="clip.mp4"))
    item = client.snapshot()["done"][0]
    assert item["can_open"] and item["can_open_folder"] and not item["can_retry"]
    state["done"][0].update(status="error", msg="Extractor failed token=private-token")
    item = client.snapshot()["done"][0]
    assert item["can_retry"] and "private-token" not in item["error"]
    client.retry(item["id"])
    assert state["requests"][-1] == ("/retry", {"id": "download-1"})
    assert client.snapshot()["queue"][0]["status"] == "queued"
    client.cancel("download-1")
    assert not client.snapshot()["queue"]


def test_audio_formats_and_pending_downloads(metube_server):
    client, state, _ = metube_server
    client.submit("https://example.com/audio", "audio", "mp3", "192")
    assert state["requests"][-1][1]["download_type"] == "audio"
    state["pending"] = [dict(state["queue"].pop(), status="pending")]
    assert client.snapshot()["pending"][0]["can_start"]
    client.start_pending("download-1")
    assert state["requests"][-1] == ("/start", {"ids": ["download-1"]})
    assert not client.snapshot()["pending"]
    assert {item["value"] for item in client.formats("audio")} == {"mp3", "m4a", "opus", "wav", "flac"}


def test_service_failure_is_clear_and_credentials_are_removed(metube_server):
    client, state, _ = metube_server
    state["failure"] = True
    with pytest.raises(MeTubeError, match="unavailable") as failure:
        client.snapshot()
    assert "super-secret" not in str(failure.value)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://alice:secret@example.com/", "javascript:alert(1)",
                                "https://", "https://example.com/a\nb", "https://example.com:bad/video"])
def test_bad_media_urls_do_not_reach_download_engine(metube_server, url):
    client, state, _ = metube_server
    with pytest.raises(ValueError):
        client.submit(url)
    assert not state["requests"]


@pytest.mark.parametrize("url", ["https://127.0.0.1:8082", "http://example.com", "http://user:password@127.0.0.1",
                                "http://127.0.0.1/?token=secret"])
def test_service_address_must_be_local_without_credentials(url):
    with pytest.raises(ValueError):
        MeTubeClient(url)


def test_no_unsupported_quality_or_arbitrary_ytdlp_arguments(metube_server):
    client, state, _ = metube_server
    with pytest.raises(ValueError):
        client.submit("https://example.com/a", "audio", "flac", "320")
    with pytest.raises(ValueError):
        client.submit("https://example.com/a", "video", "custom:exec=bad", "best")
    assert not state["requests"]


@pytest.mark.parametrize("filename", ["../other.mp4", "/outside.mp4", "C:\\secret.mp4", ".state/queue", "safe.mp4:payload.exe"])
def test_download_path_traversal_rejected(metube_server, filename):
    client, state, _ = metube_server
    state["done"] = [{"id": "d", "title": "A", "status": "finished", "filename": filename}]
    assert not client.snapshot()["done"][0]["can_open"]
    with pytest.raises(ValueError):
        client.open_download("d")


def test_only_completed_media_can_open_and_browser_fields_are_allowlisted(metube_server, monkeypatch):
    client, state, directory = metube_server
    (directory / "media").mkdir()
    (directory / "media" / "clip.mp4").write_bytes(b"test")
    state["done"] = [{"id": "d", "title": "A", "status": "finished", "filename": "media/clip.mp4",
                      "folder": "media", "url": "https://example.com?token=secret", "video_password": "bad",
                      "ytdl_options_overrides": {"password": "secret"}}]
    item = client.snapshot()["done"][0]
    assert item["can_open"] and item["filename"] == "clip.mp4"
    assert "secret" not in json.dumps(item) and "video_password" not in item
    calls = []
    monkeypatch.setattr("app.metube.os.startfile", lambda value: calls.append(value), raising=False)
    monkeypatch.setattr("app.metube.subprocess.Popen", lambda values, **kwargs: calls.append(values[-1]))
    client.open_download("d")
    client.open_download("d", folder=True)
    assert calls == [str(directory / "media" / "clip.mp4"), str(directory / "media")]
    (directory / "media" / "bad.exe").write_bytes(b"test")
    state["done"][0]["filename"] = "media/bad.exe"
    assert not client.snapshot()["done"][0]["can_open"]
    with pytest.raises(ValueError):
        client.open_download("d")


def test_stale_actions_do_not_mutate_completed_or_missing_items(metube_server):
    client, state, _ = metube_server
    state["done"] = [{"id": "d", "title": "A", "status": "finished"}]
    for action, identifier in ((client.cancel, "d"), (client.retry, "d"), (client.open_download, "missing")):
        with pytest.raises(ValueError):
            action(identifier)
    assert not state["requests"]


@pytest.mark.parametrize("timestamp", [1720000000, 1720000000000, 1720000000000000, 1720000000000000000])
def test_history_timestamps_are_seconds_for_browser_dates(metube_server, timestamp):
    client, state, _ = metube_server
    state["done"] = [{"id": "d", "title": "Completed", "status": "finished", "timestamp": timestamp}]
    assert client.snapshot()["done"][0]["timestamp"] == 1720000000
