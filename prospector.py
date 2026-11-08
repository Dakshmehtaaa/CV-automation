"""Run the bundled Prospector in an isolated, lazy local process."""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading


class ProspectorService:
    """Keep Prospector's imports, records and API separate from Studio's."""

    def __init__(self, root):
        self.root = Path(root)
        self.bundle = self.root / "Prospector"
        self.lock = threading.Lock()
        self.process = None
        self.port = None
        self.closed = False
        # Studio's own address, so Prospect's Applied board can read /api/roles.
        self.studio_url = None

    def ensure_started(self):
        with self.lock:
            if self.closed:
                raise RuntimeError("CV Studio is closing. Reopen it to use Prospect.")
            if self.process is not None and self.process.poll() is None:
                return self.port
            self._stop()
            if not (self.bundle / "server.py").is_file():
                raise FileNotFoundError("Prospector is missing from this CV folder.")
            local = self.root / "Resources" / "Studio" / ".local"
            local.mkdir(parents=True, exist_ok=True)
            environment = dict(os.environ)
            if self.studio_url:
                environment["CV_STUDIO_URL"] = self.studio_url
            with (local / "prospector.log").open("ab") as log:
                self.process = subprocess.Popen(
                    [sys.executable, str(self.bundle / "server.py"), "--port", "0",
                     "--embedded"],
                    cwd=self.bundle, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=log, start_new_session=True, env=environment,
                )
            try:
                ready, _, _ = select.select([self.process.stdout], [], [], 10)
                if not ready:
                    raise RuntimeError("Prospector took too long to start. Try again.")
                state = json.loads(self.process.stdout.readline())
                if state.get("app") != "prospector" or not isinstance(state.get("port"), int):
                    raise ValueError("Invalid Prospector startup response.")
                self.port = state["port"]
                return self.port
            except (OSError, ValueError, RuntimeError):
                self._stop()
                raise RuntimeError(
                    "Prospector could not start. Check Resources/Studio/.local/prospector.log."
                ) from None

    def _stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.process.wait(timeout=5)
            if self.process.stdout is not None:
                self.process.stdout.close()
            if self.process.stdin is not None:
                self.process.stdin.close()
        self.process = None
        self.port = None

    def close(self):
        with self.lock:
            self.closed = True
            self._stop()
