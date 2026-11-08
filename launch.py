#!/usr/bin/env python3
"""Start or reopen this workspace's local CV Studio without duplicate servers."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
LOCAL = HERE / ".local"


def healthy(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
            data = json.load(response)
        return data.get("app") == "cv-studio" and data.get("workspace_id") == hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
    except (OSError, ValueError):
        return False


def available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as check:
        check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            check.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def launch(*, open_browser=True, page=None):
    os.umask(0o077)
    LOCAL.mkdir(parents=True, exist_ok=True)
    lock_path = LOCAL / "launch.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        saved = LOCAL / "server.json"
        try:
            previous = json.loads(saved.read_text())
            ports = [int(previous["port"])] + list(range(8767, 8780))
        except (OSError, ValueError, KeyError, TypeError):
            ports = list(range(8767, 8780))
        port = next((p for p in dict.fromkeys(ports) if healthy(p)), None)
        if port is None:
            port = next((p for p in range(8767, 8780) if available(p)), None)
            if port is None:
                raise RuntimeError("CV Studio needs a free local port between 8767 and 8779.")
            with (LOCAL / "server.log").open("ab") as log:
                child = subprocess.Popen(
                    [sys.executable, str(HERE / "server.py"), "--port", str(port), "--root", str(ROOT)],
                    cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    start_new_session=True,
                )
            for _ in range(80):
                if healthy(port):
                    break
                if child.poll() is not None:
                    raise RuntimeError(f"CV Studio could not start. Details: {LOCAL / 'server.log'}")
                time.sleep(0.15)
            else:
                raise RuntimeError(f"CV Studio is taking longer to start. Details: {LOCAL / 'server.log'}")
            saved.write_text(json.dumps({"pid": child.pid, "port": port}) + "\n")
        url = f"http://127.0.0.1:{port}" + ("#prospect" if page == "prospect" else "")
    if open_browser:
        if sys.platform == "darwin":
            opened = subprocess.run(["open", "-a", "Google Chrome", url], capture_output=True).returncode == 0
            if not opened:
                webbrowser.open(url)
        else:
            webbrowser.open(url)
    return url


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--page", choices=["prospect"])
    args = parser.parse_args()
    try:
        print(launch(open_browser=not args.no_browser, page=args.page), flush=True)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
