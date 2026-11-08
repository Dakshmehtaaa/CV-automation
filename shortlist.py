"""Shortlist (23 Oct 2026): jobs Daksh puts aside to find a contact and reach out.

Independent of the application status: a job can be applied AND shortlisted.
Stored locally in .local/shortlist.json; nothing leaves the Mac.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

STAGES = ("to_contact", "contact_found", "reached_out", "replied")
_ID = re.compile(r"[A-Za-z0-9:_.-]{1,120}")


class ShortlistError(ValueError):
    pass


class Shortlist:
    def __init__(self, local_dir: Path):
        self.path = Path(local_dir) / "shortlist.json"
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise ShortlistError("The shortlist file could not be read.")
        items = raw.get("items") if isinstance(raw, dict) else None
        return items if isinstance(items, dict) else {}

    def _save(self, items: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=2).encode()
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".shortlist-", suffix=".json")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def list(self) -> dict:
        with self._lock:
            return {"items": self._load(), "stages": list(STAGES)}

    def update(self, role_id: str, payload: dict) -> dict:
        if not isinstance(role_id, str) or not _ID.fullmatch(role_id):
            raise ShortlistError("This job id is not valid.")
        if not isinstance(payload, dict) or set(payload) - {"saved", "note", "stage"}:
            raise ShortlistError("Only saved, note and stage can be changed.")
        with self._lock:
            items = self._load()
            if payload.get("saved") is False:
                items.pop(role_id, None)
            else:
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                entry = items.get(role_id) or {"added_at": now, "stage": STAGES[0], "note": ""}
                if "note" in payload:
                    note = payload["note"]
                    if not isinstance(note, str) or len(note) > 4000:
                        raise ShortlistError("The note must be text of at most 4000 characters.")
                    entry["note"] = note
                if "stage" in payload:
                    if payload["stage"] not in STAGES:
                        raise ShortlistError("Choose a supported contact step.")
                    entry["stage"] = payload["stage"]
                entry["updated_at"] = now
                items[role_id] = entry
            self._save(items)
            return {"items": items, "stages": list(STAGES)}
