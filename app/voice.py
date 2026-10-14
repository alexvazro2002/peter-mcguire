"""Peter's local VoiceStudio audio adapter; the existing Assistant owns reasoning.

Only loopback HTTP is accepted. Capture and playback use Fedora's existing
PipeWire/ALSA tools. Audio lives in temporary directories and is never journaled.
The single worker serializes requests and drops results from cancelled work.
"""
from __future__ import annotations

import ipaddress
import logging
import math
import queue
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from urllib.parse import urlsplit

import requests
from app.config import BASE_DIR, DATA_DIR

logger = logging.getLogger(__name__)


class VoiceError(RuntimeError):
    pass


class _Cancelled(Exception):
    pass


def local_voice_url(value):
    """Reject credentials, remote endpoints and redirect-friendly URL suffixes."""
    url = str(value or "http://127.0.0.1:3900").rstrip("/")
    parsed = urlsplit(url)
    try:
        local = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
        port = parsed.port
    except ValueError:
        local, port = False, None
    if (parsed.scheme not in {"http", "https"} or not local or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
            or port is not None and not 1 <= port <= 65535):
        raise ValueError("VoiceStudio must use a loopback URL, such as http://127.0.0.1:3900.")
    return url


def spoken_text(value):
    """Keep speech concise and omit visual Markdown/code markup."""
    text = re.sub(r"```.*?```", "", str(value or ""), flags=re.S)
    text = re.sub(r"!?\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|[-*]\s+)", "", text)
    text = re.sub(r"[`*_]", "", text)
    return re.sub(r"\s+", " ", text).strip()


class VoiceController:
    def __init__(self, settings, *, session_factory=requests.Session, which=shutil.which,
                 popen=subprocess.Popen):
        self.settings = dict(settings or {})
        self.url = local_voice_url(self.settings.get("voice_url"))
        self.timeout = max(5.0, min(300.0, float(self.settings.get("voice_timeout", 120))))
        self.record_seconds = max(1.0, min(60.0, float(self.settings.get("record_seconds", 30))))
        self.events, self.state = queue.Queue(), "idle"
        self._session_factory, self._which, self._popen = session_factory, which, popen
        self._lock, self._closed = threading.RLock(), threading.Event()
        self._pending = queue.Queue(maxsize=1)
        self._active_cancel = threading.Event()
        self._record_stop = threading.Event()
        self._process = None
        self._thread = threading.Thread(target=self._work, name="peter-voice", daemon=True)
        self._thread.start()

    def _status(self, state, message="", cancelled=None):
        with self._lock:
            if self._closed.is_set() or cancelled is not None and cancelled.is_set():
                return
            self.state = state
            self.events.put(dict(type="voice_status", state=state, message=message))

    def _check(self, cancelled):
        if self._closed.is_set() or cancelled.is_set():
            raise _Cancelled()

    def _submit(self, operation, text=""):
        if self._closed.is_set() or not self.settings.get("enabled", True):
            return
        self.cancel()
        task = (operation, text, threading.Event())
        with self._lock:
            try:
                self._pending.get_nowait()
            except queue.Empty:
                pass
            self._pending.put_nowait(task)
        self._status("thinking", "Preparing local voice" if operation != "record" else "Opening microphone")

    def startup(self, text=None):
        self._submit("startup", text)

    def toggle_listen(self):
        with self._lock:
            listening = self.state == "listening"
        if listening:
            self._record_stop.set()
        else:
            self._submit("record")

    def speak(self, text):
        text = spoken_text(text)
        if text and self.settings.get("speech_enabled", True):
            self._submit("speak", text)

    def cancel(self):
        with self._lock:
            self._active_cancel.set()
            self._record_stop.set()
            try:
                self._pending.get_nowait()
            except queue.Empty:
                pass
            process = self._process
        if process is not None:
            self._stop_process(process)
        self._status("idle", "Voice stopped")

    def close(self):
        self.cancel()
        self._closed.set()
        # HTTP reads have a finite timeout; a daemon worker never blocks app exit.
        self._thread.join(timeout=1.0)

    def _work(self):
        while not self._closed.is_set():
            try:
                operation, text, cancelled = self._pending.get(timeout=0.1)
            except queue.Empty:
                continue
            with self._lock:
                self._active_cancel = cancelled
                self._record_stop = threading.Event()
            session = self._session_factory()
            session.trust_env = False  # Local audio must never pass through an HTTP proxy.
            try:
                self._check(cancelled)
                self._ready(session, cancelled, wait=operation == "startup")
                if operation == "record":
                    self._record(session, cancelled)
                elif operation == "speak":
                    self._speak(session, text, cancelled)
                elif operation == "startup":
                    greeting = (self.settings.get("greeting", "Hello. I'm Peter McGuire. Your workspace is ready.")
                                if text is None else text)
                    if self.settings.get("speech_enabled", True) and self.settings.get("startup_greeting", True):
                        self._speak(session, spoken_text(greeting), cancelled)
                self._check(cancelled)
                self._status("idle", "Local voice ready", cancelled)
            except _Cancelled:
                pass
            except Exception as error:
                if not cancelled.is_set() and not self._closed.is_set():
                    logger.warning("Peter voice task failed", exc_info=True)
                    message = str(error) if isinstance(error, VoiceError) else "Local voice failed. Check the microphone and VoiceStudio service."
                    self._status("error", message, cancelled)
                    self.events.put(dict(type="voice_error", message=message))
            finally:
                session.close()

    def _request(self, session, method, path, cancelled, **kwargs):
        self._check(cancelled)
        try:
            response = session.request(method, self.url + path, timeout=(3, min(10, self.timeout) if method == "GET" else self.timeout),
                                       allow_redirects=False, **kwargs)
        except requests.RequestException as error:
            self._check(cancelled)
            raise VoiceError("VoiceStudio is unavailable at " + self.url + ". Use the Linux launcher or run install-voice.sh.") from error
        try:
            self._check(cancelled)
        except _Cancelled:
            response.close()
            raise
        if not 200 <= response.status_code < 300:
            try:
                detail = response.json().get("detail", response.json())
                if isinstance(detail, dict):
                    detail = detail.get("message") or detail.get("error") or str(detail)
            except (ValueError, AttributeError):
                detail = response.text[:300]
            response.close()
            raise VoiceError(f"VoiceStudio returned HTTP {response.status_code}: {str(detail)[:160]}")
        return response

    def _json(self, session, path, cancelled):
        response = self._request(session, "GET", path, cancelled)
        try:
            return response.json()
        except ValueError as error:
            raise VoiceError("VoiceStudio returned invalid JSON at " + path) from error
        finally:
            response.close()

    def _ready(self, session, cancelled, wait=False):
        deadline = time.monotonic() + (self.timeout if wait else 0)
        while True:
            try:
                health = self._json(session, "/health", cancelled)
                if not isinstance(health, dict) or health.get("status") != "ok":
                    raise VoiceError("VoiceStudio has not finished starting.")
                break
            except VoiceError:
                if not wait or time.monotonic() >= deadline:
                    raise
                self._status("thinking", "Waiting for VoiceStudio to start", cancelled)
                if cancelled.wait(0.5):
                    raise _Cancelled()
        api = self._json(session, "/openapi.json", cancelled)
        paths = api.get("paths", {}) if isinstance(api, dict) else {}
        if any(path not in paths for path in ("/v1/audio/speech", "/v1/audio/transcriptions")):
            raise VoiceError("The local service does not expose VoiceStudio's speech and transcription APIs.")

    def _speak(self, session, text, cancelled):
        if not text:
            return
        player = self._which("pw-play") or self._which("aplay")
        if not player:
            raise VoiceError("Install pipewire-utils or alsa-utils to play Peter's speech.")
        model = self.settings.get("voice_model", "kittentts")
        if model == "kokoro-onnx":
            self._speak_british(text, player, cancelled)
            return
        catalogue = self._json(session, "/models", cancelled)
        models = catalogue.get("models", []) if isinstance(catalogue, dict) else []
        # Kitten's selected checkpoint is pinned by the Linux service launcher.
        # Refuse to trigger the engine's implicit first-use model download.
        candidates = [m for m in models if model in m.get("engines", [])]
        if model == "kittentts":
            candidates = [m for m in candidates if m.get("repo_id") == "KittenML/kitten-tts-mini-0.8"]
        if not any(m.get("installed") and m.get("supported", True) for m in candidates):
            raise VoiceError(f"VoiceStudio's {model} weights are not installed. Run install-voice.sh before speaking.")
        self._status("thinking", "Generating local speech", cancelled)
        # Respect the API's 4096-character limit without dropping a long answer.
        remaining = text
        with tempfile.TemporaryDirectory(prefix="peter-voice-") as directory:
            audio = Path(directory) / "speech.wav"
            while remaining:
                split = min(4096, len(remaining))
                if split < len(remaining):
                    boundary = remaining.rfind(" ", 0, split)
                    if boundary > 0:
                        split = boundary
                chunk, remaining = remaining[:split].strip(), remaining[split:].strip()
                response = self._request(session, "POST", "/v1/audio/speech", cancelled, stream=True,
                                         json=dict(model=model, input=chunk,
                                                   voice=self.settings.get("voice_id", "expr-voice-2-m"),
                                                   response_format="wav", speed=float(self.settings.get("voice_speed", 1.0))))
                try:
                    size = 0
                    with audio.open("wb") as output:
                        for block in response.iter_content(65536):
                            self._check(cancelled)
                            size += len(block)
                            if size > 32 * 1024 * 1024:
                                raise VoiceError("VoiceStudio returned an unexpectedly large speech file.")
                            output.write(block)
                finally:
                    response.close()
                self._validate_wav(audio)
                self._status("speaking", "Peter is speaking", cancelled)
                self._run_audio([player, str(audio)], cancelled, limit=180)

    def _speak_british(self, text, player, cancelled):
        """Use the installed runtime's real British stock voice; keep synthesis cancellable."""
        runtime = Path(self.settings.get("voice_runtime") or BASE_DIR / ".runtime" / "VoiceStudio").expanduser()
        python = runtime / ".venv" / "bin" / "python"
        models = Path(self.settings.get("british_voice_path") or DATA_DIR / ".runtime" / "british-voice").expanduser()
        if not python.is_file() or not all((models / name).is_file() for name in ("model.int8.onnx", "voices.bin", "tokens.txt")):
            raise VoiceError("Peter's British voice is not installed. Run install-voice.sh to prepare it.")
        speed, pitch = float(self.settings.get("voice_speed", 1.0)), float(self.settings.get("voice_pitch", 0.0))
        if not math.isfinite(speed) or not 0.7 <= speed <= 1.4 or not math.isfinite(pitch) or not -4 <= pitch <= 2:
            raise VoiceError("Choose voice speed from 0.7 to 1.4 and pitch from -4 to +2 semitones in Settings.")
        ffmpeg = self._which("ffmpeg") if pitch else None
        if pitch and not ffmpeg:
            raise VoiceError("Install FFmpeg to apply Peter's deeper voice pitch.")
        self._status("thinking", "Preparing Peter's British voice", cancelled)
        with tempfile.TemporaryDirectory(prefix="peter-voice-") as directory:
            directory = Path(directory)
            content, original, audio = directory / "speech.txt", directory / "original.wav", directory / "speech.wav"
            content.write_text(text, encoding="utf-8")
            self._run_audio([str(python), str(BASE_DIR / "scripts" / "british_voice.py"),
                             "--model-dir", str(models), "--text-file", str(content), "--output", str(original),
                             "--voice", self.settings.get("voice_id", "bm_george"), "--speed", str(speed)],
                            cancelled, limit=self.timeout, private=True)
            self._validate_wav(original)
            if pitch:
                self._run_audio([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(original),
                                 "-af", f"rubberband=tempo=1:pitch={2 ** (pitch / 12):.8f}:formant=preserved",
                                 "-c:a", "pcm_s16le", str(audio)], cancelled, limit=30)
                self._validate_wav(audio)
            else:
                audio = original
            self._status("speaking", "Peter is speaking", cancelled)
            self._run_audio([player, str(audio)], cancelled, limit=180)

    @staticmethod
    def _validate_wav(path):
        try:
            with wave.open(str(path), "rb") as audio:
                if audio.getnframes() == 0 or audio.getframerate() <= 0:
                    raise ValueError("empty audio")
        except (OSError, EOFError, ValueError, wave.Error) as error:
            raise VoiceError("VoiceStudio did not return a playable WAV recording.") from error

    def _record(self, session, cancelled):
        recorder = self._which("pw-record") or self._which("arecord")
        if not recorder:
            raise VoiceError("Install pipewire-utils or alsa-utils to capture microphone audio.")
        with tempfile.TemporaryDirectory(prefix="peter-voice-") as directory:
            audio = Path(directory) / "microphone.wav"
            if Path(recorder).name == "pw-record":
                argv = [recorder, "--rate=16000", "--channels=1", "--format=s16", str(audio)]
            else:
                argv = [recorder, "--quiet", "--file-type", "wav", "--format", "S16_LE",
                        "--rate", "16000", "--channels", "1", str(audio)]
            self._status("listening", "Listening — tap again to finish", cancelled)
            self._run_audio(argv, cancelled, limit=self.record_seconds, recording=True)
            self._check(cancelled)
            self._validate_wav(audio)
            self._status("thinking", "Transcribing locally", cancelled)
            with audio.open("rb") as source:
                response = self._request(session, "POST", "/v1/audio/transcriptions", cancelled,
                                         files={"file": ("microphone.wav", source, "audio/wav")},
                                         data=dict(model=self.settings.get("asr_model", "whisper-1"),
                                                   response_format="json",
                                                   language=self.settings.get("voice_language", "en")))
                try:
                    result = response.json()
                finally:
                    response.close()
            self._check(cancelled)
            text = result.get("text", "").strip() if isinstance(result, dict) else ""
            if not text:
                raise VoiceError("No speech was recognized. Check the selected microphone and try again.")
            self._status("idle", "Speech recognized", cancelled)
            self.events.put(dict(type="transcript", text=text))

    def _run_audio(self, argv, cancelled, limit, recording=False, private=False):
        self._check(cancelled)
        # Use a file for diagnostics so subprocess pipe buffers cannot deadlock.
        with tempfile.TemporaryFile() as errors:
            process = self._popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=errors, start_new_session=True)
            with self._lock:
                self._process = process
            try:
                deadline = time.monotonic() + limit
                stopped = False
                while process.poll() is None:
                    self._check(cancelled)
                    if recording and self._record_stop.is_set() or time.monotonic() >= deadline:
                        self._stop_process(process, recording=recording)
                        stopped = True
                        break
                    cancelled.wait(0.05)
                self._check(cancelled)
                if process.returncode and not recording or process.returncode and not stopped:
                    errors.seek(0)
                    detail = "" if private else errors.read(500).decode("utf-8", errors="replace").strip()
                    raise VoiceError("Local audio process failed" + (": " + detail if detail else "."))
            finally:
                self._stop_process(process, recording=recording)
                with self._lock:
                    if self._process is process:
                        self._process = None

    @staticmethod
    def _stop_process(process, recording=False):
        if process.poll() is not None:
            return
        try:
            process.send_signal(signal.SIGINT if recording else signal.SIGTERM)
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
