"""Read-only readiness projection for exact requested-build packages.

File presence and queue labels are not approval. Ordinary folders and explicit
application choices keep their existing meaning; this annotates the package.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


PROVENANCE_NAMES = ("requested-job-provenance.json", "requested_manual_job_provenance.json")


def _bytes(data, path, base, limit=32_000_000):
    path = Path(path)
    data._safe(path, base)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Missing or unsafe package evidence")
    return path.read_bytes()


def _object(raw):
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Package evidence must be an object")
    return value


def _json(data, path, base, limit=32_000_000):
    return _object(_bytes(data, path, base, limit))


def _folder(data, value):
    path = Path(value)
    if not path.is_absolute():
        path = data.workspace / path
    return data._safe(path, data.role_root).resolve()


def _ref(data, ref, base=None):
    if not isinstance(ref, dict) or not isinstance(ref.get("sha256"), str):
        raise ValueError("Missing approval or sync reference")
    path = Path(ref["path"])
    if not path.is_absolute():
        path = data.root / path
    raw = _bytes(data, path, base or data.auto / "evidence")
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise ValueError("Package evidence changed")
    return _object(raw)


def _readback(data, audit, run_id):
    """Follow the producer's queue terminal refs, retaining direct legacy refs."""
    if not isinstance(audit, dict):
        raise ValueError("Invalid queue audit")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", run_id):
        raise ValueError("Invalid requested run identity")
    run_dir = data.evidence / run_id
    approval = _ref(data, audit.get("final_approval"), run_dir)
    saved = _ref(data, audit.get("studio_sync"), run_dir)
    if saved.get("kind") == "weekday_worker_terminal":
        if (saved.get("stage") != "sync" or saved.get("status") != "DONE"
                or approval.get("kind") != "weekday_worker_terminal"
                or approval.get("stage") != "sol_review" or approval.get("status") != "DONE"):
            raise ValueError("Final review and sync terminals must both be complete")
        receipts = saved.get("receipts") or {}
        if not isinstance(receipts, dict):
            raise ValueError("Invalid sync terminal receipts")
        sync = _ref(data, receipts.get("studio_sync"), run_dir)
        saved = _ref(data, receipts.get("readback"), run_dir)
        if sync.get("run_id") != run_id:
            raise ValueError("Studio sync belongs to another run")
    if saved.get("kind") != "cv_studio_sync_readback_v1" or saved.get("run_id") != run_id:
        raise ValueError("Unsupported or mismatched sync proof")
    return saved


def annotate_requested_reviews(data, items):
    """Add review state without changing files, artifacts, or application records."""
    try:
        queue = _json(data, data.local / "manual-jobs.json", data.local)
        if queue.get("schema_version") != 1 or not isinstance(queue.get("items"), list):
            raise ValueError("Unsupported queue")
        queued = queue["items"]
    except (OSError, ValueError, KeyError, TypeError):
        queued = []
    for role in items:
        if role.get("approved_document_alias"):
            # apply_aliases has just revalidated the complete current chain.
            continue
        try:
            folder = _folder(data, role["folder"])
            paths = [folder / name for name in PROVENANCE_NAMES if (folder / name).exists() or (folder / name).is_symlink()]
            if not paths:
                continue
            reason = "This requested CV still needs final approval and verified Studio sync."
            review = {"required": True, "state": "unverified", "reason": reason}
            role["build_review"] = review
            if len(paths) != 1:
                raise ValueError("Ambiguous requested provenance")
            provenance = _json(data, paths[0], folder, 1_000_000)
            jd_path = data._role_jd_target(folder)
            if (provenance.get("kind") != "requested_manual_job_role_provenance_v1"
                    or _folder(data, provenance["role_path"]) != folder
                    or jd_path is None
                    or hashlib.sha256(jd_path.read_bytes()).hexdigest() != provenance["role_jd_sha256"]):
                raise ValueError("Requested package identity changed")
            matches = [row for row in queued if isinstance(row, dict) and row.get("id") == provenance.get("queue_id")]
            if len(matches) != 1:
                raise ValueError("Requested queue item missing or ambiguous")
            item = matches[0]
            if (_folder(data, item["role_path"]) != folder
                    or item.get("input_sha256") != provenance.get("input_sha256")):
                raise ValueError("Queue refers to another document version")
            status = item.get("status")
            review.update(state=status, queue_id=item["id"], run_id=provenance.get("run_id"))
            if status != "completed":
                review["reason"] = ("This requested CV is on hold. Final approval and Studio sync are still required."
                                    if status in {"held", "failed"} else reason)
                continue
            audit = item.get("audit") or {}
            run_id = provenance["run_id"]
            saved = _readback(data, audit, run_id)
            all_rows = saved.get("roles")
            if not isinstance(all_rows, list) or any(not isinstance(row, dict) for row in all_rows):
                raise ValueError("Invalid Studio role readback")
            rows = [row for row in all_rows if row.get("studio_role_id") == role["id"]]
            if len(rows) != 1:
                raise ValueError("Sync does not bind this package")
            from studio_sync import project_run
            current = project_run(data.root, run_id, rows[0]["role_id"], _data=data, _catalog=items)
            projections = saved.get("role_readbacks", {})
            if not isinstance(projections, dict):
                raise ValueError("Invalid aggregate Studio readback")
            expected = saved if len(all_rows) == 1 else projections.get(rows[0]["role_id"])
            if current != expected or current.get("roles") != rows:
                raise ValueError("Sync no longer matches current evidence")
            role.pop("build_review", None)
        except (OSError, ValueError, KeyError, TypeError):
            # Missing, changed and ambiguous evidence never upgrades readiness.
            continue
    return items
