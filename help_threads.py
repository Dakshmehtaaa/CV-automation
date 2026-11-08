"""Mercury 3 chats (26 Oct 2026): one saved chat per job, with unread and done state. Stored locally in .local/help-threads.json; nothing leaves the Mac."""

import datetime
import json
import os
import re
import tempfile
import threading
from pathlib import Path


class HelpThreadsError(ValueError):
    pass


_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class HelpThreads:
    def __init__(self, local_dir: Path):
        self.path = Path(local_dir) / "help-threads.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {"version": 1, "threads": {}}
        except json.JSONDecodeError as e:
            raise HelpThreadsError("invalid help-threads.json: %s" % e) from e
        if not isinstance(data, dict):
            raise HelpThreadsError("help-threads.json root must be an object")
        if data.get("version") != 1:
            raise HelpThreadsError("unsupported help-threads.json version")
        threads = data.get("threads")
        if not isinstance(threads, dict):
            raise HelpThreadsError("help-threads.json threads must be an object")
        return data

    def _save(self, data: dict) -> None:
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(parent), prefix=".help-threads-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def list(self) -> dict:
        with _LOCK:
            data = self._load()
            threads = list(data["threads"].values())
            threads.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
            return {"threads": threads}

    def save(self, thread_id: str, payload: dict) -> dict:
        if not isinstance(thread_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", thread_id):
            raise HelpThreadsError("invalid thread id")
        if not isinstance(payload, dict):
            raise HelpThreadsError("payload must be an object")

        allowed = {"role_id", "company", "title", "status", "unread", "messages", "created_at", "updated_at"}
        extra = set(payload.keys()) - allowed
        if extra:
            raise HelpThreadsError("unknown keys: %s" % sorted(extra))

        for key in ("role_id", "company", "title", "status", "unread", "messages"):
            if key not in payload:
                raise HelpThreadsError("missing key: %s" % key)

        role_id = payload["role_id"]
        if not isinstance(role_id, str) or len(role_id) > 200:
            raise HelpThreadsError("role_id must be a string <= 200 chars")

        company = payload["company"]
        if not isinstance(company, str) or len(company) > 300:
            raise HelpThreadsError("company must be a string <= 300 chars")

        title = payload["title"]
        if not isinstance(title, str) or len(title) > 300:
            raise HelpThreadsError("title must be a string <= 300 chars")

        status = payload["status"]
        if status not in ("open", "done"):
            raise HelpThreadsError("status must be 'open' or 'done'")

        unread = payload["unread"]
        if not isinstance(unread, bool):
            raise HelpThreadsError("unread must be a boolean")

        messages = payload["messages"]
        if not isinstance(messages, list):
            raise HelpThreadsError("messages must be a list")

        cleaned_messages = []
        for msg in messages:
            if not isinstance(msg, dict):
                raise HelpThreadsError("message must be an object")
            msg_allowed = {"role", "content", "at", "failed", "edited"}
            extra_msg = set(msg.keys()) - msg_allowed
            if extra_msg:
                raise HelpThreadsError("unknown message keys: %s" % sorted(extra_msg))
            for key in ("role", "content", "at"):
                if key not in msg:
                    raise HelpThreadsError("missing message key: %s" % key)

            role = msg["role"]
            if role not in ("user", "assistant"):
                raise HelpThreadsError("message role must be 'user' or 'assistant'")

            content = msg["content"]
            if not isinstance(content, str) or len(content) > 20000:
                raise HelpThreadsError("message content must be a string <= 20000 chars")

            at = msg["at"]
            if not isinstance(at, str):
                raise HelpThreadsError("message at must be a string")

            cleaned = {"role": role, "content": content, "at": at}

            if "failed" in msg:
                failed = msg["failed"]
                if not isinstance(failed, bool):
                    raise HelpThreadsError("message failed must be a boolean")
                cleaned["failed"] = failed

            if "edited" in msg:
                edited = msg["edited"]
                if edited is not None:
                    if not isinstance(edited, dict):
                        raise HelpThreadsError("message edited must be an object or null")
                    try:
                        dumped = json.dumps(edited, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                    except (TypeError, ValueError) as e:
                        raise HelpThreadsError("message edited must be JSON-serialisable: %s" % e)
                    if len(dumped) >= 4000:
                        raise HelpThreadsError("message edited must be < 4000 chars when dumped")
                cleaned["edited"] = edited

            cleaned_messages.append(cleaned)

        if len(cleaned_messages) > 400:
            cleaned_messages = cleaned_messages[-400:]

        if "created_at" in payload and not isinstance(payload["created_at"], str):
            raise HelpThreadsError("created_at must be a string")
        if "updated_at" in payload and not isinstance(payload["updated_at"], str):
            raise HelpThreadsError("updated_at must be a string")

        with _LOCK:
            data = self._load()
            threads = data["threads"]
            existing = threads.get(thread_id)

            if existing:
                created_at = existing.get("created_at") or _now_iso()
            else:
                created_at = payload.get("created_at") or _now_iso()

            now = _now_iso()
            thread = {
                "id": thread_id,
                "role_id": role_id,
                "company": company,
                "title": title,
                "status": status,
                "unread": unread,
                "messages": cleaned_messages,
                "created_at": created_at,
                "updated_at": now,
            }
            threads[thread_id] = thread

            if len(threads) > 300:
                done = [(t.get("updated_at", ""), tid) for tid, t in threads.items() if t.get("status") == "done"]
                done.sort()
                for _, tid in done:
                    if len(threads) <= 300:
                        break
                    del threads[tid]

                if len(threads) > 300:
                    all_items = [(t.get("updated_at", ""), tid) for tid, t in threads.items()]
                    all_items.sort()
                    for _, tid in all_items:
                        if len(threads) <= 300:
                            break
                        del threads[tid]

            self._save(data)
            return {"thread": thread}