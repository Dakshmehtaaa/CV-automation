"""Local Studio launch preferences; the weekday stage contracts stay authoritative.

This module performs no network access or generation. A native model catalog is
selection metadata, not proof of generation access. The runtime checks fresh
metadata and the exact returned route again before submitting its owned turn.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import stat
import tempfile


DEFAULTS = {"model": "gpt-6.1-sol", "effort": "medium"}
FINAL_APPROVAL = {"model": "gpt-6.1-sol", "effort": "high"}
SCHEMA_VERSION = 1
SCOPE = "studio_started_runs"


class RunSettingsError(ValueError):
    pass


def selection(payload):
    """Require an explicit model and effort, with no provider/config overrides."""
    if not isinstance(payload, dict) or set(payload) != {"model", "effort"}:
        raise RunSettingsError("Choose a model and reasoning effort. Provider and workflow overrides are not supported.")
    if not all(isinstance(payload[key], str) and payload[key] and len(payload[key]) <= 120
               for key in ("model", "effort")):
        raise RunSettingsError("Model and reasoning effort must be nonempty text.")
    if "astra" in payload["model"].casefold():
        raise RunSettingsError("Astra is excluded from Studio run settings by your model preference.")
    return {key: payload[key] for key in ("model", "effort")}


def model_options(metadata):
    """Preserve the native catalog order and each model's actual effort choices."""
    options, seen = [], set()
    models = metadata.get("models", []) if isinstance(metadata, dict) else []
    for row in models if isinstance(models, list) else []:
        if not isinstance(row, dict):
            continue
        ident = row.get("id")
        if (not isinstance(ident, str) or not ident or ident in seen or "astra" in ident.casefold()
                or row.get("listed") is not True
                or row.get("provider", row.get("modelProvider")) not in (None, "openai")):
            continue
        efforts = row.get("efforts")
        if not isinstance(efforts, list):
            continue
        efforts = list(dict.fromkeys(value for value in efforts if isinstance(value, str) and value))
        if not efforts:
            continue
        seen.add(ident)
        options.append({"id": ident, "name": row.get("name") or ident, "efforts": efforts,
                        "listed": True, "generation_verified": False})
    return options


def validate(settings, metadata):
    """Check catalog support without treating listing as entitlement or usage."""
    chosen = selection(settings)
    if not isinstance(metadata, dict) or metadata.get("account_type") != "chatgpt":
        raise RunSettingsError("Check the signed-in ChatGPT connection. API-key or other paid-provider fallback is not enabled.")
    if not metadata.get("available") or not metadata.get("authenticated"):
        raise RunSettingsError("Refresh the signed-in native model list before choosing run settings.")
    option = next((row for row in model_options(metadata) if row["id"] == chosen["model"]), None)
    if not option:
        raise RunSettingsError("The selected model is not in the available native model list. No model will be substituted.")
    if chosen["effort"] not in option["efforts"]:
        raise RunSettingsError("The selected reasoning effort is not listed for this model. No effort will be substituted.")
    return chosen


def validation(settings, metadata):
    try:
        validate(settings, metadata)
        return {"valid": True, "reason": "Listed in cached native metadata; checked again before dispatch."}
    except RunSettingsError as exc:
        return {"valid": False, "reason": str(exc)}


class RunSettingsStore:
    """Persist only local model/effort preferences, privately and atomically."""

    def __init__(self, path):
        self.path = Path(path)

    def _safe_parent(self):
        if self.path.parent.is_symlink() or not self.path.parent.is_dir() or self.path.is_symlink():
            raise RunSettingsError("The local run settings path must be a regular file in a regular directory.")

    def read(self):
        self._safe_parent()
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return {**copy.deepcopy(DEFAULTS), "saved_at": None, "metadata_checked_at": None,
                    "persisted": False}
        except OSError as exc:
            raise RunSettingsError("The saved run settings could not be read. Reset defaults to repair them.") from exc
        try:
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 64_000:
                    raise RunSettingsError("The saved run settings file is invalid. Reset defaults to repair it.")
                stored = json.load(stream)
            if not isinstance(stored, dict) or stored.get("schema_version") != SCHEMA_VERSION:
                raise RunSettingsError("The saved run settings format is invalid. Reset defaults to repair it.")
            chosen = selection({key: stored.get(key) for key in ("model", "effort")})
            return {**chosen, "saved_at": stored.get("saved_at"),
                    "metadata_checked_at": stored.get("metadata_checked_at"), "persisted": True}
        except (OSError, ValueError, UnicodeError) as exc:
            if isinstance(exc, RunSettingsError):
                raise
            raise RunSettingsError("The saved run settings are invalid. Reset defaults to repair them; no fallback was used.") from exc

    def save(self, settings, *, saved_at, metadata_checked_at=None):
        chosen = selection(settings)
        self._safe_parent()
        stored = {"schema_version": SCHEMA_VERSION, **chosen, "saved_at": saved_at,
                  "metadata_checked_at": metadata_checked_at}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".run-settings-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                os.fchmod(stream.fileno(), 0o600)
                json.dump(stored, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._safe_parent()
            os.replace(temporary, self.path)
            temporary = None
        except OSError as exc:
            raise RunSettingsError("The local run settings could not be saved.") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return {**chosen, "saved_at": saved_at, "metadata_checked_at": metadata_checked_at, "persisted": True}
