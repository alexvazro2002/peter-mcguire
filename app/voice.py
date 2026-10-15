"""Peter's local VoiceStudio audio adapter; the existing Assistant owns reasoning.

Only loopback HTTP is accepted. Capture and playback use Fedora's existing
PipeWire/ALSA tools. Audio lives in temporary directories and is never journaled.
One worker serializes transcription/playback and drops cancelled results. A
separate microphone stream discards input while Peter is processing a reply.
"""
from __future__ import annotations

import ipaddress
import hashlib
import json
import logging
import math
import os
import queue
import re
import shutil
import signal
import selectors
import subprocess
import sys
import tempfile
import threading
import time
import wave
from array import array
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests
from app.config import BASE_DIR, DATA_DIR
from app.speech import speech_segments
from app.speech_rendering import render_speech, speech_language
from app.hands_free import UtteranceDetector

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


def utterance_id(text):
    return hashlib.sha256(spoken_text(text).encode('utf-8')).hexdigest()


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
        # An acknowledgement and the finished answer share one ordered worker.
        # Keeping both queued prevents the answer from cutting off “Give me a sec”.
        self._pending = queue.Queue(maxsize=8)
        self._generation = 0
        self._active_cancel = threading.Event()
        self._record_stop = threading.Event()
        self._process = None
        self._processes = set()
        self._process_tasks = {}
        self.hands_free = bool(self.settings.get('hands_free', False))
        self._auto_allowed = threading.Event()
        self._auto_fault, self._operation, self._resume_after = False, None, 0
        self._manual_requested = False
        self._mic_process, self._mic_cancel = None, threading.Event()
        self._awaiting_transcript = False
        self._auto_failures, self._auto_retry_at = 0, 0
        self.capture_start_timeout, self.capture_stall_timeout = 6.0, 3.0
        self.sentence_pause = .3
        self._thread = threading.Thread(target=self._work, name="peter-voice", daemon=True)
        self._thread.start()
        self._microphone_thread = threading.Thread(target=self._hands_free_loop, name="peter-microphone", daemon=True)
        self._microphone_thread.start()

    def _status(self, state, message="", cancelled=None):
        with self._lock:
            if self._closed.is_set() or cancelled is not None and cancelled.is_set():
                return
            self.state = state
            self.events.put(dict(type="voice_status", state=state, message=message))

    def _check(self, cancelled):
        if self._closed.is_set() or cancelled.is_set():
            raise _Cancelled()

    def _submit(self, operation, text="", *, enqueue=False, captions=True):
        if self._closed.is_set() or not self.settings.get("enabled", True):
            return
        if not enqueue:
            self.cancel()
        with self._lock:
            self._auto_allowed.clear()
            payload = (text, False) if operation == 'speak' and not captions else text
            task = (operation, payload, threading.Event(), self._generation)
            if not enqueue:
                self._record_stop = threading.Event()
            self._manual_requested = operation == "record"
            try:
                self._pending.put_nowait(task)
            except queue.Full:
                raise VoiceError('The speech queue is full. Wait for Peter to finish speaking.')
        self._status("thinking", "Preparing local voice" if operation != "record" else "Opening microphone")

    def startup(self, text=None):
        self._submit("startup", text)

    def set_hands_free(self, enabled):
        if bool(enabled) != self.hands_free:
            self.cancel()
        self.hands_free = bool(enabled)
        self._auto_fault = False
        self._auto_failures, self._auto_retry_at = 0, 0
        if not enabled:
            self._mic_cancel.set()
            if self._mic_process is not None:
                self._stop_process(self._mic_process)
            self.allow_hands_free(False)

    def allow_hands_free(self, allowed):
        """The UI gates capture while reasoning, speaking or leaving the app."""
        if allowed and self.hands_free and self.settings.get('enabled', True):
            self._auto_allowed.set()
        else:
            self._auto_allowed.clear()


    def toggle_listen(self):
        with self._lock:
            listening = self._manual_requested or self._operation == "record"
        if listening:
            self._record_stop.set()
        else:
            self._submit("record")

    def speak(self, text, *, enqueue=False, captions=True):
        text = spoken_text(text)
        if text and self.settings.get("speech_enabled", True):
            self._submit("speak", text, enqueue=enqueue, captions=captions)

    def cancel(self):
        with self._lock:
            self._auto_allowed.clear()
            self._awaiting_transcript = False
            self._generation += 1
            self._active_cancel.set()
            self._manual_requested = False
            self._record_stop.set()
            while True:
                try:
                    self._pending.get_nowait()
                except queue.Empty:
                    break
            processes = list(self._processes)
        for process in processes:
            self._stop_process(process)
        self._status("idle", "Voice stopped")

    def close(self):
        self.cancel()
        self._closed.set()
        self._mic_cancel.set()
        if self._mic_process is not None:
            self._stop_process(self._mic_process)
        self._microphone_thread.join(timeout=1.0)
        # HTTP reads have a finite timeout; a daemon worker never blocks app exit.
        self._thread.join(timeout=1.0)

    def _work(self):
        while not self._closed.is_set():
            try:
                operation, text, cancelled, generation = self._pending.get(timeout=0.1)
            except queue.Empty:
                continue
            with self._lock:
                if generation != self._generation:
                    cancelled.set()
                    continue
                self._active_cancel = cancelled
                if operation != "record":
                    self._record_stop = threading.Event()
                self._operation = operation
            session = self._session_factory()
            session.trust_env = False  # Local audio must never pass through an HTTP proxy.
            try:
                self._check(cancelled)
                self._ready(session, cancelled, wait=operation == "startup")
                if operation == "record":
                    self._record(session, cancelled)
                elif operation == 'hands_free':
                    self._transcribe_pcm(session, text, cancelled, automatic=True)
                elif operation == "speak":
                    text, captions = text if isinstance(text, tuple) else (text, True)
                    self._speak(session, text, cancelled, captions=captions)
                elif operation == "startup":
                    greeting = (self.settings.get("greeting", "Hello. I'm Peter McGuire. Your workspace is ready.")
                                if text is None else text)
                    if self.settings.get("speech_enabled", True) and self.settings.get("startup_greeting", True):
                        self._speak(session, spoken_text(greeting), cancelled)
                self._check(cancelled)
                self._auto_failures, self._auto_retry_at = 0, 0
                self._status("idle", "Local voice ready", cancelled)
            except _Cancelled:
                pass
            except Exception as error:
                if not cancelled.is_set() and not self._closed.is_set():
                    if operation == 'hands_free':
                        self._auto_failures += 1
                        self._auto_fault = self._auto_failures >= 3
                        self._auto_retry_at = time.monotonic() + 3 * self._auto_failures
                    self._awaiting_transcript = False
                    logger.warning("Peter voice task failed", exc_info=True)
                    message = str(error) if isinstance(error, VoiceError) else "Local voice failed. Check the microphone and VoiceStudio service."
                    self._status("error", message, cancelled)
                    self.events.put(dict(type="voice_error", message=message))
            finally:
                session.close()
                with self._lock:
                    self._operation = None
                    if operation == "record":
                        self._manual_requested = False
                    if operation in {'startup', 'speak'}:
                        self._resume_after = time.monotonic() + .6

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

    def _speak(self, session, text, cancelled, *, captions=True):
        if not text:
            return
        player = self._which("pw-play") or self._which("aplay")
        if not player:
            raise VoiceError("Install pipewire-utils or alsa-utils to play Peter's speech.")
        model = self.settings.get("voice_model", "kittentts")
        if model == "kokoro-onnx" or any(speech_language(chunk) == 'es' for chunk in speech_segments(text)):
            self._speak_british(text, player, cancelled, captions=captions)
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
        self._status("thinking", "Generating the first sentence", cancelled)
        with self._speech_directory() as (directory, finished):
            def produce(chunks, stop):
                # The producer owns its session; playback never blocks generation.
                with self._session_factory() as producer_session:
                    producer_session.trust_env = False
                    for index, chunk in enumerate(chunks):
                        self._wait_for_space(directory, index, stop)
                        audio = directory / f'{index:04d}.wav'
                        partial = directory / f'{index:04d}.partial'
                        rendered = render_speech(chunk, language=speech_language(chunk), now=datetime.now().astimezone())
                        if not any(character.isalnum() for character in rendered):
                            self._silent_clip(partial)
                            partial.replace(audio)
                            continue
                        response = self._request(producer_session, "POST", "/v1/audio/speech", stop, stream=True,
                            json=dict(model=model, input=rendered, voice=self.settings.get("voice_id", "expr-voice-2-m"),
                                      response_format="wav", speed=float(self.settings.get("voice_speed", 1.0))))
                        try:
                            size = 0
                            with partial.open('wb') as output:
                                for block in response.iter_content(65536):
                                    self._check(stop)
                                    size += len(block)
                                    if size > 32 * 1024 * 1024:
                                        raise VoiceError("VoiceStudio returned an unexpectedly large speech file.")
                                    output.write(block)
                        finally:
                            response.close()
                        self._validate_wav(partial)
                        partial.replace(audio)
            self._sentence_pipeline(text, directory, produce, player, cancelled, finished=finished, captions=captions)

    def _speak_british(self, text, player, cancelled, *, captions=True):
        """Use native English/Spanish stock voices while retaining the written captions."""
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
        self._status("thinking", "Generating the first sentence", cancelled)
        english_voice = self.settings.get('voice_id', 'bm_george')
        if english_voice not in {'bm_daniel', 'bm_fable', 'bm_george', 'bm_lewis'}:
            english_voice = 'bm_george'
        with self._speech_directory() as (directory, finished):
            def produce(chunks, stop):
                content = directory / 'sentences.json'
                spoken = []
                for chunk in chunks:
                    language = speech_language(chunk, default=self.settings.get('voice_language', 'en'))
                    rendered = render_speech(chunk, language=language, now=datetime.now().astimezone())
                    if not any(character.isalnum() for character in rendered):
                        rendered = ''
                    if language == 'es':
                        spoken.append(dict(text=rendered, language='es', voice='em_alex'))
                    else:
                        spoken.append(rendered)
                content.write_text(json.dumps(spoken, ensure_ascii=False), encoding='utf-8')
                content.chmod(0o600)
                self._run_audio([str(python), str(BASE_DIR / "scripts" / "british_voice.py"),
                    "--model-dir", str(models), "--segments-file", str(content), "--output-dir", str(directory),
                    "--voice", english_voice, "--speed", str(speed)],
                    stop, limit=min(3600, self.timeout * len(chunks)), private=True)
            def prepare(original):
                if not pitch:
                    return original
                audio = directory / 'pitched.wav'
                self._run_audio([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(original),
                    "-af", f"rubberband=tempo=1:pitch={2 ** (pitch / 12):.8f}:formant=preserved",
                    "-c:a", "pcm_s16le", str(audio)], cancelled, limit=30)
                return audio
            self._sentence_pipeline(text, directory, produce, player, cancelled, prepare=prepare, finished=finished, captions=captions)

    @contextmanager
    def _speech_directory(self):
        temporary = tempfile.TemporaryDirectory(prefix='peter-voice-')
        finished = threading.Event()
        finished.set()
        try:
            yield Path(temporary.name), finished
        finally:
            if finished.is_set():
                temporary.cleanup()
            else:
                # A cancelled HTTP read may still be waiting on its finite
                # timeout. Keep its private files alive until it exits, then erase.
                def clean():
                    finished.wait()
                    temporary.cleanup()
                threading.Thread(target=clean, name='peter-speech-cleanup', daemon=True).start()

    def _wait_for_space(self, directory, index, cancelled):
        while index >= 3 and not (directory / f'consumed-{index - 3}').exists():
            self._check(cancelled)
            cancelled.wait(.05)

    def _sentence_pipeline(self, text, directory, produce, player, cancelled, *, prepare=lambda path: path, finished=None, captions=True):
        """One producer, one player: ordered clips, bounded lookahead and cancellable pauses."""
        chunks, producer_stop = speech_segments(text), threading.Event()
        if not chunks:
            return
        done, errors = finished or threading.Event(), queue.Queue()
        done.clear()
        def generate():
            try:
                produce(chunks, producer_stop)
            except Exception as error:
                errors.put(error)
            finally:
                done.set()
        producer = threading.Thread(target=generate, name='peter-sentence-synthesis', daemon=True)
        producer.start()
        try:
            for index, chunk in enumerate(chunks):
                original = directory / f'{index:04d}.wav'
                deadline = time.monotonic() + self.timeout
                waiting_reported = False
                while not original.exists():
                    self._check(cancelled)
                    if done.is_set():
                        if not errors.empty():
                            raise errors.get_nowait()
                        raise VoiceError('Local speech stopped before the next sentence was ready.')
                    if time.monotonic() > deadline:
                        raise VoiceError('The next spoken sentence took too long to generate.')
                    if index and not waiting_reported:
                        self._status('thinking', 'Preparing the next sentence', cancelled)
                        waiting_reported = True
                    cancelled.wait(.05)
                self._check(cancelled)
                audio = prepare(original)
                self._validate_wav(audio)
                self._trim_silence(audio)
                with wave.open(str(audio), 'rb') as clip:
                    duration = clip.getnframes() / clip.getframerate()
                def playback_started(chunk=chunk, index=index, duration=duration):
                    self._status('speaking', 'Peter is speaking', cancelled)
                    with self._lock:
                        self._check(cancelled)
                        if captions:
                            self.events.put(dict(type='voice_caption', text=chunk, seconds=duration,
                                                 utterance_id=utterance_id(text), index=index, total=len(chunks)))
                self._run_audio([player, str(audio)], cancelled, limit=180, on_start=playback_started)
                original.unlink(missing_ok=True)
                if audio != original:
                    audio.unlink(missing_ok=True)
                (directory / f'consumed-{index}').touch(mode=0o600)
                if index < len(chunks) - 1:
                    cancelled.wait(self.sentence_pause)
                    self._check(cancelled)
            producer.join(timeout=1)
            if not errors.empty():
                raise errors.get_nowait()
        finally:
            producer_stop.set()
            # Only synthesis subprocesses from this task are stopped here.
            with self._lock:
                owned = [p for p, event in self._process_tasks.items() if event is producer_stop]
            for process in owned:
                self._stop_process(process)
            producer.join(timeout=min(2, self.timeout))

    @staticmethod
    def _silent_clip(path):
        # A visual-only link still receives its original caption, without
        # submitting an empty utterance to TTS or inventing spoken content.
        with wave.open(str(path), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b'\0\0' * 1200)

    @staticmethod
    def _trim_silence(path):
        """Trim silent edge padding conservatively so the explicit gap stays natural."""
        with wave.open(str(path), 'rb') as audio:
            if audio.getsampwidth() != 2 or audio.getnchannels() != 1 or audio.getnframes() > audio.getframerate() * 180:
                return
            params, frames = audio.getparams(), audio.readframes(audio.getnframes())
        samples = array('h', frames)
        if sys.byteorder != 'little':
            samples.byteswap()
        block = max(1, params.framerate // 100)
        active = [i for i in range(0, len(samples), block)
                  if max(map(abs, samples[i:i + block]), default=0) > 48]
        if not active:
            return
        keep = params.framerate // 40
        first, last = max(0, active[0] - keep), min(len(samples), active[-1] + block + keep)
        with wave.open(str(path), 'wb') as audio:
            audio.setparams(params)
            audio.writeframes(frames[first * 2:last * 2])

    @staticmethod
    def _validate_wav(path):
        try:
            with wave.open(str(path), "rb") as audio:
                if audio.getnframes() == 0 or audio.getframerate() <= 0:
                    raise ValueError("empty audio")
        except (OSError, EOFError, ValueError, wave.Error) as error:
            raise VoiceError("VoiceStudio did not return a playable WAV recording.") from error

    def _record(self, session, cancelled):
        self._capture_and_transcribe(session, cancelled, automatic=False)

    def _capture_and_transcribe(self, session, cancelled, *, automatic):
        pcm = self._capture_pcm(cancelled, automatic=automatic)
        self._transcribe_pcm(session, pcm, cancelled, automatic=automatic)

    def _transcribe_pcm(self, session, pcm, cancelled, *, automatic):
        self._check(cancelled)
        if len(pcm) < 16000 * 2 * .25:
            if automatic:
                self._awaiting_transcript = False
                return
            raise VoiceError("The microphone did not capture enough audio. Wait for Listening, speak, then press Finish listening.")
        with tempfile.TemporaryDirectory(prefix='peter-voice-') as directory:
            audio = Path(directory) / 'microphone.wav'
            with wave.open(str(audio), 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(pcm)
            self._transcribe(session, audio, cancelled, automatic=automatic)

    def _transcribe(self, session, audio, cancelled, *, automatic=False):
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
            if automatic:
                self._awaiting_transcript = False
                return
            raise VoiceError("No speech was recognized. Check the selected microphone and try again.")
        # Stay paused until the UI accepts this utterance and has completed
        # the reply. Never transcribe Peter's speaker audio as a new request.
        with self._lock:
            self._check(cancelled)
            self._auto_allowed.clear()
            self._status("idle", "Speech recognized", cancelled)
            self._check(cancelled)
            self.events.put(dict(type="transcript", text=text, generation=self._generation))

    def valid_event(self, event):
        with self._lock:
            return event.get('generation', self._generation) == self._generation


    def _microphone_ready(self):
        with self._lock:
            return (self._auto_allowed.is_set() and not self._awaiting_transcript
                    and self._operation is None and self._pending.empty()
                    and time.monotonic() >= self._resume_after)

    def _hands_free_loop(self):
        """Keep the capture stream warm; discard all samples during a reply."""
        while not self._closed.wait(.05):
            if (not self.hands_free or not self.settings.get('enabled', True) or self._auto_fault
                    or time.monotonic() < self._auto_retry_at):
                continue
            # Open only once listening has been enabled. Subsequently preserve
            # the stream while the reasoning/playback gate is closed.
            if not self._microphone_ready():
                continue
            stop = threading.Event()
            self._mic_cancel = stop
            try:
                self._listen_continuously(stop)
            except _Cancelled:
                pass
            except Exception as error:
                if not self._closed.is_set() and self.hands_free and not stop.is_set():
                    self._auto_failures += 1
                    self._auto_fault = self._auto_failures >= 3
                    self._auto_retry_at = time.monotonic() + 3 * self._auto_failures
                    message = str(error) if isinstance(error, VoiceError) else 'The microphone stream failed. Check the selected input device.'
                    self._status('error', message)
                    self.events.put(dict(type='voice_error', message=message))

    def _listen_continuously(self, stop):
        detector = UtteranceDetector(max_seconds=self.record_seconds)
        argv = self._capture_command()
        with tempfile.TemporaryFile() as errors:
            process = self._popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=errors, start_new_session=True, bufsize=0)
            self._mic_process = process
            started, last_audio, active_gate = time.monotonic(), None, False
            capture_generation = self._generation
            pending = bytearray()
            if self._microphone_ready():
                self._status('thinking', 'Opening microphone…', stop)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while self.hands_free:
                        self._check(stop)
                        now = time.monotonic()
                        if last_audio is None and now - started >= self.capture_start_timeout:
                            raise VoiceError('The microphone is not delivering audio. Check that Yeti Orb is connected and selected as the input device.')
                        if last_audio is not None and now - last_audio >= self.capture_stall_timeout:
                            raise VoiceError('Microphone audio stopped. Check the input device, then try again.')
                        ready = self._microphone_ready()
                        if not ready:
                            detector.reset()
                            pending.clear()
                            active_gate = False
                        elif last_audio is not None and not active_gate:
                            self._status('armed', 'Hands-free on · Speak when ready', stop)
                            active_gate = True
                            capture_generation = self._generation
                        if not selector.select(timeout=.05):
                            if process.poll() is not None:
                                raise VoiceError('The microphone capture stopped. Check the selected input device.')
                            continue
                        data = os.read(process.stdout.fileno(), 9600)
                        if not data:
                            raise VoiceError('The microphone capture stopped. Check the selected input device.')
                        last_audio = now
                        if not ready:
                            continue
                        pending.extend(data)
                        while len(pending) >= detector.frame_bytes:
                            frame = bytes(pending[:detector.frame_bytes])
                            del pending[:detector.frame_bytes]
                            onset, clip = detector.feed(frame)
                            if onset:
                                self._status('listening', 'Listening · Finish speaking to send', stop)
                            if clip is not None:
                                with self._lock:
                                    if self._microphone_ready() and capture_generation == self._generation:
                                        self._awaiting_transcript = True
                                        self._pending.put_nowait(('hands_free', clip, threading.Event(), capture_generation))
                                pending.clear()
                                active_gate = False
                                break
                            if self.state == 'listening' and not detector.active:
                                self._status('armed', 'Hands-free on · Speak when ready', stop)
            finally:
                self._stop_process(process)
                if process.stdout is not None:
                    process.stdout.close()
                if self._mic_process is process:
                    self._mic_process = None

    def _capture_command(self):
        recorder = self._which('pw-record') or self._which('arecord')
        if not recorder:
            raise VoiceError('Install pipewire-utils or alsa-utils to capture microphone audio.')
        argv = ([recorder, '--raw', '--rate=16000', '--channels=1', '--format=s16', '-']
                if Path(recorder).name == 'pw-record' else
                [recorder, '--quiet', '-t', 'raw', '-f', 'S16_LE', '-r', '16000', '-c', '1', '-'])
        target = str(self.settings.get('microphone_target', '')).strip()
        if target and Path(recorder).name == 'pw-record':
            argv.insert(1, '--target=' + target)
        return argv

    def _capture_pcm(self, cancelled, *, automatic):
        """Consume raw PCM directly so stopping capture cannot leave an empty WAV."""
        detector = UtteranceDetector(max_seconds=self.record_seconds) if automatic else None
        argv = self._capture_command()
        stop = self._record_stop
        with tempfile.TemporaryFile() as errors:
            process = self._popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=errors, start_new_session=True, bufsize=0)
            with self._lock:
                self._processes.add(process)
                self._process_tasks[process] = cancelled
            pending, captured = bytearray(), bytearray()
            started, first_audio, last_audio = time.monotonic(), None, None
            self._status('thinking', 'Opening microphone…', cancelled)
            try:
                self._check(cancelled)
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        self._check(cancelled)
                        now = time.monotonic()
                        if automatic and (not self._auto_allowed.is_set() or not self.hands_free):
                            raise _Cancelled()
                        if first_audio is None and now - started >= self.capture_start_timeout:
                            raise VoiceError('The microphone is not delivering audio. Check that Yeti Orb is connected and selected as the input device.')
                        if last_audio is not None and now - last_audio >= self.capture_stall_timeout:
                            raise VoiceError('Microphone audio stopped. Check the input device, then try again.')
                        if not automatic and first_audio is not None and (
                                stop.is_set() and len(captured) >= 8000 or now - first_audio >= self.record_seconds):
                            return bytes(captured[:len(captured) // 2 * 2])
                        if not selector.select(timeout=.05):
                            if process.poll() is not None:
                                if not automatic and captured:
                                    return bytes(captured[:len(captured) // 2 * 2])
                                raise VoiceError('The microphone stopped before recording audio. Check the selected input device.')
                            continue
                        data = os.read(process.stdout.fileno(), 9600)
                        if not data:
                            if not automatic and captured:
                                return bytes(captured[:len(captured) // 2 * 2])
                            raise VoiceError('The microphone is unavailable. Check the selected input device.')
                        last_audio = now
                        if first_audio is None:
                            first_audio = now
                            self._status('armed' if automatic else 'listening',
                                'Hands-free on · Speak when ready' if automatic else 'Listening — tap again to finish', cancelled)
                        if not automatic:
                            captured.extend(data)
                            continue
                        pending.extend(data)
                        while len(pending) >= detector.frame_bytes:
                            frame = bytes(pending[:detector.frame_bytes])
                            del pending[:detector.frame_bytes]
                            onset, clip = detector.feed(frame)
                            if onset:
                                self._status('listening', 'Listening · Finish speaking to send', cancelled)
                            if clip is not None:
                                return clip
                            if self.state == 'listening' and not detector.active:
                                self._status('armed', 'Hands-free on · Speak when ready', cancelled)
            finally:
                self._stop_process(process)
                if process.stdout is not None:
                    process.stdout.close()
                with self._lock:
                    self._processes.discard(process)
                    self._process_tasks.pop(process, None)

    def _run_audio(self, argv, cancelled, limit, recording=False, private=False, on_start=None):
        self._check(cancelled)
        # Use a file for diagnostics so subprocess pipe buffers cannot deadlock.
        with tempfile.TemporaryFile() as errors:
            process = self._popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=errors, start_new_session=True)
            with self._lock:
                self._process = process
                self._processes.add(process)
                self._process_tasks[process] = cancelled
            try:
                if on_start is not None:
                    on_start()
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
                    self._processes.discard(process)
                    self._process_tasks.pop(process, None)
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
