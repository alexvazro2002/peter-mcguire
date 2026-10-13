"""Persistent JSON-lines worker. Runs inside the existing AI Python environment."""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import traceback
import time

from app.config import load_config
from library.indexing import build_index, index_changed
from library.paths import resolve_source
from library.retrieval import Librarian
from library.server import ModelServer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--index-only", action="store_true")
    parser.add_argument("--external-model", action="store_true", help="UI owns the shared model process")
    args = parser.parse_args()
    protocol = sys.stdout

    def emit(kind, **payload):
        protocol.write(json.dumps({"type": kind, **payload}, ensure_ascii=False) + "\n")
        protocol.flush()

    log = lambda message: emit("log", message=message)
    progress = lambda **event: emit("progress", **event)
    settings = load_config()["library"]
    server = ModelServer(settings, log)
    try:
        if args.external_model and settings.get("runtime_provider") == "lmstudio" and not settings.get("external_model"):
            # Same provider and actual local port as the UI-owned runtime, without
            # loading another model or starting/stopping the shared service.
            from library.lmstudio import LMStudio
            LMStudio(settings, log).resolve_endpoint(time.monotonic() + float(settings.get("startup_timeout", 180)))
        # Third-party logging cannot corrupt the JSON protocol.
        with contextlib.redirect_stdout(sys.stderr):
            paths = resolve_source(args.source)
            if args.rebuild or not paths.indexed or index_changed(paths):
                build_index(paths, settings, log, progress)
            if args.index_only:
                emit("ready", source=str(paths.source), index=str(paths.index))
                return 0
            progress(phase="loading", total=None, completed=0, message="Loading search index")
            librarian = Librarian(paths, settings)
            progress(phase="connecting", total=None, completed=0, message="Connecting to your local AI model")
            if args.external_model:
                from library.server import validate_url
                validate_url(settings["server_url"])
                deadline = time.monotonic() + float(settings.get("startup_timeout", 180))
                while not server.healthy():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("The shared local model is not ready. Check Settings and the model startup log.")
                    time.sleep(.5)
            else:
                server.ensure()
            emit("ready", source=str(paths.source), index=str(paths.index),
                 chunks=len(librarian.metadata), documents=librarian.documents, sources=librarian.sources)
            for line in sys.stdin:
                try:
                    request = json.loads(line)
                    if request.get("command") == "close":
                        break
                    question = str(request.get("question", "")).strip()
                    if not question or len(question) > 8000:
                        raise ValueError("Enter a question between 1 and 8,000 characters.")
                    is_search = request.get("command") == "search"
                    emit("thinking", message="Searching passages…" if is_search else "Reading evidence…")
                    started = time.monotonic()
                    if is_search:
                        results = librarian.search(question, request.get("source"), limit=20)
                        emit("results", results=results, query=question, seconds=round(time.monotonic() - started, 3))
                    else:
                        answer, sources = librarian.ask(question, request.get("source"))
                        emit("answer", answer=answer, sources=sources)
                except Exception as error:
                    emit("error", message=str(error))
    except Exception as error:
        message = str(error)
        if isinstance(error, ModuleNotFoundError):
            message = f"Missing AI dependency: {error.name}. Use your existing AI environment's Python interpreter in Settings, " \
                      "or install requirements-ai.txt into that environment. The models do not need to be uploaded."
        emit("error", message=message)
        traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        server.close()
        emit("closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
