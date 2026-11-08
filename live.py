"""Bounded, read-only observation of verified external Codex pipeline chats.

Assistant commentary/final items produce fixed progress descriptions; arbitrary
transcript prose never leaves this module. Session analysis, inputs, commands,
tool results and inbox content are never part of the projection. Chat completion
is not stage approval.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import threading
from typing import Any, Callable


FRESH_SECONDS = 180
HEADER_BYTES = 256_000
TAIL_BYTES = 768_000
MAX_LINE_BYTES = 64_000
MAX_SESSIONS = 160
MAX_OWNERSHIP_DEPTH = 16
MAX_LOGS = 32
STAGE_IDS = ("inbox", "hunt", "hunt_audit", "build", "build_audit", "sol_review", "sync")
LANES = {"hellowork": "HelloWork", "france_travail": "France Travail", "linkedin": "LinkedIn", "indeed": "Indeed",
         "apec": "APEC", "wttj": "Welcome to the Jungle", "mail": "Mail alerts"}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,100}$")
_MODEL = re.compile(r"^(?:gpt|gemini|claude)[A-Za-z0-9_.-]{1,80}$")
_STAGE_LABELS = {"inbox": "Inbox review", "hunt": "Find opportunities", "hunt_audit": "Hunt audit",
                 "build": "Build CVs", "build_audit": "Build audit", "sol_review": "Final review", "sync": "Tracker sync"}


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (TypeError, ValueError):
        return None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _number(value: Any) -> int | float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return int(value) if math.isfinite(value) and value >= 0 and int(value) == value else None
    except (ValueError, OverflowError):
        return None


def _thread_id(value: Any) -> str | None:
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _model_label(model: Any, effort: Any = None) -> str | None:
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        return None
    names = {"gpt-6.1-sol": "GPT-6.1 Sol", "gpt-6-sol": "GPT-6 Sol", "gpt-6-luna": "GPT-6 Luna",
             "gpt-6-astra": "GPT-6 Astra", "gemini-3.8-flash-high": "Gemini 3.8 Flash · High",
             "claude-opus-4-6-thinking": "Claude Opus 4.6 · Thinking"}
    label = names.get(model, model)
    if effort in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
        label += " · " + ("MAX" if effort == "max" else effort.title())
    return label


def _agent_stage(path: str) -> tuple[str | None, str | None]:
    name = path.rsplit("/", 1)[-1].lower().replace("-", "_")
    for lane in LANES:
        if name in ("hunt_" + lane, "hunt:" + lane, lane + "_hunt"):
            return "hunt", lane
    if "readiness" in name or name in ("usage_watch", "runtime_review"):
        return None, None
    for stage in ("hunt_audit", "build_audit", "sol_review"):
        if stage in name:
            return stage, None
    if name.startswith(("inbox", "mail_review")):
        return "inbox", None
    if name.startswith(("build", "cv_build")):
        return "build", None
    if name.startswith(("sync", "tracker_sync", "sheet_sync")):
        return "sync", None
    if name in ("final_approval", "final_review", "sol_approval"):
        return "sol_review", None
    return None, None


def _comment_stage(message: str) -> str | None:
    """Only explicit present activity; mentions of future stages are not progress."""
    choices = (("hunt_audit", r"hunt audit"), ("build_audit", r"build audit"),
               ("sol_review", r"final review|sol review|final approval"),
               ("sync", r"tracker sync|sheet sync|syncing"), ("build", r"build(?:ing)? cvs?|cv build"),
               ("hunt", r"hunt|finding opportunities|reading (?:public )?adverts?|discovery"),
               ("inbox", r"inbox review|reviewing (?:the )?inbox"))
    for line in message.lower().splitlines():
        if re.search(r"\b(?:next|later|after|will|once|queued|not started|not run)\b", line):
            continue
        for stage, words in choices:
            if re.search(r"\b(?:now|running|starting|started|active|underway|in progress|working on|performing|stage|step)\b.{0,55}\b(?:" + words + r")\b", line) or re.search(r"\b(?:" + words + r")\b.{0,45}\b(?:now|running|started|active|underway|in progress|ongoing)\b", line):
                return stage
    return None


@dataclass
class _Session:
    path: Path
    identity: tuple[int, int]
    metadata: dict
    size: int = 0
    mtime_ns: int = 0
    offset: int = 0
    anchor: bytes = b""
    head_hash: bytes = b""
    carry: bytes = b""
    skipping_line: bool = False
    open_turn: bool = False
    status: str = "unconfirmed"
    turn_id: str | None = None
    started_at: str | None = None
    last_activity_at: str | None = None
    terminal_at: str | None = None
    model: str | None = None
    effort: str | None = None
    comment_stage: str | None = None
    label: str = "Waiting for activity"
    logs: deque = field(default_factory=lambda: deque(maxlen=MAX_LOGS))
    gap: bool = False
    proof_turn_id: str | None = None
    unavailable: bool = False
    seen_active: bool = False
    uncertain_hold: bool = False


class BoundedSessionReader:
    """Small header + tail once, then appended bytes only; no disk writes."""

    def __init__(self, sessions_dir: Path, workspace: Path, resources: Path):
        self.sessions_dir = Path(sessions_dir).expanduser().resolve()
        self.cwds = {workspace.resolve(), resources.resolve()}
        self.cache: dict[Path, _Session] = {}
        self.errors: dict[Path, str] = {}
        self.bytes_read = 0

    def safe_path(self, value: Any) -> Path | None:
        if not isinstance(value, (str, Path)):
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.sessions_dir / path
        try:
            resolved = path.resolve()
            if resolved.is_relative_to(self.sessions_dir) and resolved.suffix == ".jsonl":
                return resolved
        except (OSError, ValueError):
            pass
        return None

    def _read(self, stream, size: int) -> bytes:
        content = stream.read(size)
        self.bytes_read += len(content)
        return content

    def metadata(self, value: Any) -> dict | None:
        path = self.safe_path(value)
        if path is None:
            return None
        try:
            self.errors.pop(path, None)
            with path.open("rb") as stream:
                raw = self._read(stream, MAX_LINE_BYTES + 1)
            first = raw.split(b"\n", 1)[0]
            if len(first) > MAX_LINE_BYTES:
                return None
            row = json.loads(first)
            data = row.get("payload")
            if row.get("type") != "session_meta" or not isinstance(data, dict):
                return None
            thread = _thread_id(data.get("id"))
            cwd = data.get("cwd")
            if thread is None or not isinstance(cwd, str) or Path(cwd).resolve() not in self.cwds:
                return None
            source = data.get("source")
            spawn = source.get("subagent", {}).get("thread_spawn", {}) if isinstance(source, dict) and isinstance(source.get("subagent"), dict) else {}
            parent = _thread_id(spawn.get("parent_thread_id")) if isinstance(spawn, dict) else None
            agent_path = spawn.get("agent_path", "") if isinstance(spawn, dict) else ""
            agent_path = agent_path if isinstance(agent_path, str) and re.fullmatch(r"[/A-Za-z0-9_:-]{1,160}", agent_path) else ""
            automation = data.get("automation")
            marker = data.get("automation_id") == "cv-weekday-pipeline" or (
                isinstance(automation, dict) and automation.get("id") == "cv-weekday-pipeline")
            # Inspect only the exact first-message scheduler header, never a
            # mention in embedded instructions or an unrelated UI conversation.
            if not marker and not parent:
                for line in raw.split(b"\n")[1:]:
                    try:
                        message = json.loads(line)
                        payload = message.get("payload", {})
                        if message.get("type") != "response_item" or payload.get("type") != "message" or payload.get("role") != "user":
                            continue
                        parts = payload.get("content", [])
                        header = next((p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "input_text"), "")
                        marker = isinstance(header, str) and bool(re.match(
                            r"(?:# Automation Run:|Automation name:)\s*(?:CV Pipeline|CV Weekday Pipeline)(?:\s|$)",
                            header, re.I))
                        break
                    except (ValueError, TypeError, AttributeError):
                        continue
            return {"id": thread, "parent_id": parent, "agent_path": agent_path,
                    "timestamp": data.get("timestamp") or row.get("timestamp"), "automation": marker}
        except OSError:
            self.errors[path] = "unavailable"
            cached = self.unavailable_cached(path)
            return cached.metadata if cached else None
        except (ValueError, TypeError, AttributeError):
            self.errors[path] = "invalid_metadata"
            return None

    def _log(self, state: _Session, at: str, message: str, kind: str, label: str) -> None:
        if kind == "activity" and state.logs and state.logs[-1]["label"] == label:
            return
        key = hashlib.sha256((state.metadata["id"] + at + kind + message).encode()).hexdigest()[:24]
        state.logs.append({"id": key, "at": at, "message": message, "kind": kind, "label": label})

    def _record(self, state: _Session, row: dict) -> None:
        data = row.get("payload")
        if not isinstance(data, dict):
            return
        at = row.get("timestamp")
        if _time(at) is None:
            return
        outer, kind = row.get("type"), data.get("type")
        if outer == "turn_context":
            if _model_label(data.get("model")):
                state.model = data["model"]
                state.effort = data.get("effort") or data.get("reasoning_effort")
            context_turn = _thread_id(data.get("turn_id"))
            if context_turn and context_turn != state.turn_id:
                # A newer context identifies the thread's current turn. Older
                # unmatched starts must not keep the thread falsely open.
                state.open_turn, state.status, state.terminal_at = True, "running", None
                state.turn_id = context_turn
                state.proof_turn_id = context_turn
                state.started_at = data.get("started_at") if _time(data.get("started_at")) else at
                state.last_activity_at, state.label = at, "Turn context"
                state.comment_stage = None
            return
        if outer == "event_msg" and kind in ("task_started", "turn_started"):
            state.open_turn, state.status, state.terminal_at = True, "running", None
            state.comment_stage = None
            state.turn_id = _thread_id(data.get("turn_id"))
            state.proof_turn_id = state.turn_id
            state.started_at = data.get("started_at") if _time(data.get("started_at")) else at
            state.last_activity_at, state.label = at, "Turn started"
            self._log(state, at, "Chat turn started.", "activity", state.label)
            return
        terminals = {"task_complete": "completed", "task_completed": "completed", "turn_completed": "completed",
                     "turn_aborted": "interrupted", "task_aborted": "interrupted", "task_cancelled": "interrupted",
                     "task_failed": "failed", "turn_failed": "failed"}
        if outer == "event_msg" and kind in terminals:
            terminal_turn = _thread_id(data.get("turn_id"))
            if terminal_turn and state.turn_id and terminal_turn != state.turn_id:
                # Some retained tails omit a later start/context, but the native
                # terminal event still includes its actual start and end times.
                # Only a strictly newer, coherently timestamped turn supersedes
                # an unmatched old start. An older terminal cannot close new work.
                raw_start, raw_end = data.get("started_at"), data.get("completed_at")
                try:
                    terminal_start = (_time(raw_start) if isinstance(raw_start, str) else
                                      datetime.fromtimestamp(raw_start, timezone.utc)
                                      if isinstance(raw_start, (int, float)) and not isinstance(raw_start, bool) else None)
                    terminal_end = (_time(raw_end) if isinstance(raw_end, str) else
                                    datetime.fromtimestamp(raw_end, timezone.utc)
                                    if isinstance(raw_end, (int, float)) and not isinstance(raw_end, bool) else None)
                except (ValueError, OverflowError, OSError):
                    return
                current_start = _time(state.started_at)
                if not (terminal_start and terminal_end and current_start and
                        terminal_start > current_start and terminal_end >= terminal_start and
                        terminal_end <= _time(at) + timedelta(seconds=1)):
                    return
                state.turn_id = state.proof_turn_id = terminal_turn
                state.started_at = _iso(terminal_start)
            state.open_turn, state.status = False, terminals[kind]
            state.uncertain_hold = False
            state.last_activity_at = state.terminal_at = at
            state.label = "Turn " + state.status
            self._log(state, at, "Chat turn " + state.status + ". Stage approval requires its saved receipt.", "activity", state.label)
            return
        recognized = outer == "response_item" or (outer == "event_msg" and kind in ("item_completed", "agent_message", "agent_reasoning"))
        if not recognized:
            return
        state.last_activity_at = at
        if outer == "event_msg" and kind == "item_completed":
            state.proof_turn_id = _thread_id(data.get("turn_id")) or state.proof_turn_id
        if outer == "response_item" and kind == "message" and data.get("role") == "assistant" and data.get("phase") in ("commentary", "final", "final_answer"):
            message_turn = _thread_id(data.get("turn_id"))
            if message_turn and state.turn_id and message_turn != state.turn_id:
                return
            parts = data.get("content", [])
            message = "\n".join(p["text"][:4000] for p in parts[:8] if isinstance(p, dict) and p.get("type") == "output_text" and isinstance(p.get("text"), str)) if isinstance(parts, list) else ""
            message = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", message).strip()[:4000]
            inferred = _comment_stage(message)
            if inferred:
                state.comment_stage = inferred
            worker_stage, _ = _agent_stage(state.metadata["agent_path"])
            # Even user-visible assistant messages can quote a recruiter or a
            # command. Emit only closed-vocabulary progress, never raw prose.
            projected_stage = worker_stage or inferred
            message = ("Progress updated for " + _STAGE_LABELS[projected_stage] + "." if projected_stage else
                       "Assistant progress updated." if data["phase"] == "commentary" else
                       "Assistant result recorded. Stage approval requires its saved receipt.")
            if message:
                state.label = "Assistant update" if data["phase"] == "commentary" else "Assistant result"
                self._log(state, at, message, data["phase"], state.label)
            if data["phase"] in ("final", "final_answer"):
                # Older completed transcripts can end at the final assistant item.
                state.open_turn, state.status, state.terminal_at = False, "completed", at
                state.uncertain_hold = False
            return
        if outer == "response_item" and kind in ("function_call", "custom_tool_call"):
            name = data.get("name", "")
            label = "Waiting for workers" if isinstance(name, str) and re.search(r"wait_(?:agent|threads)|clock.*sleep", name) else "Reading public sources" if isinstance(name, str) and (name.startswith("web") or "web__" in name) else "Using tools"
            state.label = label
            self._log(state, at, label + ".", "activity", label)
        # Reasoning/input/tool-result contents never become log entries.

    def _consume(self, state: _Session, raw: bytes, *, initial: bool = False) -> None:
        if initial:
            state.carry, state.skipping_line = b"", False
        raw = state.carry + raw
        state.carry = b""
        if state.skipping_line:
            end = raw.find(b"\n")
            if end < 0:
                return
            raw, state.skipping_line = raw[end + 1:], False
        lines = raw.split(b"\n")
        last = lines.pop()
        if len(last) <= MAX_LINE_BYTES:
            state.carry = last
        else:
            state.skipping_line = True
        for line in lines:
            if not line or len(line) > MAX_LINE_BYTES:
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    self._record(state, row)
            except (ValueError, TypeError, UnicodeError):
                continue

    def read(self, value: Any, *, expected_id: str | None = None) -> _Session | None:
        path = self.safe_path(value)
        if path is None:
            return None
        try:
            stat = path.stat()
            state = self.cache.get(path)
            if state and not state.unavailable and state.size == stat.st_size and state.mtime_ns == stat.st_mtime_ns:
                return state if expected_id is None or state.metadata["id"] == expected_id else None
            with path.open("rb") as stream:
                head = self._read(stream, 1024)
                head_hash = hashlib.sha256(head).digest()
                reset = state is None or state.identity != (stat.st_dev, stat.st_ino) or stat.st_size < state.offset or state.head_hash != head_hash
                if state and not reset:
                    stream.seek(max(0, state.offset - len(state.anchor)))
                    reset = self._read(stream, len(state.anchor)) != state.anchor or stat.st_size == state.size
                if reset:
                    metadata = self.metadata(path)
                    if metadata is None or (expected_id and metadata["id"] != expected_id):
                        if metadata is None and self.errors.get(path) == "unavailable":
                            return self.unavailable_cached(path, expected_id)
                        self.cache.pop(path, None)
                        return None
                    if self.errors.get(path) == "unavailable":
                        return self.unavailable_cached(path, expected_id)
                    previous_hold = bool(state and state.uncertain_hold)
                    state = _Session(path, (stat.st_dev, stat.st_ino), metadata, head_hash=head_hash,
                                     uncertain_hold=previous_hold, seen_active=bool(state and state.seen_active),
                                     open_turn=previous_hold, turn_id=state.turn_id if previous_hold else None,
                                     started_at=state.started_at if previous_hold else None)
                    stream.seek(0)
                    header = self._read(stream, min(stat.st_size, HEADER_BYTES))
                    self._consume(state, header, initial=True)
                    if stat.st_size > HEADER_BYTES:
                        start = max(len(header), stat.st_size - TAIL_BYTES)
                        stream.seek(start)
                        tail = self._read(stream, stat.st_size - start)
                        state.gap = start > len(header)
                        # With a gap, an old header start needs matching recent
                        # turn evidence. Do not treat its own ID as that proof.
                        if state.gap:
                            state.proof_turn_id = None
                            tail = tail.partition(b"\n")[2]
                        self._consume(state, tail, initial=state.gap)
                    self.cache[path] = state
                else:
                    start = state.offset
                    if stat.st_size - start > TAIL_BYTES:
                        start = stat.st_size - TAIL_BYTES
                        state.gap = True
                        state.proof_turn_id = None
                        state.carry, state.skipping_line = b"", False
                    stream.seek(start)
                    raw = self._read(stream, stat.st_size - start)
                    if start != state.offset:
                        raw = raw.partition(b"\n")[2]
                    self._consume(state, raw)
                state.size, state.mtime_ns, state.offset = stat.st_size, stat.st_mtime_ns, stat.st_size
                stream.seek(max(0, stat.st_size - 96))
                state.anchor = self._read(stream, 96)
                state.unavailable = False
                self.errors.pop(path, None)
            if len(self.cache) > MAX_SESSIONS:
                oldest = next(iter(self.cache))
                if oldest != path:
                    self.cache.pop(oldest, None)
            return state if expected_id is None or state.metadata["id"] == expected_id else None
        except OSError:
            self.errors[path] = "unavailable"
            return self.unavailable_cached(path, expected_id)
        except (ValueError, TypeError):
            return None

    def unavailable_cached(self, path: Path, expected_id: str | None = None) -> _Session | None:
        state = self.cache.get(path)
        if state is None or (expected_id and state.metadata["id"] != expected_id):
            return None
        state.unavailable = True
        if state.open_turn and state.seen_active:
            state.uncertain_hold = True
        return state


class LivePipelineObserver:
    def __init__(self, workspace: Path, resources: Path, sessions_dir: Path | None):
        self.workspace, self.resources = Path(workspace).resolve(), Path(resources).resolve()
        self.reader = BoundedSessionReader(sessions_dir, self.workspace, self.resources) if sessions_dir is not None else None
        self._lock = threading.RLock()
        self._index_at: datetime | None = None
        self._index: list[tuple[Path, dict]] = []

    def _discover(self, now: datetime, directories: list[Path]) -> list[tuple[Path, dict]]:
        if self.reader is None:
            return []
        if self._index_at and now - self._index_at < timedelta(seconds=10):
            return self._index
        days = {now.date(), (now - timedelta(days=1)).date()}
        for directory in directories[:3]:
            date = _time(directory.name[:10])
            if date:
                days.update((date.date(), (date - timedelta(days=1)).date()))
        try:
            paths = list(self.reader.sessions_dir.glob("*.jsonl"))
            for day in sorted(days, reverse=True):
                paths.extend((self.reader.sessions_dir / day.strftime("%Y/%m/%d")).glob("*.jsonl"))
        except OSError:
            paths = []
        # Missing/unreadable descendants remain discoverable from their earlier
        # verified metadata. Their contents are rechecked before observation.
        paths.extend(p for p in self.reader.cache if p not in paths)
        try:
            paths.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
        except OSError:
            pass
        self._index = [(p, meta) for p in paths[:MAX_SESSIONS] if (meta := self.reader.metadata(p))]
        self._index_at = now
        return self._index

    @staticmethod
    def _fresh(state: _Session, now: datetime) -> bool:
        last = _time(state.last_activity_at)
        proof = not state.gap or (state.turn_id is not None and state.proof_turn_id == state.turn_id)
        fresh = bool(not state.unavailable and state.open_turn and proof and last and -60 <= (now - last).total_seconds() <= FRESH_SECONDS)
        if fresh:
            state.seen_active = True
        return fresh

    def _verified_read(self, value: Any, expected_id: str) -> _Session | None:
        state = self.reader.read(value, expected_id=expected_id)
        if state is not None:
            return state
        # A valid, previously observed identity cannot become proof of stopped
        # work merely because a read is unavailable. Explicit invalid metadata
        # and unsafe paths do not inherit another session's cached identity.
        path = self.reader.safe_path(value)
        if path is not None and self.reader.errors.get(path) != "invalid_metadata":
            return self.reader.unavailable_cached(path, expected_id)
        return None

    def _descendants(self, owner: _Session, contexts: list, index: list) -> tuple[dict, bool]:
        known = {}
        for context in contexts[:MAX_SESSIONS]:
            if not isinstance(context, dict):
                continue
            child_id = _thread_id(context.get("id"))
            meta = self.reader.metadata(context.get("rollout_path")) if child_id else None
            if meta and meta["id"] == child_id:
                known[child_id] = (context.get("rollout_path"), meta, context)
        for path, meta in index:
            known.setdefault(meta["id"], (path, meta, {}))
        links = {}
        for child_id, entry in known.items():
            if child_id != owner.metadata["id"] and entry[1]["parent_id"]:
                links.setdefault(entry[1]["parent_id"], []).append(entry)
        owned, limited = {}, False
        queue = deque([(owner.metadata["id"], 0, [owner.metadata["id"]], None, None)])
        visited = {owner.metadata["id"]}
        while queue:
            parent_id, depth, chain, inherited_stage, inherited_lane = queue.popleft()
            entries = links.get(parent_id, [])
            if depth >= MAX_OWNERSHIP_DEPTH or len(owned) >= MAX_SESSIONS:
                limited = limited or any(meta["id"] not in visited for _, meta, _ in entries)
                continue
            for path, meta, context in entries:
                child_id = meta["id"]
                if child_id in visited:
                    continue
                if len(owned) >= MAX_SESSIONS:
                    limited = True
                    break
                child = self._verified_read(path, child_id)
                if child is None or child.metadata["parent_id"] != parent_id:
                    continue
                visited.add(child_id)
                stage, lane = _agent_stage(child.metadata["agent_path"])
                stage, lane = stage or inherited_stage, lane or inherited_lane
                child_chain = chain + [child_id]
                detail = dict(context, _ownership_depth=depth + 1, _ownership_chain=child_chain,
                              _stage=stage, _lane=lane)
                owned[child_id] = (child, detail)
                queue.append((child_id, depth + 1, child_chain, stage, lane))
        return owned, limited

    def observe(self, directories: list[Path], read_json: Callable, summarize: Callable,
                sources: Callable, default_stages: list[dict], *, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        empty = {"active": False, "status": "not_observed", "thread_id": None, "title": "CV Weekday Pipeline",
                 "current_turn_id": None, "coordinator_status": "not_observed",
                 "run_id": None, "current_stage": None, "stage_position": None, "started_at": None,
                 "last_activity_at": None, "observed_at": _iso(now), "summary": "No verified external pipeline chat is active.",
                 "stale": False, "read_only": True, "stages": default_stages, "logs": [], "workers": [], "sources": sources(None)}
        empty.update(uncertain=False, admission_blocked=False, current_stage_confirmed=False,
                     activity_confirmed=False, other_open_work_count=0)
        if self.reader is None:
            return empty
        with self._lock:
            return self._observe(directories, read_json, summarize, sources, default_stages, now, empty)

    def _observe(self, directories, read_json, summarize, sources, default_stages, now, empty):
        candidates: dict[tuple[str, str], tuple[Path | None, _Session, dict, list, int]] = {}
        bound_ids = set()
        index = self._discover(now, directories)
        indexed = {meta["id"]: (path, meta) for path, meta in index}
        for directory in directories[:32]:
            contexts = read_json(directory / "native-actual-model-contexts.json")
            contexts = contexts if isinstance(contexts, list) else contexts.get("contexts", []) if isinstance(contexts, dict) else []
            for name in ("actual-coordinator-runtime.json", "coordinator-actual-runtime.json"):
                receipt = read_json(directory / name)
                if not isinstance(receipt, dict):
                    continue
                thread = _thread_id(receipt.get("thread_id"))
                source = receipt.get("source")
                if isinstance(source, dict):
                    source = source.get("rollout_path") or source.get("path")
                source_was_supplied = source is not None
                if thread is None and isinstance(source, str):
                    meta = self.reader.metadata(source) if self.reader else None
                    thread = _thread_id(meta.get("id")) if isinstance(meta, dict) else None
                owner = self._verified_read(source, thread) if thread and source else None
                if owner is None and not source_was_supplied and thread in indexed:
                    owner = self._verified_read(indexed[thread][0], thread)
                if owner is None or owner.metadata["parent_id"]:
                    continue
                bound_ids.add(thread)
                key = (directory.name, thread)
                previous = candidates.get(key)
                rank = 1
                if previous is None or rank > previous[4]:
                    candidates[key] = (directory, owner, receipt, contexts, rank)

            # Newer batch manifests bind directly to the Codex task that owns
            # them. The task can omit the older automation header, so its exact
            # saved identity is the proof; cwd alone never creates a match.
            manifest = read_json(directory / "batch-manifest.json")
            thread = _thread_id(manifest.get("actual_coordinator_id")) if isinstance(manifest, dict) else None
            if thread:
                bound_ids.add(thread)
                entry = indexed.get(thread)
                owner = self._verified_read(entry[0], thread) if entry else None
                if owner and not owner.metadata["parent_id"]:
                    key = (directory.name, thread)
                    previous = candidates.get(key)
                    binding = {"thread_id": thread, "actual_current_context": {}}
                    if previous:
                        binding["actual_current_context"] = previous[2].get("actual_current_context", {})
                    candidates[key] = (directory, owner, binding, contexts, 2)
        for path, meta in index:
            if meta["id"] in bound_ids or meta["parent_id"] or not meta["automation"]:
                continue
            owner = self._verified_read(path, meta["id"])
            if owner:
                # Without a binding receipt, attach only a temporally matching
                # saved batch. An unrelated chat in the same cwd is insufficient.
                directory = None
                started = _time(owner.started_at or meta["timestamp"])
                for possible in directories[:32]:
                    summary = summarize(possible)
                    batch_start = _time(summary.get("started_at")) if summary else None
                    if started and batch_start and -300 <= (started - batch_start).total_seconds() <= 119 * 60:
                        directory = possible
                        break
                # The exact scheduler marker is itself a run binding when no
                # saved manifest is available; never require a batch directory
                # merely to show its currently active chat.
                if directory or meta["automation"]:
                    key = (directory.name if directory else "unbound", meta["id"])
                    candidates.setdefault(key, (directory, owner, {}, [], 0))
        observations = []
        for directory, owner, receipt, contexts, binding_rank in candidates.values():
            owned, limited = self._descendants(owner, contexts, index)
            states = [owner] + [s for s, _ in owned.values()]
            activity_confirmed = any(self._fresh(s, now) for s in states)
            uncertain = limited or any(s.uncertain_hold for s in states)
            last = max((_time(s.last_activity_at) for s in states if _time(s.last_activity_at)), default=datetime.min.replace(tzinfo=timezone.utc))
            saved = summarize(directory) if directory else None
            run_started = _time(saved.get("started_at")) if saved else None
            deadline = _time(saved.get("deadline")) if saved else None
            if run_started and deadline is None:
                deadline = run_started + timedelta(minutes=119)
            run_open = bool(saved and not saved.get("ended_at") and run_started and deadline and run_started <= now <= deadline)
            stages = [dict(row) for row in (saved["stages"] if saved else default_stages)]
            running_stages = self._stage_metrics(directory, read_json, stages) if directory and not (saved and saved.get("ended_at")) else []
            # An exact task binding plus an unclosed batch window and a running
            # stage-owner receipt keeps the current run visible during a quiet
            # external wait. Its freshness remains explicit in the response.
            receipt_active = bool(binding_rank >= 2 and run_open and owner.open_turn and running_stages)
            active = activity_confirmed or receipt_active
            observations.append({"active": active, "activity_confirmed": activity_confirmed,
                "uncertain": uncertain, "last": last, "directory": directory, "owner": owner,
                "receipt": receipt, "owned": owned, "limited": limited, "binding_rank": binding_rank,
                "run_open": run_open, "run_started": run_started, "open_chat": any(s.open_turn for s in states),
                "saved": saved, "stages": stages, "running_stages": running_stages})
        if not observations:
            return empty
        # Prefer a task explicitly bound by the newest open batch. Historical
        # chats left open keep the admission guard below, without owning the
        # banner when a later run has its own exact manifest binding.
        selected = max(observations, key=lambda o: (o["binding_rank"], o["run_open"], o["active"],
                         o["run_started"] or datetime.min.replace(tzinfo=timezone.utc), o["last"], o["uncertain"]))
        active, activity_confirmed = selected["active"], selected["activity_confirmed"]
        uncertain, directory, owner, receipt = (selected[k] for k in ("uncertain", "directory", "owner", "receipt"))
        owned, limited, saved, stages = (selected[k] for k in ("owned", "limited", "saved", "stages"))
        running_stages = selected["running_stages"]
        other_open_work_count = sum(bool(o["active"] or o["uncertain"] or o["open_chat"] or o["limited"])
                                    for o in observations if o is not selected)
        lane_sources = [dict(row) for row in sources(directory)]
        if directory:
            self._inbox_stage(directory, read_json, stages)
        # Match live worker labels to the selected run's frozen policy. A
        # finalized policy-3 run keeps its historical target; an unbound/new
        # pipeline uses the current policy-4 target.
        policy_version = saved.get("policy_version") if isinstance(saved, dict) else None
        hunt_target = (31 if policy_version == 4 or not directory else
                       20 if policy_version == 3 else None)
        workers = []
        active_stages: dict[str, list[dict]] = {}
        all_states = [owner]
        lane_targets = {row.get("id"): row.get("target") for row in lane_sources if isinstance(row, dict)}
        for child, context in owned.values():
            all_states.append(child)
            stage, lane = context["_stage"], context["_lane"]
            fresh = self._fresh(child, now)
            latest = context.get("latest", {})
            latest = latest if isinstance(latest, dict) else {}
            model = _model_label(child.model, child.effort) or _model_label(latest.get("model"), latest.get("effort"))
            worker = {"id": child.metadata["id"], "lane": lane,
                      "label": LANES.get(lane) or next((s["label"] for s in stages if s["id"] == stage), "Pipeline support"),
                      "status": "running" if fresh else "stale" if child.open_turn else child.status,
                      "stage": stage, "last_activity_at": child.last_activity_at, "model": model,
                      "current_turn_id": child.turn_id, "parent_thread_id": child.metadata["parent_id"],
                      "ownership_depth": context["_ownership_depth"], "ownership_chain": context["_ownership_chain"],
                      "open_turn": child.open_turn, "read_unavailable": child.unavailable,
                      "admission_blocked": fresh or child.uncertain_hold,
                      "screened": None,
                      "target": lane_targets.get(lane, hunt_target) if lane and lane != "mail" else None}
            workers.append(worker)
            if fresh and stage:
                active_stages.setdefault(stage, []).append(worker)
        activity_confirmed = any(self._fresh(state, now) for state in all_states)
        receipt_active = bool(selected["binding_rank"] >= 2 and selected["run_open"] and owner.open_turn and running_stages)
        active = activity_confirmed or receipt_active
        stale = not active and (uncertain or any(state.open_turn for state in all_states))
        # A saved terminal finish closes the run immediately. Later chat
        # turns are follow-up work, not pipeline progress; locks stay conservative.
        finished_at = _time(saved.get("ended_at")) if saved else None
        if finished_at:
            return self._finalized(saved, owner, workers, stages, lane_sources, all_states, directory,
                                   read_json, limited, uncertain, active, now, other_open_work_count)
        current = next((stage for stage in STAGE_IDS[1:] if stage in active_stages), None)
        current = current or ("inbox" if "inbox" in active_stages else None)
        current_confirmed = current is not None
        if current is None and self._fresh(owner, now):
            current = owner.comment_stage
            if current is None:
                current = next((s["id"] for s in stages if str(s.get("raw_status", "")).lower() in ("running", "active", "in_progress", "started")), None)
            current_confirmed = current is not None
        current_source = "owned_worker" if current in active_stages else "coordinator_chat" if current_confirmed else None
        if current is None and receipt_active:
            current = running_stages[0]
            current_confirmed = True
            current_source = "saved_stage_receipt"
        if active and current is None:
            # Keep the last known position visible while owned workers are
            # quiet, with explicit uncertainty rather than a running claim.
            quiet_stages = {w["stage"] for w in workers if w["status"] == "stale" and w["stage"]}
            current = next((stage for stage in STAGE_IDS[1:] if stage in quiet_stages), None)
            current = current or ("inbox" if "inbox" in quiet_stages else None)
        owner_context = receipt.get("actual_current_context", {})
        owner_context = owner_context if isinstance(owner_context, dict) else {}
        owner_model = _model_label(owner.model, owner.effort) or _model_label(owner_context.get("model"), owner_context.get("effort"))
        if active:
            for stage in stages:
                if stage["id"] not in active_stages and stage["id"] != current:
                    continue
                stage["saved_status"] = stage["status"]
                stage["status"] = "running" if stage["id"] in active_stages or current_confirmed else "stale"
                stage["observed"] = True
                stage["detail"] = ("Recent activity in a verified pipeline chat. Saved completion and approval checks remain separate." if stage["status"] == "running" else
                                   "Last observed stage in an owned worker chat. Recent worker activity is unconfirmed; no completion is inferred.")
                observed_model = next((w["model"] for w in active_stages.get(stage["id"], []) if w["model"]), None)
                observed_model = observed_model or (next((w["model"] for w in workers if w["stage"] == stage["id"] and w["model"]), None) if not current_confirmed else owner_model)
                if observed_model:
                    stage["model"] = observed_model
                    stage["model_note"] = "Model observed in the owned chat; no generation call was made."
        if directory:
            self._source_counts(directory, read_json, lane_sources, workers)
        logs = sorted([dict(log, label=("Coordinator" if state is owner else next((w["label"] for w in workers if w["id"] == state.metadata["id"]), "Worker")) + " · " + log["label"]) for state in all_states for log in state.logs], key=lambda log: log["at"])[-MAX_LOGS:]
        last = max((_time(s.last_activity_at) for s in all_states if _time(s.last_activity_at)), default=None)
        if active:
            status = "running"
            stage_text = next(s["label"] for s in stages if s["id"] == current) if current else None
            if activity_confirmed:
                summary = "Live pipeline activity observed" + (" · " + stage_text if current_confirmed else
                          " · last observed stage: " + stage_text + "; worker activity is unconfirmed" if stage_text else " · current stage is unconfirmed") + "."
            else:
                summary = "The batch window is open" + (" · " + stage_text + " is the last saved running stage" if stage_text else " · the current stage is unconfirmed") + "; recent chat activity is not confirmed."
        elif stale:
            status, summary = "stale", ("Verified pipeline activity cannot currently be fully checked. Its last open work keeps launch and source edits paused." if uncertain else
                                        "The chat has an unclosed turn, but recent activity is not confirmed.")
        else:
            status, summary = owner.status, "The coordinator turn " + owner.status + ". Pipeline completion and approval remain receipt-based."
        admission_blocked = active or uncertain or any(state.open_turn for state in all_states) or other_open_work_count > 0
        return {"active": active, "status": status, "thread_id": owner.metadata["id"], "title": "CV Weekday Pipeline",
                "current_turn_id": owner.turn_id, "coordinator_status": owner.status,
                "coordinator_open_turn": owner.open_turn, "read_unavailable": owner.unavailable,
                "uncertain": uncertain, "admission_blocked": admission_blocked, "ownership_limited": limited,
                "activity_confirmed": activity_confirmed, "current_stage_source": current_source,
                "other_open_work_count": other_open_work_count,
                "run_id": directory.name if directory else None, "current_stage": current,
                "current_stage_confirmed": current_confirmed,
                "stage_position": STAGE_IDS.index(current) + 1 if current in STAGE_IDS else None,
                "started_at": owner.started_at or owner.metadata["timestamp"], "last_activity_at": _iso(last) if last else None,
                "batch_started_at": saved.get("started_at") if saved else None,
                "batch_deadline_at": saved.get("deadline") if saved else None,
                "finalized": False,
                "observed_at": _iso(now), "summary": summary, "stale": stale, "read_only": True,
                "stages": stages, "logs": logs, "workers": workers, "sources": lane_sources,
                "freshness_seconds": FRESH_SECONDS}

    @staticmethod
    def _stage_metrics(directory: Path, read_json: Callable, stages: list[dict]) -> list[str]:
        receipt = read_json(directory / "stage-metrics.json")
        records = receipt.get("records", []) if isinstance(receipt, dict) else []
        if not isinstance(records, list):
            return []
        by_id = {row.get("id"): row for row in stages if isinstance(row, dict)}
        running = []
        for row in records:
            if not isinstance(row, dict):
                continue
            stage_id = row.get("stage") or row.get("id")
            if stage_id not in by_id:
                continue
            stage = by_id[stage_id]
            raw = row.get("status") or row.get("outcome")
            normalized = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
            if not normalized:
                continue
            if normalized in ("running", "active", "in_progress", "started", "start"):
                stage.update(status="running", raw_status=str(raw),
                             detail="The saved stage-owner receipt is still running.",
                             started_at=row.get("started_at") or row.get("worker_started_at"))
                if stage_id not in running:
                    running.append(stage_id)
            elif normalized.startswith(("not_run", "not_started", "pending", "queued", "unstarted")):
                if stage.get("status") in ("not_recorded", "not_run"):
                    stage.update(status="not_run", raw_status=str(raw), detail="The saved run marks this step as not started.")
            elif stage.get("status") == "not_recorded":
                stage.update(status="partial", raw_status=str(raw), detail="A saved stage-metrics receipt records this outcome.")
        return running

    def _finalized(self, saved, owner, workers, stages, lane_sources, all_states, directory,
                   read_json, limited, uncertain, chat_active, now, other_open_work_count=0):
        """A closed batch whose chats may still be open for follow-up requests."""
        left_open = 0
        for worker in workers:
            if worker["status"] == "stale":
                worker["status"] = "left_open"
                left_open += 1
        self._source_counts(directory, read_json, lane_sources, workers)
        for row in lane_sources:
            if row.get("status") in ("running", "stale"):
                row["status"] = row.get("saved_status") or "partial"
        open_chat = chat_active or any(state.open_turn for state in all_states)
        run_label = str(saved.get("raw_status") or saved.get("status") or "recorded").replace("_", " ").lower()
        if chat_active:
            status = "follow_up"
            summary = f"This run finished ({run_label}). Its chat is open on a follow-up request; new runs and source edits stay paused until it ends."
        elif open_chat:
            status = "left_open"
            summary = (f"This run finished ({run_label}), but {left_open} worker chat{'s were' if left_open != 1 else ' was'} left open. "
                       "New runs stay paused until they close.") if left_open else f"This run finished ({run_label}). Its chat turn was left open."
        else:
            status = "finished"
            summary = f"This run finished ({run_label})."
        logs = sorted([dict(log, label=("Coordinator" if state is owner else next((w["label"] for w in workers if w["id"] == state.metadata["id"]), "Worker")) + " · " + log["label"]) for state in all_states for log in state.logs], key=lambda log: log["at"])[-MAX_LOGS:]
        last = max((_time(s.last_activity_at) for s in all_states if _time(s.last_activity_at)), default=None)
        return {"active": False, "status": status, "thread_id": owner.metadata["id"], "title": "CV Weekday Pipeline",
                "current_turn_id": owner.turn_id, "coordinator_status": owner.status,
                "coordinator_open_turn": owner.open_turn, "read_unavailable": owner.unavailable,
                "uncertain": uncertain, "admission_blocked": open_chat or uncertain or other_open_work_count > 0,
                "other_open_work_count": other_open_work_count, "ownership_limited": limited,
                "run_id": directory.name, "current_stage": None, "current_stage_confirmed": False, "stage_position": None,
                "started_at": owner.started_at or owner.metadata["timestamp"], "last_activity_at": _iso(last) if last else None,
                "batch_started_at": saved.get("started_at"), "batch_deadline_at": saved.get("deadline"),
                "finalized": True, "finished_at": saved.get("ended_at"), "run_status": saved.get("status"),
                "run_raw_status": saved.get("raw_status"), "left_open_workers": left_open,
                "observed_at": _iso(now), "summary": summary, "stale": False, "read_only": True,
                "stages": stages, "logs": logs, "workers": workers, "sources": lane_sources,
                "freshness_seconds": FRESH_SECONDS}

    @staticmethod
    def _inbox_stage(directory, read_json, stages):
        for name in ("inbox_receipt.v3.json", "inbox_receipt.v2.json", "inbox_receipt.json"):
            receipt = read_json(directory / "inbox" / name)
            if not isinstance(receipt, dict):
                continue
            if receipt.get("reviewed") is True and receipt.get("status") == "REVIEWED":
                complete = receipt.get("complete") is True and receipt.get("pagination_complete") is True
                stage = next((s for s in stages if s["id"] == "inbox"), None)
                if stage:
                    stage["saved_status"] = stage["status"]
                    stage["status"] = "done" if complete else "partial"
                    stage["raw_status"] = "REVIEWED_COMPLETE" if complete else "REVIEWED_INCOMPLETE"
                    stage["detail"] = "Read-only inbox review recorded." + (" Complete review receipt is saved." if complete else " The saved receipt still marks completion incomplete.")
            break

    @staticmethod
    def _source_counts(directory, read_json, rows, workers):
        by_lane = {row["id"]: row for row in rows}
        for worker in workers:
            lane = worker["lane"]
            if lane and lane not in by_lane:
                row = {"id": lane, "label": LANES[lane], "status": "not_run", "screened": None,
                       "target": worker["target"], "detail": "No saved lane count.", "target_note": "Count requires a saved source receipt."}
                rows.append(row)
                by_lane[lane] = row
        for lane, row in by_lane.items():
            summary = read_json(directory / "native-workers" / lane / "site_summary.json")
            summary = summary if isinstance(summary, dict) else {}
            counts = summary.get("counts", {})
            counts = counts if isinstance(counts, dict) else {}
            screened = next((n for n in [_number(summary.get("full_relevant_ads")), _number(summary.get("full_live_ads_reviewed")),
                                        _number(counts.get("unique_full_live_relevant_reads")), _number(counts.get("unique_full_live_ads_read")),
                                        _number(counts.get("unique_full_relevant_live_ads_read")),
                                        _number(counts.get("full_relevant_ads"))] if n is not None), None)
            captured = next((n for n in [_number(summary.get("durable_full_ad_captures")), _number(summary.get("live_full_ad_count"))] if n is not None), None)
            if screened is not None:
                row.update(screened=screened, count=screened, count_label="Full relevant adverts read",
                           detail="Full-ad read count recorded in the worker's saved site summary.")
            elif captured is not None:
                if row["status"] in ("held", "not_run"):
                    row["saved_screened"], row["screened"] = row.get("screened"), None
                row.update(captured=captured, count=captured, count_label="Saved full-ad captures",
                           detail="Saved full-ad captures; screening completion is not confirmed by this count.")
                if row.get("screened") is not None:
                    row.update(count=row["screened"], count_label="Full relevant adverts read",
                               detail="Full-ad reads recorded in the saved lane receipt; captures are recorded separately.")
            elif captured is None:
                ads = read_json(directory / "native-workers" / lane / "full_ads.json")
                if isinstance(ads, dict):
                    ads = next((ads.get(key) for key in ("ads", "full_ads", "entries", "items") if isinstance(ads.get(key), list)), None)
                if isinstance(ads, list):
                    captured = sum(isinstance(ad, dict) for ad in ads)
                    if row["status"] in ("held", "not_run"):
                        row["saved_screened"], row["screened"] = row.get("screened"), None
                    row.update(captured=captured, count=captured, count_label="Saved full-ad captures",
                               detail="Provisional saved full-ad captures; this file count is not a screened-ad total.")
                    if row.get("screened") is not None:
                        row.update(count=row["screened"], count_label="Full relevant adverts read",
                                   detail="Full-ad reads recorded in the saved lane receipt; provisional captures are separate.")
            target = next((n for n in [_number(summary.get("full_ad_goal")), _number(counts.get("target_full_ads")),
                                       _number(counts.get("full_ad_goal"))] if n is not None), None)
            if target is not None:
                row["target"] = target
            lane_workers = [w for w in workers if w["lane"] == lane]
            if any(w["status"] == "running" for w in lane_workers):
                row["saved_status"], row["status"] = row["status"], "running"
                row["observed"] = True
            elif any(w["status"] == "stale" for w in lane_workers):
                row["saved_status"], row["status"] = row["status"], "stale"
                row["observed"] = True
            elif any(w["status"] in ("completed", "failed", "interrupted") for w in lane_workers) and row["status"] not in ("done", "ready"):
                row["saved_status"], row["status"] = row["status"], "partial"
                row["observed"] = True
            model = next((w["model"] for w in lane_workers if w["model"]), None)
            if model:
                row["model"] = model
            for worker in lane_workers:
                for key in ("screened", "target", "captured", "count", "count_label"):
                    if key in row:
                        worker[key] = row[key]
