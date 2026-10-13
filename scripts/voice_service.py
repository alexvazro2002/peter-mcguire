#!/usr/bin/env python3
"""Start an installed VoiceStudio backend without embedding its desktop UI.

Installation is explicit. This launcher never downloads Python dependencies or
models. The upstream Electron desktop remains installed independently.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
from app.voice import local_voice_url  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402


def runtime_command(runtime, url):
    runtime = Path(runtime).expanduser().resolve()
    python = runtime / ".venv" / "bin" / "python"
    if not python.is_file() or not os.access(python, os.X_OK) or not (runtime / "backend" / "main.py").is_file():
        raise RuntimeError("VoiceStudio runtime is not installed at " + str(runtime) + ". Run ./install-voice.sh first.")
    parsed = urlsplit(local_voice_url(url))
    if parsed.scheme != "http":
        raise ValueError("The bundled VoiceStudio launcher requires an HTTP loopback endpoint.")
    host = "::1" if parsed.hostname == "::1" else "127.0.0.1"
    port = parsed.port or 80
    return runtime, [str(python), "-m", "uvicorn", "main:app", "--app-dir", "backend",
                     "--host", host, "--port", str(port)]


def service_environment(root=ROOT, data_dir=None, cache_dir=None):
    env = os.environ.copy()
    env.pop("OMNIVOICE_DESKTOP_CONTAINED", None)
    env.pop("OMNIVOICE_BACKEND_DRAIN_FD", None)
    defaults = {
        "OMNIVOICE_DATA_DIR": str(data_dir or root / ".runtime" / "voice-data"),
        "OMNIVOICE_CACHE_DIR": str(cache_dir or root / ".runtime" / "voice-cache"),
        "HF_HOME": str(cache_dir or root / ".runtime" / "voice-cache"),
        "OMNIVOICE_TORCH_VARIANT": "cpu",
        "OMNIVOICE_DEVICE": "cpu",
        "OMNIVOICE_TTS_BACKEND": "kittentts",
        "OMNIVOICE_KITTENTTS_MODEL": "KittenML/kitten-tts-mini-0.8",
        "OMNIVOICE_ASR_BACKEND": "faster-whisper",
        "ASR_MODEL_FASTER": "Systran/faster-whisper-base",
    }
    for key, value in defaults.items():
        env.setdefault(key, value)
    return env


def healthy(session, url):
    try:
        response = session.get(url + "/health", timeout=(2, 3), allow_redirects=False)
        if response.status_code != 200:
            return None
        info = response.json()
        if info.get("status") != "ok":
            return None
        response = session.get(url + "/openapi.json", timeout=(2, 3), allow_redirects=False)
        if response.status_code != 200:
            return None
        paths = response.json().get("paths", {})
        if not all(p in paths for p in ("/v1/audio/speech", "/v1/audio/transcriptions")):
            return None
        return info
    except (requests.RequestException, ValueError, AttributeError):
        return None


class StartupCancelled(RuntimeError):
    pass


def stop_owned(process, *, group=False):
    """Stop only a child we created, including its dedicated group when owned."""
    if process is None:
        return
    if process.poll() is not None:
        return
    def send(value):
        try:
            if group:
                os.killpg(process.pid, value)
            else:
                process.send_signal(value)
        except ProcessLookupError:
            pass
    send(signal.SIGTERM)
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        send(signal.SIGKILL)
        process.wait(timeout=3)
    if group:
        # Descendants can outlive a group leader; they belong to this launch,
        # including a backend that was still importing when its shell stopped.
        send(signal.SIGKILL)


def start_service(runtime, url="http://127.0.0.1:3900", *, budget=180, root=ROOT,
                  data_dir=None, cache_dir=None, detached=False, cancel=None):
    """Return (owned process or None, verified health). Reuse existing service."""
    url = local_voice_url(url)
    cancel = cancel or threading.Event()
    def check_cancel():
        if cancel.is_set():
            raise StartupCancelled("VoiceStudio startup cancelled.")
    with requests.Session() as session:
        session.trust_env = False
        check_cancel()
        info = healthy(session, url)
        check_cancel()
        if info:
            return None, info
        runtime, command = runtime_command(runtime, url)
        log_dir = root / ".runtime"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "voicestudio.log").open("ab") as log:
            check_cancel()
            process = subprocess.Popen(command, cwd=runtime,
                                       env=service_environment(root, data_dir, cache_dir),
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       start_new_session=detached)
        deadline = time.monotonic() + max(5, min(600, budget))
        try:
            while time.monotonic() < deadline:
                check_cancel()
                if process.poll() is not None:
                    # Another launcher may have won the port race; reuse only
                    # after verifying the service's actual speech API.
                    info = healthy(session, url)
                    if info:
                        return None, info
                    raise RuntimeError(f"VoiceStudio exited with {process.returncode}. Check {log_dir / 'voicestudio.log'}.")
                info = healthy(session, url)
                check_cancel()
                if info:
                    return process, info
                cancel.wait(0.5)
            raise RuntimeError(f"VoiceStudio did not become ready at {url}. Check {log_dir / 'voicestudio.log'}.")
        except BaseException:
            stop_owned(process, group=detached)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=os.environ.get("VOICESTUDIO_RUNTIME", ROOT / ".runtime" / "VoiceStudio"))
    parser.add_argument("--url", default="http://127.0.0.1:3900")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--budget", type=float, default=180)
    parser.add_argument("--ensure", action="store_true", help="Leave a ready service running and exit; JSON contains ownership/PID.")
    parser.add_argument("--check", action="store_true", help="Verify health and speech APIs without starting anything.")
    args = parser.parse_args(argv)
    cancelled, process = threading.Event(), None
    previous = {}
    def stop(_signum, _frame):
        cancelled.set()
    for kind in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[kind] = signal.signal(kind, stop)
    try:
        url = local_voice_url(args.url)
        if args.check:
            with requests.Session() as session:
                session.trust_env = False
                info = healthy(session, url)
            if not info:
                raise RuntimeError("VoiceStudio is not ready at " + url)
            print(json.dumps(dict(url=url, started=False, health=info)))
            return 0
        process, info = start_service(args.runtime, url, budget=args.budget,
                                      data_dir=args.data_dir, cache_dir=args.cache_dir, detached=args.ensure,
                                      cancel=cancelled)
        print(json.dumps(dict(url=url, started=process is not None,
                              pid=process.pid if process else None, health=info)), flush=True)
        if args.ensure or process is None:
            if cancelled.is_set():
                stop_owned(process, group=args.ensure)
                return 130
            return 0
        while process.poll() is None and not cancelled.wait(0.2):
            pass
        if cancelled.is_set():
            stop_owned(process)
            return 130
        return process.returncode
    except StartupCancelled:
        return 130
    except (ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        if process is not None and not args.ensure:
            stop_owned(process)
        for kind, handler in previous.items():
            signal.signal(kind, handler)


if __name__ == "__main__":
    raise SystemExit(main())
