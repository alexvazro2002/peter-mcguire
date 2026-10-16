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
from app.speech import speech_segments
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
        def poll(self): return self.returncode
    def factory(argv, **kwargs):
        if Path(argv[0]).name in {"pw-record", "arecord"}:
            import subprocess, sys
            seen.append((argv, kwargs))
            return subprocess.Popen([sys.executable, '-u', '-c',
                "import sys;sys.stdout.buffer.write(b'\\0\\0'*16000);sys.stdout.flush()"], **kwargs)
        return Process(argv, **kwargs)
    return factory


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
        payloads = [json.loads(item[1]) for item in capture['requests']]
        assert [payload['input'] for payload in payloads] == speech_segments(expected)
        assert all(payload == {"model": "kittentts", "input": payload['input'], "voice": "expr-voice-2-m", "response_format": "wav", "speed": 1.0} for payload in payloads)
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
        assert seen[0][0] == ["/usr/bin/pw-record", "--raw", "--rate=16000", "--channels=1", "--format=s16", "-"]
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
    controller.sentence_pause = 0
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
    controller.sentence_pause = 0
    try:
        text = "unbroken" * 700
        controller.speak(text)
        wait_event(controller, "voice_status", "speaking")
        wait_event(controller, "voice_status", "idle")
        chunks = [json.loads(body)["input"] for path, body, _ in capture["requests"] if path == "/v1/audio/speech"]
        assert "".join(chunks) == text and max(map(len, chunks)) <= 4096
        assert len(seen) == len(speech_segments(text))
        assert all(not Path(argv[1]).exists() for argv, _ in seen)
    finally:
        controller.close()


def test_second_toggle_finishes_capture_before_transcribing(speech_server):
    import subprocess, sys
    url, capture = speech_server
    processes = []
    def record(argv, **kwargs):
        process = subprocess.Popen([sys.executable, '-u', '-c',
            "import sys,time;sys.stdout.buffer.write(b'\\0\\0'*16000);sys.stdout.flush();time.sleep(10)"], **kwargs)
        processes.append(process)
        return process
    controller = VoiceController({"voice_url": url}, which=lambda _: "/usr/bin/pw-record", popen=record)
    try:
        controller.toggle_listen()
        wait_event(controller, 'voice_status', 'listening')
        assert capture['requests'] == []
        controller.toggle_listen()
        assert wait_event(controller, "transcript")["text"] == "Add a task for tomorrow."
        wait_event(controller, "voice_status", "idle")
        assert processes[0].poll() is not None
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


@pytest.mark.parametrize("pitch,speed", [(None, 1.0), (-1.5, 1.0), (0.8, 1.15)])
def test_british_voice_uses_installed_runtime_and_preserves_private_text(tmp_path, pitch, speed):
    from app.voice import VoiceController
    model = tmp_path / 'model'
    model.mkdir()
    for name in ('model.int8.onnx', 'voices.bin', 'tokens.txt'):
        (model / name).write_bytes(b'fixture')
    runtime = tmp_path / 'runtime'
    (runtime / '.venv/bin').mkdir(parents=True)
    (runtime / '.venv/bin/python').write_text('fixture')
    settings = dict(voice_model='kokoro-onnx', voice_runtime=str(runtime), british_voice_path=str(model), voice_id='bm_george', voice_speed=speed)
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
        if '--segments-file' in argv:
            textfile = Path(argv[argv.index('--segments-file')+1])
            assert json.loads(textfile.read_text()) == ['Private synthetic reply']
            output = Path(argv[argv.index('--output-dir')+1]) / '0000.wav'; output.write_bytes(wav_bytes())
        elif '-af' in argv:
            Path(argv[-1]).write_bytes(wav_bytes())
    controller._run_audio = run
    try:
        controller._speak_british('Private synthetic reply', '/usr/bin/pw-play', threading.Event())
        assert calls[0][1]['private'] is True
        assert calls[0][0][calls[0][0].index('--voice')+1] == 'bm_george'
        assert calls[0][0][calls[0][0].index('--speed')+1] == str(speed)
        assert all('Private synthetic reply' not in arg for argv, _ in calls for arg in argv)
        if pitch:
            assert f'pitch={2 ** (pitch / 12):.8f}' in calls[1][0][calls[1][0].index('-af')+1]
            assert 'tempo=1' in calls[1][0][calls[1][0].index('-af')+1]
        else:
            assert len(calls) == 2 and 'ffmpeg' not in requested_tools
        assert calls[-1][0][0] == '/usr/bin/pw-play'
        assert not Path(calls[-1][0][-1]).exists()
    finally:
        controller.close()


def test_sentence_playback_emits_caption_text_duration_and_reply_identity(tmp_path):
    from app.voice import VoiceController,utterance_id
    calls=[]
    controller=VoiceController({})
    def playback(args,*a,**kw):
        calls.append(args)
        assert not any(e['type']=='voice_caption' for e in list(controller.events.queue)) if len(calls)==1 else True
        kw['on_start']()
    controller._run_audio=playback
    controller.sentence_pause=0
    def produce(chunks,stop):
        for index,chunk in enumerate(chunks):
            partial=tmp_path/f'{index:04d}.partial'
            partial.write_bytes(wav_bytes())
            partial.replace(tmp_path/f'{index:04d}.wav')
    try:
        controller._sentence_pipeline('First sentence. Second sentence.',tmp_path,produce,'fake-player',threading.Event())
        captions=[]
        while not controller.events.empty():
            event=controller.events.get_nowait()
            if event['type']=='voice_caption':captions.append(event)
        assert [e['text'] for e in captions]==['First sentence.','Second sentence.']
        assert all(e['seconds']>0 and e['utterance_id']==utterance_id('First sentence. Second sentence.') for e in captions)
        assert len(calls)==2
    finally:controller.close()


def hands_free_capture(monkeypatch, captures, *, voiced=True, trigger=None):
    import subprocess
    import sys
    from app.hands_free import UtteranceDetector
    class Vad:
        def is_speech(self, frame, rate): return frame[0] == 1
    monkeypatch.setattr('app.voice.UtteranceDetector', lambda **kw: UtteranceDetector(vad=Vad(), **kw))
    def popen(argv, **kwargs):
        if Path(argv[0]).name in {'pw-record', 'arecord'}:
            # Real stdout pipe/select/cancellation; no microphone access.
            frames = ("b'\\0\\0'*480*10+b'\\x01\\0'*480*30+b'\\0\\0'*480*35" if voiced else "b'\\0\\0'*480*100")
            script = "import sys,time;from pathlib import Path\n" + "clip=" + frames + "\n" + "sys.stdout.buffer.write(clip);sys.stdout.flush()\n"
            script += "trigger=Path(" + repr(str(trigger)) + ") if " + repr(trigger is not None) + " else None\n"
            script += "while True:\n if trigger is not None and trigger.exists():\n  trigger.unlink();sys.stdout.buffer.write(clip)\n else:sys.stdout.buffer.write(b'\\0\\0'*480)\n sys.stdout.flush();time.sleep(.03)"
            process = subprocess.Popen([sys.executable, '-u', '-c', script], **kwargs)
            captures.append(process)
            return process
        return audio_process_factory([])(argv, **kwargs)
    return popen


def test_hands_free_transcribes_one_utterance_and_waits_for_reply_before_rearming(speech_server, monkeypatch, tmp_path):
    url, capture = speech_server
    processes = []
    trigger=tmp_path/'next-utterance'
    controller = VoiceController(dict(voice_url=url, hands_free=True), which=lambda n: '/usr/bin/' + n,
                                 popen=hands_free_capture(monkeypatch, processes, trigger=trigger))
    try:
        controller.allow_hands_free(True)
        assert wait_event(controller, 'voice_status', 'armed')['message'].startswith('Hands-free on')
        wait_event(controller, 'voice_status', 'listening')
        assert wait_event(controller, 'transcript')['text'] == 'Add a task for tomorrow.'
        wait_event(controller, 'voice_status', 'idle')
        time.sleep(.2)
        assert len(processes) == 1 and processes[0].poll() is None
        assert len([row for row in capture['requests'] if row[0].endswith('transcriptions')]) == 1
        controller.speak('Your task is ready.')
        controller.allow_hands_free(True)  # UI may re-arm while speech is queued.
        wait_event(controller, 'voice_status', 'speaking')
        assert len(processes) == 1
        wait_event(controller, 'voice_status', 'idle')
        wait_event(controller, 'voice_status', 'armed')
        trigger.touch()
        assert wait_event(controller, 'transcript')['text'] == 'Add a task for tomorrow.'
        assert len(processes) == 1 and processes[0].poll() is None
    finally:
        controller.close()
    assert all(process.poll() is not None for process in processes)


def test_idle_hands_free_does_not_transcribe_silence_and_toggle_closes_microphone(speech_server, monkeypatch):
    url, capture = speech_server
    processes = []
    controller = VoiceController(dict(voice_url=url, hands_free=True), which=lambda n: '/usr/bin/' + n,
                                 popen=hands_free_capture(monkeypatch, processes, voiced=False))
    try:
        controller.allow_hands_free(True)
        wait_event(controller, 'voice_status', 'armed')
        time.sleep(.15)
        assert capture['requests'] == []
        controller.set_hands_free(False)
        deadline = time.monotonic() + 2
        while processes[0].poll() is None and time.monotonic() < deadline: time.sleep(.01)
        assert processes[0].poll() is not None
        time.sleep(.2)
        assert len(processes) == 1 and capture['requests'] == []
    finally:
        controller.close()


def test_hands_free_microphone_failure_is_reported_once_without_restart_loop(speech_server, monkeypatch):
    import subprocess
    import sys
    url, _ = speech_server
    captures = []
    def popen(argv, **kwargs):
        process = subprocess.Popen([sys.executable, '-c', 'pass'], **kwargs)
        captures.append(process)
        return process
    controller = VoiceController(dict(voice_url=url, hands_free=True), which=lambda n: '/usr/bin/' + n, popen=popen)
    try:
        controller.allow_hands_free(True)
        assert 'microphone' in wait_event(controller, 'voice_error')['message'].lower()
        time.sleep(.25)
        assert len(captures) == 1
    finally:
        controller.close()


def test_finishing_during_microphone_startup_waits_for_pcm_instead_of_empty_wav(speech_server):
    import subprocess, sys
    url, capture = speech_server
    processes = []
    def delayed(argv, **kwargs):
        process = subprocess.Popen([sys.executable, '-u', '-c',
            "import sys,time;time.sleep(.25);sys.stdout.buffer.write(b'\0\0'*16000);sys.stdout.flush();time.sleep(10)".replace('\0', '\\0')], **kwargs)
        processes.append(process); return process
    controller = VoiceController({'voice_url': url}, which=lambda _: '/usr/bin/pw-record', popen=delayed)
    try:
        controller.toggle_listen()
        wait_event(controller, 'voice_status', 'thinking')
        time.sleep(.08)
        controller.toggle_listen()
        assert controller.state == 'thinking'
        wait_event(controller, 'voice_status', 'listening')
        assert wait_event(controller, 'transcript')['text'] == 'Add a task for tomorrow.'
        assert len(processes) == 1 and processes[0].poll() is not None
        assert b'RIFF' in capture['requests'][0][1]
    finally: controller.close()


def test_unresponsive_microphone_times_out_instead_of_armed_forever(speech_server):
    import subprocess, sys
    url, _ = speech_server
    processes = []
    def empty(argv, **kwargs):
        process = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(10)'], **kwargs)
        processes.append(process); return process
    controller = VoiceController({'voice_url': url, 'hands_free': True}, which=lambda _: '/usr/bin/pw-record', popen=empty)
    controller.capture_start_timeout = .15
    try:
        controller.allow_hands_free(True)
        assert 'not delivering audio' in wait_event(controller, 'voice_error')['message']
        assert controller.state == 'error' and processes[0].poll() is not None
    finally: controller.close()


def test_switching_to_hands_free_stops_manual_recording_before_rearming(speech_server, monkeypatch):
    url, _ = speech_server
    processes = []
    controller = VoiceController({'voice_url': url}, which=lambda n: '/usr/bin/' + n,
                                 popen=hands_free_capture(monkeypatch, processes))
    try:
        controller.toggle_listen()
        wait_event(controller, 'voice_status', 'listening')
        controller.set_hands_free(True)
        controller.allow_hands_free(True)
        wait_event(controller, 'voice_status', 'armed')
        assert len(processes) == 2 and processes[0].poll() is not None
        assert wait_event(controller, 'transcript')['text'] == 'Add a task for tomorrow.'
    finally: controller.close()


def test_cancel_after_dequeue_prevents_the_old_task_from_starting(speech_server):
    url,capture=speech_server
    controller=VoiceController({'voice_url':url},which=lambda n:'/usr/bin/'+n,popen=audio_process_factory([]))
    dequeued,release=threading.Event(),threading.Event()
    original=controller._pending.get
    def get(*args,**kw):
        task=original(*args,**kw)
        dequeued.set();release.wait(2)
        return task
    controller._pending.get=get
    try:
        time.sleep(.15)
        controller.speak('A cancelled reply.')
        assert dequeued.wait(2)
        controller.cancel();release.set();time.sleep(.2)
        assert capture['requests']==[]
    finally:release.set();controller.close()


def test_transcript_publication_rechecks_cancellation_and_marks_generation(speech_server, tmp_path):
    from app.voice import _Cancelled
    url,_=speech_server
    c=VoiceController({'voice_url':url})
    path=tmp_path/'capture.wav';path.write_bytes(wav_bytes())
    cancelled=threading.Event();status=c._status
    def cancel_at_status(state,message='',token=None):
        status(state,message,token)
        if message=='Speech recognized':cancelled.set()
    c._status=cancel_at_status
    try:
        import requests
        with requests.Session() as session:
            with pytest.raises(_Cancelled):c._transcribe(session,path,cancelled)
        assert not any(e['type']=='transcript' for e in list(c.events.queue))
        previous={'type':'transcript','text':'An old voice command.','generation':c._generation}
        assert c.valid_event(previous)
        c.cancel()
        assert not c.valid_event(previous)
    finally:c.close()


def test_cancel_clears_an_utterance_enqueued_while_waiting_for_capture_lock(speech_server):
    url, _ = speech_server

    class CaptureOnlyController(VoiceController):
        def _work(self):
            # Keep this test's captured utterance queued until cancellation.
            pass

    controller = CaptureOnlyController({'voice_url': url})
    attempted = threading.Event()
    original_lock = controller._lock

    class CaptureLock:
        def __enter__(self):
            if threading.current_thread().name == 'test-voice-cancel':
                attempted.set()
            original_lock.acquire()
            return self

        def __exit__(self, *_):
            original_lock.release()

    controller._lock = CaptureLock()
    cancellation = threading.Thread(target=controller.cancel, name='test-voice-cancel')
    try:
        controller._auto_allowed.set()
        with controller._lock:
            assert controller._microphone_ready()
            cancellation.start()
            assert attempted.wait(2)
            # Capture was already inside the queueing lock when Cancel began.
            controller._awaiting_transcript = True
            controller._pending.put_nowait(('hands_free', b'fixture', threading.Event(), controller._generation))
        cancellation.join(timeout=2)
        assert not cancellation.is_alive()
        assert controller._pending.empty()
        assert not controller._awaiting_transcript
        controller._auto_allowed.set()
        assert controller._microphone_ready()
    finally:
        cancellation.join(timeout=2)
        controller.close()


def interrupt_capture(monkeypatch, captures, trigger):
    """A paced PCM pipe exercises the same select/read path as the desk mic."""
    import subprocess
    import sys
    from app.hands_free import UtteranceDetector

    class Vad:
        def is_speech(self, frame, rate): return frame != b'\0\0' * 480

    monkeypatch.setattr('app.voice.UtteranceDetector',
                        lambda **kwargs: UtteranceDetector(vad=Vad(), **kwargs))
    script = '''import sys,time
from pathlib import Path
trigger=Path(TRIGGER)
silence=b'\\0\\0'*480
while True:
 frames=[silence]
 if trigger.exists():
  kind=trigger.read_text();trigger.unlink()
  if kind=='speech':frames=[b'\\xb0\\x04'*480]+[b'\\xd0\\x07'*480]*29+[silence]*35
  elif kind=='noise':frames=[b'\\x01\\0'*480]*15+[silence]*35
  elif kind=='click':frames=[b'\\xd0\\x07'*480]*3+[silence]*35
 for frame in frames:
  sys.stdout.buffer.write(frame);sys.stdout.flush();time.sleep(.03)
'''.replace('TRIGGER', repr(str(trigger)))

    def popen(argv, **kwargs):
        if Path(argv[0]).name in {'pw-record', 'arecord'}:
            process = subprocess.Popen([sys.executable, '-u', '-c', script], **kwargs)
            captures.append(process)
            return process
        return subprocess.Popen(argv, **kwargs)
    return popen


def test_hands_free_interrupts_thinking_and_keeps_the_whole_replacement_request(speech_server, monkeypatch, tmp_path):
    url, capture = speech_server
    processes, trigger = [], tmp_path / 'interrupt'
    controller = VoiceController({'voice_url': url, 'hands_free': True},
        which=lambda name: '/usr/bin/' + name,
        popen=interrupt_capture(monkeypatch, processes, trigger))
    try:
        old_cancel = controller._active_cancel
        controller.allow_interruptions(True)  # Thinking gate, normal capture paused.
        trigger.write_text('speech')
        interruption = wait_event(controller, 'voice_interrupt')
        assert controller.valid_event(interruption) and old_cancel.is_set()
        # The UI releases reasoning without cancelling voice a second time.
        controller.allow_interruptions(False)
        controller.allow_hands_free(True)
        transcript = wait_event(controller, 'transcript')
        assert transcript['interrupted'] and controller.valid_event(transcript)
        assert transcript['generation'] == interruption['generation']
        assert transcript['text'] == 'Add a task for tomorrow.'
        requests = [body for path, body, _ in capture['requests'] if path.endswith('transcriptions')]
        assert len(requests) == 1
        assert b'\xb0\x04' * 480 + b'\xd0\x07' * 480 * 29 in requests[0]
        assert len(processes) == 1 and processes[0].poll() is None
    finally:
        controller.close()


def test_hands_free_speech_interrupt_stops_playback_process(speech_server, monkeypatch, tmp_path):
    import sys
    url, capture = speech_server
    processes, trigger, playback = [], tmp_path / 'interrupt', []
    controller = VoiceController({'voice_url': url, 'hands_free': True},
        which=lambda name: '/usr/bin/' + name,
        popen=interrupt_capture(monkeypatch, processes, trigger))

    def speak(session, text, cancelled, **kwargs):
        controller._status('speaking', 'Speaking', cancelled)
        controller._run_audio([sys.executable, '-c', 'import time;time.sleep(20)'],
                              cancelled, limit=30, on_start=lambda: playback.append(controller._process))
    controller._speak = speak
    try:
        controller.speak('A long answer.')
        wait_event(controller, 'voice_status', 'speaking')
        controller.allow_interruptions(True)
        trigger.write_text('speech')
        wait_event(controller, 'voice_interrupt')
        assert playback and playback[0].poll() is not None
        assert wait_event(controller, 'transcript')['interrupted']
        assert not any(path.endswith('speech') for path, _, _ in capture['requests'])
    finally:
        controller.close()


@pytest.mark.parametrize('kind', ['noise', 'click'])
def test_quiet_noise_and_brief_clicks_do_not_interrupt(speech_server, monkeypatch, tmp_path, kind):
    url, capture = speech_server
    trigger = tmp_path / 'interrupt'
    controller = VoiceController({'voice_url': url, 'hands_free': True},
        which=lambda name: '/usr/bin/' + name,
        popen=interrupt_capture(monkeypatch, [], trigger))
    try:
        controller.allow_interruptions(True)
        trigger.write_text(kind)
        time.sleep(1.65)
        assert controller._generation == 0 and not controller._awaiting_transcript
        assert capture['requests'] == []
        assert not any(event['type'] == 'voice_interrupt' for event in list(controller.events.queue))
    finally:
        controller.close()


def test_explicit_stop_discards_the_interruption_still_being_spoken(speech_server, monkeypatch, tmp_path):
    url, capture = speech_server
    trigger = tmp_path / 'interrupt'
    controller = VoiceController({'voice_url': url, 'hands_free': True},
        which=lambda name: '/usr/bin/' + name,
        popen=interrupt_capture(monkeypatch, [], trigger))
    try:
        controller.allow_interruptions(True)
        trigger.write_text('speech')
        interruption = wait_event(controller, 'voice_interrupt')
        controller.cancel()
        assert not controller.valid_event(interruption)
        time.sleep(1.85)
        assert capture['requests'] == [] and not controller._awaiting_transcript
        assert not any(event['type'] == 'transcript' for event in list(controller.events.queue))
    finally:
        controller.close()
