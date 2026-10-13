"""One desktop lifecycle for NexusDesk, its local model, and owned voice service."""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def healthy(url):
    import requests
    from scripts.voice_service import healthy as voice_health
    from app.voice import local_voice_url
    session = requests.Session()
    session.trust_env = False
    try:
        return bool(voice_health(session, local_voice_url(url)))
    except ValueError:
        return False
    finally:
        session.close()


def diagnose(config, root=ROOT):
    from scripts.linux_runtime import check_shared_library
    runtime = Path(config['peter'].get('voice_runtime') or root / '.runtime/VoiceStudio').expanduser()
    native_library = root / '.runtime/lib/libmpv.so.1'
    native_error = ''
    try:
        check_shared_library(native_library if native_library.exists() else 'libmpv.so.1', root)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
        native_error = str(error)
    return dict(project=str(root), python=sys.executable,
                voice_runtime=str(runtime),
                voice_python_exists=(runtime / '.venv/bin/python').is_file(),
                voice_backend_exists=(runtime / 'backend/main.py').is_file(),
                voice_ready=healthy(config['peter']['voice_url']),
                native_library_ready=not native_error, native_library_error=native_error,
                model_url=config['library']['server_url'],
                model_path_exists=Path(config['library'].get('model_path', '')).is_file())


def terminate(process):
    from scripts.voice_service import stop_owned
    stop_owned(process, group=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--diagnose', action='store_true')
    parser.add_argument('--web', action='store_true')
    parser.add_argument('--port', type=int, default=8550)
    args = parser.parse_args(argv)
    from app.config import DATA_DIR, load_config
    config = load_config()
    if args.diagnose:
        print(json.dumps(diagnose(config), indent=2))
        return 0
    state = DATA_DIR / 'logs'
    state.mkdir(parents=True, exist_ok=True)
    lock = (state / 'peter.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('Peter is already running. Switch to the existing window.', flush=True)
        return 0
    voice, ui = None, None
    cancelled = threading.Event()
    log = (state / 'startup.log').open('a', buffering=1, encoding='utf-8')
    log.write('\nPeter startup ' + time.strftime('%Y-%m-%d %H:%M:%S') + '\n')
    def stop(_signal, _frame):
        cancelled.set()
    previous = {}
    for kind in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[kind] = signal.signal(kind, stop)
    try:
        peter = config['peter']
        if cancelled.is_set():
            return 130
        if peter.get('enabled', True) and peter.get('autostart', True) and not healthy(peter['voice_url']):
            runtime = Path(peter.get('voice_runtime') or ROOT / '.runtime/VoiceStudio').expanduser()
            command = [sys.executable, str(ROOT / 'scripts/voice_service.py'), '--runtime', str(runtime),
                       '--url', peter['voice_url'], '--budget', str(peter.get('voice_timeout', 180)),
                       '--data-dir', str(DATA_DIR / '.runtime/voice-data'),
                       '--cache-dir', str(DATA_DIR / '.runtime/voice-cache')]
            if cancelled.is_set():
                return 130
            voice = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
            deadline = time.monotonic() + float(peter.get('voice_timeout', 180))
            while time.monotonic() < deadline and voice.poll() is None and not cancelled.is_set() and not healthy(peter['voice_url']):
                cancelled.wait(.5)
            if cancelled.is_set():
                return 130
            if not healthy(peter['voice_url']):
                log.write('Voice service is unavailable; Peter will show its error in the workspace.\n')
        command = [sys.executable, str(ROOT / 'main_flet.py')]
        if args.web:
            command += ['--web', '--port', str(args.port)]
        if cancelled.is_set():
            return 130
        ui = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                              start_new_session=True)
        while ui.poll() is None and not cancelled.wait(.2):
            pass
        return 130 if cancelled.is_set() else ui.returncode
    finally:
        terminate(ui)
        terminate(voice)
        log.close()
        lock.close()
        for kind, handler in previous.items():
            signal.signal(kind, handler)


if __name__ == '__main__':
    raise SystemExit(main())
