"""Local, hash-bound readback for an approved CV Studio role.

This module writes no tracker records and grants no approval. It projects the
actual Studio role inventory and the saved run history after the Hunt Audit,
artifact, Build Audit and Sol approval milestones already exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from typing import Any


_STUDIO_DIR = Path(__file__).resolve().parent
if str(_STUDIO_DIR) not in sys.path:
    sys.path.insert(0, str(_STUDIO_DIR))

from data import StudioData, StudioDataError  # noqa: E402
from history import RunHistory  # noqa: E402


READBACK_KIND = "cv_studio_sync_readback_v1"
ARTIFACT_MANIFEST_KIND = "cv_studio_artifact_manifest_v1"
ARTIFACT_REGISTRY_KIND = "cv_studio_artifact_refs_v1"
GATE_REGISTRY_KIND = "cv_studio_gate_refs_v1"
REQUIRED_ARTIFACTS = ("html", "pdf", "jd", "letter_pdf", "motivation")
GATE_STAGES = ("hunt_audit", "build_audit", "sol_review")
GATE_ALLOWED_STATUS = {
    "hunt_audit": {"DONE", "PASS", "ACCEPTED"},
    "build_audit": {"DONE", "PASS", "PASSED"},
    "sol_review": {"APPROVED"},
}
CHAIN_STAGES = ("hunt_audit", "artifact", "build_audit", "sol_review")
SUCCESS_STATUS = {
    "hunt_audit": "accepted",
    "artifact": "done",
    "build_audit": "passed",
    "sol_review": "approved",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,180}$")
_ROLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_IDENTITY_KEYS = {"role_id", "candidate_id", "job_id", "public_role_id"}
_PATH_KEYS = {"canonical_role_path", "role_path", "accepted_role_path", "accepted_role_paths",
              "accepted_paths", "folder"}


class StudioSyncError(ValueError):
    """An evidence or identity check failed closed."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise StudioSyncError(f"Could not read evidence file: {path}") from exc
    return digest.hexdigest()


def _json_file(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise StudioSyncError(f"{label} is missing or is a symlink: {path}")
    try:
        if path.stat().st_size > 16_000_000:
            raise StudioSyncError(f"{label} is too large")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise StudioSyncError(f"{label} is not readable JSON") from exc
    if not isinstance(value, dict):
        raise StudioSyncError(f"{label} must contain a JSON object")
    return value


def _safe_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
        raise StudioSyncError("run_id is not a safe saved run identifier")
    return run_id


def _safe_role_id(role_id: str) -> str:
    if not isinstance(role_id, str) or not _ROLE_ID.fullmatch(role_id):
        raise StudioSyncError("role must be the exact public candidate role ID")
    return role_id


def _path_from_workspace(value: str, data: StudioData, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise StudioSyncError(f"{label} path is missing")
    raw = Path(value).expanduser()
    if raw.is_absolute():
        path = raw
    else:
        candidates = (data.workspace / raw, data.root / raw)
        existing = []
        for candidate in candidates:
            try:
                resolved_candidate = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if resolved_candidate.is_file():
                existing.append(resolved_candidate)
        unique = {str(item) for item in existing}
        if len(unique) != 1:
            raise StudioSyncError(f"{label} path is missing or ambiguous: {value}")
        path = Path(next(iter(unique)))
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise StudioSyncError(f"{label} path does not resolve: {value}") from exc
    if not resolved.is_file() or path.is_symlink():
        raise StudioSyncError(f"{label} path must be a regular non-symlink file")
    return resolved


def _workspace_relative(path: Path, data: StudioData) -> str:
    try:
        return path.resolve().relative_to(data.workspace.resolve()).as_posix()
    except ValueError as exc:
        raise StudioSyncError(f"Evidence path is outside the CV workspace: {path}") from exc


def _resources_relative(path: Path, data: StudioData) -> str:
    try:
        return path.resolve().relative_to(data.root.resolve()).as_posix()
    except ValueError as exc:
        raise StudioSyncError(f"Gate evidence path is outside Resources: {path}") from exc


def _local_relative(path: Path, data: StudioData) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(data.root.resolve()).as_posix()
    except ValueError:
        try:
            return (PurePosixPath("Tailored CVs") /
                    PurePosixPath(resolved.relative_to(data.role_root.resolve()).as_posix())).as_posix()
        except ValueError as exc:
            raise StudioSyncError(f"Evidence path is outside Resources and the actual role tree: {path}") from exc


def _lexical_workspace_relative(path: Path, data: StudioData) -> str:
    """Return a workspace path without resolving an authorized legacy alias."""
    lexical = Path(os.path.abspath(path))
    try:
        return lexical.relative_to(Path(os.path.abspath(data.workspace))).as_posix()
    except ValueError as exc:
        raise StudioSyncError(f"Artifact alias is outside the CV workspace: {path}") from exc


def _lexical_resource_relative(path: Path, data: StudioData) -> str:
    lexical = Path(os.path.abspath(path))
    for base in (Path(os.path.abspath(data.root)), Path(os.path.abspath(data.workspace / "Tailored CVs")).parent):
        try:
            relative = lexical.relative_to(base)
        except ValueError:
            continue
        value = relative.as_posix()
        # The canonical artifact namespace is the role tree, with Resources
        # supplied by StudioData's exact reverse alias when needed.
        if value.startswith("Resources/"):
            value = value.removeprefix("Resources/")
        return value
    raise StudioSyncError(f"Artifact path has no stable Resources-relative spelling: {path}")


def _canonical_role_path(item: dict[str, Any]) -> str:
    value = item.get("folder")
    if not isinstance(value, str) or not value.startswith("Tailored CVs/"):
        raise StudioSyncError("Studio role has no canonical Tailored CVs path")
    normalized = PurePosixPath(value).as_posix()
    if normalized != value or ".." in PurePosixPath(normalized).parts:
        raise StudioSyncError("Studio role path is not canonical")
    return normalized


def _role_directory(data: StudioData, canonical_path: str) -> Path:
    relative = PurePosixPath(canonical_path).relative_to("Tailored CVs")
    role_dir = (data.role_root / Path(*relative.parts)).resolve()
    try:
        role_dir.relative_to(data.role_root.resolve())
    except ValueError as exc:
        raise StudioSyncError("Canonical role path escaped the actual Studio role root") from exc
    if not role_dir.is_dir():
        raise StudioSyncError("Canonical accepted role folder is missing")
    return role_dir


def _artifact_path(value: str, expected_sha: str, data: StudioData,
                   canonical_path: str, role_dir: Path, label: str) -> tuple[Path, str]:
    """Resolve one role-local artifact, allowing only tidy_roles-sealed aliases."""
    raw = Path(value).expanduser()
    lexical = raw if raw.is_absolute() else data.workspace / raw
    lexical = Path(os.path.abspath(lexical))
    if lexical.is_symlink():
        manifest_path = data.root / "Master" / "auto" / "local" / "pipeline-repair" / "tidy_roles_manifest.json"
        manifest = _json_file(manifest_path, "Tidy-role compatibility manifest")
        if manifest.get("schema_version") != 1 or manifest.get("root_relative") != "Tailored CVs":
            raise StudioSyncError("Tidy-role compatibility manifest has an unsupported schema")
        canonical_relative = canonical_path.removeprefix("Tailored CVs/")
        matches = []
        for entry in manifest.get("roles", []):
            if not isinstance(entry, dict) or entry.get("role_path") != canonical_relative:
                continue
            support_dir_text = entry.get("support_dir")
            support_dir = PurePosixPath(str(support_dir_text or ""))
            if support_dir.is_absolute() or not support_dir.parts or ".." in support_dir.parts:
                continue
            support_root = (role_dir / Path(*support_dir.parts)).resolve()
            if not support_root.is_dir() or not support_root.is_relative_to(role_dir):
                continue
            for item in entry.get("files", []):
                if not isinstance(item, dict):
                    continue
                old_value = item.get("legacy_link_path", item.get("original_path", ""))
                old = data.workspace / "Tailored CVs" / str(old_value)
                old_parts = PurePosixPath(str(old_value).replace("\\", "/"))
                if old_parts.is_absolute() or ".." in old_parts.parts or old_parts.as_posix() != f"{canonical_relative}/{lexical.name}":
                    continue
                try:
                    support_path = PurePosixPath(str(item.get("support_path", "")))
                    if support_path.is_absolute() or ".." in support_path.parts:
                        continue
                    target = (role_dir / Path(*support_path.parts)).resolve(strict=True)
                    same_legacy = (old.name == lexical.name and old.parent.resolve() == lexical.parent.resolve())
                except (OSError, RuntimeError):
                    continue
                if (same_legacy and target == lexical.resolve() and target.is_relative_to(support_root) and
                        item.get("sha256") == expected_sha and os.readlink(lexical) == item.get("legacy_link_target")):
                    matches.append(item)
        if len(matches) != 1:
            raise StudioSyncError(f"{label} compatibility alias is not uniquely bound to the exact role and hash")
        target_path = lexical.resolve(strict=True)
    else:
        try:
            target_path = lexical.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise StudioSyncError(f"{label} file is missing") from exc
        if not target_path.is_file():
            raise StudioSyncError(f"{label} path is not a regular file")
    if not target_path.is_file() or not target_path.is_relative_to(role_dir):
        raise StudioSyncError(f"{label} resolved outside the exact canonical role folder")
    if sha256_file(target_path) != expected_sha:
        raise StudioSyncError(f"{label} hash changed")
    return target_path, _lexical_resource_relative(lexical, data)


def _parse_manifest(raw: bytes, *, run_id: str, role_id: str, canonical_path: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise StudioSyncError("Artifact manifest is not valid UTF-8 JSON") from exc
    if (not isinstance(value, dict) or value.get("kind") != ARTIFACT_MANIFEST_KIND or
            value.get("sealed") is not True or value.get("run_id") != run_id or
            value.get("role_id") != role_id or value.get("canonical_role_path") != canonical_path):
        raise StudioSyncError("Artifact manifest is not a sealed manifest for this exact run and role")
    receipt = value.get("artifact_receipt")
    refs = value.get("artifact_refs")
    if receipt is not None and not isinstance(receipt, dict):
        raise StudioSyncError("Artifact manifest receipt reference must be an object")
    if not isinstance(refs, list):
        raise StudioSyncError("Artifact manifest lacks its receipt or artifact references")
    receipt = receipt or {}
    receipt_path = receipt.get("path") or value.get("receipt_path")
    receipt_sha = receipt.get("sha256") or value.get("receipt_sha256")
    if not isinstance(receipt_path, str) or not isinstance(receipt_sha, str) or not _SHA256.fullmatch(receipt_sha):
        raise StudioSyncError("Artifact manifest receipt reference is malformed")
    names: dict[str, dict[str, str]] = {}
    for row in refs:
        if not isinstance(row, dict):
            raise StudioSyncError("Artifact references must be objects")
        name, path, digest = row.get("name"), row.get("path"), row.get("sha256")
        if name not in REQUIRED_ARTIFACTS or name in names:
            raise StudioSyncError("Artifact manifest has a missing, duplicate or unexpected logical artifact name")
        if not isinstance(path, str) or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise StudioSyncError(f"Artifact reference {name!r} has an invalid path or hash")
        names[name] = {"path": path, "sha256": digest}
    if set(names) != set(REQUIRED_ARTIFACTS):
        raise StudioSyncError("Artifact manifest must bind html, pdf, jd, letter_pdf and motivation")
    if len({row["path"] for row in names.values()}) != len(names):
        raise StudioSyncError("Artifact manifest repeats an artifact path")
    return {"artifact_receipt": {"path": receipt_path, "sha256": receipt_sha},
            "artifact_refs_by_name": names}


def _read_history(data: StudioData, run_id: str) -> dict[str, Any]:
    # Check actual evidence first: RunHistory can also recover a saved Studio
    # snapshot when source receipts disappear, which is not sufficient for Sync.
    try:
        source = data.run(run_id)
    except (StudioDataError, OSError) as exc:
        raise StudioSyncError(f"The saved run {run_id!r} is unavailable") from exc
    if source.get("id") != run_id or source.get("kind") != "batch":
        raise StudioSyncError("run_id did not resolve to the exact saved CV Pipeline batch")
    try:
        history = RunHistory(data).run(run_id)
    except (StudioDataError, OSError, ValueError) as exc:
        raise StudioSyncError(f"Studio could not project the saved run {run_id!r}") from exc
    if (history.get("id") != run_id or history.get("snapshot_status") == "recovered" or
            history.get("snapshot_warning")):
        raise StudioSyncError("Studio history is recovered or mismatched; current run receipts are required")
    if history.get("policy_version") != 4:
        raise StudioSyncError("Studio Sync readback is supported only for newly sealed policy-4 runs")
    return history


def _milestones(history: dict[str, Any], role_id: str) -> list[dict[str, Any]]:
    gathered: list[dict[str, Any]] = []
    root = history.get("role_milestones")
    if isinstance(root, list):
        gathered.extend(row for row in root if isinstance(row, dict))
    jobs = history.get("jobs")
    if isinstance(jobs, list):
        for job in jobs:
            if isinstance(job, dict) and isinstance(job.get("milestones"), list):
                gathered.extend(row for row in job["milestones"] if isinstance(row, dict))
    by_identity: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in gathered:
        if row.get("role_id") != role_id or row.get("kind") != "role_milestone_receipt":
            continue
        key = (row.get("stage"), row.get("worker_id"), row.get("at"), row.get("status"), row.get("evidence_sha256"))
        by_identity[key] = row
    result = list(by_identity.values())
    result.sort(key=lambda row: (str(row.get("at", "")), CHAIN_STAGES.index(row["stage"]) if row.get("stage") in CHAIN_STAGES else 99))
    return result


def _current_chain(history: dict[str, Any], role_id: str) -> dict[str, dict[str, Any]]:
    rows = _milestones(history, role_id)
    current = {row.get("stage"): row for row in rows if row.get("current") is True}
    if any(stage not in current for stage in CHAIN_STAGES):
        raise StudioSyncError("The exact accepted role lacks a current accepted→artifact→audit→approval chain")
    chain = {stage: current[stage] for stage in CHAIN_STAGES}
    labels = {row.get("role") for row in chain.values()}
    if len(labels) != 1 or not next(iter(labels), None):
        raise StudioSyncError("Current role milestones disagree on role identity")
    for stage in CHAIN_STAGES:
        row = chain[stage]
        if row.get("status") != SUCCESS_STATUS[stage]:
            raise StudioSyncError(f"Current {stage} milestone is not successful")
        if not isinstance(row.get("worker_id"), str) or not row["worker_id"]:
            raise StudioSyncError(f"Current {stage} milestone has no owning worker")
        if not isinstance(row.get("at"), str) or not row["at"]:
            raise StudioSyncError(f"Current {stage} milestone has no timestamp")
        evidence = row.get("evidence_sha256")
        if not isinstance(evidence, str) or not _SHA256.fullmatch(evidence):
            raise StudioSyncError(f"Current {stage} milestone has no sealed evidence hash")
    for before, after in zip(CHAIN_STAGES, CHAIN_STAGES[1:]):
        prior, row = chain[before], chain[after]
        if str(row["at"]) < str(prior["at"]):
            raise StudioSyncError(f"Current {after} milestone predates {before}")
        if row.get("predecessor_sha256") != prior.get("evidence_sha256"):
            raise StudioSyncError(f"Current {after} milestone does not bind the current {before} receipt")
    return chain


def _walk_json_identity(value: Any, role_id: str, canonical_path: str) -> tuple[bool, bool]:
    found_id = False
    found_path = False
    if isinstance(value, dict):
        for key, child in value.items():
            key_norm = str(key).casefold().replace("-", "_")
            if key_norm in _IDENTITY_KEYS and child == role_id:
                found_id = True
            if key_norm in _PATH_KEYS:
                path_values = child if isinstance(child, list) else [child]
                for path_value in path_values:
                    if isinstance(path_value, str) and path_value.replace("\\", "/") == canonical_path:
                        found_path = True
            sub_id, sub_path = _walk_json_identity(child, role_id, canonical_path)
            found_id |= sub_id
            found_path |= sub_path
    elif isinstance(value, list):
        for child in value:
            sub_id, sub_path = _walk_json_identity(child, role_id, canonical_path)
            found_id |= sub_id
            found_path |= sub_path
    return found_id, found_path


def _same_receipt_record_binds_role_and_path(value: Any, role_id: str,
                                             canonical_path: str, role_dir: Path) -> bool:
    """Require the public ID and accepted folder on one JSON record.

    Receipt-level aggregation is unsafe when several accepted roles share one
    Hunt Audit packet: one row's ID must never borrow another row's path.
    """
    if isinstance(value, dict):
        direct_id = any(str(key).casefold().replace("-", "_") in _IDENTITY_KEYS and child == role_id
                        for key, child in value.items())
        direct_path = False
        for key, child in value.items():
            if str(key).casefold().replace("-", "_") not in _PATH_KEYS:
                continue
            path_values = child if isinstance(child, list) else [child]
            for path_value in path_values:
                if not isinstance(path_value, str):
                    continue
                if path_value.replace("\\", "/") == canonical_path:
                    direct_path = True
                elif Path(path_value).is_absolute():
                    try:
                        direct_path = direct_path or Path(path_value).resolve() == role_dir.resolve()
                    except OSError:
                        pass
        if direct_id and direct_path:
            return True
        return any(_same_receipt_record_binds_role_and_path(child, role_id, canonical_path, role_dir)
                   for child in value.values())
    if isinstance(value, list):
        return any(_same_receipt_record_binds_role_and_path(child, role_id, canonical_path, role_dir)
                   for child in value)
    return False


def _receipt_json(path: Path, label: str) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise StudioSyncError(f"{label} receipt is not readable text") from exc
    try:
        return json.loads(raw)
    except ValueError:
        rows = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise StudioSyncError(f"{label} receipt is neither JSON nor JSONL") from exc
        if not rows:
            raise StudioSyncError(f"{label} receipt is empty")
        return rows


def _receipt_matches_role(path: Path, role_id: str, canonical_path: str, role_dir: Path, *, require_path: bool) -> None:
    value = _receipt_json(path, "Stage gate")
    found_id, _ = _walk_json_identity(value, role_id, canonical_path)
    if require_path:
        if not _same_receipt_record_binds_role_and_path(value, role_id, canonical_path, role_dir):
            raise StudioSyncError("Stage gate does not bind this public role ID to the exact canonical Studio role path")
    elif not found_id:
        raise StudioSyncError(f"Stage gate does not contain the exact public role ID {role_id!r}")


def _sync_dir(data: StudioData, run_id: str, role_id: str, *, create: bool) -> tuple[Path, Path]:
    run_id = _safe_run_id(run_id)
    role_id = _safe_role_id(role_id)
    run_dir = data.evidence / run_id
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise StudioSyncError("The exact run evidence directory is missing or is a symlink")
    try:
        if run_dir.resolve(strict=True).parent != data.evidence.resolve(strict=True):
            raise StudioSyncError("run_id escaped the canonical run evidence root")
    except OSError as exc:
        raise StudioSyncError("The exact run evidence directory is unavailable") from exc
    sync_root = run_dir / "cv-studio-sync"
    if sync_root.is_symlink():
        raise StudioSyncError("The run-local Studio Sync directory may not be a symlink")
    if create:
        sync_root.mkdir(mode=0o700, parents=False, exist_ok=True)
        os.chmod(sync_root, 0o700)
    if not sync_root.is_dir() or sync_root.resolve().parent != run_dir.resolve():
        raise StudioSyncError("The run-local Studio Sync directory is unavailable")
    sync_dir = sync_root / role_id
    if sync_dir.is_symlink():
        raise StudioSyncError("The per-role Studio Sync directory may not be a symlink")
    if create:
        sync_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(sync_dir, 0o700)
    if not sync_dir.is_dir() or sync_dir.resolve().parent != sync_root.resolve():
        raise StudioSyncError("The per-role Studio Sync directory is unavailable")
    return run_dir.resolve(), sync_dir.resolve()


def _stage_artifact_manifest(root: Path | str, run_id: str, role_id: str, manifest_path: Path | str) -> Path:
    data = StudioData(Path(root))
    run_id, role_id = _safe_run_id(run_id), _safe_role_id(role_id)
    history = _read_history(data, run_id)
    chain = _current_chain(history, role_id)
    source = _path_from_workspace(str(manifest_path), data, label="Artifact manifest")
    raw = source.read_bytes()
    try:
        manifest_value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise StudioSyncError("Artifact manifest is not valid JSON") from exc
    if not isinstance(manifest_value, dict):
        raise StudioSyncError("Artifact manifest must be a JSON object")
    canonical_path = manifest_value.get("canonical_role_path")
    studio_role_id = manifest_value.get("studio_role_id")
    if not isinstance(canonical_path, str):
        raise StudioSyncError("Artifact manifest must name the exact canonical Studio role path")
    roles = data.roles().get("items", [])
    matches = [row for row in roles if isinstance(row, dict) and row.get("folder") == canonical_path]
    if len(matches) != 1:
        raise StudioSyncError("Artifact manifest canonical path does not match exactly one Studio role")
    actual_studio_role_id = matches[0].get("id")
    if not isinstance(actual_studio_role_id, str) or (studio_role_id is not None and studio_role_id != actual_studio_role_id):
        raise StudioSyncError("Artifact manifest Studio catalog ID differs from actual inventory")
    role_dir = _role_directory(data, canonical_path)
    run_dir, sync_dir = _sync_dir(data, run_id, role_id, create=True)
    if not (source.is_relative_to(run_dir) or source.is_relative_to(role_dir)):
        raise StudioSyncError("Artifact manifest must be saved in this run or its exact accepted role folder")
    raw = source.read_bytes()
    parsed = _parse_manifest(raw, run_id=run_id, role_id=role_id, canonical_path=canonical_path)
    if parsed["artifact_receipt"]["sha256"] != chain["artifact"]["evidence_sha256"]:
        raise StudioSyncError("Artifact receipt hash differs from the current artifact milestone")
    receipt_path = _path_from_workspace(parsed["artifact_receipt"]["path"], data, label="Artifact receipt")
    if not (receipt_path.is_relative_to(run_dir) or receipt_path.is_relative_to(role_dir)):
        raise StudioSyncError("Artifact receipt must be saved in this run or its exact accepted role folder")
    if sha256_file(receipt_path) != parsed["artifact_receipt"]["sha256"]:
        raise StudioSyncError("Artifact receipt hash changed")
    _receipt_matches_role(receipt_path, role_id, canonical_path, role_dir, require_path=True)

    refs = []
    for name in REQUIRED_ARTIFACTS:
        ref = parsed["artifact_refs_by_name"][name]
        path, preserved_path = _artifact_path(ref["path"], ref["sha256"], data,
            canonical_path, role_dir, f"Artifact {name}")
        refs.append({"name": name, "path": preserved_path, "sha256": ref["sha256"]})
    staged = {
        "kind": ARTIFACT_REGISTRY_KIND,
        "run_id": run_id,
        "role_id": role_id,
        "canonical_role_path": canonical_path,
        "studio_role_id": actual_studio_role_id,
        "source_manifest": {"path": _local_relative(source, data), "sha256": _digest_bytes(raw)},
        "artifact_receipt": {"path": _local_relative(receipt_path, data),
                             "sha256": parsed["artifact_receipt"]["sha256"]},
        "artifact_refs": refs,
    }
    target = sync_dir / "artifact-refs.json"
    _write_immutable_json(target, staged, label="Artifact refs")
    return target


def _write_immutable_json(path: Path, value: dict[str, Any], *, label: str) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.is_symlink():
        raise StudioSyncError(f"{label} output may not be a symlink")
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise StudioSyncError(f"Existing {label} is not valid JSON; refusing to replace it") from exc
        if existing != value:
            raise StudioSyncError(f"Existing {label} is immutable and differs from the current evidence")
        return
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _load_staged_artifacts(data: StudioData, sync_dir: Path, run_id: str, role_id: str,
                           studio_role_id: str, canonical_path: str, role_dir: Path,
                           artifact_milestone: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    registry_path = sync_dir / "artifact-refs.json"
    staged = _json_file(registry_path, "Run-local artifact refs")
    if (staged.get("kind") != ARTIFACT_REGISTRY_KIND or staged.get("run_id") != run_id or
            staged.get("role_id") != role_id or staged.get("studio_role_id") != studio_role_id or
            staged.get("canonical_role_path") != canonical_path):
        raise StudioSyncError("Run-local artifact refs do not match the exact role and run")
    source = staged.get("source_manifest")
    receipt = staged.get("artifact_receipt")
    refs = staged.get("artifact_refs")
    if not isinstance(source, dict) or not isinstance(receipt, dict) or not isinstance(refs, list):
        raise StudioSyncError("Run-local artifact refs lack sealed source references")
    source_path = _path_from_workspace(source.get("path", ""), data, label="Artifact manifest source")
    if not (source_path.is_relative_to((data.evidence / run_id).resolve()) or source_path.is_relative_to(role_dir)):
        raise StudioSyncError("Sealed artifact manifest source escaped this run and role")
    if sha256_file(source_path) != source.get("sha256"):
        raise StudioSyncError("Source artifact manifest changed after it was staged")
    parsed = _parse_manifest(source_path.read_bytes(), run_id=run_id, role_id=role_id, canonical_path=canonical_path)
    if parsed["artifact_receipt"]["sha256"] != artifact_milestone.get("evidence_sha256"):
        raise StudioSyncError("Source artifact receipt is stale against the current artifact milestone")
    receipt_path = _path_from_workspace(receipt.get("path", ""), data, label="Artifact receipt")
    if not (receipt_path.is_relative_to((data.evidence / run_id).resolve()) or receipt_path.is_relative_to(role_dir)):
        raise StudioSyncError("Artifact receipt escaped this run and role")
    if receipt.get("sha256") != artifact_milestone.get("evidence_sha256") or sha256_file(receipt_path) != receipt.get("sha256"):
        raise StudioSyncError("Artifact receipt no longer matches the current artifact milestone")
    _receipt_matches_role(receipt_path, role_id, canonical_path, role_dir, require_path=True)

    parsed_by_name = parsed["artifact_refs_by_name"]
    staged_by_name = {row.get("name"): row for row in refs if isinstance(row, dict)}
    if set(staged_by_name) != set(REQUIRED_ARTIFACTS) or len(staged_by_name) != len(refs):
        raise StudioSyncError("Run-local artifact refs must contain the exact five required logical artifacts")
    out = []
    for name in REQUIRED_ARTIFACTS:
        ref = staged_by_name[name]
        source_ref = parsed_by_name[name]
        if ref.get("sha256") != source_ref["sha256"]:
            raise StudioSyncError(f"Artifact {name} staged hash differs from its sealed source manifest")
        source_path, source_preserved_path = _artifact_path(source_ref["path"], source_ref["sha256"], data,
            canonical_path, role_dir, f"Artifact {name} source")
        path, preserved_path = _artifact_path(ref.get("path", ""), ref["sha256"], data,
            canonical_path, role_dir, f"Artifact {name}")
        if preserved_path != source_preserved_path or path != source_path:
            raise StudioSyncError(f"Artifact {name} staged path differs from its sealed source manifest")
        out.append({"name": name, "path": preserved_path, "sha256": ref["sha256"]})
    out.sort(key=lambda row: row["path"])
    return out, {"receipt_path": _local_relative(receipt_path, data), "sha256": receipt["sha256"]}


def _load_gates(data: StudioData, sync_dir: Path, run_id: str, role_id: str,
                canonical_path: str, role_dir: Path, chain: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    registry = _json_file(sync_dir / "gate-refs.json", "Run-local gate refs")
    if registry.get("kind") != GATE_REGISTRY_KIND or registry.get("run_id") != run_id:
        raise StudioSyncError("Run-local gate refs do not match the exact saved run")
    if registry.get("role_id") != role_id:
        raise StudioSyncError("Run-local gate refs do not match the exact public role ID")
    entry = registry.get("gates")
    if not isinstance(entry, dict):
        raise StudioSyncError("Run-local gate refs have no exact public role ID entry")
    gates: dict[str, dict[str, Any]] = {}
    run_dir = data.evidence / run_id
    source = registry.get("source_manifest")
    if not isinstance(source, dict):
        raise StudioSyncError("Run-local gate refs lack their sealed source manifest binding")
    source_path = _path_from_workspace(source.get("path", ""), data, label="Gate manifest source")
    if not (source_path.is_relative_to(run_dir.resolve()) or source_path.is_relative_to(role_dir)):
        raise StudioSyncError("Sealed gate manifest source escaped this run and role")
    if sha256_file(source_path) != source.get("sha256"):
        raise StudioSyncError("Source gate manifest changed after staging")
    source_value = _json_file(source_path, "Gate manifest source")
    if (source_value.get("kind") != GATE_REGISTRY_KIND or source_value.get("sealed") is not True or
            source_value.get("run_id") != run_id or source_value.get("role_id") != role_id):
        raise StudioSyncError("Source gate manifest lost its exact run/role binding")
    source_gates = source_value.get("gates")
    if not isinstance(source_gates, dict):
        source_roles = source_value.get("roles")
        source_gates = source_roles.get(role_id) if isinstance(source_roles, dict) else None
    if source_gates != entry:
        raise StudioSyncError("Staged gate refs differ from the immutable source manifest")
    for stage in GATE_STAGES:
        ref = entry.get(stage)
        if not isinstance(ref, dict):
            raise StudioSyncError(f"Run-local gate refs lack {stage}")
        path = _path_from_workspace(ref.get("receipt_path", ""), data, label=f"{stage} gate")
        if not (path.is_relative_to(run_dir.resolve()) or path.is_relative_to(role_dir)):
            raise StudioSyncError(f"{stage} gate is outside this run and exact accepted role")
        digest = ref.get("sha256")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise StudioSyncError(f"{stage} gate has a malformed SHA-256")
        if digest != chain[stage]["evidence_sha256"] or sha256_file(path) != digest:
            raise StudioSyncError(f"{stage} gate does not match its current sealed role milestone")
        gate_value = _json_file(path, f"{stage} gate")
        status = ref.get("status")
        status_upper = str(status).upper()
        if status_upper not in GATE_ALLOWED_STATUS[stage] or str(gate_value.get("status", "")).upper() != status_upper:
            raise StudioSyncError(f"{stage} gate status does not match its actual receipt")
        if ref.get("at") != chain[stage]["at"]:
            raise StudioSyncError(f"{stage} gate time does not match the current role milestone")
        _receipt_matches_role(path, role_id, canonical_path, role_dir, require_path=True)
        gates[stage] = {"receipt_path": _local_relative(path, data), "sha256": digest,
                        "status": status, "at": chain[stage]["at"]}
    return gates


def _stage_gate_refs(root: Path | str, run_id: str, role_id: str, gate_manifest_path: Path | str) -> Path:
    data = StudioData(Path(root))
    run_id, role_id = _safe_run_id(run_id), _safe_role_id(role_id)
    history = _read_history(data, run_id)
    chain = _current_chain(history, role_id)
    run_dir, sync_dir = _sync_dir(data, run_id, role_id, create=True)
    artifacts = _json_file(sync_dir / "artifact-refs.json", "Run-local artifact refs")
    canonical_path = artifacts.get("canonical_role_path")
    if artifacts.get("run_id") != run_id or artifacts.get("role_id") != role_id or not isinstance(canonical_path, str):
        raise StudioSyncError("Artifact refs must be staged before the gate refs")
    role_dir = _role_directory(data, canonical_path)
    source = _path_from_workspace(str(gate_manifest_path), data, label="Gate manifest")
    if not (source.is_relative_to(run_dir) or source.is_relative_to(role_dir)):
        raise StudioSyncError("Gate manifest must be saved in this run or its exact accepted role folder")
    raw = source.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise StudioSyncError("Gate manifest is not valid JSON") from exc
    if (not isinstance(value, dict) or value.get("kind") != GATE_REGISTRY_KIND or
            value.get("sealed") is not True or value.get("run_id") != run_id or value.get("role_id") != role_id):
        raise StudioSyncError("Gate manifest is not sealed for this exact run and public role ID")
    gates = value.get("gates")
    if not isinstance(gates, dict):
        roles = value.get("roles")
        gates = roles.get(role_id) if isinstance(roles, dict) else None
    if not isinstance(gates, dict):
        raise StudioSyncError("Gate manifest lacks the exact role's gate refs")
    staged = {"kind": GATE_REGISTRY_KIND, "run_id": run_id, "role_id": role_id,
              "source_manifest": {"path": _local_relative(source, data), "sha256": _digest_bytes(raw)},
              "gates": gates}
    target = sync_dir / "gate-refs.json"
    _write_immutable_json(target, staged, label="Gate refs")
    _load_gates(data, sync_dir, run_id, role_id, canonical_path, role_dir, chain)
    return target


def _history_projection(history: dict[str, Any], role_id: str, chain: dict[str, dict[str, Any]]) -> dict[str, Any]:
    milestones = []
    for stage in CHAIN_STAGES:
        row = chain[stage]
        milestones.append({key: row[key] for key in (
            "kind", "stage", "worker_id", "role_id", "role", "at", "status",
            "evidence_sha256", "predecessor_sha256", "current") if key in row})
    stages = []
    for row in history.get("stage_metrics", []):
        if not isinstance(row, dict) or row.get("id") not in {"hunt_audit", "build", "build_audit", "sol_review"}:
            continue
        keep = ("id", "status", "raw_status", "started_at", "finished_at", "duration_seconds",
                "jobs_shortlisted", "cvs_built", "model", "model_source")
        stages.append({key: row[key] for key in keep if key in row})
    stages.sort(key=lambda row: row.get("id", ""))
    return {"run_id": history.get("id"), "role_id": role_id,
            "stage_metrics": stages, "role_milestones": milestones}


def project_run(root: Path | str, run_id: str, role_id: str, *, _data=None, _catalog=None) -> dict[str, Any]:
    """Recompute actual Studio and run-history readback for one accepted role.

    The run-local artifact and gate registries are inputs, never approvals. The
    returned manifest excludes the sync milestone so it does not depend on a
    circular "already synced" marker.
    """
    run_id, role_id = _safe_run_id(run_id), _safe_role_id(role_id)
    data = _data if _data is not None else StudioData(Path(root))
    history = _read_history(data, run_id)
    chain = _current_chain(history, role_id)

    run_dir, sync_dir = _sync_dir(data, run_id, role_id, create=False)
    staged_artifacts = _json_file(sync_dir / "artifact-refs.json", "Run-local artifact refs")
    canonical_path = staged_artifacts.get("canonical_role_path")
    studio_role_id = staged_artifacts.get("studio_role_id")
    if not isinstance(canonical_path, str) or not isinstance(studio_role_id, str) or not _ROLE_ID.fullmatch(studio_role_id):
        raise StudioSyncError("Run-local artifact refs do not map the public ID to a real Studio catalog role")
    role_rows = _catalog if _catalog is not None else data.roles().get("items", [])
    matches = [row for row in role_rows if isinstance(row, dict) and row.get("folder") == canonical_path]
    if len(matches) != 1 or matches[0].get("id") != studio_role_id:
        raise StudioSyncError("Run-local artifact refs do not match the actual Studio role inventory")
    inventory = matches[0]
    role = inventory if _catalog is not None else data.role(studio_role_id)
    role_dir = _role_directory(data, canonical_path)
    if role.get("id") != studio_role_id or role.get("folder") != canonical_path or staged_artifacts.get("role_id") != role_id:
        raise StudioSyncError("Studio role detail does not match the exact inventory role")
    company, title, url = role.get("company"), role.get("title"), role.get("source_url")
    if not all(isinstance(value, str) and value.strip() for value in (company, title, url)):
        raise StudioSyncError("Studio role is missing its observed company, title or source URL")

    artifact_refs, artifact_receipt = _load_staged_artifacts(data, sync_dir, run_id, role_id,
        studio_role_id, canonical_path, role_dir, chain["artifact"])
    gates = _load_gates(data, sync_dir, run_id, role_id, canonical_path, role_dir, chain)

    observed_pdfs = {row.get("name"): row for row in role.get("artifacts", []) if isinstance(row, dict)}
    refs_by_name = {row["name"]: row for row in staged_artifacts["artifact_refs"]}
    for logical, expected_label in (("pdf", "CV"), ("letter_pdf", "Cover letter")):
        name = Path(refs_by_name[logical]["path"]).name
        observed = observed_pdfs.get(name)
        if not observed or observed.get("label") != expected_label:
            raise StudioSyncError(f"Studio does not expose the manifest's actual {expected_label} PDF")

    identity = {"canonical_role_path": canonical_path, "company": company, "title": title,
                "url": url, "artifact_refs": artifact_refs}
    role_hash = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    projection = _history_projection(history, role_id, chain)
    return {
        "schema_version": 1,
        "kind": READBACK_KIND,
        "status": "DONE",
        "run_id": run_id,
        "history_projection": projection,
        "history_projection_sha256": _digest_bytes(_canonical_json(projection)),
        "roles": [{"role_id": role_id, "studio_role_id": studio_role_id, **identity,
                   "artifact_receipt": artifact_receipt,
                   "gate_refs": gates,
                   "studio_role_readback": role_hash}],
    }


def _write_output(root: Path | str, run_id: str, role_id: str, output: Path | str, value: dict[str, Any]) -> Path:
    data = StudioData(Path(root))
    _run_dir, sync_dir = _sync_dir(data, run_id, role_id, create=True)
    raw = Path(output).expanduser()
    target = raw if raw.is_absolute() else data.workspace / raw
    # Permit a short filename as a convenience, but all outputs remain scoped
    # to this run's dedicated directory.
    if not raw.is_absolute() and len(raw.parts) == 1:
        target = sync_dir / raw
    try:
        target = target.resolve(strict=False)
        target.relative_to(sync_dir.resolve())
    except (ValueError, OSError) as exc:
        raise StudioSyncError("Readback output must stay inside this run's cv-studio-sync directory") from exc
    if target.is_symlink() or target.suffix.lower() != ".json":
        raise StudioSyncError("Readback output must be a non-symlink JSON file")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if target.parent.resolve() != sync_dir.resolve():
        raise StudioSyncError("Readback output may not create nested directories")
    _write_immutable_json(target, value, label="Studio readback")
    return target


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if "--requested-intent" in arguments:
        requested = argparse.ArgumentParser(description="Verify a separate explicit requested continuation; preserve batch history and application choices.")
        requested.add_argument("--root", required=True)
        requested.add_argument("--requested-intent", required=True)
        requested.add_argument("--output", required=True)
        args = requested.parse_args(arguments)
        try:
            from requested_studio_sync import execute_requested
            value = execute_requested(args.root, args.requested_intent, args.output)
        except (StudioSyncError, StudioDataError, OSError, ValueError, KeyError, TypeError) as exc:
            requested.exit(2, f"studio_sync requested: {exc}\n")
        print(json.dumps({"status": value["status"], "role_id": value["role_id"], "output": args.output}, sort_keys=True))
        return 0
    parser = argparse.ArgumentParser(description="Save a local hash-bound CV Studio role and run-history readback.")
    parser.add_argument("--root", required=True, help="Canonical Resources directory or visible CV workspace")
    parser.add_argument("--run-id", required=True, help="Exact saved pipeline run ID")
    parser.add_argument("--role", required=True, help="Exact public candidate role ID")
    parser.add_argument("--artifact-manifest", required=True,
                        help="Path to the sealed build artifact projection for this exact run and role")
    parser.add_argument("--gate-refs", required=True,
                        help="Path to the sealed current Hunt Audit, Build Audit and Sol review receipts")
    parser.add_argument("--output", required=True, help="JSON filename or path inside run-local cv-studio-sync/")
    args = parser.parse_args(argv)
    try:
        _stage_artifact_manifest(args.root, args.run_id, args.role, args.artifact_manifest)
        _stage_gate_refs(args.root, args.run_id, args.role, args.gate_refs)
        projected = project_run(args.root, args.run_id, args.role)
        output = _write_output(args.root, args.run_id, args.role, args.output, projected)
    except (StudioSyncError, StudioDataError, OSError, ValueError) as exc:
        parser.exit(2, f"studio_sync: {exc}\n")
    print(json.dumps({"status": "DONE", "run_id": args.run_id, "role_id": args.role,
                      "output": str(output), "history_projection_sha256": projected["history_projection_sha256"]},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
