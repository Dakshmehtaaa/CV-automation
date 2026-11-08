"""Persistent, local queue for jobs the user explicitly wants to build.

Saving a queue item is deterministic and never starts a model.  Queue writes are
atomic and protected across Studio processes.  A pipeline caller can freeze a
point-in-time set of pending inputs for a newly admitted run; later enqueues are
not part of that run's snapshot.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_ID = re.compile(r"[a-f0-9]{16}")
_STATUSES = {"queued", "building", "built_pending_approval", "completed", "held", "failed"}
_MAX_JD_BYTES = 120_000
_MAX_URL_CHARS = 8192
_MAX_TITLE = 300
_MAX_COMPANY = 200
_CREDENTIAL_QUERY_KEYS = {
    "token", "access_token", "refresh_token", "id_token", "api_key", "apikey",
    "authorization", "password", "secret", "client_secret", "sessionid", "session_id",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _clean_text(value, field: str, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or "\x00" in value:
        raise ManualJobsError(f"{field} must be text.")
    value = value.strip()
    if len(value) > limit or any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ManualJobsError(f"{field} is too long or contains unsupported characters.")
    return value


def safe_job_url(value) -> str | None:
    """Accept a literal HTTP(S) advert URL without credentials or secret query keys."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > _MAX_URL_CHARS:
        raise ManualJobsError("The job URL is too long or invalid.")
    if (any(char.isspace() for char in value) or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(char in value for char in '\\<>"`')):
        raise ManualJobsError("Enter one complete job URL without extra text.")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise ManualJobsError("The job URL must be a public HTTP or HTTPS address without sign-in details.")
        parsed.port  # validates malformed and out-of-range ports
        if any(key.casefold().replace("-", "_") in _CREDENTIAL_QUERY_KEYS
               for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
               + parse_qsl(parsed.fragment, keep_blank_values=True)):
            raise ManualJobsError("The job URL contains a credential-like field and was not saved.")
    except ValueError as exc:
        if isinstance(exc, ManualJobsError):
            raise
        raise ManualJobsError("The job URL is not valid.") from exc
    return value


def canonical_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    port = parsed.port
    if port and not (parsed.scheme.casefold() == "http" and port == 80) and not (
            parsed.scheme.casefold() == "https" and port == 443):
        host = f"{host}:{port}"
    query = [(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.casefold().startswith("utm_")]
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parsed.scheme.casefold(), host, path, urlencode(sorted(query)), ""))


class ManualJobsError(ValueError):
    def __init__(self, message: str, *, status: int = 400, code: str = "invalid_manual_job"):
        super().__init__(message)
        self.status = status
        self.code = code


class ManualJobQueue:
    """Atomic queue storage inside the calling Studio local-data directory."""

    def __init__(self, local_dir: str | Path):
        requested_dir = Path(local_dir).expanduser()
        if requested_dir.is_symlink():
            raise ManualJobsError("The local manual-job storage path is unsafe.", status=403,
                                  code="unsafe_path")
        self.local_dir = requested_dir.resolve()
        self.local_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.local_dir / "manual-jobs.json"
        self.lock_path = self.local_dir / ".manual-jobs.lock"
        self._thread_lock = threading.RLock()

    @staticmethod
    def _input_hash(item: dict) -> str:
        payload = {key: item.get(key) or "" for key in ("url", "jd", "title", "company")}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode("utf-8")).hexdigest()

    @staticmethod
    def _jd_hash(text: str) -> str:
        normalized = " ".join(text.split()).casefold()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""

    @staticmethod
    def _public_item(item: dict) -> dict:
        item = {**item, "find_contacts": item.get("find_contacts") is True,
                "contact_research": item.get("contact_research") or {"status": "off", "job_id": None, "contacts": []}}
        return {key: copy.deepcopy(item.get(key)) for key in (
            "id", "status", "url", "title", "company", "source_kind", "created_at",
            "summary", "build_job_id", "role_path", "finished_at", "retry_of", "find_contacts", "contact_research",
            "review_comment", "review_rounds", "approval")}

    def _safe_paths(self) -> None:
        if self.local_dir.is_symlink() or self.path.is_symlink() or self.lock_path.is_symlink():
            raise ManualJobsError("The local manual-job storage path is unsafe.", status=403,
                                  code="unsafe_path")

    def _read_unlocked(self) -> dict:
        if not self.path.exists():
            return {"schema_version": 1, "revision": 0, "items": []}
        try:
            if not self.path.is_file() or self.path.stat().st_size > 32_000_000:
                raise ValueError("storage file is not a bounded regular file")
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or value.get("schema_version") != 1
                    or type(value.get("revision")) is not int or not isinstance(value.get("items"), list)):
                raise ValueError("unsupported queue schema")
            seen = set()
            for item in value["items"]:
                if (not isinstance(item, dict) or not _ID.fullmatch(str(item.get("id", "")))
                        or item.get("status") not in _STATUSES or item["id"] in seen
                        or not isinstance(item.get("input_sha256"), str)
                        or not re.fullmatch(r"[a-f0-9]{64}", item["input_sha256"])
                        or self._input_hash(item) != item["input_sha256"]
                        or item.get("url") != safe_job_url(item.get("url"))
                        or not isinstance(item.get("jd"), str)
                        or self._jd_hash(item["jd"]) != item.get("jd_sha256")):
                    raise ValueError("invalid queue item")
                seen.add(item["id"])
            return value
        except (OSError, ValueError, UnicodeError, TypeError) as exc:
            raise ManualJobsError("The saved manual-job queue is unreadable; its contents were preserved.",
                                  status=409, code="invalid_manual_job_store") from exc

    def _write_unlocked(self, value: dict) -> None:
        temporary = self.local_dir / (".manual-jobs-" + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _locked(self):
        class _Context:
            def __init__(inner, queue):
                inner.queue = queue
                inner.handle = None

            def __enter__(inner):
                inner.queue._thread_lock.acquire()
                try:
                    inner.queue._safe_paths()
                    inner.handle = inner.queue.lock_path.open("a+b")
                    fcntl.flock(inner.handle.fileno(), fcntl.LOCK_EX)
                    return inner
                except Exception:
                    inner.queue._thread_lock.release()
                    raise

            def __exit__(inner, *_args):
                try:
                    if inner.handle:
                        fcntl.flock(inner.handle.fileno(), fcntl.LOCK_UN)
                        inner.handle.close()
                finally:
                    inner.queue._thread_lock.release()

        return _Context(self)

    @staticmethod
    def _counts(items: list[dict]) -> dict:
        result = {status: 0 for status in sorted(_STATUSES)}
        for item in items:
            result[item["status"]] += 1
        return result

    def list(self) -> dict:
        with self._locked():
            items = [self._public_item(row) for row in self._read_unlocked()["items"]]
        items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return {"items": items, "total": len(items), "counts": self._counts(items)}

    def get(self, ident: str) -> dict:
        if not isinstance(ident, str) or not _ID.fullmatch(ident):
            raise ManualJobsError("This saved job was not found.", status=404, code="not_found")
        with self._locked():
            doc = self._read_unlocked()
            item = next((row for row in doc["items"] if row["id"] == ident), None)
            if item is None:
                raise ManualJobsError("This saved job was not found.", status=404, code="not_found")
            return copy.deepcopy(item)

    def enqueue(self, payload: dict, *, preserve_jd_bytes: bool = False) -> dict:
        if not isinstance(payload, dict):
            raise ManualJobsError("Enter a job URL, job description, or both.")
        if "find_contacts" in payload and type(payload["find_contacts"]) is not bool:
            raise ManualJobsError("Choose yes or no for contact research.")
        url = safe_job_url(payload.get("url"))
        jd = _clean_text(payload.get("jd"), "Job description", _MAX_JD_BYTES)
        if preserve_jd_bytes and payload.get("jd") is not None:
            raw_jd = payload["jd"]
            if (len(raw_jd) > _MAX_JD_BYTES or
                    any(ord(char) < 32 and char not in "\n\r\t" for char in raw_jd)):
                raise ManualJobsError("The exact job description is too long or contains unsupported characters.")
            jd = raw_jd
        if len(jd.encode("utf-8")) > _MAX_JD_BYTES:
            raise ManualJobsError("The job description is too long.")
        title = _clean_text(payload.get("title"), "Job title", _MAX_TITLE)
        company = _clean_text(payload.get("company"), "Company", _MAX_COMPANY)
        if not url and not jd:
            raise ManualJobsError("Paste a job URL or job description first.")
        source_kind = "url_and_jd" if url and jd else "url" if url else "jd"
        now = _now()
        candidate = {"id": uuid.uuid4().hex[:16], "status": "queued", "url": url, "jd": jd,
            "title": title or "Saved job", "company": company or "Company not named",
            "source_kind": source_kind, "created_at": now,
            "summary": "Saved locally. Build now is available after the current pipeline finishes.",
            "build_job_id": None, "role_path": None, "finished_at": None,
            "find_contacts": payload.get("find_contacts", False),
            "contact_research": {"status": "queued" if payload.get("find_contacts", False) else "off", "job_id": None, "contacts": []},
            "claim": None, "url_key": canonical_url(url), "jd_sha256": self._jd_hash(jd)}
        candidate["input_sha256"] = self._input_hash(candidate)
        with self._locked():
            doc = self._read_unlocked()
            def matches(existing):
                same_url = bool(candidate["url_key"] and existing.get("url_key") == candidate["url_key"])
                same_jd = bool(candidate["jd_sha256"] and existing.get("jd_sha256") == candidate["jd_sha256"])
                return same_url or same_jd

            # Prefer a live queued record so a terminal historical match cannot
            # shadow a retry already created for the same job.
            for existing in doc["items"]:
                if not matches(existing) or existing.get("status") != "queued" or existing.get("claim"):
                    continue
                changed = False
                if ("find_contacts" in payload and existing.get("find_contacts", False) != payload["find_contacts"]
                        and not (existing.get("contact_research") or {}).get("job_id")):
                    existing["find_contacts"] = payload["find_contacts"]
                    existing["contact_research"] = {"status": "queued" if payload["find_contacts"] else "off", "job_id": None, "contacts": []}
                    changed = True
                for key in ("url", "jd"):
                    if not existing.get(key) and candidate.get(key):
                        existing[key] = candidate[key]
                        changed = True
                for key in ("title", "company"):
                    default = "Saved job" if key == "title" else "Company not named"
                    if existing.get(key) == default and candidate.get(key) != default:
                        existing[key] = candidate[key]
                        changed = True
                if changed:
                    existing["source_kind"] = ("url_and_jd" if existing.get("url") and existing.get("jd")
                        else "url" if existing.get("url") else "jd")
                    existing["url_key"] = canonical_url(existing.get("url"))
                    existing["jd_sha256"] = self._jd_hash(existing.get("jd") or "")
                    existing["input_sha256"] = self._input_hash(existing)
                    existing["summary"] = "Saved locally. Build now is available after the current pipeline finishes."
                    doc["revision"] += 1
                    doc["updated_at"] = now
                    self._write_unlocked(doc)
                return {"item": self._public_item(existing), "duplicate": True,
                        "updated": changed, "enriched": changed}

            for existing in doc["items"]:
                if matches(existing) and existing.get("status") == "building":
                    return {"item": self._public_item(existing), "duplicate": True,
                            "updated": False, "enriched": False}

            for existing in doc["items"]:
                if not matches(existing):
                    continue
                if existing.get("status") in {"held", "failed"}:
                    retry = copy.deepcopy(candidate)
                    if not retry.get("url") and existing.get("url"):
                        retry["url"] = existing["url"]
                    if not retry.get("jd") and existing.get("jd"):
                        retry["jd"] = existing["jd"]
                    for key, default in (("title", "Saved job"), ("company", "Company not named")):
                        if retry.get(key) == default and existing.get(key) != default:
                            retry[key] = existing[key]
                    changed = (
                        bool(retry.get("url") and canonical_url(retry.get("url")) != existing.get("url_key"))
                        or bool(retry.get("jd") and self._jd_hash(retry.get("jd")) != existing.get("jd_sha256"))
                        or any(retry.get(key) not in (None, "", default)
                               and retry.get(key) != existing.get(key)
                               for key, default in (("title", "Saved job"), ("company", "Company not named")))
                    )
                    if changed:
                        retry["source_kind"] = ("url_and_jd" if retry.get("url") and retry.get("jd")
                            else "url" if retry.get("url") else "jd")
                        retry["url_key"] = canonical_url(retry.get("url"))
                        retry["jd_sha256"] = self._jd_hash(retry.get("jd") or "")
                        retry["retry_of"] = existing["id"]
                        retry["summary"] = "Saved locally as a new attempt. Build now is available after the current pipeline finishes."
                        retry["input_sha256"] = self._input_hash(retry)
                        doc["items"].append(retry)
                        doc["revision"] += 1
                        doc["updated_at"] = now
                        self._write_unlocked(doc)
                        return {"item": self._public_item(retry), "duplicate": False,
                                "updated": True, "retry_of": existing["id"]}
                return {"item": self._public_item(existing), "duplicate": True,
                        "updated": False, "enriched": False}
            doc["items"].append(candidate)
            doc["revision"] += 1
            doc["updated_at"] = now
            self._write_unlocked(doc)
        return {"item": self._public_item(candidate), "duplicate": False, "updated": False}

    def claim_contact_research(self, ident: str, job_id: str, *, run_id: str | None = None, expected_input_sha256: str | None = None) -> dict | None:
        """Reserve an explicit opt-in once, independently of CV Build ownership."""
        with self._locked():
            doc = self._read_unlocked()
            item = next((row for row in doc["items"] if row["id"] == ident), None)
            research = (item or {}).get("contact_research") or {}
            if (not item or item.get("status") not in {"queued", "building"}
                    or (expected_input_sha256 is not None and item.get("input_sha256") != expected_input_sha256)
                    or item.get("find_contacts") is not True or research.get("job_id") or research.get("status") != "queued"):
                return None
            item["contact_research"] = {"status": "queued", "job_id": job_id, "contacts": [], "requested_at": _now(), "run_id": run_id}
            doc["revision"] += 1
            self._write_unlocked(doc)
            return copy.deepcopy(item)

    def set_contact_choice(self, ident: str, find_contacts: bool) -> dict:
        if type(find_contacts) is not bool:
            raise ManualJobsError("Choose yes or no for contact research.")
        with self._locked():
            doc = self._read_unlocked()
            item = next((row for row in doc["items"] if row["id"] == ident), None)
            if not item or item.get("status") not in {"queued", "building"}:
                raise ManualJobsError("Only queued or building jobs can change this choice.", status=409)
            research = item.get("contact_research") or {"status":"off", "job_id":None, "contacts":[]}
            item["find_contacts"] = find_contacts
            if not research.get("job_id"):
                research["status"] = "queued" if find_contacts else "off"
            elif not find_contacts and research.get("status") in {"queued", "running"}:
                research.update(status="held", summary="Contact search cancelled by your choice.")
            item["contact_research"] = research
            doc["revision"] += 1
            self._write_unlocked(doc)
            return copy.deepcopy(item)

    def update_contact_research(self, ident: str, job_id: str, **fields) -> dict:
        with self._locked():
            doc = self._read_unlocked()
            item = next((row for row in doc["items"] if row["id"] == ident), None)
            if not item or (item.get("contact_research") or {}).get("job_id") != job_id:
                raise ManualJobsError("Contact research owner does not match.", status=409)
            if fields.get("status") not in {"queued", "running", "completed", "partial", "held"}:
                raise ManualJobsError("Invalid contact research status.")
            if item.get("find_contacts") is not True and fields.get("status") in {"running", "completed", "partial"}:
                raise ManualJobsError("Contact search was cancelled. No new results saved.", status=409)
            item["contact_research"].update(copy.deepcopy(fields), updated_at=_now())
            doc["revision"] += 1
            self._write_unlocked(doc)
            return copy.deepcopy(item)

    def snapshot_pending(self) -> dict:
        """Freeze the pending items visible to one newly admitted pipeline."""
        with self._locked():
            doc = self._read_unlocked()
            items = []
            for row in doc["items"]:
                if row.get("status") == "queued":
                    item = copy.deepcopy(row)
                    items.append({"id": item["id"], "input_sha256": item["input_sha256"], "item": item})
            return {"schema_version": 1, "kind": "manual_build_queue_snapshot", "queue_revision": doc["revision"],
                    "captured_at": _now(), "items": items}

    def snapshot_pending_refs(self) -> list[dict]:
        """Freeze only identifiers/hashes for a new Studio run's later evidence file."""
        with self._locked():
            doc = self._read_unlocked()
            return [{"id": row["id"], "input_sha256": row["input_sha256"]}
                    for row in doc["items"] if row.get("status") == "queued"]

    def resolve_snapshot_refs(self, refs: list[dict], *, run_id: str) -> dict:
        """Resolve frozen references without admitting items added after run start."""
        if (not isinstance(refs, list) or len(refs) > 500
                or not isinstance(run_id, str) or not run_id):
            raise ManualJobsError("The new-run manual queue snapshot is invalid.", status=409,
                                  code="invalid_manual_job_snapshot")
        with self._locked():
            doc = self._read_unlocked()
            by_id = {row["id"]: row for row in doc["items"]}
            resolved = []
            for ref in refs:
                if (not isinstance(ref, dict) or not _ID.fullmatch(str(ref.get("id", "")))
                        or not isinstance(ref.get("input_sha256"), str)):
                    raise ManualJobsError("The new-run manual queue snapshot has an invalid item reference.",
                                          status=409, code="invalid_manual_job_snapshot")
                row = by_id.get(ref["id"])
                if (not row or row.get("input_sha256") != ref["input_sha256"]
                        or row.get("status") != "queued"):
                    resolved.append({"id": ref["id"], "input_sha256": ref["input_sha256"],
                                     "status": "held", "reason": "Queue item changed after run admission."})
                else:
                    resolved.append({"id": row["id"], "input_sha256": row["input_sha256"],
                                     "status": "queued", "item": copy.deepcopy(row)})
            return {"schema_version": 1, "kind": "manual_build_queue_snapshot", "run_id": run_id,
                    "queue_revision": doc["revision"], "captured_at": _now(), "items": resolved}

    def claim(self, ident: str, owner_id: str, *, owner_kind: str = "studio") -> dict:
        if not isinstance(ident, str) or not _ID.fullmatch(ident):
            raise ManualJobsError("This saved job was not found.", status=404, code="not_found")
        if not isinstance(owner_id, str) or not owner_id:
            raise ManualJobsError("A saved Build owner is required.")
        with self._locked():
            doc = self._read_unlocked()
            item = next((row for row in doc["items"] if row["id"] == ident), None)
            if item is None:
                raise ManualJobsError("This saved job was not found.", status=404, code="not_found")
            if item.get("status") != "queued":
                raise ManualJobsError("This job is already in progress or has a saved outcome.",
                                      status=409, code="manual_job_not_queued")
            item["status"] = "building"
            item["build_job_id"] = owner_id
            item["claim"] = {"owner_kind": owner_kind, "owner_id": owner_id, "claimed_at": _now()}
            item["summary"] = "Build is running. Final review is still pending."
            doc["revision"] += 1
            doc["updated_at"] = _now()
            self._write_unlocked(doc)
            return copy.deepcopy(item)

    def finish(self, ident: str, owner_id: str, status: str, summary: str, *,
               role_path: str | None = None, audit: dict | None = None) -> dict:
        if status not in {"built_pending_approval", "completed", "held", "failed"}:
            raise ManualJobsError("A Build outcome must be completed, held, or failed.")
        if not isinstance(ident, str) or not _ID.fullmatch(ident):
            raise ManualJobsError("This saved job was not found.", status=404, code="not_found")
        with self._locked():
            doc = self._read_unlocked()
            item = next((row for row in doc["items"] if row["id"] == ident), None)
            if item is None:
                raise ManualJobsError("This saved job was not found.", status=404, code="not_found")
            claim = item.get("claim") or {}
            if item.get("status") != "building" or claim.get("owner_id") != owner_id:
                raise ManualJobsError("This Build no longer owns the saved job.", status=409,
                                      code="manual_job_owner_mismatch")
            item["status"] = status
            item["summary"] = _clean_text(summary, "Build summary", 800)
            item["role_path"] = _clean_text(role_path, "Role path", 1000) or None
            item["audit"] = copy.deepcopy(audit) if isinstance(audit, dict) else None
            item["finished_at"] = _now()
            doc["revision"] += 1
            doc["updated_at"] = item["finished_at"]
            self._write_unlocked(doc)
            return copy.deepcopy(item)

    def review(self, ident: str, decision: str, comment: str = "") -> dict:
        """22 Oct (Daksh): manual approval of a built job. approve -> completed; reject -> queued again with the
        comment, so Mercury 3 rebuilds the same role folder with that comment as a must-fix."""
        if decision not in ("approve", "reject"):
            raise ManualJobsError("Choose approve or reject.")
        if not isinstance(ident, str) or not _ID.fullmatch(ident):
            raise ManualJobsError("This saved job was not found.", status=404, code="not_found")
        text = _clean_text(comment, "Review comment", 2000) if comment else ""
        if decision == "reject" and not text:
            raise ManualJobsError("Write what Mercury 3 should fix before sending it back.")
        with self._locked():
            doc = self._read_unlocked()
            item = next((row for row in doc["items"] if row["id"] == ident), None)
            if item is None:
                raise ManualJobsError("This saved job was not found.", status=404, code="not_found")
            if item.get("status") != "built_pending_approval":
                raise ManualJobsError("Only a built job waiting for approval can be reviewed.", status=409,
                                      code="manual_job_not_reviewable")
            now = _now()
            if decision == "approve":
                item["status"] = "completed"
                item["approval"] = {"by": "daksh", "manual": True, "at": now, "comment": text or None}
                item["summary"] = "Approved by Daksh."
                item["finished_at"] = now
            else:
                item["status"] = "queued"
                item.pop("claim", None)
                item.pop("build_job_id", None)
                item["review_comment"] = text
                item["review_rounds"] = int(item.get("review_rounds") or 0) + 1
                item["summary"] = "Sent back to Mercury 3 with your comment."
            doc["revision"] += 1
            doc["updated_at"] = now
            self._write_unlocked(doc)
            return copy.deepcopy(item)

    def release(self, ident: str, owner_id: str, summary: str) -> dict:
        """Put a claimed job back in the queue when nothing was built (e.g. the Gemini daily quota is used up)."""
        with self._locked():
            doc = self._read_unlocked()
            item = next((row for row in doc["items"] if row["id"] == ident), None)
            if item is None or item.get("status") != "building" or (item.get("claim") or {}).get("owner_id") != owner_id:
                raise ManualJobsError("This Build no longer owns the saved job.", status=409,
                                      code="manual_job_owner_mismatch")
            item["status"] = "queued"
            item.pop("claim", None)
            item.pop("build_job_id", None)
            item["summary"] = _clean_text(summary, "Build summary", 800)
            doc["revision"] += 1
            doc["updated_at"] = _now()
            self._write_unlocked(doc)
            return copy.deepcopy(item)


__all__ = ["ManualJobQueue", "ManualJobsError", "safe_job_url", "canonical_url"]
