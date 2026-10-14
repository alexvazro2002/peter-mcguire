"""Exercise real loopback HTTP, API identity, cancellation and temporary audio."""
import io
import json
import queue
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.voice import VoiceController, local_voice_url, spoken_text
from scripts.voice_service import runtime_command, service_environment


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 1600)
    return buffer.getvalue()


@pytest.fixture
def speech_server():
    capture = {"requests": [], "installed": True, "audio": wav_bytes(), "paths": True,
               "speech_started": threading.Event(), "speech_release": threading.Event()}
    capture["speech_release"].set()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, body, content_type="application/json", status=200):
            if isinstance(body, dict):
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            if self.path == "/health":
                self.reply({"status": "ok", "version": "0.5.6", "device": "cpu"})
            elif self.path == "/openapi.json":
                self.reply({"paths": {"/v1/audio/speech": {}, "/v1/audio/transcriptions": {}} if capture["paths"] else {}})
            elif self.path == "/models":
                self.reply({"models": [{"repo_id": "KittenML/kitten-tts-mini-0.8", "engines": ["kittentts"],
                                         "installed": capture["installed"], "supported": True}]})
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            capture["requests"].append((self.path, body, self.headers["Content-Type"]))
            if self.path == "/v1/audio/speech":
                capture["speech_started"].set()
                capture["speech_release"].wait(3)
                self.reply(capture["audio"], "audio/wav")
            elif self.path == "/v1/audio/transcriptions":
                self.reply({"text": "Add a task for tomorrow."})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", capture
    capture["speech_release"].set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def wait_event(controller, kind, state=None):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        event = controller.events.get(timeout=max(0.01, deadline - time.monotonic()))
        if event["type"] == kind and (state is None or event.get("state") == state):
            return event
    raise AssertionError(f"No {kind}/{state} event")


def audio_process_factory(seen):
    class Process:
        returncode = 0
        def __init__(self, argv, **kwargs):
            seen.append((argv, kwargs))
            if Path(argv[0]).name in {"pw-record", "arecord"}:
                Path(argv[-1]).write_bytes(wav_bytes())
        def poll(self): return self.returncode
    return Process


@pytest.mark.parametrize("url", ["https://example.com", "http://127.0.0.1@evil.test", "http://user:password@localhost:3900",
                                  "http://localhost:3900?key=secret", "file:///tmp/voice", "http://localhost:3900/redirect"])
def test_audio_never_uses_remote_or_credential_urls(url):
    with pytest.raises(ValueError, match="loopback"):
        local_voice_url(url)


def test_accepts_actual_loopback_urls():
    assert local_voice_url("http://127.0.0.1:3900/") == "http://127.0.0.1:3900"
    assert local_voice_url("http://[::1]:3900") == "http://[::1]:3900"
    assert spoken_text("**Hello** [Alex](https://example.com).\n```python\nsecret\n```") == "Hello Alex."


@pytest.mark.parametrize("greeting, expected", [(None, "Good day, Alex."),
                                                  ("Good morning, sir. Ready for today?", "Good morning, sir. Ready for today?")])
def test_startup_speech_uses_verified_api_and_erases_audio(speech_server, greeting, expected):
    url, capture = speech_server
    seen = []
    controller = VoiceController({"voice_url": url, "greeting": "Good day, Alex."},
                                 which=lambda name: "/usr/bin/" + name,
                                 popen=audio_process_factory(seen))
    try:
        controller.startup(greeting)
        wait_event(controller, "voice_status", "speaking")
        wait_event(controller, "voice_status", "idle")
        payload = json.loads(capture["requests"][0][1])
        assert payload == {"model": "kittentts", "input": expected, "voice": "expr-voice-2-m", "response_format": "wav", "speed": 1.0}
        assert capture["requests"][0][0] == "/v1/audio/speech"
        assert seen[0][0][0] == "/usr/bin/pw-play"
        assert not Path(seen[0][0][1]).exists()
    finally:
        controller.close()


def test_recording_returns_transcript_for_existing_assistant(speech_server):
    url, capture = speech_server
    seen = []
    controller = VoiceController({"voice_url": url, "speech_enabled": False},
                                 which=lambda name: "/usr/bin/" + name,
                                 popen=audio_process_factory(seen))
    try:
        controller.toggle_listen()
        transcript = wait_event(controller, "transcript")
        assert transcript["text"] == "Add a task for tomorrow."
        wait_event(controller, "voice_status", "idle")
        path, body, content_type = capture["requests"][0]
        assert path == "/v1/audio/transcriptions" and content_type.startswith("multipart/form-data")
        assert b"whisper-1" in body and b"audio/wav" in body and b"RIFF" in body
        assert seen[0][0][:4] == ["/usr/bin/pw-record", "--rate=16000", "--channels=1", "--format=s16"]
        assert not Path(seen[0][0][-1]).exists()
    finally:
        controller.close()


def test_missing_weights_are_reported_without_implicit_download(speech_server):
    url, capture = speech_server
    capture["installed"] = False
    controller = VoiceController({"voice_url": url}, which=lambda _: "/usr/bin/pw-play")
    try:
        controller.speak("Hello.")
        assert "not installed" in wait_event(controller, "voice_error")["message"]
        assert capture["requests"] == []
    finally:
        controller.close()


def test_wrong_local_service_and_invalid_wav_never_play(speech_server):
    url, capture = speech_server
    seen = []
    controller = VoiceController({"voice_url": url}, which=lambda _: "/usr/bin/pw-play", popen=audio_process_factory(seen))
    try:
        capture["paths"] = False
        controller.speak("Hello.")
        assert "does not expose" in wait_event(controller, "voice_error")["message"]
        assert not capture["requests"] and not seen
        capture["paths"], capture["audio"] = True, b"not a wave"
        controller.speak("Hello.")
        assert "playable WAV" in wait_event(controller, "voice_error")["message"]
        assert not seen
    finally:
        controller.close()


def test_cancel_during_synthesis_discards_late_audio(speech_server):
    url, capture = speech_server
    capture["speech_release"].clear()
    seen = []
    controller = VoiceController({"voice_url": url}, which=lambda _: "/usr/bin/pw-play", popen=audio_process_factory(seen))
    try:
        controller.speak("Hello.")
        assert capture["speech_started"].wait(2)
        controller.cancel()
        capture["speech_release"].set()
        time.sleep(0.1)
        assert not seen and controller.state == "idle"
        assert not any(event["type"] == "voice_error" for event in list(controller.events.queue))
    finally:
        controller.close()


def test_long_answers_respect_api_limit_without_losing_words(speech_server):
    url, capture = speech_server
    seen = []
    controller = VoiceController({"voice_url": url}, which=lambda _: "/usr/bin/pw-play", popen=audio_process_factory(seen))
    try:
        text = "unbroken" * 700
        controller.speak(text)
        wait_event(controller, "voice_status", "speaking")
        wait_event(controller, "voice_status", "idle")
        chunks = [json.loads(body)["input"] for path, body, _ in capture["requests"] if path == "/v1/audio/speech"]
        assert "".join(chunks) == text and max(map(len, chunks)) <= 4096
        assert len(seen) == 2
        assert all(not Path(argv[1]).exists() for argv, _ in seen)
    finally:
        controller.close()


def test_second_toggle_finishes_capture_before_transcribing(speech_server):
    url, capture = speech_server
    started, finished = threading.Event(), threading.Event()
    seen = []
    class Recorder:
        returncode = None
        def __init__(self, argv, **kwargs):
            seen.append(argv)
            Path(argv[-1]).write_bytes(wav_bytes())
            started.set()
        def poll(self): return self.returncode
        def send_signal(self, value):
            self.returncode = 0
            finished.set()
        def wait(self, timeout): return self.returncode
    controller = VoiceController({"voice_url": url}, which=lambda _: "/usr/bin/pw-record", popen=Recorder)
    try:
        controller.toggle_listen()
        assert started.wait(2)
        assert controller.state == "listening" and capture["requests"] == []
        controller.toggle_listen()
        assert wait_event(controller, "transcript")["text"] == "Add a task for tomorrow."
        wait_event(controller, "voice_status", "idle")
        assert finished.is_set() and not Path(seen[0][-1]).exists()
    finally:
        controller.close()


def test_service_rejects_uninstalled_runtime_and_removes_desktop_parent_watchdog(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="not installed"):
        runtime_command(tmp_path, "http://127.0.0.1:3900")
    monkeypatch.setenv("OMNIVOICE_DESKTOP_CONTAINED", "1")
    monkeypatch.setenv("OMNIVOICE_BACKEND_DRAIN_FD", "99")
    env = service_environment(tmp_path)
    assert "OMNIVOICE_DESKTOP_CONTAINED" not in env and "OMNIVOICE_BACKEND_DRAIN_FD" not in env
    assert env["OMNIVOICE_TTS_BACKEND"] == "kittentts"
    assert env["ASR_MODEL_FASTER"] == "Systran/faster-whisper-base"
    assert env["OMNIVOICE_DATA_DIR"] == str(tmp_path / ".runtime" / "voice-data")


@pytest.mark.parametrize("pitch", [None, -1.5])
def test_british_voice_uses_installed_runtime_and_preserves_private_text(tmp_path, pitch):
    from app.voice import VoiceController
    model = tmp_path / 'model'
    model.mkdir()
    for name in ('model.int8.onnx', 'voices.bin', 'tokens.txt'):
        (model / name).write_bytes(b'fixture')
    runtime = tmp_path / 'runtime'
    (runtime / '.venv/bin').mkdir(parents=True)
    (runtime / '.venv/bin/python').write_text('fixture')
    settings = dict(voice_model='kokoro-onnx', voice_runtime=str(runtime), british_voice_path=str(model), voice_id='bm_george')
    if pitch is not None:
        settings['voice_pitch'] = pitch
    requested_tools = []
    def which(name):
        requested_tools.append(name)
        return '/usr/bin/' + name
    controller = VoiceController(settings, which=which)
    calls = []
    def run(argv, cancel, **kwargs):
        calls.append((argv, kwargs))
        if '--text-file' in argv:
            textfile = Path(argv[argv.index('--text-file')+1])
            assert textfile.read_text() == 'Private synthetic reply'
            output = Path(argv[argv.index('--output')+1]); output.write_bytes(wav_bytes())
        elif '-af' in argv:
            Path(argv[-1]).write_bytes(wav_bytes())
    controller._run_audio = run
    try:
        controller._speak_british('Private synthetic reply', '/usr/bin/pw-play', threading.Event())
        assert calls[0][1]['private'] is True
        assert calls[0][0][calls[0][0].index('--voice')+1] == 'bm_george'
        assert calls[0][0][calls[0][0].index('--speed')+1] == '1.0'
        assert all('Private synthetic reply' not in arg for argv, _ in calls for arg in argv)
        if pitch:
            assert 'pitch=0.91700404' in calls[1][0][calls[1][0].index('-af')+1]
        else:
            assert len(calls) == 2 and 'ffmpeg' not in requested_tools
        assert calls[-1][0][0] == '/usr/bin/pw-play'
        assert not Path(calls[-1][0][-1]).exists()
    finally:
        controller.close()
