"""Background process IO with queues; never update a Flet control from a reader thread."""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading

from app.config import BASE_DIR


class Session:
    def __init__(self):
        self.process = None
        self.events = queue.Queue()
        self.protocol = False
        self.graceful_input = False

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self, command: list[str], protocol=False, graceful_input=False):
        if self.running:
            raise ValueError("A process is already running. Stop it before starting another.")
        self.protocol = protocol
        self.graceful_input = graceful_input
        env = os.environ.copy()
        env.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.process = subprocess.Popen(command, cwd=BASE_DIR, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        encoding="utf-8", errors="replace", bufsize=1,
                                        start_new_session=os.name != "nt",
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        proc = self.process

        def read(handle, is_protocol):
            for line in handle:
                try:
                    event = json.loads(line) if is_protocol else {"type": "log", "message": line.rstrip()}
                    if not isinstance(event, dict) or "type" not in event:
                        raise ValueError("Not an event")
                except ValueError:
                    event = {"type": "log", "message": line.rstrip()}
                self.events.put(event)
            handle.close()

        threading.Thread(target=read, args=(proc.stdout, protocol), daemon=True).start()
        threading.Thread(target=read, args=(proc.stderr, False), daemon=True).start()

        def wait():
            code = proc.wait()
            self.events.put({"type": "exit", "code": code})
        threading.Thread(target=wait, daemon=True).start()

    def send(self, payload):
        if not self.running:
            raise ValueError("The AI session is not running. Launch the library again.")
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def stop(self):
        proc = self.process
        if not proc or proc.poll() is not None:
            return
        # Graceful close gives the worker time to clean up its owned model server.
        if self.protocol:
            try:
                self.send({"command": "close"})
            except (OSError, ValueError):
                pass
        elif self.graceful_input:
            try:
                proc.stdin.write("STOP\n")
                proc.stdin.flush()
            except OSError:
                pass

        def finish():
            try:
                proc.wait(timeout=3)
                return
            except subprocess.TimeoutExpired:
                pass
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=3)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGKILL)
        # A non-daemon cleanup thread also completes when the desktop window closes.
        threading.Thread(target=finish, daemon=False).start()
