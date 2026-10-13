#!/usr/bin/env python3
"""Explicit VoiceStudio setup: install only Peter's missing local voice weights."""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
from app.voice import local_voice_url  # noqa: E402
from scripts.voice_service import start_service, stop_owned, StartupCancelled  # noqa: E402

MODELS = (("KittenML/kitten-tts-mini-0.8", "~80 MB", "Apache-2.0"),
          ("Systran/faster-whisper-base", "~150 MB", "MIT"))


def api_json(session, url, path, *, data=None):
    response = session.request("GET" if data is None else "POST", url + path,
                               json=data, timeout=(3, 30), allow_redirects=False)
    try:
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"VoiceStudio {path} returned HTTP {response.status_code}: {response.text[:300]}")
        return response.json()
    finally:
        response.close()


def install_models(session, url, *, budget=900, cancel=None, report=print):
    """Observe real install jobs; no empty-jobs success without installed weights."""
    cancel = cancel or threading.Event()
    for repo_id, size, license_name in MODELS:
        if cancel.is_set():
            raise StartupCancelled("Voice model installation cancelled.")
        catalogue = api_json(session, url, "/models")
        if catalogue.get("target", "local") != "local":
            raise RuntimeError("VoiceStudio's model catalogue targets a remote worker. Select the local backend before installing Peter's models.")
        entries = {entry["repo_id"]: entry for entry in catalogue.get("models", [])}
        if repo_id not in entries:
            raise RuntimeError("The running VoiceStudio version does not advertise " + repo_id)
        if entries[repo_id].get("installed"):
            report("Reusing installed " + repo_id)
            continue
        report(f"Installing {repo_id} ({size}, {license_name}) through VoiceStudio's local API.")
        api_json(session, url, "/models/install", data={"repo_id": repo_id, "target": "local"})
        deadline = time.monotonic() + max(10, min(3600, budget))
        while time.monotonic() < deadline:
            if cancel.is_set():
                raise StartupCancelled("Voice model installation cancelled.")
            jobs = api_json(session, url, "/models/install/status").get("jobs", [])
            failed = next((job for job in jobs if job.get("repo_id") == repo_id and job.get("state") == "failed"), None)
            if failed:
                raise RuntimeError("VoiceStudio could not install " + repo_id + ": " + str(failed.get("message") or failed.get("error") or "see voice-data/omnivoice.log"))
            catalogue = api_json(session, url, "/models")
            installed = any(entry.get("repo_id") == repo_id and entry.get("installed") for entry in catalogue.get("models", []))
            if installed:
                report("Installed and verified " + repo_id)
                break
            cancel.wait(1)
        else:
            raise RuntimeError("Voice model installation timed out for " + repo_id + ". Check VoiceStudio's model status before retrying.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=ROOT / ".runtime" / "VoiceStudio")
    parser.add_argument("--url", default="http://127.0.0.1:3900")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--budget", type=float, default=900)
    args = parser.parse_args(argv)
    cancelled, process, previous = threading.Event(), None, {}
    def stop(_signum, _frame):
        cancelled.set()
    for kind in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[kind] = signal.signal(kind, stop)
    try:
        url = local_voice_url(args.url)
        process, health = start_service(args.runtime, url, data_dir=args.data_dir,
                                        cache_dir=args.cache_dir, cancel=cancelled, detached=True)
        print("Verified VoiceStudio " + str(health.get("version", "")) + " on " + str(health.get("device", "")), flush=True)
        with requests.Session() as session:
            session.trust_env = False
            install_models(session, url, budget=args.budget, cancel=cancelled,
                           report=lambda message: print(message, flush=True))
        return 0
    except StartupCancelled:
        return 130
    except (requests.RequestException, RuntimeError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        stop_owned(process, group=True)
        for kind, handler in previous.items():
            signal.signal(kind, handler)


if __name__ == "__main__":
    raise SystemExit(main())
