"""Bounded collector for native Codex worker-thread token counters.

The rollout stream is private conversation data. This module retains only an
exact token-counter event's byte coordinates and hash plus the minimum identity
and dispatch hashes needed to verify its owner. It never copies chat text into
Studio history or pipeline receipts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
import tempfile
from typing import Any


KIND = "native_worker_usage_snapshot_v1"
BASIS = "codex_rollout_token_count"
SCOPE = "worker_thread_cumulative"
_RUN_ID = re.compile(r"^(\d{4}-\d{2}-\d{2})-[A-Za-z0-9][A-Za-z0-9_.-]{0,140}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_THREAD = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_STAGES = {"inbox", "hunt", "hunt_audit", "build", "build_audit", "sol_review", "sync"}
_MAX_JSON = 2_000_000
_MAX_SESSION_META = 1_000_000
_MAX_SESSION_LINE = 8_000_000
_MAX_ROLLOUT_BYTES = 512_000_000
_MAX_ROLLOUT_FILES = 4096
_MAX_WORKERS = 500
_PAYLOAD_KEYS = {"kind", "schema_version", "run_id", "generated_at", "counter_basis",
                 "usage_scope", "counter_semantics", "whole_task_exact_tokens",
                 "owner_thread_id", "owner_agent_path", "coverage", "worker_usage_snapshots"}
_ROW_KEYS = {"stage", "worker_id", "thread_id", "usage_thread_id", "usage_scope",
             "usage_counter_basis", "token_scope", "recorded_tokens", "usage_observed_at",
             "usage_binding_verified", "usage_source_integrity_verified", "cache_included",
             "whole_task_exact_tokens", "source_metadata_line_sha256", "source_identity_sha256",
             "owner_metadata_line_sha256", "owner_identity_sha256", "source_event_offset",
             "source_event_length", "source_event_sha256", "dispatch_request_sha256",
             "native_agent_receipt_sha256", "submission_marker_sha256", "spawn_tool_args_sha256",
             "native_ack_sha256", "dispatch_owner_relpath", "owner_thread_id", "owner_agent_path",
             "coverage_kind"}


class NativeUsageError(ValueError):
    """A usage source could not be bound or validated safely."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _duplicate_key_rejected(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _loads(raw: bytes | str) -> Any:
    return json.loads(raw, object_pairs_hook=_duplicate_key_rejected, parse_constant=_reject_constant)


def _json(raw: bytes, label: str) -> dict:
    if len(raw) > _MAX_JSON:
        raise NativeUsageError(f"{label} exceeds the bounded JSON size.")
    try:
        value = _loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise NativeUsageError(f"{label} is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise NativeUsageError(f"{label} must be a JSON object.")
    return value


def _read_file(path: Path, *, root: Path | None = None, limit: int = _MAX_JSON) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise NativeUsageError("A required evidence file is absent or unsafe.")
    try:
        resolved = path.resolve(strict=True)
        if root is not None and not resolved.is_relative_to(root.resolve(strict=True)):
            raise NativeUsageError("An evidence file escaped its bound directory.")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise NativeUsageError("An evidence file is not regular or exceeds its size limit.")
        return path.read_bytes()
    except OSError as exc:
        raise NativeUsageError("A required evidence file could not be read.") from exc


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _valid_counts(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    names = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
    counts: dict[str, int] = {}
    for name in names:
        number = _int(value.get(name))
        if number is None:
            return None
        counts[name] = number
    cache_write = value.get("cache_write_input_tokens")
    if cache_write is not None:
        number = _int(cache_write)
        if number is None:
            return None
        counts["cache_write_input_tokens"] = number
    reasoning = value.get("reasoning_output_tokens")
    if reasoning is not None:
        number = _int(reasoning)
        if number is None or number > counts["output_tokens"]:
            return None
        counts["reasoning_output_tokens"] = number
    if (counts["total_tokens"] != counts["input_tokens"] + counts["output_tokens"] or
            counts["cached_input_tokens"] > counts["input_tokens"] or
            counts.get("cache_write_input_tokens", 0) > counts["input_tokens"]):
        return None
    return counts


def _identity(metadata: dict, *, require_spawn: bool = True) -> tuple[dict, str] | None:
    payload = metadata.get("payload") if isinstance(metadata.get("payload"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    subagent = source.get("subagent") if isinstance(source.get("subagent"), dict) else {}
    spawn = subagent.get("thread_spawn") if isinstance(subagent.get("thread_spawn"), dict) else {}
    fields = {
        "thread_id": payload.get("id"),
        "parent_thread_id": payload.get("parent_thread_id"),
        "cwd": payload.get("cwd"),
        "agent_path": payload.get("agent_path"),
        "spawn_parent_thread_id": spawn.get("parent_thread_id"),
        "spawn_agent_path": spawn.get("agent_path"),
        "spawn_depth": spawn.get("depth"),
    }
    valid_base = (isinstance(fields["thread_id"], str) and _THREAD.fullmatch(fields["thread_id"]) and
            (fields["parent_thread_id"] is None or isinstance(fields["parent_thread_id"], str) and _THREAD.fullmatch(fields["parent_thread_id"])) and
            isinstance(fields["cwd"], str) and isinstance(fields["agent_path"], str))
    valid_spawn = (isinstance(fields["parent_thread_id"], str) and fields["parent_thread_id"] == fields["spawn_parent_thread_id"] and
            fields["agent_path"] == fields["spawn_agent_path"] and
            isinstance(fields["spawn_depth"], int) and not isinstance(fields["spawn_depth"], bool) and
            fields["spawn_depth"] >= 1)
    if (not valid_base or (require_spawn and not valid_spawn)):
        return None
    if (not isinstance(fields["thread_id"], str) or not _THREAD.fullmatch(fields["thread_id"]) or
            require_spawn and (not isinstance(fields["parent_thread_id"], str) or not _THREAD.fullmatch(fields["parent_thread_id"]))):
        return None
    return fields, _sha(_canonical(fields))


def _check_workspace(workspace: Path) -> Path:
    workspace = Path(os.path.abspath(Path(workspace).expanduser()))
    if workspace.is_symlink() or not workspace.is_dir():
        raise NativeUsageError("The visible CV workspace is missing or unsafe.")
    resolved = workspace.resolve(strict=True)
    return resolved


def _run_paths(workspace: Path, run_id: str, today: str, *, allow_noncurrent: bool) -> Path:
    match = _RUN_ID.fullmatch(run_id) if isinstance(run_id, str) else None
    if not match:
        raise NativeUsageError("The run identifier is malformed.")
    try:
        datetime.strptime(match.group(1), "%Y-%m-%d")
    except ValueError as exc:
        raise NativeUsageError("The run identifier has an invalid UTC date.") from exc
    if not allow_noncurrent and match.group(1) != today:
        raise NativeUsageError("Collection is limited to the current UTC date run.")
    evidence = workspace / "Resources" / "Master" / "auto" / "evidence" / "runs"
    run_root = evidence / run_id
    for path in (workspace / "Resources", workspace / "Resources" / "Master", evidence, run_root):
        if path.is_symlink():
            raise NativeUsageError("The run evidence path contains a symlink.")
    if not run_root.is_dir() or run_root.resolve(strict=True) != run_root:
        raise NativeUsageError("The requested run is not inside the visible CV evidence workspace.")
    return run_root


def _verify_pointer(receipt: dict, field: str, expected: Path, codex_dir: Path) -> tuple[dict, bytes]:
    pointer = receipt.get(field)
    if not isinstance(pointer, dict) or not isinstance(pointer.get("path"), str) or not _SHA.fullmatch(str(pointer.get("sha256", ""))):
        raise NativeUsageError(f"The {field} evidence pointer is incomplete.")
    path = Path(pointer["path"])
    if not path.is_absolute() or path.resolve(strict=False) != expected.resolve(strict=False):
        raise NativeUsageError(f"The {field} evidence pointer does not target its assigned file.")
    raw = _read_file(expected, root=codex_dir)
    if _sha(raw) != pointer["sha256"]:
        raise NativeUsageError(f"The {field} evidence hash does not match its receipt.")
    return pointer, raw


def _receipt_binding(receipt_path: Path, stage_dir: Path, owner_dir: Path, run_root: Path,
                     run_id: str, workspace: Path) -> dict:
    codex_dir = owner_dir / ".codex-app"
    if codex_dir.is_symlink() or not codex_dir.is_dir():
        raise NativeUsageError("The native dispatch evidence directory is missing or unsafe.")
    receipt_raw = _read_file(receipt_path, root=codex_dir)
    receipt = _json(receipt_raw, "Native agent receipt")
    if receipt.get("kind") != "codex_app_native_agent_receipt_v1":
        raise NativeUsageError("The native agent receipt kind is unsupported.")
    stage = stage_dir.name
    worker_id = receipt.get("worker_id")
    if (stage not in _STAGES or receipt.get("run_id") != run_id or receipt.get("stage") != stage or
            not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200):
        raise NativeUsageError("The native agent receipt does not match the selected run owner.")
    req_ptr, req_raw = _verify_pointer(receipt, "dispatch_request",
        codex_dir / "stage-dispatch-request.json", codex_dir)
    request = _json(req_raw, "Stage dispatch request")
    if (request.get("kind") != "codex_app_subagent_dispatch_request" or
            request.get("run_id") != run_id or request.get("stage") != stage or
            request.get("worker_id") != worker_id or
            request.get("workspace") != str(owner_dir.resolve(strict=True))):
        raise NativeUsageError("The dispatch request does not match its native owner receipt.")
    short_name = request.get("requested_task_name")
    task_name = receipt.get("task_name")
    if (not isinstance(short_name, str) or not short_name or "/" in short_name or ".." in short_name or
            request.get("task_name") != short_name or task_name != receipt.get("actual_agent_id") or
            not isinstance(task_name, str) or not task_name.startswith("/root/") or
            Path(task_name).name != short_name):
        raise NativeUsageError("The native task identity does not match its dispatch request.")
    if receipt.get("workspace") != str(owner_dir.resolve(strict=True)):
        raise NativeUsageError("The native receipt workspace does not match its owner directory.")

    marker_ptr, marker_raw = _verify_pointer(receipt, "submission_marker",
        codex_dir / "codex-app-spawn-submission-marker.json", codex_dir)
    marker = _json(marker_raw, "Submission marker")
    marker_request = marker.get("dispatch_request")
    if (marker.get("kind") != "codex_app_spawn_submission_marker_v1" or
            marker.get("run_id") != run_id or marker.get("worker_id") != worker_id or
            marker.get("submitted_call") != "collaboration.spawn_agent" or
            not isinstance(marker_request, dict) or marker_request.get("path") != req_ptr.get("path") or
            marker_request.get("sha256") != req_ptr.get("sha256") or
            _time(marker.get("observed_at")) is None):
        raise NativeUsageError("The submission marker does not bind the dispatched worker.")

    args_ptr, args_raw = _verify_pointer(receipt, "spawn_tool_args",
        codex_dir / "codex-app-spawn-tool-args.json", codex_dir)
    args = _json(args_raw, "Spawn tool arguments")
    ack_ptr, ack_raw = _verify_pointer(receipt, "raw_ack",
        codex_dir / "codex-app-spawn-tool-result.json", codex_dir)
    result_ptr, result_raw = _verify_pointer(receipt, "spawn_tool_result",
        codex_dir / "codex-app-spawn-tool-result.json", codex_dir)
    if ack_ptr != result_ptr or ack_raw != result_raw:
        raise NativeUsageError("The raw ACK and saved spawn result disagree.")
    ack = _json(ack_raw, "Native spawn ACK")
    result = _json(result_raw, "Spawn tool result")
    if (args.get("task_name") != short_name or ack.get("task_name") != task_name or
            result.get("task_name") != task_name or receipt.get("prompt_submitted_at") != marker.get("observed_at")):
        raise NativeUsageError("The spawn arguments or ACK do not match the canonical worker task.")
    start = _time(marker.get("observed_at"))
    if start is None:
        raise NativeUsageError("The worker submission clock is unavailable.")
    request_start = _time(request.get("worker_started_at"))
    start = max(start, request_start) if request_start else start
    return {
        "stage": stage, "worker_id": worker_id, "task_name": task_name,
        "worker_started_at": _iso(start), "marker_at": _iso(_time(marker.get("observed_at"))),
        "dispatch_owner_relpath": owner_dir.relative_to(run_root).as_posix(),
        "request_sha256": req_ptr["sha256"], "receipt_sha256": _sha(receipt_raw),
        "submission_marker_sha256": marker_ptr["sha256"], "spawn_args_sha256": args_ptr["sha256"],
        "ack_sha256": ack_ptr["sha256"],
    }


def _owner_receipts(run_root: Path, run_id: str, workspace: Path) -> list[dict]:
    rows = []
    if not run_root.is_dir():
        return rows
    for stage_dir in sorted(run_root.iterdir(), key=lambda p: p.name):
        if stage_dir.name not in _STAGES or stage_dir.is_symlink() or not stage_dir.is_dir():
            continue
        for owner_dir in sorted(stage_dir.iterdir(), key=lambda p: p.name):
            if owner_dir.is_symlink() or not owner_dir.is_dir():
                continue
            codex_dir = owner_dir / ".codex-app"
            receipt_path = codex_dir / "native-agent-receipt.json"
            if not receipt_path.exists():
                continue
            if len(rows) >= _MAX_WORKERS:
                raise NativeUsageError("The run has too many native worker receipts for one bounded scan.")
            rows.append(_receipt_binding(receipt_path, stage_dir, owner_dir, run_root, run_id, workspace))
    if (len({(row["stage"], row["worker_id"]) for row in rows}) != len(rows) or
            len({row["dispatch_owner_relpath"] for row in rows}) != len(rows) or
            len({row["task_name"] for row in rows}) != len(rows)):
        raise NativeUsageError("Native receipts contain duplicate canonical worker ownership.")
    return rows


def _metadata_for_workers(sessions_root: Path, day: str, bindings: list[dict], workspace: Path) -> tuple[dict[str, dict], dict]:
    wanted = {row["task_name"]: row for row in bindings}
    date_path = sessions_root / day[:4] / day[5:7] / day[8:10]
    if any(path.is_symlink() for path in (sessions_root, sessions_root / day[:4],
            sessions_root / day[:4] / day[5:7], date_path)):
        raise NativeUsageError("The current-date session metadata path contains a symlink.")
    if not date_path.is_dir():
        return {}, {}
    if not date_path.resolve(strict=True).is_relative_to(sessions_root.resolve(strict=True)):
        raise NativeUsageError("The current-date session metadata escaped its session root.")
    matches: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    files_seen = 0
    for path in sorted(date_path.iterdir(), key=lambda p: p.name):
        if not path.name.startswith("rollout-") or not path.name.endswith(".jsonl"):
            continue
        files_seen += 1
        if files_seen > _MAX_ROLLOUT_FILES:
            raise NativeUsageError("The current-date session metadata directory exceeds its scan limit.")
        if path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open("rb") as stream:
                raw = stream.readline(_MAX_SESSION_META + 1)
        except OSError:
            continue
        if not raw or len(raw) > _MAX_SESSION_META:
            continue
        try:
            event = _loads(raw)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(event, dict) or event.get("type") != "session_meta":
            continue
        identity = _identity(event, require_spawn=False)
        if identity is None:
            continue
        fields, identity_sha = identity
        if fields["thread_id"] in by_id:
            raise NativeUsageError("More than one current-date rollout claims the same thread identity.")
        by_id[fields["thread_id"]] = {**fields, "metadata_line_sha256": _sha(raw),
                                      "identity_sha256": identity_sha}
        if fields["agent_path"] not in wanted:
            continue
        worker_identity = _identity(event, require_spawn=True)
        if worker_identity is None:
            continue
        fields, identity_sha = worker_identity
        binding = wanted[fields["agent_path"]]
        if (fields["cwd"] != str(workspace) or Path(fields["cwd"]).resolve(strict=False) != workspace or
                Path(fields["agent_path"]).name != Path(binding["task_name"]).name):
            continue
        if fields["thread_id"] in matches:
            raise NativeUsageError("More than one current-date rollout claims a selected worker thread.")
        matches[fields["thread_id"]] = {
            **fields, "metadata_line_sha256": _sha(raw), "identity_sha256": identity_sha,
            "rollout_path": path,
        }
    # Bind each worker to the coordinator's own current-date session metadata.
    for thread_id, row in list(matches.items()):
        owner = by_id.get(row["parent_thread_id"])
        if (owner is None or owner["agent_path"] != str(Path(row["agent_path"]).parent) or
                owner["cwd"] != str(workspace) or
                isinstance(owner["spawn_depth"], int) and row["spawn_depth"] != owner["spawn_depth"] + 1):
            matches.pop(thread_id)
            continue
        row["owner_metadata_line_sha256"] = owner["metadata_line_sha256"]
        row["owner_identity_sha256"] = owner["identity_sha256"]
    # A shared owner path and immediate parent thread are required across the cohort.
    if len({row["agent_path"] for row in matches.values()}) != len(matches):
        raise NativeUsageError("More than one thread claims the same canonical worker agent path.")
    owners = {(row["parent_thread_id"], str(Path(row["agent_path"]).parent)) for row in matches.values()}
    if len(owners) > 1:
        raise NativeUsageError("Selected worker threads do not share one verified coordinator owner.")
    return matches, {"worker_threads_found": len(matches),
                     "owner_thread_id": next(iter(owners))[0] if owners else None,
                     "owner_agent_path": next(iter(owners))[1] if owners else None}


def _metrics_clocks(run_root: Path) -> dict[tuple[str, str], tuple[datetime | None, datetime | None]]:
    path = run_root / "stage-metrics.json"
    if not path.exists():
        return {}
    metrics = _json(_read_file(path, root=run_root, limit=_MAX_JSON), "Stage metrics")
    result = {}
    records = metrics.get("records")
    if not isinstance(records, list):
        return result
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get("stage"), str) or not isinstance(row.get("worker_id"), str):
            continue
        key = (row["stage"], row["worker_id"])
        start = _time(row.get("started_at") or row.get("worker_started_at"))
        finish = _time(row.get("finished_at"))
        old = result.get(key, (None, None))
        starts = [value for value in (old[0], start) if value is not None]
        finishes = [value for value in (old[1], finish) if value is not None]
        result[key] = (min(starts) if starts else None, max(finishes) if finishes else None)
    return result


def _latest_counter(rollout: Path, start: datetime, finish: datetime) -> tuple[dict, dict] | None:
    try:
        size = rollout.stat().st_size
    except OSError:
        return None
    if size > _MAX_ROLLOUT_BYTES or rollout.is_symlink() or not rollout.is_file():
        return None
    offset = 0
    last = None
    previous: dict[str, int] | None = None
    try:
        with rollout.open("rb") as stream:
            while True:
                raw = stream.readline(_MAX_SESSION_LINE + 1)
                if not raw:
                    break
                if len(raw) > _MAX_SESSION_LINE:
                    return None
                event_offset = offset
                offset += len(raw)
                try:
                    event = _loads(raw)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(event, dict) or event.get("type") != "event_msg":
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "token_count":
                    continue
                info = payload.get("info") if isinstance(payload, dict) else None
                raw_counts = info.get("total_token_usage") if isinstance(info, dict) else None
                if raw_counts is None:
                    continue
                timestamp = _time(event.get("timestamp"))
                if timestamp is None:
                    return None
                if not start <= timestamp <= finish:
                    continue
                counts = _valid_counts(raw_counts)
                if counts is None:
                    return None
                comparable = set(counts) & set(previous or {})
                if previous and any(counts[key] < previous[key] for key in comparable):
                    return None
                previous = counts
                last = (timestamp, counts, event_offset, len(raw), _sha(raw))
                del event
    except OSError:
        return None
    if last is None:
        return None
    observed, counts, event_offset, event_length, event_sha = last
    proof = {"source_event_offset": event_offset, "source_event_length": event_length,
             "source_event_sha256": event_sha}
    return {"recorded_tokens": counts, "usage_observed_at": _iso(observed)}, proof


def collect_run(workspace: Path, run_id: str, *, sessions_root: Path | None = None,
                now: datetime | None = None, allow_noncurrent: bool = False) -> dict:
    """Read only current-date metadata and receipts-bound worker rollouts."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise NativeUsageError("The collection clock must include a timezone.")
    now = now.astimezone(timezone.utc)
    workspace = _check_workspace(workspace)
    run_match = _RUN_ID.fullmatch(run_id) if isinstance(run_id, str) else None
    if not run_match:
        raise NativeUsageError("The run identifier is malformed.")
    run_root = _run_paths(workspace, run_id, now.date().isoformat(), allow_noncurrent=allow_noncurrent)
    bindings = _owner_receipts(run_root, run_id, workspace)
    clocks = _metrics_clocks(run_root)
    sessions_root = Path(sessions_root) if sessions_root is not None else Path.home() / ".codex" / "sessions"
    sessions_root = Path(os.path.abspath(sessions_root.expanduser()))
    if sessions_root.is_symlink() or not sessions_root.is_dir():
        raise NativeUsageError("The Codex session root is missing or unsafe.")
    metadata, cohort = _metadata_for_workers(sessions_root, run_match.group(1), bindings, workspace)
    thread_by_task = {row["agent_path"]: row for row in metadata.values()}
    snapshots = []
    worker_ids_with_metadata = set()
    for binding in bindings:
        identity = thread_by_task.get(binding["task_name"])
        if identity is None:
            continue
        worker_ids_with_metadata.add(binding["worker_id"])
        start = _time(binding["worker_started_at"])
        stage_start, stage_finish = clocks.get((binding["stage"], binding["worker_id"]), (None, None))
        if stage_start and stage_start > start:
            start = stage_start
        ceiling = _batch_ceiling(run_root, now)
        # Per-worker finished_at can precede genuine cleanup token events.
        # The run-level close/observation ceiling is the upper bound.
        finish = ceiling
        if not start or finish < start:
            continue
        counter = _latest_counter(identity["rollout_path"], start, finish)
        if counter is None:
            continue
        row = {
            "stage": binding["stage"], "worker_id": binding["worker_id"],
            "thread_id": identity["thread_id"], "usage_thread_id": identity["thread_id"],
            "usage_scope": SCOPE, "usage_counter_basis": BASIS,
            "token_scope": "thread_cumulative", "recorded_tokens": counter[0]["recorded_tokens"],
            "usage_observed_at": counter[0]["usage_observed_at"],
            "usage_binding_verified": True, "usage_source_integrity_verified": True,
            "cache_included": True, "whole_task_exact_tokens": None,
            "source_metadata_line_sha256": identity["metadata_line_sha256"],
            "source_identity_sha256": identity["identity_sha256"],
            "owner_metadata_line_sha256": identity["owner_metadata_line_sha256"],
            "owner_identity_sha256": identity["owner_identity_sha256"],
            **counter[1], "dispatch_request_sha256": binding["request_sha256"],
            "native_agent_receipt_sha256": binding["receipt_sha256"],
            "submission_marker_sha256": binding["submission_marker_sha256"],
            "spawn_tool_args_sha256": binding["spawn_args_sha256"],
            "native_ack_sha256": binding["ack_sha256"],
            "dispatch_owner_relpath": binding["dispatch_owner_relpath"],
            "owner_thread_id": identity["parent_thread_id"],
            "owner_agent_path": str(Path(identity["agent_path"]).parent),
            "coverage_kind": "verified_worker_thread_snapshot",
        }
        snapshots.append(row)
    snapshots.sort(key=lambda row: (row["stage"], row["worker_id"], row["usage_thread_id"]))
    if len({row["usage_thread_id"] for row in snapshots}) != len(snapshots):
        raise NativeUsageError("Duplicate thread identities were found in the selected worker receipts.")
    coverage = {
        "status": "partial" if snapshots else "unavailable",
        "assigned_native_workers": len(bindings),
        "metadata_bound_workers": len(worker_ids_with_metadata),
        "recorded_worker_threads": len(snapshots),
        "missing_worker_counters": max(0, len(bindings) - len(snapshots)),
        "scope_note": "Selected native worker-thread counters only; this is incomplete task coverage.",
    }
    return {
        "kind": KIND, "schema_version": 1, "run_id": run_id,
        "generated_at": _iso(now), "counter_basis": BASIS, "usage_scope": SCOPE,
        "counter_semantics": "Cumulative totals are deduplicated by native worker thread; cached input is included in input and total.",
        "whole_task_exact_tokens": None, "owner_thread_id": cohort.get("owner_thread_id"),
        "owner_agent_path": cohort.get("owner_agent_path"), "coverage": coverage,
        "worker_usage_snapshots": snapshots,
    }


def _valid_snapshot_row(row: Any, run_id: str) -> bool:
    if not isinstance(row, dict):
        return False
    counts = _valid_counts(row.get("recorded_tokens"))
    hashes = ("source_metadata_line_sha256", "source_identity_sha256", "owner_metadata_line_sha256",
              "owner_identity_sha256", "source_event_sha256",
              "dispatch_request_sha256", "native_agent_receipt_sha256", "submission_marker_sha256",
              "spawn_tool_args_sha256", "native_ack_sha256")
    rel = PurePosixPath(row.get("dispatch_owner_relpath", ""))
    safe_rel = (not rel.is_absolute() and len(rel.parts) == 2 and rel.parts[0] == row.get("stage") and
                all(part not in ("", ".", "..") for part in rel.parts) and "\\" not in str(rel))
    return bool(
        set(row) == _ROW_KEYS and
        row.get("usage_scope") == SCOPE and row.get("usage_counter_basis") == BASIS and
        row.get("token_scope") == "thread_cumulative" and row.get("usage_binding_verified") is True and
        row.get("usage_source_integrity_verified") is True and row.get("cache_included") is True and
        row.get("whole_task_exact_tokens") is None and counts is not None and
        isinstance(row.get("stage"), str) and row.get("stage") in _STAGES and
        isinstance(row.get("worker_id"), str) and row.get("worker_id").strip() and len(row["worker_id"]) <= 200 and
        isinstance(row.get("usage_thread_id"), str) and _THREAD.fullmatch(row["usage_thread_id"]) and
        row.get("thread_id") == row.get("usage_thread_id") and _time(row.get("usage_observed_at")) is not None and
        all(isinstance(row.get(key), str) and _SHA.fullmatch(row[key]) for key in hashes) and
        isinstance(row.get("source_event_offset"), int) and not isinstance(row.get("source_event_offset"), bool) and row["source_event_offset"] >= 0 and
        isinstance(row.get("source_event_length"), int) and not isinstance(row.get("source_event_length"), bool) and row["source_event_length"] > 0 and
        isinstance(row.get("owner_thread_id"), str) and _THREAD.fullmatch(row["owner_thread_id"]) and
        isinstance(row.get("owner_agent_path"), str) and safe_rel and
        row.get("coverage_kind") == "verified_worker_thread_snapshot"
    )


def _session_path(sessions_root: Path, day: str, thread_id: str) -> Path | None:
    date_dir = sessions_root / day[:4] / day[5:7] / day[8:10]
    for item in (sessions_root, sessions_root / day[:4], sessions_root / day[:4] / day[5:7], date_dir):
        if item.is_symlink():
            return None
    if not date_dir.is_dir() or not date_dir.resolve(strict=True).is_relative_to(sessions_root.resolve(strict=True)):
        return None
    matches = [path for path in date_dir.iterdir()
               if path.name.startswith("rollout-") and path.name.endswith(f"-{thread_id}.jsonl")]
    if len(matches) != 1:
        return None
    path = matches[0]
    if path.is_symlink() or not path.is_file():
        return None
    try:
        info = path.stat()
    except OSError:
        return None
    return path if stat.S_ISREG(info.st_mode) and info.st_size <= _MAX_ROLLOUT_BYTES else None


def _read_identity(path: Path, *, require_spawn: bool) -> tuple[dict, bytes] | None:
    try:
        with path.open("rb") as stream:
            raw = stream.readline(_MAX_SESSION_META + 1)
    except OSError:
        return None
    if not raw or len(raw) > _MAX_SESSION_META:
        return None
    try:
        event = _loads(raw)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(event, dict) or event.get("type") != "session_meta":
        return None
    identity = _identity(event, require_spawn=require_spawn)
    if identity is None:
        return None
    fields, identity_sha = identity
    return {**fields, "metadata_line_sha256": _sha(raw), "identity_sha256": identity_sha}, raw


def _verify_source_row(row: dict, run_root: Path, workspace: Path, sessions_root: Path,
                       run_id: str, generated_at: datetime) -> str | None:
    date = run_id[:10]
    worker_path = _session_path(sessions_root, date, row["usage_thread_id"])
    owner_path = _session_path(sessions_root, date, row["owner_thread_id"])
    if worker_path is None or owner_path is None:
        return None
    worker_result = _read_identity(worker_path, require_spawn=True)
    owner_result = _read_identity(owner_path, require_spawn=False)
    if worker_result is None or owner_result is None:
        return None
    worker, _worker_raw = worker_result
    owner, _owner_raw = owner_result
    if (worker["thread_id"] != row["usage_thread_id"] or
            worker["metadata_line_sha256"] != row["source_metadata_line_sha256"] or
            worker["identity_sha256"] != row["source_identity_sha256"] or
            worker["parent_thread_id"] != row["owner_thread_id"] or
            worker["cwd"] != str(workspace) or
            owner["thread_id"] != row["owner_thread_id"] or
            owner["metadata_line_sha256"] != row["owner_metadata_line_sha256"] or
            owner["identity_sha256"] != row["owner_identity_sha256"] or
            owner["agent_path"] != row["owner_agent_path"] or owner["cwd"] != str(workspace) or
            worker["spawn_parent_thread_id"] != row["owner_thread_id"] or
            worker["spawn_agent_path"] != worker["agent_path"] or
            str(Path(worker["agent_path"]).parent) != owner["agent_path"]):
        return None
    if isinstance(owner.get("spawn_depth"), int) and worker["spawn_depth"] != owner["spawn_depth"] + 1:
        return None

    stage_dir = run_root / row["stage"]
    owner_dir = run_root.joinpath(*PurePosixPath(row["dispatch_owner_relpath"]).parts)
    if (stage_dir.is_symlink() or owner_dir.is_symlink() or owner_dir.parent != stage_dir or
            not owner_dir.is_dir()):
        return None
    binding = _receipt_binding(owner_dir / ".codex-app/native-agent-receipt.json",
        stage_dir, owner_dir, run_root, run_id, workspace)
    if (binding["dispatch_owner_relpath"] != row["dispatch_owner_relpath"] or
            binding["worker_id"] != row["worker_id"] or binding["task_name"] != worker["agent_path"] or
            binding["request_sha256"] != row["dispatch_request_sha256"] or
            binding["receipt_sha256"] != row["native_agent_receipt_sha256"] or
            binding["submission_marker_sha256"] != row["submission_marker_sha256"] or
            binding["spawn_args_sha256"] != row["spawn_tool_args_sha256"] or
            binding["ack_sha256"] != row["native_ack_sha256"]):
        return None

    metrics = _metrics_clocks(run_root)
    stage_start, _stage_finish = metrics.get((row["stage"], row["worker_id"]), (None, None))
    start = _time(binding["worker_started_at"])
    if stage_start and stage_start > start:
        start = stage_start
    finish = _batch_ceiling(run_root, generated_at)
    if not start or finish < start:
        return None
    offset = row["source_event_offset"]
    length = row["source_event_length"]
    try:
        size = worker_path.stat().st_size
        if offset + length > size or length > _MAX_SESSION_LINE:
            return None
        with worker_path.open("rb") as stream:
            if offset:
                stream.seek(offset - 1)
                if stream.read(1) != b"\n":
                    return None
            stream.seek(offset)
            raw = stream.read(length)
        if len(raw) != length or not raw.endswith(b"\n") or _sha(raw) != row["source_event_sha256"]:
            return None
        event = _loads(raw)
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(event, dict) or event.get("type") != "event_msg":
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    counts = _valid_counts(info.get("total_token_usage") if isinstance(info, dict) else None)
    observed = _time(event.get("timestamp"))
    saved_observed = _time(row.get("usage_observed_at"))
    if not (counts == row.get("recorded_tokens") and observed is not None and observed == saved_observed and
            start <= observed <= finish and observed <= generated_at):
        return None
    return worker["agent_path"]


def _batch_ceiling(run_root: Path, observation: datetime) -> datetime:
    ends = []
    metrics_path = run_root / "stage-metrics.json"
    if metrics_path.exists():
        try:
            metrics = _json(_read_file(metrics_path, root=run_root, limit=_MAX_JSON), "Stage metrics")
            if value := _time(metrics.get("finished_at")):
                ends.append(value)
        except (OSError, NativeUsageError):
            pass
    for name in ("terminal.json", "batch_closed.json", "batch-closed.json"):
        path = run_root / name
        if not path.exists():
            continue
        try:
            value = _json(_read_file(path, root=run_root, limit=_MAX_JSON), "Run terminal receipt")
            end = _time(value.get("finished_at") or value.get("closed_at") or value.get("ended_at"))
            if end:
                ends.append(end)
        except (OSError, NativeUsageError):
            continue
    return min([observation, *ends]) if ends else observation


def load_snapshot(local_root: Path, run_id: str, *, workspace: Path | None = None,
                  sessions_root: Path | None = None) -> list[dict] | None:
    """Load and validate the owned local sidecar; never modify pipeline receipts."""
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        return None
    local_root = Path(local_root)
    history = local_root / "history"
    parent = history / "native-usage"
    path = parent / f"{run_id}.json"
    if any(item.is_symlink() for item in (local_root, history, parent, path)):
        return None
    try:
        raw = _read_file(path, root=parent, limit=5_000_000)
        wrapper = _json(raw, "Native usage sidecar")
        payload = wrapper.get("payload")
        if (not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS or
                wrapper.get("sha256") != _sha(_canonical(payload)) or
                payload.get("kind") != KIND or payload.get("schema_version") != 1 or
                payload.get("run_id") != run_id or payload.get("counter_basis") != BASIS or
                payload.get("usage_scope") != SCOPE or payload.get("whole_task_exact_tokens") is not None or
                not isinstance(payload.get("generated_at"), str) or _time(payload["generated_at"]) is None):
            return None
        rows = payload.get("worker_usage_snapshots")
        if not isinstance(rows, list) or len(rows) > _MAX_WORKERS or not all(_valid_snapshot_row(row, run_id) for row in rows):
            return None
        seen = set()
        seen_owners = set()
        seen_stage_workers = set()
        seen_relpaths = set()
        for row in rows:
            stage_worker = (row["stage"], row["worker_id"])
            if (row["usage_thread_id"] in seen or stage_worker in seen_stage_workers or
                    row["dispatch_owner_relpath"] in seen_relpaths):
                return None
            seen.add(row["usage_thread_id"])
            seen_stage_workers.add(stage_worker)
            seen_relpaths.add(row["dispatch_owner_relpath"])
            seen_owners.add((row["owner_thread_id"], row["owner_agent_path"]))
        if rows:
            if (len(seen_owners) != 1 or
                    (payload.get("owner_thread_id"), payload.get("owner_agent_path")) != next(iter(seen_owners))):
                return None
        elif ((payload.get("owner_thread_id") is not None and
               (not isinstance(payload.get("owner_thread_id"), str) or not _THREAD.fullmatch(payload["owner_thread_id"]))) or
              (payload.get("owner_agent_path") is not None and not isinstance(payload.get("owner_agent_path"), str))):
            return None
        workspace = _check_workspace(workspace or local_root.parents[2])
        run_root = _run_paths(workspace, run_id, datetime.now(timezone.utc).date().isoformat(), allow_noncurrent=True)
        sessions_root = Path(sessions_root) if sessions_root is not None else Path.home() / ".codex" / "sessions"
        sessions_root = Path(os.path.abspath(sessions_root.expanduser()))
        if sessions_root.is_symlink() or not sessions_root.is_dir():
            return None
        generated_at = _time(payload["generated_at"])
        assert generated_at is not None
        verified_tasks = set()
        for row in rows:
            task_path = _verify_source_row(row, run_root, workspace, sessions_root, run_id, generated_at)
            if task_path is None or task_path in verified_tasks:
                return None
            verified_tasks.add(task_path)
        return [dict(row) for row in rows]
    except (OSError, UnicodeError, ValueError, NativeUsageError):
        return None


def write_snapshot(local_root: Path, payload: dict) -> Path:
    """Atomically write one private sidecar snapshot, replacing only itself."""
    run_id = payload.get("run_id") if isinstance(payload, dict) else None
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise NativeUsageError("The sidecar run identifier is malformed.")
    rows = payload.get("worker_usage_snapshots")
    if (set(payload) != _PAYLOAD_KEYS or payload.get("kind") != KIND or payload.get("schema_version") != 1 or
            payload.get("counter_basis") != BASIS or payload.get("usage_scope") != SCOPE or
            payload.get("whole_task_exact_tokens") is not None or
            not isinstance(payload.get("generated_at"), str) or _time(payload["generated_at"]) is None or
            not isinstance(rows, list) or not all(_valid_snapshot_row(row, run_id) for row in rows)):
        raise NativeUsageError("The sidecar contains an invalid worker counter.")
    rows = payload.get("worker_usage_snapshots", [])
    if (len({row["usage_thread_id"] for row in rows}) != len(rows) or
            len({(row["stage"], row["worker_id"]) for row in rows}) != len(rows) or
            len({row["dispatch_owner_relpath"] for row in rows}) != len(rows) or
            len({(row["owner_thread_id"], row["owner_agent_path"]) for row in rows}) > 1 or
            rows and (payload.get("owner_thread_id"), payload.get("owner_agent_path")) !=
            (rows[0]["owner_thread_id"], rows[0]["owner_agent_path"])):
        raise NativeUsageError("The sidecar contains duplicate or mixed worker ownership.")
    local_root = Path(os.path.abspath(Path(local_root).expanduser()))
    history = local_root / "history"
    parent = history / "native-usage"
    for path in (local_root, history, parent):
        if path.is_symlink():
            raise NativeUsageError("The Studio history directory contains a symlink.")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(history, 0o700)
    os.chmod(parent, 0o700)
    target = parent / f"{run_id}.json"
    if target.is_symlink():
        raise NativeUsageError("The destination sidecar is a symlink.")
    wrapper = {"payload": payload, "sha256": _sha(_canonical(payload))}
    encoded = _canonical(wrapper) + b"\n"
    fd = -1
    tmp_path: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{run_id}.", suffix=".tmp", dir=parent)
        tmp_path = Path(name)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, target)
        os.chmod(target, 0o600)
        return target
    except OSError as exc:
        raise NativeUsageError("The Studio usage sidecar could not be saved atomically.") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Collect bounded native worker token counters for one current-day CV run.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2], help="Visible CV workspace root")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sessions-root", type=Path, default=Path.home() / ".codex" / "sessions")
    parser.add_argument("--write", action="store_true", help="Save the verified snapshot under Studio/.local/history/native-usage")
    args = parser.parse_args(argv)
    try:
        payload = collect_run(args.root, args.run_id, sessions_root=args.sessions_root)
        if args.write:
            path = write_snapshot(args.root / "Resources" / "Studio" / ".local", payload)
            print(json.dumps({"status": "saved", "path": str(path),
                              "coverage": payload["coverage"],
                              "whole_task_exact_tokens": None}, sort_keys=True))
        else:
            print(json.dumps({"status": payload["coverage"]["status"],
                              "coverage": payload["coverage"],
                              "worker_counters": len(payload["worker_usage_snapshots"]),
                              "whole_task_exact_tokens": None}, sort_keys=True))
        return 0 if payload["worker_usage_snapshots"] else 2
    except NativeUsageError as exc:
        print(json.dumps({"status": "unavailable", "reason": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
