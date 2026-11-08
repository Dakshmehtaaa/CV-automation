"""Application flow (23 Oct 2026): jobs Daksh set aside to apply later or marked as won't-apply.

Independent of the application status: a job can be applied, deferred or skipped.
Stored locally in .local/apply_flow.json; nothing leaves the Mac.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

REASONS = ("internship", "not_a_fit", "already_applied", "job_closed", "location", "other")
LATER_DAYS = (1, 2, 3, 4)
_ID = re.compile(r"[A-Za-z0-9:_.-]{1,120}")


class ApplyFlowError(ValueError):
    pass


class ApplyFlow:
    def __init__(self, local_dir: Path):
        self.path = Path(local_dir) / "apply_flow.json"
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise ApplyFlowError("The apply flow file could not be read.")
        items = raw.get("items") if isinstance(raw, dict) else None
        return items if isinstance(items, dict) else {}

    def _save(self, items: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=2).encode()
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".apply-flow-", suffix=".json")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def list(self) -> dict:
        with self._lock:
            return {"items": self._load(), "reasons": list(REASONS), "later_days": list(LATER_DAYS)}

    def update(self, role_id: str, payload: dict) -> dict:
        if not isinstance(role_id, str) or not _ID.fullmatch(role_id):
            raise ApplyFlowError("This job id is not valid.")
        if not isinstance(payload, dict):
            raise ApplyFlowError("The update must be an object.")
        kind = payload.get("kind")
        with self._lock:
            items = self._load()
            now = datetime.now(timezone.utc)
            stamp = now.isoformat(timespec="seconds")
            if kind == "wont":
                if set(payload) != {"kind", "reason"}:
                    raise ApplyFlowError("A won't-apply update needs only kind and reason.")
                if payload["reason"] not in REASONS:
                    raise ApplyFlowError("Choose a supported reason.")
                items[role_id] = {"kind": "wont", "reason": payload["reason"], "updated_at": stamp}
            elif kind == "later":
                if set(payload) != {"kind", "days"}:
                    raise ApplyFlowError("A later update needs only kind and days.")
                days = payload["days"]
                if isinstance(days, bool) or not isinstance(days, int) or days not in LATER_DAYS:
                    raise ApplyFlowError("The days must be a whole number between 1 and 4.")
                until = (now + timedelta(days=days)).isoformat(timespec="seconds")
                items[role_id] = {"kind": "later", "until": until, "days": days, "updated_at": stamp}
            elif kind == "clear":
                if set(payload) != {"kind"}:
                    raise ApplyFlowError("A clear update needs only kind.")
                items.pop(role_id, None)
            else:
                raise ApplyFlowError("Choose a supported flow kind.")
            self._save(items)
            return {"items": items, "reasons": list(REASONS), "later_days": list(LATER_DAYS)}