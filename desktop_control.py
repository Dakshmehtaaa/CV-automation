"""Scoped control of an observed pipeline owned by the running Codex desktop.

Construction, capabilities(), list(), and get() never interrupt a turn. Only
stop() sends the installed desktop's expected-turn interrupt request. The
descriptor must come from the server's live observer, never from browser input.
No daemon is started, no chat is resumed/messaged/moved, and no process is killed.

This is the installed desktop IPC protocol, not a stable public API. Its route
versions and expected-turn guard are checked against the installed app before
use. A native ACK is not proof that remote agents or background tools stopped.
Receipts therefore keep the recovery hold until separate cleanup is verified.
Tests inject a private fixture transport; they never stop a user's live run.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import threading
import time
import uuid

from live import BoundedSessionReader


ROUTE = "desktop_ipc_expected_turn_interrupt"
INTERRUPT_METHOD = "thread-follower-interrupt-turn"
OWNER_METHOD = "thread-owner-discovery"
FOLLOW_METHOD = "thread-stream-following-changed"
STATE_METHOD = "thread-stream-state-changed"
MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_HEADER_BYTES = 128 * 1024
MAX_TAIL_BYTES = 1024 * 1024
MAX_DESCRIPTOR_AGE = 180
MAX_CHILDREN = 24
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,100}$")
_RECEIPT = re.compile(r"^desktop-stop-[a-f0-9]{32}$")
_TERMINALS = {"task_complete", "task_completed", "turn_completed", "turn_aborted",
              "task_aborted", "task_cancelled", "task_failed", "turn_failed"}


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def _identifier(value):
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


class DesktopControlError(ValueError):
    status = code = 400


class DesktopControlConflict(DesktopControlError):
    status = code = 409


class DesktopControlUnavailable(DesktopControlError):
    status = code = 503


def installed_protocol(app_bundle):
    """Read only the relevant bundled members; fail closed after a protocol change."""
    archive = Path(app_bundle) / "Contents/Resources/app.asar"
    try:
        with archive.open("rb") as stream:
            header = stream.read(16)
            if len(header) != 16:
                raise ValueError("Invalid app archive")
            _, header_size, _, json_size = struct.unpack("<4I", header)
            if not 0 < json_size < 16 * 1024 * 1024:
                raise ValueError("Invalid app archive header")
            tree = json.loads(stream.read(json_size))
            members = tree["files"][".vite"]["files"]["build"]["files"]
            version_source, handler_source = None, None
            for name, entry in members.items():
                if not (name.startswith("src-") or name.startswith("bootstrap-")) or not name.endswith(".js"):
                    continue
                size = entry.get("size", 0)
                if not isinstance(size, int) or not 0 < size < 8 * 1024 * 1024:
                    continue
                stream.seek(8 + header_size + int(entry["offset"]))
                source = stream.read(size).decode("utf-8")
                if '"thread-follower-interrupt-turn":' in source:
                    version_source = source
                if "sendInterruptRequest" in source and "expectedTurnId" in source and "interruptedTurnId" in source:
                    handler_source = source
            expected = {INTERRUPT_METHOD: 4, OWNER_METHOD: 1, FOLLOW_METHOD: 1, STATE_METHOD: 11}
            if not version_source or not handler_source:
                raise ValueError("Scoped desktop control route missing")
            for method, version in expected.items():
                match = re.search(r'"' + re.escape(method) + r'":(\d+)', version_source)
                if not match or int(match[1]) != version:
                    raise ValueError("Desktop control protocol changed")
            # Version 4 must retain the native expected-turn comparison before
            # the request, including the inProgress check. No broad v3 fallback.
            guard = re.search(r"if\(([a-zA-Z_$][\w$]*)!=null&&\(\1!==([a-zA-Z_$][\w$]*)\|\|[a-zA-Z_$][\w$]*\?\.status!==`inProgress`\)\)return null", handler_source)
            if not guard:
                raise ValueError("Expected-turn guard cannot be verified")
            return {"available": True, "versions": expected, "source": "installed_desktop_code"}
    except (OSError, ValueError, KeyError, TypeError, struct.error, UnicodeError):
        return {"available": False, "reason": "This installed desktop control route could not be verified."}


class DesktopIPC:
    """An ephemeral observation/control client, attached only to an existing router."""

    def __init__(self, socket_path, versions, timeout=5):
        self.path = Path(socket_path)
        self.versions = versions
        self.timeout = timeout
        self.socket = None
        self.buffer = b""
        self.client_id = "initializing-client"

    def __enter__(self):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(self.timeout)
        try:
            self.socket.connect(str(self.path))
            response = self._request("initialize", {"clientType": "cv-studio"}, version=0)
            self.client_id = _identifier(response.get("result", {}).get("clientId"))
            if not self.client_id:
                raise DesktopControlUnavailable("The desktop control handshake failed.")
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_args):
        if self.socket:
            self.socket.close()
        self.socket = None
        self.buffer = b""

    def _send(self, value):
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        if len(raw) > MAX_FRAME_BYTES or not self.socket:
            raise DesktopControlUnavailable("The desktop control connection is unavailable.")
        self.socket.sendall(struct.pack("<I", len(raw)) + raw)

    def _receive(self, deadline):
        def more():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Desktop control readback timed out.")
            self.socket.settimeout(remaining)
            chunk = self.socket.recv(65536)
            if not chunk:
                raise DesktopControlUnavailable("The desktop control connection closed.")
            self.buffer += chunk

        while len(self.buffer) < 4:
            more()
        size = struct.unpack("<I", self.buffer[:4])[0]
        if not 0 < size <= MAX_FRAME_BYTES:
            raise DesktopControlUnavailable("The desktop sent an unsupported control frame.")
        while len(self.buffer) < size + 4:
            more()
        raw, self.buffer = self.buffer[4:size + 4], self.buffer[size + 4:]
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise DesktopControlUnavailable("The desktop sent an invalid control response.")
        if result.get("type") == "client-discovery-request":
            self._send({"type": "client-discovery-response", "requestId": result.get("requestId"),
                        "response": {"canHandle": False}})
        return result

    def _request(self, method, params, *, target=None, version=None):
        if method not in ("initialize", OWNER_METHOD, INTERRUPT_METHOD):
            raise DesktopControlUnavailable("That desktop request is not allowed.")
        ident = uuid.uuid4().hex
        packet = {"type": "request", "requestId": ident, "sourceClientId": self.client_id,
                  "method": method, "params": params,
                  "version": version if version is not None else self.versions[method],
                  "timeoutMs": int(self.timeout * 1000)}
        if target:
            packet["targetClientId"] = target
        self._send(packet)
        deadline = time.monotonic() + self.timeout
        for _ in range(512):
            response = self._receive(deadline)
            if response.get("type") != "response" or response.get("requestId") != ident:
                continue
            if response.get("resultType") != "success":
                # Do not echo arbitrary native error text into the web UI.
                raise DesktopControlUnavailable("The desktop did not accept the scoped request.")
            if response.get("method") != method or target and response.get("handledByClientId") != target:
                raise DesktopControlUnavailable("The desktop control response did not match its owner.")
            return response
        raise DesktopControlUnavailable("The desktop did not return a bounded control response.")

    def find_owner(self, thread_id):
        response = self._request(OWNER_METHOD, {"hostId": "local", "conversationId": thread_id})
        owner = _identifier(response.get("handledByClientId"))
        if not owner:
            raise DesktopControlUnavailable("The live pipeline's desktop owner is unavailable.")
        return owner

    def snapshot(self, thread_id, owner):
        def follow(value):
            self._send({"type": "broadcast", "method": FOLLOW_METHOD,
                        "version": self.versions[FOLLOW_METHOD], "sourceClientId": self.client_id,
                        "targetClientIds": [owner], "params": {"hostId": "local",
                        "conversationId": thread_id, "following": value}})
        follow(True)
        try:
            deadline = time.monotonic() + self.timeout
            for _ in range(512):
                row = self._receive(deadline)
                params = row.get("params", {})
                if row.get("type") != "broadcast" or row.get("method") != STATE_METHOD or row.get("sourceClientId") != owner:
                    continue
                if row.get("version") != self.versions[STATE_METHOD] or params.get("hostId") != "local" or params.get("conversationId") != thread_id:
                    continue
                change = params.get("change", {})
                if change.get("type") == "snapshot" and isinstance(change.get("conversationState"), dict):
                    return change["conversationState"]
            raise DesktopControlUnavailable("The desktop did not return a bounded pipeline snapshot.")
        finally:
            follow(False)

    def interrupt(self, thread_id, turn_id, owner):
        if not _identifier(thread_id) or not _identifier(turn_id):
            raise DesktopControlConflict("The observed turn identity is invalid.")
        return self._request(INTERRUPT_METHOD, {"conversationId": thread_id, "mode": "user-stop",
                             "expectedTurnId": turn_id}, target=owner).get("result", {})


def _view(state):
    """Strip transcript/tool/input content before it leaves the controller."""
    turns = []
    history = state.get("turnHistory") or {}
    if history.get("kind") == "canonical":
        data = history.get("history") or {}
        entities = data.get("entitiesByKey") or {}
        for island in data.get("islands", []):
            for entry in island.get("entries", []):
                value = entities.get(entry.get("value"))
                if isinstance(value, dict):
                    turns.append(value)
    turns.extend(t for t in state.get("turns", []) if isinstance(t, dict))
    ordered = {}
    for turn in turns:
        ident = _identifier(turn.get("turnId"))
        if ident:
            ordered[ident] = {"id": ident, "status": turn.get("status")}
    latest = next(reversed(ordered.values()), {}) if ordered else {}
    source = state.get("source") or {}
    subagent = source.get("subAgent", {}) if isinstance(source, dict) else {}
    spawn = subagent.get("thread_spawn", {}) if isinstance(subagent, dict) else {}
    goal = state.get("threadGoal") or {}
    return {"thread_id": state.get("id"), "cwd": state.get("cwd"),
            "parent_thread_id": state.get("parentThreadId") or spawn.get("parent_thread_id"),
            "rollout_path": state.get("rolloutPath"), "turns": list(ordered.values()),
            "current_turn_id": latest.get("id") if latest.get("status") == "inProgress" else None,
            "runtime_active": (state.get("threadRuntimeStatus") or {}).get("type") == "active",
            "goal_active": goal.get("status") == "active"}


class DesktopPipelineController:
    def __init__(self, workspace, *, codex_home=None, app_bundle="/Applications/ChatGPT.app",
                 receipt_dir=None, transport_factory=None, protocol_provider=None, descriptor_provider=None):
        self.workspace = Path(workspace).resolve()
        self.codex_home = Path(codex_home or Path.home() / ".codex").resolve()
        self.sessions_dir = self.codex_home / "sessions"
        self.socket_path = self.codex_home / "ipc/ipc.sock"
        self.app_bundle = Path(app_bundle)
        self.receipt_dir = Path(receipt_dir or self.workspace / "Resources/Studio/.local/desktop_stops")
        self.transport_factory = transport_factory
        self.protocol_provider = protocol_provider or (lambda: installed_protocol(self.app_bundle))
        self.descriptor_provider = descriptor_provider
        self.lock = threading.RLock()
        self._protocol_cache = None

    def _protocol(self):
        if self._protocol_cache is None:
            self._protocol_cache = self.protocol_provider()
        # Recheck the installed route on each explicit stop; routine capability
        # projection does not repeatedly read the whole archive.
        return copy.deepcopy(self._protocol_cache)

    def _socket_ready(self):
        if self.transport_factory:
            return True
        try:
            socket_stat, parent_stat = self.socket_path.lstat(), self.socket_path.parent.lstat()
            return (stat.S_ISSOCK(socket_stat.st_mode) and stat.S_ISDIR(parent_stat.st_mode)
                    and socket_stat.st_uid == parent_stat.st_uid == os.getuid()
                    and not socket_stat.st_mode & 0o077 and not parent_stat.st_mode & 0o022)
        except OSError:
            return False

    def _transport(self, protocol):
        if not protocol.get("available") or not self._socket_ready():
            raise DesktopControlUnavailable("Scoped desktop control is unavailable. Open the pipeline chat to stop it there.")
        if self.transport_factory:
            return self.transport_factory(self.socket_path, protocol["versions"])
        return DesktopIPC(self.socket_path, protocol["versions"])

    def _descriptor(self, value):
        if not isinstance(value, dict):
            raise DesktopControlConflict("No live pipeline turn is available to stop.")
        observed = _date(value.get("observed_at"))
        age = (dt.datetime.now(dt.timezone.utc) - observed).total_seconds() if observed else None
        if age is None or not -5 <= age <= MAX_DESCRIPTOR_AGE:
            raise DesktopControlConflict("Pipeline observation expired. Refresh before stopping it.")
        if str(self.workspace) != value.get("workspace"):
            raise DesktopControlConflict("The pipeline observation belongs to another workspace.")
        root = _identifier(value.get("thread_id"))
        turn = _identifier(value.get("current_turn_id"))
        if not root or not turn:
            raise DesktopControlConflict("The live pipeline's turn identity is unavailable.")
        if self.descriptor_provider:
            fresh = self.descriptor_provider()
            if not isinstance(fresh, dict) or fresh.get("thread_id") != root or fresh.get("current_turn_id") != turn or fresh.get("workspace") != str(self.workspace):
                raise DesktopControlConflict("The live pipeline turn changed. Refresh before stopping it.")
            value = fresh
        children = value.get("children") or []
        if not isinstance(children, list) or len(children) > MAX_CHILDREN:
            raise DesktopControlConflict("The pipeline worker scope could not be verified.")
        targets = [{"thread_id": root, "current_turn_id": turn, "parent_thread_id": None}]
        seen = {root}
        for child in children:
            if not isinstance(child, dict):
                raise DesktopControlConflict("The pipeline worker identity is invalid.")
            ident, current, parent = (_identifier(child.get(key)) for key in ("thread_id", "current_turn_id", "parent_thread_id"))
            if not ident or not current or not parent or ident in seen:
                raise DesktopControlConflict("The pipeline worker identity is invalid.")
            targets.append({"thread_id": ident, "current_turn_id": current, "parent_thread_id": parent})
            seen.add(ident)
        ancestry = {item["thread_id"]: item["parent_thread_id"] for item in targets}
        for target in targets[1:]:
            parent, visited = target["parent_thread_id"], {target["thread_id"]}
            while parent != root:
                if parent in visited or parent not in ancestry:
                    raise DesktopControlConflict("A worker does not belong to the observed pipeline.")
                visited.add(parent)
                parent = ancestry[parent]
        return {"workspace": str(self.workspace), "thread_id": root, "current_turn_id": turn,
                "run_id": value.get("run_id") if _identifier(value.get("run_id")) else None,
                "observed_at": value["observed_at"], "targets": targets}

    def _validate_snapshot(self, target, state, *, require_active):
        view = _view(state)
        cwd = view.get("cwd")
        if view["thread_id"] != target["thread_id"] or not isinstance(cwd, str) or Path(cwd).resolve() not in (self.workspace, self.workspace / "Resources"):
            raise DesktopControlConflict("The desktop pipeline identity does not match this workspace.")
        if target["parent_thread_id"] and view["parent_thread_id"] != target["parent_thread_id"]:
            raise DesktopControlConflict("The desktop worker does not belong to this pipeline.")
        if require_active and view["current_turn_id"] != target["current_turn_id"]:
            raise DesktopControlConflict("The desktop turn changed. Refresh before stopping it.")
        if view["goal_active"]:
            raise DesktopControlConflict("This chat has an active Goal. Open its chat and use Stop there to pause the Goal too.")
        return view

    def _child_view(self, target):
        """Native agents lack UI IPC owners; prove their bound rollout locally.

        The parent's own native Stop schedules descendant interrupts through its
        app-server. No fabricated standalone child control route is attempted.
        """
        ident = target["thread_id"]
        paths = set(self.sessions_dir.glob("????/??/??/*-" + ident + ".jsonl"))
        paths.update(self.sessions_dir.glob("????/??/??/" + ident + ".jsonl"))
        if len(paths) != 1:
            raise DesktopControlConflict("A pipeline worker's session identity is unavailable.")
        path = paths.pop()
        reader = BoundedSessionReader(self.sessions_dir, self.workspace, self.workspace / "Resources")
        metadata = reader.metadata(path)
        state = reader.read(path, expected_id=ident)
        if not metadata or metadata["id"] != ident or metadata["parent_id"] != target["parent_thread_id"]:
            raise DesktopControlConflict("A worker does not belong to the observed pipeline.")
        if not state or not state.open_turn or state.turn_id != target["current_turn_id"]:
            raise DesktopControlConflict("A worker's active turn changed. Refresh before stopping the pipeline.")
        return {"rollout_path": str(path.resolve())}

    def capabilities(self, descriptor=None):
        protocol = self._protocol()
        result = {"available": bool(protocol.get("available") and self._socket_ready()), "can_stop": False,
                  "route": ROUTE, "verified": False, "safe_identity": None,
                  "reason": "Open the pipeline chat to use its native Stop control.",
                  "cleanup_verified": False, "cleanup_limitations": ["Background tools and remote model jobs need separate cleanup verification."]}
        if not result["available"]:
            return result
        result["reason"] = "The installed scoped desktop route is present. A fresh live identity is required."
        if descriptor is None:
            return result
        try:
            checked = self._descriptor(descriptor)
            with self._transport(protocol) as client:
                root = checked["targets"][0]
                owner = client.find_owner(root["thread_id"])
                self._validate_snapshot(root, client.snapshot(root["thread_id"], owner), require_active=True)
                for target in checked["targets"][1:]:
                    self._child_view(target)
            result.update(can_stop=True, verified=True,
                          safe_identity={key: checked[key] for key in ("thread_id", "current_turn_id", "run_id")},
                          reason="Stop targets this observed turn and its verified native workers. Cleanup remains separately checked.")
        except (DesktopControlError, OSError, ValueError, KeyError, TypeError):
            result["reason"] = "The desktop turn could not be verified. Refresh or open the pipeline chat."
        return result

    def _save(self, receipt):
        self.receipt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.receipt_dir / (receipt["id"] + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)

    def stop(self, descriptor):
        """Explicit user action only. Revalidate every identity before any interrupt."""
        with self.lock:
            checked = self._descriptor(descriptor)
            protocol = self.protocol_provider()
            self._protocol_cache = protocol
            receipt = {"id": "desktop-stop-" + uuid.uuid4().hex, "kind": "external_pipeline",
                       "status": "stop_requested", "route": ROUTE, "run_id": checked["run_id"],
                       "workspace": str(self.workspace), "thread_id": checked["thread_id"],
                       "turn_id": checked["current_turn_id"], "requested_at": _now(),
                       "updated_at": _now(), "action_sent": False, "parent_stop_confirmed": False,
                       "native_children_stop_confirmed": False, "pipeline_complete": False,
                       "recovery_required": True, "cleanup_status": "needs_review",
                       "cleanup_limitations": ["Background terminals and remote model jobs are not exposed by this desktop control route.",
                                                "Native descendant cleanup is asynchronous; acknowledgements are not completion evidence."],
                       "summary": "Stop requested. Waiting for turn readback; cleanup still needs review.", "targets": []}
            with self._transport(protocol) as client:
                # Complete the read-only validation before the first mutation.
                root = checked["targets"][0]
                owner = client.find_owner(root["thread_id"])
                root_view = self._validate_snapshot(root, client.snapshot(root["thread_id"], owner), require_active=True)
                verified = [(root, root_view)]
                for target in checked["targets"][1:]:
                    verified.append((target, self._child_view(target)))
                receipt["targets"] = [{**target, "rollout_path": view["rollout_path"],
                                       "request_sent": False, "acknowledged": False,
                                       "native_descendant_cleanup_requested": False,
                                       "terminal_confirmed": False, "request_error": None}
                                      for target, view in verified]
                self._save(receipt)
                entry = receipt["targets"][0]
                # Save intent before the request, so loss of ACK/restart is an
                # unresolved stop hold, never an invented stopped state.
                entry["request_sent"] = receipt["action_sent"] = True
                self._save(receipt)
                try:
                    response = client.interrupt(root["thread_id"], root["current_turn_id"], owner)
                    entry["acknowledged"] = response.get("ok") is True and response.get("interruptedTurnId") == root["current_turn_id"]
                    if not entry["acknowledged"]:
                        entry["request_error"] = "The desktop did not acknowledge this exact turn."
                    for child in receipt["targets"][1:]:
                        child["native_descendant_cleanup_requested"] = entry["acknowledged"]
                except (DesktopControlError, OSError, ValueError, KeyError, TypeError):
                    entry["request_error"] = "The scoped request could not be confirmed."
                receipt["updated_at"] = _now()
                self._save(receipt)
            return self.get(receipt["id"])

    def _terminal_proof(self, entry):
        """Bounded receipt readback, never assistant prose or optimistic UI patches."""
        value = entry.get("rollout_path")
        if not isinstance(value, str):
            return {"confirmed": False, "reason": "Terminal evidence is unavailable."}
        try:
            path = Path(value).resolve()
            if not path.is_relative_to(self.sessions_dir) or path.suffix != ".jsonl":
                return {"confirmed": False, "reason": "Terminal evidence is outside the session store."}
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as stream:
                file_stat = os.fstat(stream.fileno())
                if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_uid != os.getuid():
                    raise ValueError("Invalid evidence owner")
                first = stream.readline(MAX_HEADER_BYTES + 1)
                meta = json.loads(first)
                payload = meta.get("payload", {})
                if meta.get("type") != "session_meta" or payload.get("id") != entry["thread_id"] or Path(payload.get("cwd", "")).resolve() not in (self.workspace, self.workspace / "Resources"):
                    raise ValueError("Invalid evidence identity")
                stream.seek(max(len(first), file_stat.st_size - MAX_TAIL_BYTES))
                raw = stream.read(MAX_TAIL_BYTES)
            if file_stat.st_size - MAX_TAIL_BYTES > len(first):
                raw = raw.partition(b"\n")[2]
            current, proof, newer_turn = None, None, False
            for line in raw.splitlines():
                if len(line) > MAX_HEADER_BYTES:
                    continue
                try:
                    row = json.loads(line)
                    data = row.get("payload", {})
                    if row.get("type") != "event_msg" or not isinstance(data, dict):
                        continue
                    kind = data.get("type")
                    if kind in ("task_started", "turn_started"):
                        current = _identifier(data.get("turn_id"))
                        if proof and current and current != entry["current_turn_id"]:
                            newer_turn = True
                    elif kind in _TERMINALS and (_identifier(data.get("turn_id")) or current) == entry["current_turn_id"]:
                        proof = {"event": kind, "at": row.get("timestamp")}
                except (ValueError, AttributeError, TypeError):
                    continue
            # A successor can start before the old turn's terminal event is
            # flushed. Its presence still invalidates a whole-run stop claim.
            newer_turn = newer_turn or bool(proof and current and current != entry["current_turn_id"])
            return {"confirmed": bool(proof) and not newer_turn, "terminal": proof,
                    "new_turn_detected": newer_turn,
                    "reason": "A later turn started; the previous Stop does not cover it." if newer_turn else ""}
        except (OSError, ValueError, KeyError, TypeError):
            return {"confirmed": False, "reason": "Terminal evidence could not be verified."}

    def _load(self, ident):
        if not isinstance(ident, str) or not _RECEIPT.fullmatch(ident):
            raise DesktopControlError("Invalid desktop Stop receipt.")
        try:
            with (self.receipt_dir / (ident + ".json")).open("rb") as stream:
                raw = stream.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024:
                raise ValueError("Oversize receipt")
            receipt = json.loads(raw)
            if not isinstance(receipt, dict) or receipt.get("id") != ident or receipt.get("workspace") != str(self.workspace):
                raise ValueError("Mismatched receipt")
            targets = receipt.get("targets")
            if (receipt.get("route") != ROUTE or receipt.get("kind") != "external_pipeline"
                    or receipt.get("recovery_required") is not True or receipt.get("pipeline_complete") is not False
                    or receipt.get("cleanup_status") != "needs_review" or not isinstance(targets, list)
                    or not 1 <= len(targets) <= MAX_CHILDREN + 1):
                raise ValueError("Incomplete receipt")
            for target in targets:
                if not isinstance(target, dict) or not _identifier(target.get("thread_id")) or not _identifier(target.get("current_turn_id")):
                    raise ValueError("Invalid target receipt")
            if receipt.get("thread_id") != targets[0]["thread_id"] or receipt.get("turn_id") != targets[0]["current_turn_id"]:
                raise ValueError("Mismatched target receipt")
            return receipt
        except (OSError, ValueError, AttributeError, TypeError):
            raise DesktopControlError("Desktop Stop receipt is unavailable.") from None

    def get(self, ident):
        with self.lock:
            receipt = self._load(ident)
            for entry in receipt.get("targets", []):
                entry["terminal_readback"] = self._terminal_proof(entry)
                entry["terminal_confirmed"] = entry["terminal_readback"]["confirmed"]
            targets = receipt.get("targets", [])
            receipt["parent_stop_confirmed"] = bool(targets and targets[0]["terminal_confirmed"])
            receipt["native_children_stop_confirmed"] = all(target["terminal_confirmed"] for target in targets[1:])
            if receipt["parent_stop_confirmed"] and receipt["native_children_stop_confirmed"]:
                receipt["status"] = "needs_review"
                receipt["summary"] = "The observed native turns ended. Background tools and remote jobs still need cleanup verification."
            elif any(target.get("request_error") for target in targets):
                receipt["status"] = "needs_review"
                receipt["summary"] = "Stop could not be fully confirmed. Open the pipeline chat and review remaining workers."
            receipt["updated_at"] = _now()
            self._save(receipt)
            return copy.deepcopy(receipt)

    def list(self):
        if not self.receipt_dir.exists():
            return []
        receipts = []
        for path in sorted(self.receipt_dir.glob("desktop-stop-*.json"), reverse=True)[:40]:
            try:
                receipts.append(self.get(path.stem))
            except DesktopControlError:
                # A lost/corrupted local receipt is unresolved stop evidence,
                # never permission to silently start another pipeline.
                receipts.append({"id": path.stem, "kind": "external_pipeline", "status": "needs_review",
                                 "recovery_required": True, "pipeline_complete": False,
                                 "cleanup_status": "needs_review", "workspace": str(self.workspace),
                                 "summary": "A saved Stop receipt could not be read. Review cleanup before starting another run."})
        return receipts
