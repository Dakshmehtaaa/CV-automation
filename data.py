"""Read-only projections of the CV workspace, with narrowly scoped local editing.

The Studio never treats the presence of a PDF as an approval or a saved Sheet
snapshot as live tracker state.  No pipeline module is imported or executed.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from live import LivePipelineObserver, _number as _count_value
from ratings import role_ratings
import fit_score
from dashboard_adjustments import load_dashboard_adjustments
from hark_metrics import HarkMetrics, guess_advert_language as _guess_advert_language, merge_events as _merge_collection_events, public_capture_events, public_capture_signature


def _sheet_run_date(value: str | None) -> str | None:
    """Sheet-built roles have no 'Found' line; their pipeline line names the run (…-YYYY-MM-DD-…)."""
    match = re.search(r"(20\d\d-\d\d-\d\d)", value or "")
    return match.group(1) if match else None




class StudioDataError(ValueError):
    def __init__(self, message: str, code: str = "invalid_request", status: int = 400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


_STAGES = [
    ("inbox", "Inbox review", "GPT-6 Luna · MAX"),
    ("hunt", "Find opportunities", "Gemini 3.8 Flash · High"),
    ("hunt_audit", "Hunt audit", "Claude Opus 4.6 · Thinking"),
    ("build", "Build CVs", "GPT-6 Luna · MAX"),
    ("build_audit", "Build audit", "GPT-6.1 Sol · Medium"),
    ("sol_review", "Final review", "GPT-6.1 Sol · High"),
    ("sync", "Tracker sync", "GPT-6 Luna · MAX"),
]
_POLICY4_STAGE_MODELS = {
    "inbox": "Connected Codex",
    "hunt": "Gemini 3.8 Flash · High",
    "hunt_audit": "GPT-6.1 Sol · High",
    "build": "GPT-6 Luna · MAX",
    "build_audit": "GPT-6.1 Sol · Medium",
    "sol_review": "GPT-6.1 Sol · High",
    "sync": "GPT-6 Luna · MAX",
}
_POLICY4_HUNT_TARGET = 31
_SITES = [
    ("hellowork", "HelloWork"), ("france_travail", "France Travail"),
    ("linkedin", "LinkedIn"), ("apec", "APEC"),
    ("wttj", "Welcome to the Jungle"),
]
_ANALYTICS_HOSTS = {
    "hellowork": ("hellowork.fr", "hellowork.com"),
    "france_travail": ("francetravail.fr", "pole-emploi.fr"),
    "linkedin": ("linkedin.com",),
    "apec": ("apec.fr",),
    "wttj": ("welcometothejungle.com", "welcometothejungle.fr"),
}
_ANALYTICS_TRACKING_KEYS = {"trk", "trackingid", "refid", "ref_id", "source", "src"}
_ANALYTICS_MAX_BATCHES = 150
_ANALYTICS_MAX_INDEX_BYTES = 512_000
_ANALYTICS_MAX_INDEX_ROWS = 2_000
_ANALYTICS_CACHE_SECONDS = 300
_DOCUMENTS = {
    "profile": ("PROFILE.md", "Your profile", True,
                "Your factual experience and writing preferences. The generated CV bank mirror is protected."),
    "search": ("SEARCH.md", "Search preferences", True,
               "Employer priorities, sources and keywords. Search gates and workflow rules are protected."),
    "bank": ("Master/cv/bank.json", "Approved CV wording", False,
             "The approved English and French wording bank used by the CV builder."),
    "layout": ("LAYOUT.md", "CV design guide", False,
               "The current CV format and layout contract."),
    "routing": ("Master/auto/local/ROUTING.md", "Pipeline routing", False,
                "Model assignments, clocks and approval requirements for the existing pipeline."),
}
_SETTINGS = {"theme": "system", "density": "comfortable", "refresh_seconds": 15}
_RUN_TERMINALS = ("terminal.json", "batch-terminal.json", "terminal-report.v3.json",
                  "terminal-report.v2.json", "terminal-report.json", "manifest.final.json")
_RUN_MANIFESTS = ("batch-manifest.json", "manifest.json", "plan.json", "manifest.initial.json", "launch-receipt.json")
_RUN_END_KEYS = ("finished_at", "terminal_at", "finalized_at", "administratively_finalized_at", "stage_finished_at")
# Finalization receipts may record stage outcomes as top-level keys rather
# than under "stages". These supersede the earlier stage-outcomes snapshot.
_TERMINAL_STAGE_KEYS = {"inbox": ("inbox",), "hunt": ("hunt",), "hunt_audit": ("hunt_audit",),
                        "build": ("build",), "build_audit": ("build_audit",),
                        "sol_review": ("sol_review", "sol_approval", "final_review"),
                        "sync": ("sync", "sheet_sync", "deliverable_sheet_sync", "sync_stage", "deliverable_sync")}
_EDIT_LOCK = threading.RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat()


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Legacy state.py timestamps are Paris wall times.
        return parsed.replace(tzinfo=ZoneInfo("Europe/Paris")) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return None


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _id(value: str) -> str:
    return _sha(value.encode("utf-8"))[:24]


def _status(value: Any) -> str:
    value = str(value or "").lower().replace(" ", "_").replace("-", "_")
    if value.startswith(("not_run", "not_started", "pending", "queued", "unstarted")) or not value:
        return "not_run"
    if "held" in value or value == "blocked":
        return "held"
    if value in ("failed", "pre_dispatch_failed", "error", "timed_out", "cancelled", "canceled", "expired"):
        return "failed"
    if "partial" in value or "shortfall" in value or "with_source" in value:
        return "partial"
    if value in ("running", "active", "in_progress", "started", "start"):
        return "running"
    if value in ("ready", "approved"):
        return "ready"
    if value.startswith(("done", "complete", "passed", "pass", "success", "verified", "reviewed", "sent")):
        return "done"
    return "partial"


def _pre_dispatch_failure(value: Any, manifest: dict, metrics: dict) -> dict:
    """Project an observed startup failure without rewriting its earlier receipts."""
    if (not isinstance(value, dict) or manifest.get("kind") != "fresh_pipeline_launch"
            or manifest.get("status") not in {"PENDING_SUBMISSION", "PRE_DISPATCH_FAILED"}
            or manifest.get("first_submission_at") or manifest.get("supervisor_pid")
            or value.get("status") not in {"HELD_PRE_DISPATCH", "PRE_DISPATCH_FAILED"}
            or type(value.get("workers_dispatched")) is not int or value["workers_dispatched"] != 0
            or value.get("downstream_status") != "NOT_RUN"):
        return {}
    started, ended = _time(manifest.get("started_at")), _time(value.get("recorded_at"))
    records = metrics.get("records", []) if isinstance(metrics, dict) else []
    if (not started or not ended or ended < started or not isinstance(records, list)
            or any(not isinstance(row, dict) or row.get("started_at") or row.get("status") != "not_run"
                   for row in records)):
        return {}
    return {**value, "finished_at": value["recorded_at"], "failure_class": "launch_failed",
            "exit_code": value.get("launcher_exit_code")}


def _plain(value: str) -> str:
    return re.sub(r"[*`]", "", value).strip()


def _web_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    markdown = re.fullmatch(r"\[[^\]\r\n]*\]\((https?://[^\s<>\)]+)\)", value)
    url = markdown.group(1) if markdown else value
    if not re.fullmatch(r"https?://[^\s<>]+", url) or re.search(r"[\x00-\x1f\\]", url):
        return None
    try:
        parsed = urlparse(url)
        credentials = {"token", "access_token", "refresh_token", "api_key", "apikey", "authorization", "password", "secret", "client_secret"}
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                any(key.lower().replace("-", "_") in credentials for key, _ in parse_qsl(parsed.query))):
            return None
        # Accessing port also rejects malformed or out-of-range ports.
        parsed.port
        return url
    except ValueError:
        return None


def _analytics_url(value: Any) -> str:
    """Canonicalize a saved job URL for cross-run deduplication only."""
    safe = _web_url(value)
    if not safe:
        return ""
    try:
        parsed = urlsplit(safe)
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        port = parsed.port
        if port and not (parsed.scheme.casefold() == "http" and port == 80) and not (
                parsed.scheme.casefold() == "https" and port == 443):
            host = f"{host}:{port}"
        query = [(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                 if not key.casefold().startswith("utm_")
                 and key.casefold() not in _ANALYTICS_TRACKING_KEYS]
        path = re.sub(r"/{2,}", "/", parsed.path or "/")
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit((parsed.scheme.casefold(), host, path, urlencode(sorted(query)), ""))
    except (ValueError, UnicodeError):
        return ""


def _analytics_platform(value: Any) -> str | None:
    """Classify only exact source labels or matching DNS host boundaries."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    host = ""
    try:
        parsed = urlsplit(raw if "://" in raw else "https://" + raw)
        if "://" in raw and parsed.scheme.casefold() not in ("http", "https"):
            return None
        host = (parsed.hostname or "").casefold().removeprefix("www.")
    except ValueError:
        pass
    for site_id, hosts in _ANALYTICS_HOSTS.items():
        if any(host == domain or host.endswith("." + domain) for domain in hosts):
            return site_id
    label = re.sub(r"\s+", " ", raw).casefold().strip()
    aliases = {
        "hellowork": ("hellowork", "hello work"),
        "france_travail": ("france travail", "francetravail", "pôle emploi", "pole emploi"),
        "linkedin": ("linkedin",),
        "apec": ("apec",),
        "wttj": ("welcome to the jungle", "wttj"),
    }
    return next((site_id for site_id, values in aliases.items() if label in values), None)


def _analytics_identity(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def _card_location_label(value: Any) -> str | None:
    """A short saved locality for cards; preserve the complete source separately."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = unicodedata.normalize("NFKC", html.unescape(value))
    raw = re.sub(r"<[^>]+>", " ", raw).strip()
    # Metadata after these separators is never part of the address label.
    locality = re.split(r"[·;\n]|\s[—–]\s|\s/\s|\s\+\s", raw, maxsplit=1)[0]
    pieces = [p.strip() for p in locality.split(",") if p.strip()]
    if not pieces:
        return None
    if re.fullmatch(r"\d{5}", pieces[0]) and len(pieces) > 1:
        locality = pieces[1]
    else:
        locality = pieces[0]
    if re.search(r"(?:[ÎI]le[ -]de[ -]France|Greater Paris|Paris Region)", locality, re.I):
        return "Île-de-France"
    locality = re.split(r"\s[-–]\s*(?:CDI|CDD|intérim|contrat|langue|français|anglais)\b", locality, maxsplit=1, flags=re.I)[0]
    if re.search(r"\bParis\b", locality, re.I):
        arr = re.search(r"\bParis\s*\(?\s*(\d{1,2})(?:er|e|ème|eme)?\b", locality, re.I)
        postal = re.search(r"\b750(0[1-9]|1\d|20)\b", locality)
        number = int(arr.group(1)) if arr else int(postal.group(1)) if postal else None
        if number and 1 <= number <= 20:
            return f"Paris {number}{'er' if number == 1 else 'e'}"
        return "Paris"
    if re.fullmatch(r"(?:94\s*[-–]\s*)?Val-de-Marne", locality, re.I):
        return "Île-de-France"
    locality = re.sub(r"^(?:\d{2,5}|2A|2B)\s*[-–]\s*", "", locality, flags=re.I)
    locality = re.sub(r"\([^)]*\)", "", locality)
    locality = re.sub(r"\b\d{5}\b|\s*[-–]\s*(?:\d{2,3}|2A|2B)\b.*$", "", locality, flags=re.I)
    locality = re.sub(r"\s+", " ", locality).strip(" -–—()")
    if (not locality or locality.casefold() in {"france", "unknown", "none"} or
            len(locality) > 60 or len(locality.split()) > 6 or
            re.search(r"\b(?:contrat|langue|salaire|poste|télétravail|remote|non précisé)\b", locality, re.I)):
        return None
    return locality


def _office_location_label(value: Any) -> str | None:
    """Normalize saved JD office text without geocoding or inventing postal codes."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip(" ,;\t\r\n")
    if not raw:
        return None
    postal_match = re.search(r"(?<!\d)(\d{5})(?!\d)", raw)
    postal = postal_match.group(1) if postal_match else None
    department = None
    if postal:
        if postal[:3] in {"971", "972", "973", "974", "975", "976", "984", "986", "987", "988"}:
            department = postal[:3]
        else:
            candidate = postal[:2]
            if candidate == "20":
                number = int(postal)
                department = "2A" if 20000 <= number <= 20190 else "2B" if (20200 <= number <= 20299 or 20600 <= number <= 20699) else None
            elif 1 <= int(candidate) <= 95:
                department = candidate

    paris_match = re.search(r"\bParis\b", raw, re.IGNORECASE)
    arrondissement = None
    explicit_arr = re.search(r"\b(?:Paris\s*)?(\d{1,2})\s*(?:er|e|ème|eme)\b", raw, re.IGNORECASE)
    if explicit_arr and 1 <= int(explicit_arr.group(1)) <= 20:
        arrondissement = int(explicit_arr.group(1))
    elif postal and 75001 <= int(postal) <= 75020 and paris_match:
        arrondissement = int(postal[-2:])
    elif postal and 75001 <= int(postal) <= 75020 and not re.search(r"[A-Za-zÀ-ÿ]", raw.replace(postal, "")):
        arrondissement = int(postal[-2:])

    explicit_department = re.search(r"\(\s*(2A|2B|\d{2,3})\s*\)", raw, re.IGNORECASE)
    department_prefix = re.match(r"^(2A|2B|\d{2,3})\s*[-–—]\s*(?=[A-Za-zÀ-ÿ])", raw, re.IGNORECASE)
    if explicit_department is None:
        explicit_department = department_prefix
    if explicit_department:
        explicit = explicit_department.group(1).upper()
        if explicit in {"2A", "2B"} or (explicit.isdigit() and (
                1 <= int(explicit) <= 95 or int(explicit) in {971, 972, 973, 974, 975, 976, 984, 986, 987, 988})):
            department = explicit

    # Prefer the first saved locality component. Later comma-separated pieces
    # commonly name a region/country rather than a second office.
    city = raw.split(",", 1)[0].strip()
    if department_prefix and department:
        city = city[department_prefix.end():]
    city = re.sub(r"[([]\s*\d{5}\s*[)\]]", " ", city)
    city = re.sub(r"\b\d{5}\b", " ", city)
    city = re.sub(r"\(\s*(?:2A|2B|\d{2,3})\s*\)", " ", city, flags=re.IGNORECASE)
    city = re.sub(r"\b\d{1,2}\s*(?:er|e|ème|eme)\b", " ", city, flags=re.IGNORECASE)
    city = re.sub(r"\s+", " ", city).strip(" -–—()[]")
    city = re.sub(r"^(?:à|au|aux|en|dans la commune de)\s+", "", city, flags=re.IGNORECASE).strip()
    if not city and postal and 75001 <= int(postal) <= 75020:
        city = "Paris"
    if not city:
        return None
    if city.casefold() == "paris" and arrondissement:
        return f"Paris ({arrondissement}e)"
    if city.casefold() == "paris":
        city = "Paris"
    if department:
        return f"{city} ({department})"
    return city


class StudioData:
    """A safe, JSON-serializable view of an existing workspace.

    ``root`` may name the visible CV folder or its canonical Resources folder.
    Initialization and all read methods leave the workspace untouched.
    """

    def __init__(self, root: Path, *, schedule_path: Path | None = None, sessions_dir: Path | None = None):
        root = Path(root).expanduser().resolve()
        self.root = (root / "Resources").resolve() if (root / "Resources").is_dir() else root
        self.workspace = self.root.parent if self.root.name == "Resources" else self.root
        self.auto = self.root / "Master" / "auto"
        self.evidence = self.auto / "evidence" / "runs"
        role_alias = self.root / "Tailored CVs"
        actual_roles = role_alias.resolve()
        # The tidy workspace intentionally keeps this one reverse alias. Register
        # its exact expected target, never an arbitrary symlink destination.
        visible_roles = self.workspace / "Tailored CVs"
        if (actual_roles == role_alias or
                (actual_roles == visible_roles and not visible_roles.is_symlink())):
            self.role_root = actual_roles
        else:
            self.role_root = self.root / "__unavailable_roles__"
        self._standalone_pdfs = tuple(self.workspace / f"Candidate - CV ({lang}).pdf" for lang in ("EN", "FR"))
        self.local = self.root / "Studio" / ".local"
        # Fixture workspaces do not inherit the real user's scheduler. Tests can
        # inject a small TOML fixture explicitly.
        expected_project = Path(__file__).resolve().parents[2]
        self.schedule_path = (Path(schedule_path) if schedule_path is not None else
                              Path.home() / ".codex/automations/cv-weekday-pipeline/automation.toml"
                              if self.workspace == expected_project else None)
        # External session observation is opt-in for fixtures. A temporary CV
        # workspace never scans the real user's unrelated chats.
        session_root = (Path(sessions_dir) if sessions_dir is not None else
                        Path.home() / ".codex/sessions" if self.workspace == expected_project else None)
        self._live = LivePipelineObserver(self.workspace, self.root, session_root)
        self._dashboard_cache_lock = threading.Lock()
        self._dashboard_cache_signature = None
        self._dashboard_cache_value = None
        self._dashboard_cache_at = 0.0
        self._read_proof_cache = {}
        self._read_proof_lock = threading.Lock()
        self._tidy_manifest_lock = threading.Lock()
        self._tidy_manifest_signature = None
        self._tidy_manifest_rows = None

    def _safe(self, path: Path, base: Path | None = None) -> Path:
        resolved = path.resolve()
        within_root = resolved.is_relative_to(self.root) or resolved.is_relative_to(self.role_root)
        standalone = resolved in self._standalone_pdfs and not resolved.is_symlink()
        if not (within_root or standalone) or (base is not None and not resolved.is_relative_to(base.resolve())):
            raise StudioDataError("That file is outside the Studio workspace.", "unsafe_path", 403)
        return resolved

    def _text(self, path: Path, limit: int = 2_000_000) -> str | None:
        try:
            safe = self._safe(path)
            if safe.stat().st_size > limit:
                return None
            return safe.read_bytes().decode("utf-8")
        except (OSError, UnicodeError, StudioDataError):
            return None

    def _json(self, path: Path) -> Any:
        raw = self._text(path, 16_000_000)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            # Writers can be between receipts. A missing fact remains missing.
            return None

    def _first_json(self, directory: Path, names: tuple[str, ...]) -> tuple[dict, Path | None]:
        for name in names:
            candidate = directory / name
            data = self._json(candidate)
            if isinstance(data, dict):
                return data, candidate
        return {}, None

    def _native_finished_terminal(self, directory: Path, manifest: dict) -> dict:
        """Observe an exact hash-bound native release; never grant source approval."""
        def checked(value, expected):
            if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
                raise ValueError("missing native terminal reference")
            if value.get("path") != str(expected) or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("sha256", ""))):
                raise ValueError("foreign native terminal reference")
            current = Path(expected.anchor)
            for part in expected.parts[1:]:
                current = current / part
                if current.is_symlink():
                    raise ValueError("symlink native terminal reference")
            if not expected.is_file() or expected.stat().st_size > 16_000_000:
                raise ValueError("missing or oversized native terminal reference")
            raw = self._safe(expected, directory).read_bytes()
            if hashlib.sha256(raw).hexdigest() != value["sha256"]:
                raise ValueError("native terminal reference changed")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("native terminal object required")
            return result
        try:
            state = manifest.get("status")
            if (manifest.get("kind") != "fresh_pipeline_launch"
                    or manifest.get("run_id") != directory.name
                    or not manifest.get("owner_token")
                    or state not in {"COMPLETED_SAFE", "OWNERSHIP_RELEASED_UNAPPROVED"}
                    or manifest.get("owner_release_status") != state):
                return {}
            release = checked(manifest.get("owner_release_receipt"),
                directory / "native-app/safe-owner-release-receipt.json")
            if (release.get("kind") != "codex_app_safe_owner_release_v1"
                    or release.get("schema_version") != 1
                    or release.get("run_id") != directory.name
                    or release.get("owner_token") != manifest["owner_token"]
                    or release.get("status") != state
                    or release.get("verified_at") != manifest.get("finished_at")):
                return {}
            finish = checked(release.get("finish_request"), directory / "finish-request.json")
            if (finish.get("run_id") != directory.name
                    or finish.get("owner_token") != manifest["owner_token"]
                    or finish.get("stage_ledger") != release.get("stage_ledger")
                    or finish.get("status") not in {"DONE", "PARTIAL", "HELD"}
                    or type(finish.get("approved")) is not bool
                    or type(finish.get("abort")) is not bool
                    or (state == "OWNERSHIP_RELEASED_UNAPPROVED" and
                        (finish["approved"] or not finish["abort"]))):
                return {}
            ledger = checked(finish["stage_ledger"], directory / "stage-ledger.json")
            if (ledger.get("kind") != "verified_pipeline_stage_ledger"
                    or any(ledger.get(key) != manifest.get(key) for key in
                        ("run_id", "owner_token", "started_at"))):
                return {}
            decision = release.get("flow_decision")
            if (not isinstance(decision, dict) or decision.get("status") != finish["status"]
                    or (state == "OWNERSHIP_RELEASED_UNAPPROVED" and decision.get("action") != "abort")
                    or (state == "COMPLETED_SAFE" and
                        (finish["abort"] or decision.get("action") not in {"complete", "complete_no_roles"}
                         or finish["approved"] != (decision.get("action") == "complete")))):
                return {}
            started, ended, at = _time(manifest.get("started_at")), _time(release.get("verified_at")), _time(finish.get("at"))
            if (not started or not ended or not at or not started <= at <= ended <= _now()
                    or finish.get("started_at") != manifest.get("started_at")):
                return {}
            return {**finish, "finished_at": release["verified_at"],
                "closure_basis": "hash_bound_native_owner_release"}
        except (OSError, ValueError, TypeError, KeyError, StudioDataError):
            return {}

    def _modified(self, path: Path) -> str | None:
        try:
            return _iso(datetime.fromtimestamp(self._safe(path).stat().st_mtime, timezone.utc))
        except (OSError, StudioDataError):
            return None

    def _relative(self, path: Path) -> str:
        if path.is_relative_to(self.role_root):
            return str(Path("Tailored CVs") / path.relative_to(self.role_root))
        if path in self._standalone_pdfs:
            return path.name
        return str(path.relative_to(self.root))

    def _run_dirs(self) -> list[Path]:
        try:
            return sorted([p for p in self._safe(self.evidence).iterdir()
                           if p.is_dir() and not p.is_symlink() and re.match(r"^\d{4}-\d{2}-\d{2}", p.name)],
                          key=lambda p: p.name, reverse=True)
        except (OSError, StudioDataError):
            return []

    def _run_summary(self, directory: Path) -> dict | None:
        terminal, terminal_path = self._first_json(directory, _RUN_TERMINALS)
        manifest, manifest_path = self._first_json(directory, _RUN_MANIFESTS)
        if not terminal:
            terminal = self._native_finished_terminal(directory, manifest)
            if terminal:
                terminal_path = directory / "finish-request.json"
        outcomes = self._json(directory / "stage-outcomes.json") or {}
        if not isinstance(outcomes, dict):
            outcomes = {}
        history_metrics = self._json(directory / "stage-metrics.json") or {}
        if not isinstance(history_metrics, dict) or history_metrics.get("kind") != "scheduled_run_metrics_v1":
            history_metrics = {}
        if not terminal:
            terminal = _pre_dispatch_failure(self._json(directory / "launch-failure.json"), manifest, history_metrics)
            if terminal:
                terminal_path = directory / "launch-failure.json"
        if not (terminal or manifest or outcomes or history_metrics):
            return None
        started = next((v for v in [terminal.get("batch_started_at"), manifest.get("batch_started_at"),
                    manifest.get("invocation_started_at"), manifest.get("started_at"),
                    manifest.get("actual_start"), terminal.get("actual_start"), history_metrics.get("started_at")] if isinstance(v, str)), None)
        ended = next((v for v in (terminal.get(k) for k in _RUN_END_KEYS) if isinstance(v, str)), None) or history_metrics.get("finished_at")
        raw_status = terminal.get("status") or outcomes.get("capacity_admission") or manifest.get("status") or history_metrics.get("status") or "UNVERIFIED"
        status = _status(raw_status)
        if status == "running":
            # Persisted RUNNING is a historical observation, not a process check.
            status = "partial"
        # An earlier stage-outcomes reason describes a superseded snapshot once a
        # terminal receipt exists; prefer the final report's opening line.
        summary = str(terminal.get("reason") or terminal.get("summary") or
                      (self._report_lead(directory) if terminal else "") or
                      ("" if terminal else outcomes.get("reason")) or
                      ("Saved CV Pipeline stage receipt; completion still requires stage evidence." if history_metrics else ""))
        if not summary:
            pending = terminal.get("pending")
            if isinstance(pending, list) and pending:
                summary = "; ".join(str(s) for s in pending[:3])
            else:
                summary = "Saved execution evidence. Open for stage details."
        source = terminal_path or (directory / "stage-outcomes.json" if outcomes else manifest_path) or (directory / "stage-metrics.json" if history_metrics else None)
        counts: dict = {}
        for group in (terminal.get("current_screening"), terminal.get("job_outcome"), outcomes.get("counts"), terminal.get("counts")):
            if isinstance(group, dict):
                counts.update({k: v for k, v in group.items() if isinstance(v, (int, float)) and not isinstance(v, bool)})
        policy_version = manifest.get("policy_version") if isinstance(manifest.get("policy_version"), int) else history_metrics.get("policy_version")
        stages = self._stages(terminal, outcomes, manifest, history_metrics=history_metrics,
                              policy_version=policy_version)
        for source_row in self._metric_counts(history_metrics, policy_version):
            counts.update(source_row)
        return {"id": directory.name, "date": directory.name[:10], "kind": "batch",
                "status": status, "raw_status": str(raw_status), "summary": summary,
                "started_at": started, "ended_at": ended,
                "updated_at": self._modified(source) if source else None,
                "source": self._relative(source) if source else None, "stages": stages,
                "counts": counts, "policy_version": policy_version,
                "workflow_name": history_metrics.get("workflow_name"),
                "workflow_mode": history_metrics.get("workflow_mode"),
                "deadline": next((v for v in (terminal.get("batch_deadline_at"), terminal.get("global_deadline"),
                            manifest.get("batch_deadline_at"), manifest.get("global_deadline"),
                            manifest.get("invocation_deadline")) if isinstance(v, str)), None),
                "historical": True}

    def _report_lead(self, directory: Path) -> str:
        """First sentence-bearing line after the title of a saved run report."""
        for name in ("REPORT.md", "report.md"):
            path = directory / name
            if path.is_symlink():
                continue
            content = self._text(path, 300_000)
            if not content:
                continue
            lines = [_plain(line) for line in content.splitlines() if line.strip()]
            return next((line[:400] for line in lines[1:4] if not line.startswith("#")), "")
        return ""

    @staticmethod
    def _stage_raw(entry: Any) -> Any:
        if isinstance(entry, dict):
            return entry.get("status") or entry.get("outcome") or entry.get("six_lanes") or entry.get("lanes_status")
        return entry if isinstance(entry, str) and entry else None

    @staticmethod
    def _metric_counts(history_metrics: dict, policy_version: int | None = None) -> list[dict]:
        records = history_metrics.get("records", []) if isinstance(history_metrics, dict) else []
        if not isinstance(records, list):
            return []
        result = []
        stage_map = {"hunt": ("ads_reviewed", "jobs_found", "jobs_proposed"),
                     "hunt_audit": ("jobs_shortlisted",),
                     "build": ("cvs_built",),
                     "sync": (("roles_synced",) if policy_version == 4 else ("rows_synced",))}
        for stage, count_keys in stage_map.items():
            rows = [row for row in records if isinstance(row, dict) and
                    row.get("kind") == "stage_owner_receipt" and row.get("stage") == stage and
                    row.get("status") in ("done", "partial", "failed", "held")]
            for count_key in count_keys:
                count_field = ("jobs_accepted" if count_key == "jobs_shortlisted" else count_key)
                values = [_count_value(row.get(count_field)) for row in rows]
                if rows and all(value is not None for value in values):
                    result.append({count_key: sum(values)})
        return result

    def _stages(self, terminal: dict, outcomes: dict, manifest: dict, *,
                history_metrics: dict | None = None, policy_version: int | None = None) -> list[dict]:
        stage_data = terminal.get("stages") or terminal.get("stage_status") or {}
        if not isinstance(stage_data, dict):
            stage_data = {}
        history_metrics = history_metrics if isinstance(history_metrics, dict) else {}
        metric_records = history_metrics.get("records", [])
        metric_records = metric_records if isinstance(metric_records, list) else []
        policy4 = policy_version == 4
        configured = manifest.get("config", {}) if isinstance(manifest, dict) else {}
        native = configured.get("routing_revision") in {
            "2026-10-05-luna-max", "2026-10-07-sol-high-hunt", "2026-10-07-sol-only-timebox"}
        aliases = {"sol_review": ("sol_review", "sol_approval"), "sync": ("sync", "deliverable_sheet_sync")}
        result = []
        for key, label, default_model in _STAGES:
            if policy4:
                default_model = _POLICY4_STAGE_MODELS.get(key, default_model)
                if native:
                    selected = configured.get("routes", {}).get(key, {})
                    names = {"gpt-6-luna":"GPT-6 Luna", "gpt-6.1-sol":"GPT-6.1 Sol"}
                    if selected.get("model") in names and selected.get("effort"):
                        default_model = names[selected["model"]] + " · " + ("MAX" if selected["effort"] == "max" else selected["effort"].title())
                if key == "sync":
                    label = "CV Studio sync"
            elif policy_version is None:
                default_model = None
            keys = aliases.get(key, (key,))
            entry: Any = next((stage_data[k] for k in keys if k in stage_data), None)
            if entry is None:
                entry = next((terminal[k] for k in _TERMINAL_STAGE_KEYS[key] if self._stage_raw(terminal.get(k))), None)
            if entry is None:
                entry = next((outcomes[k] for k in keys if k in outcomes), None)
            if key == "inbox" and entry is None:
                entry = terminal.get("inbox")
            if key == "sync" and entry is None:
                entry = terminal.get("sync_stage") or terminal.get("deliverable_sync")
            owner_rows = [row for row in metric_records if isinstance(row, dict) and
                          row.get("kind") == "stage_owner_receipt" and row.get("stage") == key]
            if isinstance(entry, dict):
                raw = self._stage_raw(entry)
                detail = entry.get("reason") or entry.get("summary") or "Recorded in batch evidence."
                model = entry.get("model") or self._owner_model(owner_rows) or default_model
            else:
                raw, detail, model = entry, "Recorded in batch evidence." if entry else "No stage receipt in this batch.", self._owner_model(owner_rows) or default_model
                if entry is None and owner_rows:
                    statuses = [str(row.get("status", "")).lower() for row in owner_rows]
                    expected_lanes = {"hellowork", "france_travail", "linkedin", "apec", "wttj"}
                    if native:
                        expected_lanes.add("indeed")
                    lane_ids = {str(row.get("lane_id") or row.get("source") or "").lower() for row in owner_rows}
                    if "running" in statuses:
                        raw = "RUNNING"
                    elif key == "hunt" and policy4 and not expected_lanes.issubset(lane_ids):
                        raw = "PARTIAL"
                    elif statuses and all(status == "done" for status in statuses):
                        raw = "DONE"
                    elif statuses and len(set(statuses)) == 1:
                        raw = statuses[0].upper()
                    else:
                        raw = "PARTIAL"
                    detail = "Stage owner receipts recorded in the local run metrics file."
            recorded_status = "not_recorded" if raw is None or raw == "" else _status(raw)
            if recorded_status == "not_recorded" and policy4 and key in _POLICY4_STAGE_MODELS:
                detail = "Policy 4 configured route; execution is not recorded."
            elif policy_version is None and not owner_rows and not entry:
                model = None
                detail = "No stage receipt saved. Run policy and stage execution are not recorded."
            result.append({"id": key, "label": label, "status": recorded_status,
                           "raw_status": str(raw) if raw is not None else None,
                           "detail": str(detail), "model": model,
                           "model_note": "Execution route from stage receipts." if owner_rows else
                                        "Policy 4 configured route unless the receipt names a model." if policy4 else
                                        "Historical route is not recorded." if policy_version is None else
                                        "Current configured route unless the receipt names a model."})
        if native:
            result.sort(key=lambda row: 0 if row["id"] == "hunt" else 1 if row["id"] == "inbox" else 2)
        return result

    @staticmethod
    def _owner_model(rows: list[dict]) -> str | None:
        models = list(dict.fromkeys(str(row.get("actual_model") or row.get("model_requested"))
                                    for row in rows if row.get("actual_model") or row.get("model_requested")))
        efforts = list(dict.fromkeys(str(row.get("actual_reasoning_effort") or row.get("reasoning_effort"))
                                     for row in rows if row.get("actual_reasoning_effort") or row.get("reasoning_effort")))
        if not models:
            return None
        model = models[0] if len(models) == 1 else "Multiple models"
        effort = efforts[0] if len(efforts) == 1 else "mixed" if efforts else None
        return f"{model} · {effort.title()}" if effort else model

    def _legacy_runs(self) -> list[dict]:
        result = []
        try:
            paths = sorted((self.auto / "state").glob("*.json"), reverse=True)
        except OSError:
            return result
        for path in paths:
            state = self._json(path)
            if not isinstance(state, dict) or not isinstance(state.get("stages"), dict):
                continue
            for stage, current in state["stages"].items():
                if not isinstance(current, dict):
                    continue
                runs = [r for r in current.get("previous_runs", []) if isinstance(r, dict)] + [current]
                for run in runs:
                    raw = str(run.get("status") or "UNVERIFIED")
                    result.append({"id": f"legacy-{path.stem}-{stage}-{run.get('run', 1)}",
                        "date": str(state.get("date") or path.stem), "kind": "legacy_stage",
                        "status": "partial" if _status(raw) == "running" else _status(raw), "raw_status": raw,
                        "summary": str(run.get("summary") or "Historical stage record."),
                        "started_at": run.get("started_at"), "ended_at": run.get("ended_at"),
                        "updated_at": run.get("at"), "source": self._relative(path), "historical": True,
                        "stages": [{"id": stage, "label": stage.replace("_", " ").title(),
                                    "status": "partial" if _status(raw) == "running" else _status(raw),
                                    "detail": str(run.get("summary") or ""), "model": run.get("model")}],
                        "counts": run.get("counts") if isinstance(run.get("counts"), dict) else {}})
        return result

    _CLAUDE_STAGES = (("screening", "Screen sheet rows"), ("provenance", "Provenance check"), ("build", "Build CVs"),
                      ("build_audit", "Build audit"), ("approval", "Final approval"), ("sync", "CV Studio sync"))
    _MODEL_NAMES = {"claude-haiku-5-5": "Claude Haiku 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5",
                    "claude-opus-5-5": "Claude Opus 5.5"}

    def _claude_runs(self) -> list[dict]:
        """Claude sheet runs (Master/auto/evidence/claude-runs/<run>/), read-only. Since 13 Oct the
        Claude pipeline is the only one in use, so History must show its runs too."""
        result = []
        base = self.auto / "evidence" / "claude-runs"
        try:
            dirs = sorted(d for d in base.iterdir() if d.is_dir() and not d.is_symlink() and (d / "run.json").is_file())
        except OSError:
            return result
        for directory in dirs:
            header = self._json(directory / "run.json") or {}
            report = self._json(directory / "report.json") or {}
            if header.get("fixture"):
                continue
            events = []
            try:
                for line in (directory / "ledger.jsonl").read_text(encoding="utf-8").splitlines():
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        continue
            except OSError:
                pass
            stages = []
            for stage, label in self._CLAUDE_STAGES:
                done = [e["data"] for e in events if e.get("event") == "stage_completed" and e.get("data", {}).get("stage") == stage]
                failed = [e["data"] for e in events if e.get("event") == "stage_failed" and e.get("data", {}).get("stage") == stage]
                models = sorted({m for d in done + failed for m in d.get("observed_models") or []})
                if not done and not failed:
                    status, detail = "not_run", "Not needed or not reached in this run."
                else:
                    status = "done" if done and not failed else "partial" if done else "failed"
                    detail = f"{len(done)} completed, {len(failed)} failed"
                    if stage == "build_audit":
                        detail += f" · {sum(d.get('verdict') == 'PASS' for d in done)} passed audit"
                stages.append({"id": stage, "label": label, "status": status, "detail": detail,
                               "model": " + ".join(self._MODEL_NAMES.get(m, m) for m in models) or None})
            if report:
                status = "partial" if report.get("aborted") else "done"
                bits = [f"{report.get('admitted', 0)} roles worked",
                        f"{report.get('approved', 0)} approved", f"{report.get('synced', 0)} synced to Studio"]
                summary = ("Stopped: " + str(report["aborted"])) if report.get("aborted") else "Sheet run finished: " + ", ".join(bits) + "."
            else:
                status, summary = "running", "Claude sheet run in progress."
            result.append({"id": directory.name, "date": directory.name[:10], "kind": "claude_sheet",
                           "status": status, "raw_status": "ABORTED" if report.get("aborted") else ("FINISHED" if report else "RUNNING"),
                           "summary": summary, "started_at": header.get("started_at") or report.get("started_at"),
                           "ended_at": report.get("finished_at"), "updated_at": report.get("finished_at") or header.get("started_at"),
                           "source": self._relative(directory / ("report.md" if report else "run.json")),
                           "workflow_name": "CV Pipeline from Hark Job Feed (Claude)", "workflow_mode": "sheet",
                           "stages": stages,
                           "summary_metrics": {"jobs_shortlisted": (report.get("screening_counts") or {}).get("ACCEPTED", 0) if report else None,
                                               "cvs_built": report.get("approved") if report else None},
                           "counts": {k: report.get(k) for k in ("admitted", "built", "audits_passed", "approved", "synced")
                                      if isinstance(report.get(k), int)}})
        return result

    def runs(self) -> dict:
        items = [summary for p in self._run_dirs() if (summary := self._run_summary(p))]
        items.extend(self._claude_runs())
        items.extend(self._legacy_runs())
        items.sort(key=lambda r: (_time(r.get("started_at")) or _time(r.get("date")) or datetime.min.replace(tzinfo=timezone.utc), r["id"]), reverse=True)
        return {"items": items, "total": len(items)}

    def run(self, run_id: str) -> dict:
        item = next((r for r in self.runs()["items"] if r["id"] == run_id), None)
        if item is None:
            raise StudioDataError("This saved run was not found.", "not_found", 404)
        item = dict(item)
        item["files"], item["reports"] = [], []
        if item["kind"] == "batch":
            directory = self.evidence / run_id
            # Deliberately do not expose Gmail bodies, credentials, prompts or raw inbox receipts.
            for name in (*_RUN_TERMINALS, *_RUN_MANIFESTS, "stage-outcomes.json", "site-lane-outcomes.json",
                         "launch-failure.json", "LAUNCH_FAILURE.md", "REPORT.md", "DELAY_REPORT.md", "report.md"):
                path = directory / name
                if not path.is_file() or path.is_symlink():
                    continue
                item["files"].append({"name": name, "path": self._relative(path), "updated_at": self._modified(path)})
                if path.suffix == ".md":
                    content = self._text(path, 300_000)
                    if content is not None:
                        item["reports"].append({"name": name, "content": content})
            item["sources"] = self._sources(directory)
        elif item["kind"] == "claude_sheet":
            path = self.auto / "evidence" / "claude-runs" / run_id / "report.md"
            if path.is_file() and not path.is_symlink():
                item["files"].append({"name": "report.md", "path": self._relative(path), "updated_at": self._modified(path)})
                content = self._text(path, 300_000)
                if content is not None:
                    item["reports"].append({"name": "report.md", "content": content})
        return item

    def _sources(self, directory: Path | None) -> list[dict]:
        outcomes = self._json(directory / "site-lane-outcomes.json") if directory else None
        lanes = outcomes.get("lanes", []) if isinstance(outcomes, dict) else []
        rows = {str(row.get("worker_id", "")).split(":")[-1]: row for row in lanes if isinstance(row, dict)}
        manifest = self._json(directory / "batch-manifest.json") if directory else None
        history_metrics = self._json(directory / "stage-metrics.json") if directory else None
        policy_version = (manifest.get("policy_version") if isinstance(manifest, dict) else None)
        if not isinstance(policy_version, int) and isinstance(history_metrics, dict):
            policy_version = history_metrics.get("policy_version")
        launch = self._json(directory / "launch-receipt.json") if directory else None
        configured = launch.get("config", {}) if isinstance(launch, dict) else (
            self._json(self.root / "Master/auto/local/pipeline-routing.json") or {} if not directory else {})
        native = configured.get("routing_revision") in {
            "2026-10-05-luna-max", "2026-10-07-sol-high-hunt", "2026-10-07-sol-only-timebox"}
        timeboxed = configured.get("routing_revision") == "2026-10-07-sol-only-timebox"
        display_sites = list(_SITES) + ([("indeed", "Indeed")] if native else [])
        default_target = 20 if native else _POLICY4_HUNT_TARGET if policy_version == 4 else 20 if policy_version == 3 else None
        metric_lanes = {}
        records = history_metrics.get("records", []) if isinstance(history_metrics, dict) and history_metrics.get("kind") == "scheduled_run_metrics_v1" else []
        if isinstance(records, list):
            for item in records:
                if not isinstance(item, dict) or item.get("kind") != "stage_owner_receipt" or item.get("stage") != "hunt":
                    continue
                lane = str(item.get("lane_id") or item.get("source") or "").casefold().replace("-", "_")
                lane_aliases = {"france travail": "france_travail", "francetravail": "france_travail",
                                "welcome_to_the_jungle": "wttj", "welcome to the jungle": "wttj"}
                lane = lane_aliases.get(lane, lane)
                if lane in {site for site, _label in display_sites}:
                    metric_lanes[lane] = item
        result = []
        for key, label in display_sites:
            row = rows.get(key, {})
            metric_row = metric_lanes.get(key, {})
            # The local metrics owner row is the policy-4 lane receipt. A saved
            # legacy site summary remains useful only where that row has no fact.
            screened = _count_value(metric_row.get("ads_reviewed"))
            if screened is None:
                screened = _count_value(row.get("full_live_ads_reviewed"))
            count_recorded = isinstance(screened, (int, float)) and not isinstance(screened, bool)
            metric_status = metric_row.get("status")
            saved_status = metric_status if metric_status is not None else row.get("status")
            detail = row.get("reason") or ("Full-ad count recorded in the saved lane receipt." if count_recorded
                                            else "No lane count in the latest saved batch.")
            if "target" in row:
                target = _count_value(row.get("target"))
            elif "target" in metric_row:
                target = _count_value(metric_row.get("target"))
            else:
                target = default_target
            if timeboxed:
                target = None
            result.append({"id": key, "label": label, "status": _status(saved_status),
                           "screened": screened, "target": target,
                           "detail": str(detail),
                           "target_note": "Collect for 20 minutes, wrap up by 30; no advert-count minimum." if timeboxed else
                                         "Target from the saved run policy; counts need a saved lane receipt." if target is not None else
                                         "No saved policy or lane target; target is unknown."})
        if "mail" in rows:
            row = rows["mail"]
            result.append({"id": "mail", "label": "Mail alerts", "status": _status(row.get("status")),
                           "screened": _count_value(row.get("full_live_ads_reviewed")), "target": _count_value(row.get("target")),
                           "detail": str(row.get("reason") or "Saved mail-alert lane receipt.")})
        return result

    def _tracker(self) -> tuple[dict, list[dict]]:
        names = ("pipeline-inventory-final.json", "pipeline-after.json", "pipeline-inventory.json", "online-history.snapshot.json")
        for directory in self._run_dirs():
            for name in names:
                path = directory / name
                data = self._json(path)
                values = data.get("values") if isinstance(data, dict) else None
                if not isinstance(values, list) or not values or not isinstance(values[0], list):
                    continue
                headers = [str(v).strip() for v in values[0]]
                if "Company" not in headers or "Job profile" not in headers:
                    continue
                rows = [{header: row[i] if i < len(row) else "" for i, header in enumerate(headers)}
                        for row in values[1:] if isinstance(row, list) and any(str(v).strip() for v in row)]
                meta = {"available": True, "historical": True, "live": False, "total": len(rows),
                        "date": directory.name[:10], "updated_at": self._modified(path),
                        "source": self._relative(path), "label": "Saved tracker snapshot",
                        "note": "Read from saved Sheet evidence. Current online tracker status is not checked."}
                return meta, rows
        return {"available": False, "historical": True, "live": False, "total": None,
                "date": None, "updated_at": None, "source": None,
                "label": "Saved tracker snapshot", "note": "No readable saved native Sheet snapshot found."}, []

    def _role_dirs(self) -> list[Path]:
        try:
            root = self._safe(self.role_root)
            if not root.is_dir():
                return []
        except StudioDataError:
            return []
        result = []
        collections = {"_Applied", "_No longer active", "Already applied"}
        for current, dirs, files in os.walk(root, followlinks=False):
            path = Path(current)
            dirs[:] = sorted(d for d in dirs if not (path / d).is_symlink()
                             and (not d.startswith(("_", ".")) or d in collections))
            if path != root and path.name not in collections:
                result.append(path)
                dirs[:] = []
        return result

    def _assets(self, folder: Path) -> list[dict]:
        result = []
        try:
            for path in sorted(folder.glob("*.pdf")):
                safe = self._safe(path, folder)
                if not safe.is_file():
                    continue
                key = _id(self._relative(path))
                label = "Cover letter" if "cover" in path.name.casefold() or "lettre" in path.name.casefold() else "CV" if "cv" in path.name.casefold() else path.stem
                result.append({"key": key, "label": label, "name": path.name, "url": f"/api/assets?key={key}",
                               "size": safe.stat().st_size})
            # 23 Oct: Mercury 3 hand-edited CVs live in their own sub-folders; list each combined PDF.
            for sub in sorted(folder.glob("Mercury 3 edited CV*")):
                path = sub / "CV_Candidate.pdf"
                if sub.is_symlink() or not sub.is_dir() or path.is_symlink() or not path.is_file():
                    continue
                safe = self._safe(path, folder)
                name = f"{sub.name}/{path.name}"
                key = _id(self._relative(path))
                result.append({"key": key, "label": "Edited CV" + sub.name[len("Mercury 3 edited CV"):],
                               "name": name, "url": f"/api/assets?key={key}", "size": safe.stat().st_size,
                               "edited": True})
        except (OSError, StudioDataError):
            pass
        return result

    def _fields(self, content: str) -> dict[str, str]:
        result = {}
        for line in content.splitlines()[:90]:
            clean = re.sub(r"[*`]+", "", line).strip().lstrip("- ")
            match = re.match(r"^([\w /'’À-ÿ-]{2,40})\s*:\s*(.+)$", clean)
            if match:
                result.setdefault(match.group(1).strip().casefold(), match.group(2).strip())
        return result

    @staticmethod
    def _manifest_relative(value: Any) -> tuple[str, ...] | None:
        if not isinstance(value, str) or not value or "\\" in value:
            return None
        path = Path(value)
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            return None
        return path.parts

    def _tidy_jd_manifest_rows(self) -> dict[str, dict | None]:
        """Load cumulative tidy mappings, but authorize only explicit JD rows."""
        path = self.root / "Master/auto/local/pipeline-repair/tidy_roles_manifest.json"
        try:
            if path.is_symlink() or not path.is_file():
                return {}
            before = path.stat()
            signature = (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size)
            if before.st_size > 8_000_000:
                return {}
            with self._tidy_manifest_lock:
                if signature == self._tidy_manifest_signature and self._tidy_manifest_rows is not None:
                    return self._tidy_manifest_rows
                raw = path.read_bytes()
                after = path.stat()
                if (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size) != signature:
                    return {}
                manifest = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError):
            return {}
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or
                manifest.get("root_relative") != "Tailored CVs" or
                not isinstance(manifest.get("manifest_id"), str) or
                not isinstance(manifest.get("roles"), list) or len(manifest["roles"]) > 5_000):
            return {}
        rows: dict[str, dict | None] = {}
        for role in manifest["roles"]:
            if not isinstance(role, dict):
                continue
            role_path = role.get("role_path")
            role_parts = self._manifest_relative(role_path)
            files = role.get("files")
            if role_parts is None or not isinstance(files, list) or len(files) > 2_000:
                continue
            jd_rows = []
            for entry in files:
                if not isinstance(entry, dict) or entry.get("kind") != "job_description":
                    continue
                original = self._manifest_relative(entry.get("original_path"))
                legacy = self._manifest_relative(entry.get("legacy_link_path"))
                support = self._manifest_relative(entry.get("support_path"))
                support_dir = self._manifest_relative(role.get("support_dir"))
                expected_old = (*role_parts, "JD.md")
                digest = entry.get("sha256")
                if (original != expected_old or legacy != expected_old or support is None or
                        support_dir is None or support[:len(support_dir)] != support_dir or
                        len(support) <= len(support_dir) or
                        not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or
                        not isinstance(entry.get("size_bytes"), int) or entry["size_bytes"] < 0):
                    continue
                expected_target = Path(*support).as_posix()
                if entry.get("legacy_link_target") != expected_target:
                    continue
                jd_rows.append(entry)
            if jd_rows:
                rows[Path(*role_parts).as_posix()] = jd_rows[0] if len(jd_rows) == 1 else None
        with self._tidy_manifest_lock:
            self._tidy_manifest_signature = signature
            self._tidy_manifest_rows = rows
        return rows

    def _role_jd_target(self, folder: Path) -> Path | None:
        """Return a regular JD or an exact, same-role manifest-bound legacy link."""
        jd_path = folder / "JD.md"
        try:
            if not jd_path.is_symlink():
                safe = self._safe(jd_path, folder)
                return safe if safe.is_file() else None
            safe_folder = self._safe(folder, self.role_root)
            role_path = safe_folder.relative_to(self.role_root).as_posix()
            mapping = self._tidy_jd_manifest_rows().get(role_path)
            if not isinstance(mapping, dict):
                return None
            if (mapping.get("legacy_link_path") != f"{role_path}/JD.md" or
                    mapping.get("original_path") != f"{role_path}/JD.md"):
                return None
            target_text = os.readlink(jd_path)
            if target_text != mapping.get("legacy_link_target") or Path(target_text).is_absolute():
                return None
            target_parts = self._manifest_relative(mapping.get("support_path"))
            if target_parts is None:
                return None
            target = safe_folder.joinpath(*target_parts)
            safe_target = self._safe(target, safe_folder)
            if safe_target.is_symlink() or not safe_target.is_file():
                return None
            if jd_path.resolve(strict=True) != safe_target.resolve(strict=True):
                return None
            if target.stat().st_size != mapping.get("size_bytes"):
                return None
            return safe_target
        except (OSError, StudioDataError, ValueError):
            return None

    def _role_jd_text(self, folder: Path, limit: int = 2_000_000) -> str | None:
        target = self._role_jd_target(folder)
        if target is None:
            return None
        try:
            if target.stat().st_size > limit:
                return None
            raw = target.read_bytes()
            if (folder / "JD.md").is_symlink() and hashlib.sha256(raw).hexdigest() != self._jd_expected_hash(folder, target):
                return None
            return raw.decode("utf-8")
        except (OSError, UnicodeError, StudioDataError):
            return None

    def _jd_expected_hash(self, folder: Path, target: Path) -> str | None:
        jd_path = folder / "JD.md"
        if not jd_path.is_symlink():
            return None
        try:
            safe_folder = self._safe(folder, self.role_root)
            role_path = safe_folder.relative_to(self.role_root).as_posix()
            mapping = self._tidy_jd_manifest_rows().get(role_path)
            if (isinstance(mapping, dict) and
                    mapping.get("support_path") == target.relative_to(safe_folder).as_posix()):
                return mapping.get("sha256")
        except (OSError, StudioDataError, ValueError):
            pass
        return None

    def _tracker_role_index(self, rows: list[dict]) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        for candidate in rows:
            saved = str(candidate.get("CV file", "")).replace("\\", "/")
            if "Tailored CVs/" not in saved:
                continue
            try:
                saved_folder = self._safe(self.role_root / saved.split("Tailored CVs/", 1)[1], self.role_root).parent
                result.setdefault(str(saved_folder), []).append(candidate)
            except StudioDataError:
                continue
        return result

    def _role_summary(self, folder: Path, tracker: dict, rows: list[dict], index: dict[str, list[dict]]) -> dict:
        jd = self._role_jd_text(folder) or ""
        fields = self._fields(jd)
        title = next((_plain(line.lstrip("# ")) for line in jd.splitlines() if line.startswith("# ")), folder.name)
        company = fields.get("company") or fields.get("entreprise") or folder.name.split(" - ")[0]
        rel = self._relative(folder)
        exact = index.get(str(folder), [])
        row = exact[0] if len(exact) == 1 else {}
        if not row:
            same = [r for r in rows if str(r.get("Company", "")).casefold() == company.casefold()
                    and str(r.get("Job profile", "")).casefold() == title.casefold()]
            row = same[0] if len(same) == 1 else {}
        artifacts = self._assets(folder)
        cv = any(a["label"] == "CV" for a in artifacts)
        canonical_cv = any(a["name"] == "CV_Candidate.pdf" for a in artifacts)
        letter = any(a["label"] == "Cover letter" for a in artifacts)
        fit_value = fields.get("fit") or fields.get("fit score") or str(row.get("Fit /5", ""))
        fit_match = re.search(r"(?<!\d)([1-5])(?:\s*/\s*5)?", fit_value)
        collection = "applied" if "_Applied" in folder.parts else "inactive" if "_No longer active" in folder.parts else "current"
        build_spec = self._json(folder / "build-spec.json") or {}
        cv_specs = build_spec.get("cvs", []) if isinstance(build_spec, dict) else []
        built_language = cv_specs[0].get("language") if cv_specs and isinstance(cv_specs[0], dict) else None
        language = (fields.get("advert language") or fields.get("langue de l'annonce") or row.get("Lang") or built_language
                    or _guess_advert_language(jd))
        item = {"id": _id(rel), "company": company, "title": title,
                "location": fields.get("location") or fields.get("lieu") or row.get("Location") or None,
                "location_label": _card_location_label(fields.get("location") or fields.get("lieu") or row.get("Location")),
                "contract": fields.get("contract") or fields.get("contrat") or None,
                "language": language, "fit": int(fit_match.group(1)) if fit_match else None,
                "status": "artifact_present" if cv else "source_only" if jd else "empty",
                "status_note": "Files are present; approval and live-ad checks are separate." if cv else "Saved source folder; no CV PDF found.",
                "cv_available": cv, "canonical_cv_available": canonical_cv,
                "cv_built_at": self._modified(folder / "CV_Candidate.pdf") if canonical_cv else None,
                "cover_letter_available": letter,
                "updated_at": max((self._modified(p) or "" for p in [self._role_jd_target(folder), folder / "CV_Candidate.pdf", folder / "build-spec.json"] if p is not None), default="") or self._modified(folder),
                "folder": rel, "collection": collection,
                "source_url": _web_url(fields.get("source url") or fields.get("original advert") or fields.get("source") or row.get("Job URL")),
                "found_at": (fields.get("found") or fields.get("trouvé") or fields.get("retrieved") or row.get("Date found")
                             or _sheet_run_date(fields.get("pipeline")) or None),
                "artifacts": artifacts,
                "tracker_status": row.get("Status") or None,
                "applied": str(row.get("Applied", "")).lower() == "yes" if row else None,
                "tracker_snapshot_date": tracker.get("date") if row else None}
        hold = self._json(folder / "DO_NOT_SEND.json") if (folder / "DO_NOT_SEND.json").is_file() else None
        item["do_not_send"] = (hold.get("problems") or ["final check failed"]) if isinstance(hold, dict) else None
        item["ratings"] = role_ratings(item, jd, fields.get("advert language") or fields.get("langue de l'annonce"))
        # 26 Oct (Daksh): offline 0-100 fit with one decimal for every job (Studio/fit_score.py, no AI).
        try:
            item["fit_score"] = fit_score.score_advert(jd)
        except Exception:
            item["fit_score"] = None
        return item

    def _stat_key(self, path: Path) -> tuple:
        try:
            st = path.stat()
            return (path.name, st.st_mtime_ns, st.st_size)
        except OSError:
            return (path.name, None, None)

    def _cached_role_summary(self, folder: Path, tracker: dict, rows: list[dict], index: dict[str, list[dict]]) -> dict:
        """Reuse a folder's summary while every input it reads is unchanged (13 Oct: rebuilding all 400+
        summaries took ~1.2 s on every Home load). The key covers each folder entry (name, mtime, size,
        symlink), a linked JD's target, the tracker snapshot and its row match, the tidy JD manifest and
        the company ratings file, so any change rebuilds that folder."""
        try:
            entries = tuple(sorted((e.name, e.is_symlink(), *self._stat_key(Path(e.path))[1:]) for e in os.scandir(folder)))
        except OSError:
            return self._role_summary(folder, tracker, rows, index)
        jd_link = folder / "JD.md"
        target = self._stat_key(jd_link.resolve()) if jd_link.is_symlink() else None
        from ratings import COMPANY_FILE
        shared = (tracker.get("date"), tracker.get("updated_at"), len(rows),
                  self._stat_key(self.root / "Master/auto/local/pipeline-repair/tidy_roles_manifest.json"),
                  self._stat_key(COMPANY_FILE), self._stat_key(fit_score.WEIGHTS))
        match = json.dumps(index.get(str(folder), []), sort_keys=True, default=str)
        key = (entries, target, shared, match)
        cache = self.__dict__.setdefault("_role_summary_cache", {})
        hit = cache.get(str(folder))
        if hit and hit[0] == key:
            return copy.deepcopy(hit[1])
        item = self._role_summary(folder, tracker, rows, index)
        cache[str(folder)] = (key, copy.deepcopy(item))
        return item

    roles_cache_seconds = 0  # the server sets a few seconds so repeated Home refreshes reuse one scan

    def roles(self, *, apply_document_aliases: bool = True) -> dict:
        ttl = self.roles_cache_seconds
        if ttl:
            hit = getattr(self, "_roles_hot", {}).get(apply_document_aliases)
            if hit and time.monotonic() - hit[0] < ttl:
                return copy.deepcopy(hit[1])
        result = self._roles_scan(apply_document_aliases=apply_document_aliases)
        if ttl:
            if not hasattr(self, "_roles_hot"):
                self._roles_hot = {}
            self._roles_hot[apply_document_aliases] = (time.monotonic(), copy.deepcopy(result))
        return result

    def _roles_scan(self, *, apply_document_aliases: bool = True) -> dict:
        tracker, rows = self._tracker()
        index = self._tracker_role_index(rows)
        items = [self._cached_role_summary(folder, tracker, rows, index) for folder in self._role_dirs()]
        if apply_document_aliases:
            from requested_document_aliases import apply_aliases
            items = apply_aliases(self, items)
        from requested_review import annotate_requested_reviews
        items = annotate_requested_reviews(self, items)
        items.sort(key=lambda r: (r["updated_at"] or "", r["company"].casefold()), reverse=True)
        return {"items": items, "total": len(items), "source": "Local role folders",
                "note": "File availability is checked locally. Saved tracker labels are historical; adverts are not rechecked.",
                "tracker": tracker}

    def role(self, role_id: str) -> dict:
        item = next((r for r in self.roles()["items"] if r["id"] == role_id), None)
        if item is None:
            raise StudioDataError("This role folder was not found.", "not_found", 404)
        folder = self._safe(self.role_root / item.get("document_folder", item["folder"]).removeprefix("Tailored CVs/"), self.role_root)
        item = dict(item)
        alias = item.get("approved_document_alias")
        approved_names = ({Path(r["path"]).name for r in alias["artifact_refs"]} if alias else None)
        def approved(name):
            return item.get("requested_document_gate") not in {"invalid", "unapproved"} and (approved_names is None or name in approved_names)
        item["jd"] = (self._role_jd_text(folder) or "") if approved("JD.md") else ""
        letter = self._json(folder / "cover-letter.json") if approved("cover-letter.json") else None
        paragraphs = [letter.get(key) for key in ("p1", "p2", "p3", "p4")] if isinstance(letter, dict) else []
        item["cover_letter_text"] = "\n\n".join(
            html.unescape(re.sub(r"<[^>]*>", "", re.sub(r"<br\s*/?>", "\n", value, flags=re.I))).strip()
            for value in paragraphs if isinstance(value, str) and value.strip())
        item["documents"] = []
        for name, label in [("why-this-company.md", "Why this company"), ("experience-stretch.md", "Experience fit"),
                            ("language-review.md", "Language review"), ("application-answers.md", "Saved application answers")]:
            content = self._text(folder / name, 300_000) if approved(name) else None
            if content:
                item["documents"].append({"title": label, "content": content})
        return item

    def _master_cvs(self) -> list[dict]:
        result = []
        for language, path in zip(("EN", "FR"), self._standalone_pdfs):
            if not path.is_file() or path.is_symlink():
                continue
            key = _id(path.name)
            result.append({"id": f"master-{language.lower()}", "key": key,
                           "name": "Master CV · " + ("English" if language == "EN" else "French"),
                           "kind": "cv", "language": language, "url": f"/api/assets?key={key}"})
        return result

    def asset_path(self, key: str) -> Path:
        if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{24}", key):
            raise StudioDataError("This PDF was not found.", "not_found", 404)
        for path in self._standalone_pdfs:
            if _id(path.name) == key and not path.is_symlink() and path.is_file():
                return self._validated_pdf(self._safe(path))
        folders = self._role_dirs() + [self.root / "Master", self.root / "Master" / "cv"]
        for folder in folders:
            for asset in self._assets(folder):
                if asset["key"] == key:
                    path = self._safe(folder / asset["name"], folder)
                    from requested_document_aliases import source_asset_allowed
                    if not source_asset_allowed(self, folder, asset["name"]):
                        raise StudioDataError("This requested document has no current exact approval.", "not_found", 404)
                    if path.suffix.lower() != ".pdf" or not path.is_file():
                        break
                    return self._validated_pdf(path)
        raise StudioDataError("This PDF was not found.", "not_found", 404)

    @staticmethod
    def _validated_pdf(path: Path) -> Path:
        # A PDF key cannot be repurposed to serve another file type.
        with path.open("rb") as stream:
            if not stream.read(5).startswith(b"%PDF-"):
                raise StudioDataError("This file is not a readable PDF.", "invalid_pdf", 415)
        return path

    def active_pipeline(self, *, observed_live: dict | None = None) -> dict:
        """Conservative admission guard; it never signals or modifies a process.

        An open, current execution window is reserved even if a worker cannot be
        verified. Stale historical RUNNING records do not become live progress.
        """
        live = observed_live if observed_live is not None else self.live_pipeline()
        if live["active"] or live.get("admission_blocked"):
            uncertain = bool(live.get("uncertain"))
            return {"active": True, "uncertain": uncertain, "edit_locked": True, "run_id": live["run_id"],
                    "thread_id": live["thread_id"], "current_stage": live["current_stage"],
                    "reason": "Previously verified pipeline work cannot currently be fully checked. Source edits and another launch remain paused." if uncertain else
                              live["summary"] if live.get("finalized") else
                              "Recent activity in a verified external pipeline chat. Source edits and another launch are paused.",
                    "evidence": "external_session_uncertain" if uncertain else "external_session", "last_activity_at": live["last_activity_at"]}
        now = _now()
        for directory in self._run_dirs():
            terminal, _ = self._first_json(directory, _RUN_TERMINALS)
            if terminal and _status(terminal.get("status")) not in ("running", "ready", "not_run"):
                continue
            manifest, _ = self._first_json(directory, _RUN_MANIFESTS)
            if not manifest:
                continue
            if self._native_finished_terminal(directory, manifest):
                continue
            failure = _pre_dispatch_failure(self._json(directory / "launch-failure.json"), manifest,
                                            self._json(directory / "stage-metrics.json") or {})
            if failure and not terminal:
                continue
            started = _time(manifest.get("batch_started_at") or manifest.get("invocation_started_at") or manifest.get("started_at") or manifest.get("actual_start"))
            deadline = _time(manifest.get("batch_deadline_at") or manifest.get("global_deadline") or
                             manifest.get("invocation_deadline"))
            # Legacy plans lack a whole-run clock; reserve only a bounded recent window.
            deadline = deadline or (started + timedelta(minutes=119) if started else None)
            if started and deadline and started <= now <= deadline:
                return {"active": True, "uncertain": True, "edit_locked": True, "run_id": directory.name,
                        "reason": "A saved batch has an open execution window and no terminal receipt. Source edits are paused until it closes.",
                        "evidence": "open_batch_window", "deadline": _iso(deadline)}
        # A verified local pipeline process can outlive a window or use another run format.
        process = self._active_process()
        if process:
            if isinstance(process, dict):
                return process
            return {"active": True, "uncertain": False, "edit_locked": True, "run_id": None,
                    "reason": "A local pipeline worker is running. Source edits are paused.",
                    "evidence": "local_process", "pid": process}
        for item in self._legacy_runs():
            started = _time(item.get("started_at"))
            if _status(item.get("raw_status")) == "running" and started and timedelta(0) <= now - started < timedelta(minutes=119):
                return {"active": True, "uncertain": True, "edit_locked": True, "run_id": item["id"],
                        "reason": "A recent stage has no completion receipt. Source edits are paused while its activity is unconfirmed.",
                        "evidence": "recent_unclosed_stage"}
        return {"active": False, "uncertain": False, "edit_locked": False, "run_id": None,
                "reason": "No active local worker or current open batch window was detected. Saved status alone is not proof of live activity.",
                "evidence": "no_current_execution_evidence"}

    def live_pipeline(self) -> dict:
        """Safe observation only; it cannot signal a chat or grant approval."""
        live = self._live.observe(self._run_dirs(), self._json, self._run_summary,
                                  self._sources, self._stages({}, {}, {"config": self._json(self.root / "Master/auto/local/pipeline-routing.json") or {}}, policy_version=4), now=_now())
        if live.get("finalized") and live.get("status") == "follow_up":
            manual = self._open_manual_stage()
            if manual:
                # A label only: locks and stop scope still come from the observation.
                live["manual_stage"] = manual
                what = "CV build" if manual["stage"] == "build" else manual["stage"].replace("_", " ") + " stage"
                live["summary"] = (f"The scheduled run finished ({str(live.get('run_raw_status') or live.get('run_status') or 'recorded').lower()}). "
                                   f"A manual {what}{' for ' + manual['role'] if manual.get('role') else ''} is running in its chat; "
                                   "new runs and source edits stay paused until it ends.")
        return live

    def _open_manual_stage(self) -> dict | None:
        """The latest manual stage start in events.jsonl with no terminal event yet."""
        path = self.auto / "events.jsonl"
        try:
            with self._safe(path).open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 64_000))
                lines = stream.read().decode("utf-8", "replace").splitlines()[-200:]
        except (OSError, StudioDataError):
            return None
        latest: dict[str, dict] = {}
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("stage"), str):
                latest[row["stage"]] = row
        now = _now()
        for stage, row in latest.items():
            started = _time(row.get("ts"))
            if row.get("event") != "start" or row.get("trigger") != "manual" or not started or not timedelta(0) <= now - started <= timedelta(hours=2):
                continue
            result = {"stage": stage, "started_at": _iso(started), "run": row.get("run"), "model": row.get("model")}
            ledger = self._json_lines_first(self.evidence / str(row.get("date") or "") / f"{stage}-{row.get('run')}.jsonl")
            roles = ledger.get("expected_roles") if isinstance(ledger, dict) else None
            if isinstance(roles, list) and roles and isinstance(roles[0], dict):
                result["role"] = str(roles[0].get("role") or roles[0].get("company") or "")[:120] or None
            return result
        return None

    def _json_lines_first(self, path: Path) -> dict | None:
        raw = self._text(path, 2_000_000)
        if not raw:
            return None
        try:
            first = json.loads(raw.splitlines()[0])
        except (ValueError, IndexError):
            return None
        return first if isinstance(first, dict) else None

    def external_stop_descriptor(self) -> dict | None:
        """Return verified identities for an explicitly requested stop operation.

        No stop is performed here. Ownership must be rechecked by the stop
        controller at action time; the observer is not a control capability.
        """
        live = self.live_pipeline()
        if not (live["active"] or live.get("admission_blocked")) or not live.get("current_turn_id"):
            return None
        return {"workspace": str(self.workspace), "thread_id": live["thread_id"],
                "current_turn_id": live["current_turn_id"], "run_id": live["run_id"],
                "coordinator_open_turn": live.get("coordinator_open_turn"),
                "observed_at": live["observed_at"], "children": [
                    {"thread_id": w["id"], "current_turn_id": w.get("current_turn_id"),
                     "parent_thread_id": w.get("parent_thread_id"), "ownership_chain": w.get("ownership_chain"),
                     "ownership_depth": w.get("ownership_depth")}
                    for w in live["workers"] if w.get("open_turn") and w.get("current_turn_id")]}

    def _active_process(self) -> dict | None:
        def uncertain(reason: str, pid: int | None = None) -> dict:
            return {"active": True, "uncertain": True, "edit_locked": True, "run_id": None,
                    "reason": reason, "evidence": "process_inspection_uncertain", "pid": pid}

        try:
            result = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=2, check=False)
        except (OSError, subprocess.SubprocessError):
            return uncertain("Local worker activity could not be checked. Source edits are paused until inspection is available.")
        if result.returncode != 0:
            return uncertain("Local worker inspection failed. Source edits are paused until inspection is available.")
        workspace_markers = (str(self.root), str(self.workspace))
        for line in result.stdout.splitlines():
            fields = line.strip().split(None, 1)
            if len(fields) != 2 or not fields[0].isdigit() or int(fields[0]) == os.getpid():
                continue
            command = fields[1]
            argv = command.split(None, 1)
            executable = Path(argv[0]).name
            script = None
            if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable) and len(argv) == 2:
                tail = argv[1]
                if tail.startswith(("-c ", "-m ")):
                    continue
                match = re.match(r"(?:-[\w]+\s+)*(.*?\.py)(?:\s|$)", tail)
                script = match.group(1) if match else None
            elif executable in ("stage_runtime.py", "terminal_agents.py"):
                script = argv[0]
            if not script or Path(script).name not in ("stage_runtime.py", "terminal_agents.py"):
                continue
            pid = int(fields[0])
            in_workspace = any(marker in command for marker in workspace_markers)
            if not in_workspace:
                # Relative Python argv is common. Resolve cwd without signalling
                # the process; an uninspectable candidate remains a safe hold.
                try:
                    cwd_result = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                                                capture_output=True, text=True, timeout=2, check=False)
                except (OSError, subprocess.SubprocessError):
                    return uncertain("A pipeline worker was found, but its workspace could not be verified. Source edits are paused.", pid)
                cwd_lines = [line[1:] for line in cwd_result.stdout.splitlines() if line.startswith("n")]
                if cwd_result.returncode != 0 or not cwd_lines:
                    return uncertain("A pipeline worker was found, but its workspace could not be verified. Source edits are paused.", pid)
                cwd = Path(cwd_lines[0]).resolve()
                in_workspace = cwd == self.workspace or cwd.is_relative_to(self.root)
            if in_workspace:
                return {"active": True, "uncertain": False, "edit_locked": True, "run_id": None,
                        "reason": "A local pipeline worker is running. Source edits are paused.",
                        "evidence": "local_process", "pid": pid}
        return None

    def _dashboard_jd_metadata(self, role: dict) -> dict:
        """Read just the saved role's JD fields needed for dashboard grouping."""
        result = {"source_url": role.get("source_url"), "source_label": None,
                  "office_location": None, "jd_company": None, "jd_title": None,
                  "jd_role_evidence": False}
        folder = role.get("folder")
        if not isinstance(folder, str) or not folder.startswith("Tailored CVs/"):
            return result
        try:
            relative = folder.removeprefix("Tailored CVs/")
            directory = self._safe(self.role_root / relative, self.role_root)
            content = self._role_jd_text(directory, 300_000)
        except (OSError, StudioDataError):
            return result
        if content is None:
            return result
        fields = self._fields(content)
        result["office_location"] = fields.get("location") or fields.get("lieu")
        result["jd_company"] = fields.get("company") or fields.get("entreprise")
        result["jd_title"] = next((_plain(line.lstrip("# ")) for line in content.splitlines()
                                    if line.startswith("# ") and _plain(line.lstrip("# "))), None)
        result["jd_role_evidence"] = bool(result["jd_company"] and result["jd_title"])
        source = fields.get("source url") or fields.get("original advert") or fields.get("source")
        result["source_url"] = _web_url(source) or role.get("source_url")
        if source and not _web_url(source):
            result["source_label"] = source
        return result

    @staticmethod
    def _dashboard_path_key(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            return ""
        normalized = value.replace("\\", "/").rstrip("/")
        return _analytics_identity(normalized.rsplit("/", 1)[-1])

    @staticmethod
    def _dashboard_row_value(row: dict, *keys: str) -> Any:
        for key in keys:
            value = row.get(key)
            if value is not None:
                return value
        return None

    def _dashboard_index_records(self, path: Path) -> tuple[set[str], int | None, bool]:
        """Read a bounded full-ad URL index, never its advert-body files."""
        try:
            if path.is_symlink():
                return set(), None, False
            safe = self._safe(path, self.evidence)
            if not safe.is_file() or safe.stat().st_size > _ANALYTICS_MAX_INDEX_BYTES:
                return set(), None, False
            value = json.loads(safe.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, StudioDataError):
            return set(), None, False
        if not isinstance(value, dict):
            return set(), None, False
        rows = value.get("records") if isinstance(value.get("records"), list) else value.get("ads")
        if not isinstance(rows, list) or len(rows) > _ANALYTICS_MAX_INDEX_ROWS:
            return set(), None, False
        urls: set[str] = set()
        rows_complete = True
        for row in rows:
            if not isinstance(row, dict):
                rows_complete = False
                continue
            url = _analytics_url(self._dashboard_row_value(row, "url", "source_url", "detail_url"))
            body_path = self._dashboard_row_value(row, "body_file", "body_path", "body")
            body_hash = self._dashboard_row_value(row, "body_sha256", "sha256")
            body_recorded = (row.get("counted_unique_full_ad") is True or
                             row.get("complete_body") is True and isinstance(body_hash, str) and
                             bool(re.fullmatch(r"[a-fA-F0-9]{64}", body_hash)) or
                             isinstance(body_path, str) and bool(body_path) and isinstance(body_hash, str) and
                             bool(re.fullmatch(r"[a-fA-F0-9]{64}", body_hash)))
            if not url or not body_recorded:
                rows_complete = False
                continue
            urls.add(url)
        declared = None
        for key in ("counted_unique_full_ads", "unique_full_ads", "unique_new_full_ads",
                    "unique_full_ad_count", "full_live_ads_reviewed"):
            parsed = _count_value(value.get(key))
            if isinstance(parsed, (int, float)) and not isinstance(parsed, bool) and parsed >= 0 and float(parsed).is_integer():
                declared = int(parsed)
                break
        return urls, declared, rows_complete

    def _dashboard_read_metrics(self, run: dict, run_dir: Path | None, *, detail: dict | None = None,
                                collect_read_events: bool = False) -> dict:
        """Collect exact indexed full-ad URLs and whether this run has full coverage."""
        from history import RunHistory

        detail = detail if isinstance(detail, dict) else RunHistory(self).enrich(run, detail=False)
        expected_total = (detail.get("summary_metrics") or {}).get("ads_reviewed")
        if isinstance(expected_total, bool) or not isinstance(expected_total, (int, float)) or expected_total < 0:
            expected_total = None
        elif not float(expected_total).is_integer():
            expected_total = None
        else:
            expected_total = int(expected_total)
        sources = self._sources(run_dir) if run_dir is not None else []
        lane_counts = {row["id"]: row.get("screened") for row in sources if isinstance(row, dict)}
        run_urls: set[str] = set()
        by_source: dict[str, set[str]] = {key: set() for key, _label in _SITES}
        source_complete: dict[str, bool] = {}
        index_found = 0
        missing_known = 0
        total_complete = True
        global_zero = expected_total == 0
        if run_dir is not None:
            for site_id, _label in _SITES:
                index_path = run_dir / site_id / "full_ads.index.json"
                lane_count = lane_counts.get(site_id)
                if index_path.exists() and not index_path.is_symlink():
                    index_found += 1
                    urls, declared, index_complete = self._dashboard_index_records(index_path)
                    run_urls.update(urls)
                    for url in urls:
                        platform = _analytics_platform(url)
                        if platform:
                            by_source[platform].add(url)
                    expected = lane_count if lane_count is not None else declared
                    site_complete = (index_complete and expected is not None and len(urls) == expected and
                                     (declared is None or len(urls) == declared))
                    if any(_analytics_platform(url) not in (None, site_id) for url in urls):
                        site_complete = False
                    if expected is not None and expected > len(urls):
                        missing_known += expected - len(urls)
                    if not index_complete and declared is not None and declared > len(urls):
                        missing_known += max(0, declared - len(urls) - (expected - len(urls) if expected is not None else 0))
                    source_complete[site_id] = site_complete
                elif lane_count == 0 or global_zero:
                    source_complete[site_id] = True
                else:
                    source_complete[site_id] = False
                    if isinstance(lane_count, (int, float)) and lane_count > 0:
                        missing_known += int(lane_count)
                if not source_complete[site_id]:
                    total_complete = False
        else:
            total_complete = expected_total == 0
            if expected_total and expected_total > 0:
                missing_known += expected_total
        if expected_total is not None:
            if len(run_urls) != expected_total:
                total_complete = False
                if expected_total > len(run_urls):
                    missing_known += expected_total - len(run_urls)
        elif run_urls:
            total_complete = False
        elif any(isinstance(value, (int, float)) and value > 0 for value in lane_counts.values()):
            total_complete = False
        if expected_total == 0 and run_urls:
            total_complete = False
        has_capture_index = bool(run_dir and any(
            (run_dir / prefix / site / "full_ads.json").is_file()
            for prefix in ("hunt", "") for site in ("hellowork", "france_travail", "linkedin", "apec", "wttj", "indeed")))
        capture = (RunHistory(self).verified_read_events(run_dir)
                   if collect_read_events and has_capture_index and run_dir is not None else
                   {"events": [], "partial": False, "partial_dates": []})
        for event in capture["events"]:
            url = event.get("canonical_url")
            if not url:
                continue
            run_urls.add(url)
            platform = event.get("source")
            if platform in by_source:
                by_source[platform].add(url)
        if capture.get("partial"):
            total_complete = False
        return {"urls": run_urls, "by_source": by_source,
                "source_complete": source_complete, "complete": total_complete,
                "index_found": index_found, "missing_known": missing_known,
                "unknown_reads": expected_total is None and not run_urls,
                "read_events": capture["events"], "read_partial": capture["partial"],
                "read_partial_dates": capture["partial_dates"]}

    def _dashboard_manual_shortlists(self) -> list[dict]:
        """Project only exact manual-audit acceptances bound to queue readback."""
        root = self.auto / "evidence" / "manual"
        try:
            safe_root = self._safe(root, self.auto / "evidence")
            directories = sorted((path for path in safe_root.iterdir()
                                  if path.is_dir() and not path.is_symlink()),
                                 key=lambda path: path.name, reverse=True)[:100]
        except (OSError, StudioDataError):
            return []
        events = []
        for directory in directories:
            audit_path = directory / "proposal-audit.json"
            readback_path = directory / "parent-acceptance-and-queue-readback.json"
            try:
                audit_safe = self._safe(audit_path, safe_root)
                readback_safe = self._safe(readback_path, safe_root)
                if (not audit_safe.is_file() or not readback_safe.is_file() or
                        audit_safe.stat().st_size > 2_000_000 or readback_safe.stat().st_size > 2_000_000):
                    continue
                audit_bytes, readback_bytes = audit_safe.read_bytes(), readback_safe.read_bytes()
                audit, readback = json.loads(audit_bytes), json.loads(readback_bytes)
            except (OSError, UnicodeError, ValueError, StudioDataError):
                continue
            if (not isinstance(audit, dict) or audit.get("kind") not in {
                    "requested_11_proposals_independent_hunt_audit", "saved_advert_queue_audit_v1"} or
                    not isinstance(readback, dict) or readback.get("kind") != "parent_review_and_studio_waiting_queue_readback" or
                    readback.get("audit_sha256") != hashlib.sha256(audit_bytes).hexdigest()):
                continue
            profile_binding = audit.get("profile")
            rules_binding = audit.get("rules")
            if (not isinstance(profile_binding, dict) or
                    not isinstance(profile_binding.get("sha256"), str) or
                    re.fullmatch(r"[0-9a-fA-F]{64}", profile_binding["sha256"]) is None or
                    not isinstance(rules_binding, dict) or
                    not isinstance(rules_binding.get("sha256"), str) or
                    re.fullmatch(r"[0-9a-fA-F]{64}", rules_binding["sha256"]) is None):
                continue
            accepted_indices = audit.get("acceptance_indices")
            items = audit.get("items")
            accepted = readback.get("accepted")
            at = readback.get("checked_at")
            if (not isinstance(accepted_indices, list) or not isinstance(items, list) or
                    not isinstance(accepted, list) or len(accepted_indices) != len(accepted) or
                    audit.get("accepted_for_waiting_queue") != len(accepted_indices) or
                    not isinstance(at, str) or _time(at) is None):
                continue
            expected = []
            for index in accepted_indices:
                if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(items):
                    expected = []
                    break
                item = items[index]
                if not isinstance(item, dict) or item.get("proposal_index") != index or item.get("decision") != "ACCEPTED_FOR_WAITING_QUEUE":
                    expected = []
                    break
                url = _analytics_url(item.get("url"))
                if not url:
                    expected = []
                    break
                expected.append((url, item))
            if len(expected) != len(accepted):
                continue
            checked = set()
            receipt_events = []
            for (url, item), queue_item in zip(expected, accepted):
                if (not isinstance(queue_item, dict) or queue_item.get("duplicate") is not False or
                        not queue_item.get("queue_id") or _analytics_url(queue_item.get("url")) != url or url in checked):
                    checked = set()
                    break
                checked.add(url)
                receipt_events.append({"canonical_url": url, "url": url, "accepted_at": at,
                    "company": item.get("company"), "title": item.get("job_title") or item.get("title"),
                    "source": _analytics_platform(url), "queue_id": str(queue_item["queue_id"]),
                    "basis": "manual_independent_audit_and_queue_readback"})
            if checked and len(checked) == len(expected):
                events.extend(receipt_events)
        deduped: dict[str, dict] = {}
        for event in events:
            prior = deduped.get(event["canonical_url"])
            if prior is None or _time(event["accepted_at"]) < _time(prior["accepted_at"]):
                deduped[event["canonical_url"]] = event
        return list(deduped.values())

    def _dashboard_projection_stat_signature(self, runs: list[dict]) -> tuple:
        """Cheap file-stat keys invalidate the slower receipt verifier on source changes."""
        signature = ["verified-read-events-v1"]
        code_paths = (Path(__file__).resolve(),
                      Path(__file__).resolve().with_name("history.py"),
                      Path(__file__).resolve().parents[1] / "Master" / "auto" / "local" / "full_ad_evidence.py")
        for code_path in code_paths:
            try:
                code = code_path.read_bytes()
                signature.append(("code", str(code_path), hashlib.sha256(code).hexdigest()))
            except OSError:
                signature.append(("code", str(code_path), None))
        batches = [row for row in runs if isinstance(row, dict) and row.get("kind") == "batch"]
        for run in batches[:_ANALYTICS_MAX_BATCHES]:
            run_dir = self.evidence / str(run.get("id", ""))
            for prefix in ("hunt", ""):
                for site in ("hellowork", "france_travail", "linkedin", "apec", "wttj", "indeed"):
                    path = run_dir / prefix / site / "full_ads.json"
                    try:
                        safe = self._safe(path, self.evidence)
                        stat = safe.stat()
                        signature.append((str(path), stat.st_size, stat.st_mtime_ns))
                    except (OSError, StudioDataError):
                        continue
            # The newest run's linked captures and screens can change after the
            # aggregate index is written, so include their cheap stat signatures.
            if run is not batches[0]:
                continue
            for prefix in ("hunt", ""):
                for site in ("hellowork", "france_travail", "linkedin", "apec", "wttj", "indeed"):
                    lane = run_dir / prefix / site
                    index_path = lane / "full_ads.json"
                    try:
                        safe_index = self._safe(index_path, self.evidence)
                        if safe_index.stat().st_size > 1_000_000:
                            continue
                        indexed = json.loads(safe_index.read_text(encoding="utf-8"))
                        rows = indexed if isinstance(indexed, list) else indexed.get("ads", []) if isinstance(indexed, dict) else []
                    except (OSError, UnicodeError, ValueError, StudioDataError):
                        continue
                    for row in rows[:1_000] if isinstance(rows, list) else []:
                        paths = row.get("paths") if isinstance(row, dict) and isinstance(row.get("paths"), dict) else {}
                        names = [paths.get(key) for key in ("capture_json", "ad_json", "raw_html", "body_text")]
                        ad_name = paths.get("ad_json")
                        if isinstance(ad_name, str):
                            names.append(str(Path(ad_name).parent / "screening.json"))
                        for name in names:
                            if not isinstance(name, str) or not name:
                                continue
                            try:
                                target = self._safe(lane / name, self.evidence)
                                if not target.is_relative_to(lane.resolve()):
                                    continue
                                stat = target.stat()
                                signature.append((str(target), stat.st_size, stat.st_mtime_ns))
                            except (OSError, StudioDataError):
                                continue
        return tuple(signature)

    def _saved_advert_counts(self) -> dict:
        """Read the compact public inventory; never scan or write history on refresh."""
        local_agents = Path(__file__).resolve().parents[1] / "Master" / "auto" / "local"
        if str(local_agents) not in sys.path:
            sys.path.insert(0, str(local_agents))
        try:
            from advert_inventory import inventory_path, read_summary

            return read_summary(inventory_path(self.root))
        except (ImportError, OSError, ValueError, sqlite3.Error):
            return {"available": False, "unique_postings": None, "saved_captures": None,
                    "repeat_captures": None, "latest_run": None, "by_website": [],
                    "coverage_note": "Saved advert totals are unavailable."}

    def _dashboard_analytics(self, roles: list[dict], runs: list[dict]) -> dict:
        saved_adverts = self._saved_advert_counts()
        historical_adjustments = load_dashboard_adjustments(self.root / "Studio")
        manual_shortlists = self._dashboard_manual_shortlists()
        hark_projection = HarkMetrics(self.root)
        projection_stats = self._dashboard_projection_stat_signature(runs)
        signature = (
            tuple((row.get("folder"), row.get("updated_at"), row.get("source_url"),
                   row.get("cv_available"), row.get("canonical_cv_available"), row.get("cv_built_at"))
                  for row in roles if isinstance(row, dict)),
            tuple((row.get("id"), row.get("updated_at"), row.get("status"))
                  for row in runs if isinstance(row, dict)),
            json.dumps(manual_shortlists, ensure_ascii=False, sort_keys=True),
            projection_stats,
            json.dumps(historical_adjustments, ensure_ascii=False, sort_keys=True),
            json.dumps(saved_adverts, ensure_ascii=False, sort_keys=True),
            hark_projection.signature(), public_capture_signature(self.root),
        )
        now_mono = time.monotonic()
        with self._dashboard_cache_lock:
            if (self._dashboard_cache_value is not None and signature == self._dashboard_cache_signature and
                    now_mono - self._dashboard_cache_at < _ANALYTICS_CACHE_SECONDS):
                return copy.deepcopy(self._dashboard_cache_value)

        from history import RunHistory

        hark = hark_projection.load()
        public_captures = public_capture_events(self.root)
        history = RunHistory(self)
        role_rows = [row for row in roles if isinstance(row, dict)]
        by_basename: dict[str, list[dict]] = {}
        jd_meta: dict[str, dict] = {}
        for role in role_rows:
            key = self._dashboard_path_key(role.get("folder"))
            if key:
                by_basename.setdefault(key, []).append(role)
                jd_meta[role.get("id", key)] = self._dashboard_jd_metadata(role)

        shortlisted: list[dict] = []
        candidate_keys: list[set[str]] = []
        missing_sealed_identity = 0
        role_path_conflicts = 0

        def merge_candidate(candidate: dict) -> None:
            nonlocal role_path_conflicts
            keys = set(candidate.get("identity_keys") or [])
            matched = []
            for index, prior in enumerate(shortlisted):
                common = keys & candidate_keys[index]
                if not common:
                    continue
                path_common = any(key.startswith("path:") for key in common)
                url_common = any(key.startswith("url:") for key in common)
                if path_common and not url_common:
                    company_left, company_right = _analytics_identity(prior.get("company")), _analytics_identity(candidate.get("company"))
                    title_left, title_right = _analytics_identity(prior.get("title")), _analytics_identity(candidate.get("title"))
                    if (company_left and company_right and company_left != company_right) or (title_left and title_right and title_left != title_right):
                        role_path_conflicts += 1
                        continue
                matched.append(index)
            if not matched:
                shortlisted.append(candidate)
                candidate_keys.append(keys)
                return
            first = matched[0]
            for index in reversed(matched[1:]):
                other = shortlisted.pop(index)
                candidate_keys[first].update(candidate_keys.pop(index))
                for field in ("company", "title", "source_url", "source_label", "office_location", "office_location_label"):
                    shortlisted[first][field] = shortlisted[first].get(field) or other.get(field)
                shortlisted[first]["basis"] = set(shortlisted[first].get("basis") or []) | set(other.get("basis") or [])
            target = shortlisted[first]
            for field in ("company", "title", "source_url", "source_label", "office_location", "office_location_label"):
                target[field] = target.get(field) or candidate.get(field)
            target["basis"] = set(target.get("basis") or []) | set(candidate.get("basis") or [])
            candidate_keys[first].update(keys)

        def metadata_for_sealed(row: dict) -> dict | None:
            basename = self._dashboard_path_key(row.get("role_path"))
            matches = list(by_basename.get(basename, [])) if basename else []
            company_key, title_key = _analytics_identity(row.get("company")), _analytics_identity(row.get("title"))
            if len(matches) > 1 and company_key:
                matches = [item for item in matches if _analytics_identity(item.get("company")) == company_key]
            if len(matches) > 1 and title_key:
                matches = [item for item in matches if _analytics_identity(item.get("title")) == title_key]
            role = matches[0] if len(matches) == 1 else None
            meta = jd_meta.get(role.get("id", ""), {}) if role else {}
            has_jd_identity = bool(meta.get("jd_role_evidence"))
            company = row.get("company") or (meta.get("jd_company") if has_jd_identity else None)
            title = row.get("title") or (meta.get("jd_title") if has_jd_identity else None)
            source_url = meta.get("source_url") if meta else None
            source_label = meta.get("source_label") if meta else None
            office_location = meta.get("office_location") if meta else None
            path_key = "path:" + basename if basename else ""
            url_key = "url:" + _analytics_url(source_url) if source_url and _analytics_url(source_url) else ""
            return {"identity_keys": {key for key in (path_key, url_key) if key}, "role_path": row.get("role_path"),
                    "company": company, "title": title, "source_url": source_url,
                    "source_label": source_label, "office_location": office_location,
                    "office_location_label": _office_location_label(office_location), "basis": {"sealed_acceptance"}}

        batch_runs = [row for row in runs if isinstance(row, dict) and row.get("kind") == "batch"]
        selected_batches = batch_runs[:_ANALYTICS_MAX_BATCHES]
        omitted_batches = max(0, len(batch_runs) - len(selected_batches))
        read_urls: set[str] = set()
        read_events: dict[str, dict] = {}
        read_partial_dates: set[str] = set()
        read_events_partial = False
        read_event_unscanned_dates: set[str] = set()
        read_event_dates_unavailable = False
        read_by_source: dict[str, set[str]] = {key: set() for key, _label in _SITES}
        source_coverage = {key: True for key, _label in _SITES}
        overall_read_coverage = omitted_batches == 0
        read_missing_known = 0
        read_unknown_runs = 0
        shortlist_runs = [row for row in runs if isinstance(row, dict)]
        incomplete_read_runs = 0
        missing_sealed_runs = 0
        for run in shortlist_runs:
            if run.get("kind") == "batch" and selected_batches and run is not selected_batches[0]:
                start = _time(run.get("started_at"))
                end = _time(run.get("ended_at")) or start
                if start and end and end >= start and (end - start) <= timedelta(days=31):
                    first_day = start.astimezone(ZoneInfo("Europe/Paris")).date()
                    last_day = end.astimezone(ZoneInfo("Europe/Paris")).date()
                    current_day = first_day
                    while current_day <= last_day:
                        read_event_unscanned_dates.add(current_day.isoformat())
                        current_day += timedelta(days=1)
                else:
                    recorded_day = run.get("date")
                    if isinstance(recorded_day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", recorded_day):
                        read_event_unscanned_dates.add(recorded_day)
                    else:
                        read_event_dates_unavailable = True
            try:
                detail = history.enrich(run, detail=False)
            except (OSError, StudioDataError, ValueError, TypeError, KeyError):
                detail = {"summary_metrics": {}, "jobs": []}
            accepted = detail.get("jobs") if isinstance(detail.get("jobs"), list) else []
            metrics = detail.get("summary_metrics") if isinstance(detail.get("summary_metrics"), dict) else {}
            sealed_count = metrics.get("jobs_shortlisted")
            if run.get("kind") != "legacy_stage":
                for row in accepted:
                    if isinstance(row, dict) and row.get("role_path"):
                        candidate = metadata_for_sealed(row)
                        if candidate:
                            merge_candidate(candidate)
                if isinstance(sealed_count, int) and sealed_count > len(accepted):
                    missing_sealed_identity += sealed_count - len(accepted)
                elif sealed_count is None:
                    missing_sealed_runs += 1
            else:
                hunt_stage = any(isinstance(stage, dict) and stage.get("id") == "hunt"
                                 for stage in run.get("stages", []))
                if hunt_stage:
                    legacy_ads = metrics.get("ads_reviewed")
                    if legacy_ads is None:
                        read_unknown_runs += 1
                        incomplete_read_runs += 1
                        overall_read_coverage = False
                        source_coverage = {key: False for key in source_coverage}
                    elif legacy_ads > 0:
                        read_missing_known += int(legacy_ads)
                        incomplete_read_runs += 1
                        overall_read_coverage = False
                        source_coverage = {key: False for key in source_coverage}

            if run.get("kind") == "batch" and run in selected_batches:
                run_dir = self.evidence / str(run.get("id", ""))
                if run_dir.is_symlink() or not run_dir.is_dir():
                    evidence = {"urls": set(), "by_source": {}, "source_complete": {},
                                "complete": False, "index_found": 0, "missing_known": 0,
                                "unknown_reads": True}
                else:
                    evidence = self._dashboard_read_metrics(
                        run, run_dir, detail=detail, collect_read_events=(run is selected_batches[0]))
                read_urls.update(evidence["urls"])
                for event in evidence.get("read_events", []):
                    url = event.get("canonical_url")
                    if url and (url not in read_events or
                                (_time(event.get("captured_at")) or datetime.max.replace(tzinfo=timezone.utc)) <
                                (_time(read_events[url].get("captured_at")) or datetime.max.replace(tzinfo=timezone.utc))):
                        read_events[url] = event
                read_partial_dates.update(evidence.get("read_partial_dates", []))
                read_events_partial = read_events_partial or evidence.get("read_partial") is True
                read_missing_known += evidence.get("missing_known", 0)
                if evidence.get("unknown_reads"):
                    read_unknown_runs += 1
                if not evidence.get("complete"):
                    overall_read_coverage = False
                    incomplete_read_runs += 1
                for site_id, _label in _SITES:
                    read_by_source[site_id].update((evidence.get("by_source") or {}).get(site_id, set()))
                    if not (evidence.get("source_complete") or {}).get(site_id, evidence.get("complete") is True):
                        source_coverage[site_id] = False

        # Saved local CV packages are a legacy shortlist proxy. Each remains
        # visibly distinct from a sealed acceptance in the basis set.
        for role in role_rows:
            if role.get("cv_available") is not True:
                continue
            meta = jd_meta.get(role.get("id", ""), {})
            if not meta.get("jd_role_evidence"):
                continue
            folder = role.get("folder")
            basename = self._dashboard_path_key(folder)
            source_url = meta.get("source_url")
            source_label = meta.get("source_label")
            office_location = meta.get("office_location")
            identity_keys = {"path:" + basename} if basename else set()
            canonical = _analytics_url(source_url)
            if canonical:
                identity_keys.add("url:" + canonical)
            merge_candidate({"identity_keys": identity_keys, "role_path": folder,
                "company": meta.get("jd_company"), "title": meta.get("jd_title"),
                "source_url": source_url, "source_label": source_label,
                "office_location": office_location,
                "office_location_label": _office_location_label(office_location),
                "basis": {"legacy_completed_cv_package"}})

        for candidate in shortlisted:
            canonical = _analytics_url(candidate.get("source_url"))
            if canonical:
                platform = _analytics_platform(candidate.get("source_url")) or _analytics_platform(candidate.get("source_label"))
                candidate["platform"] = platform
                candidate["canonical_url"] = canonical
            else:
                candidate["platform"] = None
                candidate["canonical_url"] = None

        shortlist_by_source = {key: 0 for key, _label in _SITES}
        shortlist_complete = not (missing_sealed_identity or missing_sealed_runs or omitted_batches)
        source_shortlists_complete = {key: shortlist_complete for key, _label in _SITES}
        companies: dict[str, str] = {}
        locations: dict[str, int] = {}
        missing_company = missing_url = missing_location = 0
        all_shortlist_urls_compatible = shortlist_complete
        for candidate in shortlisted:
            company = candidate.get("company")
            company_key = _analytics_identity(company)
            if company_key:
                companies.setdefault(company_key, str(company).strip())
            else:
                missing_company += 1
            canonical = candidate.get("canonical_url")
            if not canonical:
                missing_url += 1
                all_shortlist_urls_compatible = False
                for site_id in source_shortlists_complete:
                    source_shortlists_complete[site_id] = False
            elif canonical not in read_urls:
                all_shortlist_urls_compatible = False
                site_id = candidate.get("platform")
                if site_id:
                    source_shortlists_complete[site_id] = False
            site_id = candidate.get("platform")
            if site_id:
                shortlist_by_source[site_id] += 1
            else:
                for key in source_shortlists_complete:
                    source_shortlists_complete[key] = False
            office = candidate.get("office_location_label")
            if office:
                locations[office] = locations.get(office, 0) + 1
            else:
                missing_location += 1

        source_rows = []
        for site_id, label in _SITES:
            count = shortlist_by_source[site_id]
            if count == 0 and not read_by_source[site_id]:
                continue
            denominator = len(read_by_source[site_id])
            complete = (source_coverage[site_id] and source_shortlists_complete[site_id] and
                        denominator > 0 and count <= denominator)
            source_rows.append({"id": site_id, "label": label, "shortlisted": count,
                "unique_jobs_read": denominator or None,
                "rate_pct": round(count * 100 / denominator, 1) if complete else None})
        source_rows.sort(key=lambda row: (-row["shortlisted"], row["label"].casefold()))
        source_rows = source_rows[:5]
        top_locations = [{"label": label, "count": count} for label, count in
                         sorted(locations.items(), key=lambda item: (-item[1], item[0].casefold()))[:5]]

        known_shortlisted = len(shortlisted)
        known_shortlisted_companies = len(companies)
        total_shortlisted = known_shortlisted if shortlist_complete else None
        shortlisted_companies = known_shortlisted_companies if shortlist_complete and not missing_company else None
        overall_rate = None
        if (overall_read_coverage and total_shortlisted is not None and all_shortlist_urls_compatible and
                len(read_urls) > 0 and total_shortlisted <= len(read_urls)):
            overall_rate = round(total_shortlisted * 100 / len(read_urls), 1)
        missing_evidence = {
            "shortlist_roles_without_identity": missing_sealed_identity,
            "runs_without_sealed_shortlist_count": missing_sealed_runs,
            "shortlist_roles_without_company": missing_company,
            "shortlist_roles_without_source_url": missing_url,
            "shortlist_roles_without_jd_location": missing_location,
            "read_count_without_indexed_urls": read_missing_known,
            "runs_without_complete_read_coverage": incomplete_read_runs,
            "runs_omitted_by_bound": omitted_batches,
            "historical_hunt_counts_without_urls": read_unknown_runs,
            "legacy_cv_packages_without_jd_role_evidence": sum(1 for role in role_rows
                if role.get("cv_available") is True and not jd_meta.get(role.get("id", ""), {}).get("jd_role_evidence")),
            "shortlist_roles_without_source_platform": sum(1 for candidate in shortlisted if not candidate.get("platform")),
            "role_path_name_collisions": role_path_conflicts,
        }
        if omitted_batches:
            coverage_note = (f"Analytics read the most recent {len(selected_batches)} of {len(batch_runs)} saved batches; older history is omitted. "
                             f"{len(read_urls)} distinct full-ad URLs have saved metadata evidence.")
        elif read_missing_known or read_unknown_runs or not overall_read_coverage:
            coverage_note = (f"{len(read_urls)} distinct full-ad URLs have saved metadata evidence. Some saved run totals lack a complete URL index, so rates are withheld where the read population is uncertain.")
        elif missing_sealed_runs or omitted_batches:
            coverage_note = "Some saved batches lack sealed accepted-role totals or were omitted by the history bound, so shortlist totals and related rates are withheld."
        elif missing_sealed_identity or missing_url or missing_company or missing_location:
            coverage_note = "Some saved shortlist records lack role, company, source, or JD-location evidence; affected totals or rates are withheld."
        else:
            coverage_note = "Counts use saved run URL indexes, sealed accepted roles, and legacy local CV packages; no live adverts or inbox were checked."
        public_collection = list(public_captures["events"])
        public_collection.extend({"canonical_url": url, "url": url,
            "captured_at": (read_events.get(url) or {}).get("captured_at"),
            "source": _analytics_platform(url), "basis": "saved_public_full_ad_evidence"}
            for url in read_urls)
        collection_events = _merge_collection_events(public_collection + hark["events"])
        collection_partial = (not overall_read_coverage or bool(omitted_batches) or
            bool(public_captures["omitted_jobs"]) or public_captures["read_failed"] or
            saved_adverts.get("available") is True and not public_captures["available"] or
            bool(hark["metadata"]["invalid_saved_sources"]) or
            bool(hark["metadata"]["omitted_source_directories"]))
        result = {"saved_adverts": saved_adverts,
            "shortlisted_companies": shortlisted_companies,
            "known_shortlisted_companies": known_shortlisted_companies,
            "shortlisted_companies_complete": shortlist_complete and not missing_company,
            "total_shortlisted": total_shortlisted,
            "known_shortlisted": known_shortlisted,
            "total_shortlisted_complete": shortlist_complete,
            "unique_jobs_read": len(read_urls) if read_urls else None,
            "read_events": list(read_events.values()),
            "read_event_partial_dates": sorted(read_partial_dates),
            "read_events_partial": read_events_partial,
            "read_event_unscanned_dates": sorted(read_event_unscanned_dates),
            "read_event_dates_unavailable": read_event_dates_unavailable,
            "manual_shortlists": manual_shortlists,
            "sheet_shortlists": hark["shortlists"],
            "hark_feed": hark["metadata"],
            "unique_jobs_collected": len(collection_events) if collection_events or public_captures["available"] or hark["metadata"]["available"] else None,
            "collection_events": collection_events,
            "collection_events_partial": collection_partial,
            "collection_undated_jobs": sum(_time(event.get("captured_at")) is None for event in collection_events),
            "collection_sources": {"public_saved_jobs": len(_merge_collection_events(public_collection)),
                                   "hark_saved_jobs": hark["metadata"]["unique_jobs"],
                                   "public_inventory_available": public_captures["available"],
                                   "public_inventory_read_failed": public_captures["read_failed"],
                                   "public_inventory_omitted_jobs": public_captures["omitted_jobs"]},
            "collection_note": "Unique identities from indexed saved public captures and supplied Hark JD text. Cross-source URL and posting-ID aliases count once. Hark text presence does not prove an independent full-ad read or a live vacancy. Historical read estimates are kept separately because their overlap is unknown.",
            "shortlist_rate_pct": overall_rate,
            "sources": source_rows, "locations": top_locations,
            "scope_note": "All saved history in this workspace: sealed Hunt Audit accepted roles plus legacy roles with a saved CV PDF. Raw proposals alone are excluded.",
            "coverage_note": coverage_note, "missing_evidence": missing_evidence,
            "historical_adjustments": historical_adjustments}
        with self._dashboard_cache_lock:
            self._dashboard_cache_signature = signature
            self._dashboard_cache_value = copy.deepcopy(result)
            self._dashboard_cache_at = time.monotonic()
        return result

    def _sheet_rejections(self) -> dict | None:
        """Rejections tracked in the Hark sheet (written by the pipeline's sheet-rejections step)."""
        saved = self._json(self.root / "Studio" / ".local" / "sheet-rejections.json")
        if not isinstance(saved, dict) or not isinstance(saved.get("count"), int) or saved["count"] < 0:
            return None
        return {key: saved.get(key) for key in ("count", "automated", "from_person", "updated_at", "source")}

    def _gemini_accuracy(self) -> dict | None:
        """Gemini pipeline accuracy, recorded by the daily Claude review (`gemini_accuracy.py record`)."""
        import gemini_accuracy
        return gemini_accuracy.load(self.root / "Studio" / ".local" / "gemini-accuracy.json")

    def _hark_kpi(self) -> dict | None:
        """Hark's daily audit KPIs, saved by `python3 hark_kpi.py import` (read-only here)."""
        import hark_kpi
        return hark_kpi.load_snapshot(self.root / "Studio" / ".local" / "hark-kpi-audit.json")

    def _firecrawl_kpi(self) -> dict | None:
        """Second feed's daily audit KPIs. 5 Nov 2026 (Daksh): that feed is removed, so Studio shows none."""
        return None
        import hark_kpi
        return hark_kpi.load_snapshot(self.root / "Studio" / ".local" / "firecrawl-kpi-audit.json")

    def overview(self, *, include_analytics: bool = True) -> dict:
        roles = self.roles()
        runs = self.runs()["items"]
        batches = [r for r in runs if r["kind"] == "batch"]
        latest = batches[0] if batches else (runs[0] if runs else None)
        tracker, rows = self._tracker()
        live = self.live_pipeline()
        active = self.active_pipeline(observed_live=live)
        cv_count = sum(r["cv_available"] for r in roles["items"])
        applied = sum(str(r.get("Applied", "")).lower() == "yes" for r in rows) if tracker["available"] else None
        metrics = [
            {"id": "local_cvs", "label": "CVs in your library", "value": cv_count, "note": "Local PDFs found. Approval is separate."},
            {"id": "roles", "label": "Role folders", "value": roles["total"], "note": "Includes applied and inactive folders."},
            {"id": "applied", "label": "Applications recorded", "value": applied,
             "note": f"Saved tracker snapshot · {tracker['date']}" if tracker["available"] else "No saved tracker snapshot available."},
            {"id": "runs", "label": "Saved batches", "value": len(batches), "note": "Execution evidence, including held batches."},
        ]
        stages = latest["stages"] if latest else self._stages({}, {}, {"config": self._json(self.root / "Master/auto/local/pipeline-routing.json") or {}}, policy_version=4)
        source_rows = self._sources(self.evidence / latest["id"] if latest and latest["kind"] == "batch" else None)
        if live["active"]:
            stages, source_rows = live["stages"], live["sources"]
        return {"generated_at": _iso(), "workspace": {"name": "CV Studio", "root": str(self.root)},
                "metrics": metrics, "pipeline": {"stages": stages, "latest_run": latest,
                    "sources": source_rows,
                    "note": "Observed external chat activity; saved completion receipts remain separate." if live["active"] else
                            "Latest saved batch evidence. Current model routes are shown when receipts do not name a model."},
                "latest_run": latest, "active_pipeline": active, "live_pipeline": live, "tracker": tracker,
                "dashboard_analytics": self._dashboard_analytics(roles["items"], runs) if include_analytics else None,
                "sheet_rejections": self._sheet_rejections(),
                "hark_kpi": self._hark_kpi(),
                "firecrawl_kpi": self._firecrawl_kpi(),
                "gemini_accuracy": self._gemini_accuracy(),
                "recent_roles": roles["items"][:6], "recent_runs": runs[:6],
                "roles": roles, "runs": {"items": runs, "total": len(runs)},
                "documents": self.documents(active=active), "settings": self.settings(), "master_cvs": self._master_cvs(),
                "connections": {"mode": "local", "online_tracker": "not_checked", "inbox": "not_checked"}}

    @staticmethod
    def _protected(content: str, document_id: str) -> list[tuple[str, str]]:
        if document_id == "profile":
            match = re.search(r"(?m)^##\s+15\.\s+.*$", content)
            return [("Approved CV bank mirror (section 15)", content[match.start():])] if match else []
        if document_id == "search":
            # Editable: employer priorities, source list and keywords. Existing policy
            # sections retain their exact bytes; Studio does not rewrite validation gates.
            blocks = []
            headings = list(re.finditer(r"(?m)^##\s+(.+)$", content))
            for index, match in enumerate(headings):
                heading = match.group(1).strip()
                if heading.startswith(("Scheduled inbox", "Current shortlisting", "2.", "4.", "5.")):
                    end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
                    blocks.append((heading, content[match.start():end]))
            return blocks
        return []

    def documents(self, *, active: dict | None = None) -> dict:
        items = []
        active = self.active_pipeline() if active is None else active
        for key in _DOCUMENTS:
            try:
                item = self._document(key)
                item.pop("content")
                item["editing_paused"] = bool(active["edit_locked"] and item["editable"])
                items.append(item)
            except StudioDataError:
                continue
        return {"items": items, "total": len(items), "active_pipeline": active}

    def _document(self, document_id: str) -> dict:
        spec = _DOCUMENTS.get(document_id)
        if spec is None:
            raise StudioDataError("This document is not available in Studio.", "not_found", 404)
        relative, title, editable, description = spec
        path = self._safe(self.root / relative)
        content = self._text(path)
        if content is None:
            raise StudioDataError("This document could not be read.", "not_found", 404)
        return {"id": document_id, "title": title, "path": relative, "editable": editable,
                "description": description, "revision": _sha(content.encode("utf-8")),
                "updated_at": self._modified(path), "content": content,
                "protected_sections": [name for name, _ in self._protected(content, document_id)],
                "format": "json" if path.suffix == ".json" else "markdown"}

    def document(self, document_id: str) -> dict:
        item = self._document(document_id)
        active = self.active_pipeline()
        item["editing_paused"] = bool(item["editable"] and active["edit_locked"])
        item["active_pipeline"] = active
        return item

    @contextmanager
    def _write_lock(self) -> Iterator[None]:
        with _EDIT_LOCK:
            if (self.root / "Studio").is_symlink() or self.local.is_symlink():
                raise StudioDataError("The local settings folder must be a regular directory.", "unsafe_path", 403)
            self._safe(self.local)
            self.local.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.local, 0o700)
            lock = self.local / "edit.lock"
            if lock.is_symlink():
                raise StudioDataError("The local edit lock is unsafe.", "unsafe_path", 403)
            self._safe(lock, self.local)
            with lock.open("a+b") as stream:
                os.fchmod(stream.fileno(), 0o600)
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _atomic(self, path: Path, content: bytes, mode: int = 0o600) -> None:
        self._safe(path)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def save_document(self, document_id: str, content: str, revision: str) -> dict:
        if document_id not in _DOCUMENTS:
            raise StudioDataError("This document is not available in Studio.", "not_found", 404)
        if not _DOCUMENTS[document_id][2]:
            raise StudioDataError("This document is read-only in Studio.", "read_only", 403)
        if not isinstance(content, str) or not content.strip() or "\x00" in content or len(content.encode("utf-8")) > 512_000:
            raise StudioDataError("Enter a non-empty document smaller than 512 KB.")
        if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
            raise StudioDataError("Reload this document before saving.", "stale_revision", 409)
        active = self.active_pipeline()
        if active["edit_locked"]:
            raise StudioDataError(active["reason"], "pipeline_active", 409)
        with self._write_lock():
            current = self._document(document_id)
            if current["revision"] != revision:
                raise StudioDataError("This file changed after you opened it. Reload it before saving; your draft has not been written.", "stale_revision", 409)
            previous_blocks = self._protected(current["content"], document_id)
            next_blocks = self._protected(content, document_id)
            if previous_blocks != next_blocks:
                raise StudioDataError("A protected section changed. Keep the listed validation rules and generated CV bank mirror unchanged.", "protected_section", 409)
            if content == current["content"]:
                return {**current, "saved": False, "unchanged": True, "backup": None}
            # Recheck activity and revision immediately before replacement.
            active = self.active_pipeline()
            if active["edit_locked"]:
                raise StudioDataError(active["reason"], "pipeline_active", 409)
            path = self.root / _DOCUMENTS[document_id][0]
            if path.is_symlink():
                raise StudioDataError("Source documents must be regular files.", "unsafe_path", 403)
            path = self._safe(path)
            original = path.read_bytes()
            if _sha(original) != revision:
                raise StudioDataError("This file changed during your edit. Reload it before saving.", "stale_revision", 409)
            backup_dir = self._safe(self.local / "backups", self.local)
            backup_dir.mkdir(mode=0o700, exist_ok=True)
            stamp = _now().strftime("%Y%m%dT%H%M%S.%fZ")
            backup = backup_dir / f"{stamp}-{document_id}-{revision[:12]}.md"
            self._atomic(backup, original)
            new_revision = _sha(content.encode("utf-8"))
            journal = self.local / "edit-history.jsonl"
            if journal.is_symlink():
                raise StudioDataError("The local edit history is unsafe.", "unsafe_path", 403)
            self._safe(journal, self.local)
            event = {"at": _iso(), "document": document_id, "path": current["path"], "before": revision,
                     "after": new_revision, "backup": self._relative(backup), "status": "prepared"}
            # Record the recoverable intent before touching the source.
            self._journal(journal, event)
            self._atomic(path, content.encode("utf-8"), path.stat().st_mode & 0o777)
            self._journal(journal, {**event, "at": _iso(), "status": "saved"})
            return {**self._document(document_id), "saved": True, "unchanged": False, "backup": self._relative(backup)}

    def _journal(self, path: Path, event: dict) -> None:
        if path.is_symlink():
            raise StudioDataError("The local edit history is unsafe.", "unsafe_path", 403)
        with path.open("a", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def settings(self) -> dict:
        stored = self._json(self.local / "settings.json")
        result = dict(_SETTINGS)
        if isinstance(stored, dict):
            for key, value in stored.items():
                if self._valid_setting(key, value):
                    result[key] = value
        return {**result, "scope": "local_ui", "schedule": self._schedule(),
                "note": "These preferences affect this local interface. They do not change the pipeline, schedule or model routing."}

    def _schedule(self) -> dict:
        unavailable = {"available": False, "editable": False, "status": "not_configured",
                       "label": "No saved schedule available", "timezone": "Europe/Paris"}
        if self.schedule_path is None:
            return unavailable
        try:
            raw = self.schedule_path.read_text(encoding="utf-8")
            try:
                import tomllib
                data = tomllib.loads(raw)
            except ImportError:
                # Python 3.9 ships on older Macs. All whitelisted saved fields
                # are basic strings/integers; never parse or return the prompt.
                data = {}
                for key in ("name", "rrule", "status", "model", "reasoning_effort", "updated_at"):
                    match = re.search(rf"(?m)^{key}\s*=\s*(.+)$", raw)
                    if match:
                        try:
                            data[key] = json.loads(match.group(1))
                        except ValueError:
                            pass
        except (OSError, ValueError):
            return {**unavailable, "status": "unavailable"}
        rule = str(data.get("rrule") or "")
        fields = dict(part.split("=", 1) for part in rule.removeprefix("RRULE:").split(";") if "=" in part)
        days = fields.get("BYDAY", "")
        hour, minute = fields.get("BYHOUR"), fields.get("BYMINUTE")
        label = "Saved schedule"
        if days == "MO,TU,WE,TH,FR" and hour and minute and hour.isdigit() and minute.isdigit() and int(hour) < 24 and int(minute) < 60:
            label = f"Weekdays at {int(hour):02d}:{int(minute):02d} · Paris"
        return {"available": True, "editable": False, "name": str(data.get("name") or "CV Weekday Pipeline"),
                "status": str(data.get("status") or "unknown").lower(), "label": label,
                "timezone": "Europe/Paris", "model": data.get("model"),
                "reasoning_effort": data.get("reasoning_effort"), "updated_at": data.get("updated_at"),
                "note": "Saved scheduler configuration. This is not proof that a run executed."}

    @staticmethod
    def _valid_setting(key: str, value: Any) -> bool:
        if key == "theme":
            return value in ("light", "dark", "system")
        if key == "density":
            return value in ("comfortable", "compact")
        if key == "refresh_seconds":
            return type(value) is int and value in (5, 15, 30, 60)
        return False

    def save_settings(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or not payload or any(not self._valid_setting(k, v) for k, v in payload.items()):
            raise StudioDataError("Choose a supported theme, spacing and refresh interval. Pipeline settings cannot be changed here.")
        with self._write_lock():
            settings = {k: self.settings()[k] for k in _SETTINGS}
            settings.update(payload)
            self._atomic(self.local / "settings.json", (json.dumps(settings, indent=2) + "\n").encode("utf-8"))
        return self.settings()
