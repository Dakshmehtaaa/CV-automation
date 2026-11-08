"""Bounded, read-only run history assembled from saved pipeline receipts.

``RunHistory(data).runs()`` keeps StudioData's inventory and adds compact metrics.
``.run(id)`` first uses StudioData's ID resolver, then adds seven stage_metrics,
builds, summary_metrics, timing, usage, models, observations and failures.  This
module never imports a pipeline runner, follows receipt references, or treats a
coordinator turn or a supervised command as a completed pipeline stage.

Null means the fact was not saved.  Account percentage changes are observations,
never exact task costs.  A CV clock ends at artifact completion, before audit or
sync; repairs retain their own evidence and do not overwrite the original clock.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from native_usage import load_snapshot as _load_native_usage_snapshot

from data import (StudioDataError, _RUN_END_KEYS, _RUN_MANIFESTS, _RUN_TERMINALS,
                  _STAGES, _analytics_url, _pre_dispatch_failure, _time)


_KEYS = tuple(stage[0] for stage in _STAGES)
_ALIASES = {"sol_approval": "sol_review", "final_review": "sol_review",
            "deliverable_sheet_sync": "sync", "tracker_sync": "sync", "sheet_sync": "sync",
            "audit": "build_audit", "artifact": "build"}
_SITES = {"hellowork", "france_travail", "linkedin", "apec", "wttj", "mail"}
_ASSIGNMENTS = {"weekday_worker_contract", "history_worker_assignment"}
_STAGE_COUNTS = ("jobs_found", "jobs_proposed", "jobs_shortlisted", "ads_reviewed", "cvs_built", "rows_synced", "roles_synced")
_MAX_FILE = 2_000_000
_MAX_BYTES = 12_000_000
_MAX_FILES = 180
_MAX_ROWS = 4000
_MAX_ENTRIES = 300
_READ_CAPTURE_MAX_ROWS = 1_000
_READ_CAPTURE_MAX_FILES = 500
_READ_CAPTURE_MAX_BYTES = 64_000_000
_READ_CAPTURE_FILE_LIMITS = {
    "raw.html": 12_000_000, "body.txt": 3_000_000, "ad.json": 1_000_000,
    "capture.json": 1_000_000, "screening.json": 64_000,
}
_NATIVE_USAGE_VALIDATION = object()
_DROP_SAVED_VALUE = object()
_PARIS = ZoneInfo("Europe/Paris")
_COUNT_KEYS = {
    # Discovery/proposal totals are kept apart from the audited accepted list.
    "jobs_found": ("jobs_found", "raw_proposals", "proposal_count", "proposed_roles", "new_roles", "shortlisted"),
    "jobs_proposed": ("jobs_proposed", "raw_proposals", "proposal_count", "proposed_roles", "new_roles", "shortlisted"),
    "jobs_shortlisted": ("jobs_shortlisted", "jobs_accepted"),
    "ads_reviewed": ("ads_reviewed", "unique_full_relevant_reads", "full_live_ads_reviewed",
                     "unique_full_ad_count", "unique_full_ads", "full_live_count",
                     "full_postings_reviewed", "full_ads", "complete_original_live_bodies",
                     "captured_full_bodies", "full_ad_bodies_on_time_france_scope", "complete_unique_reviews"),
    "cvs_built": ("cvs_built", "current_cv_completed", "local_cvs_built", "artifacts_completed", "new_cv_count"),
    "cvs_failed": ("cvs_failed", "cv_failures", "build_failures"),
    "rows_synced": ("rows_synced", "synced", "sheet_rows_readback_verified", "sheet_writes", "new_pipeline_rows"),
    "roles_synced": ("roles_synced",),
}
_FAILURE_NOTES = {
    "launch_failed": "Startup failed before Hunt; no pipeline workers were dispatched.",
    "clock_exhausted": "The original stage clock expired.",
    "time_budget": "The saved outcome names a time limit.",
    "deadline_exceeded": "The original deadline was exceeded.",
    "command_failed": "A supervised command failed.",
    "external_failure": "The saved receipt names an external failure.",
    "provider_failure": "The model provider failed.",
    "provider_unavailable": "The assigned model provider was unavailable.",
    "permission_denied": "A required permission was unavailable.",
    "access_restriction": "The source restricted access.",
    "source_restriction": "The source restricted access.",
    "usage_unavailable": "A required usage observation was unavailable.",
    "usage_ambiguous": "A required usage observation was ambiguous.",
    "usage_reset": "The account usage window reset.",
    "usage_guard_exhausted": "The saved stage usage guard stopped new work.",
    "rate_limited": "The provider reported a rate limit.",
    "internal_repair": "A saved gate required a repair.",
    "factual_uncertainty": "A factual gate remained unresolved.",
    "safety_uncertainty": "A required safety gate remained unresolved.",
    "user_stopped": "The user stopped this work; partial evidence was preserved.",
    "build_failed": "The CV build failed; its saved outcome was preserved.",
}


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    if integer:
        return int(value) if value == int(value) else None
    return value


def _safe_text(value: Any, limit: int = 180) -> str | None:
    """Short labels only; never return log bodies, addresses or credentials."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    value = re.sub(r"(?i)\b(?:bearer\s+\S+|(?:sk|ghp|github_pat)-[\w-]+|ya29\.[\w.-]+)", "[redacted]", value)
    value = re.sub(r"(?i)(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret|authorization)\s*[:=]\s*\S+", "[redacted]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[address omitted]", value)
    value = re.sub(r"(?:https?://|/Users/|/home/|file://)\S+", "[reference omitted]", value)
    return " ".join(value.split())[:limit] or None


def _identifier(value: Any) -> str | None:
    # Provider/model names and failure classes are identifiers, not prose.
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/ -]{0,95}", value):
        return value
    return None


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if value.endswith(" UTC"):
        value = value[:-4].replace(" ", "T") + "+00:00"
    parsed = _time(value)
    return parsed.isoformat() if parsed else None


def _first_time(row: dict, keys: tuple[str, ...]) -> str | None:
    return next((parsed for key in keys if (parsed := _timestamp(row.get(key)))), None)


def _seconds(start: Any, end: Any) -> float | None:
    start_time, end_time = _time(start), _time(end)
    if not start_time or not end_time or end_time < start_time:
        return None
    return (end_time - start_time).total_seconds()


def _stage(value: Any) -> str | None:
    value = str(value or "").lower().replace("-", "_")
    if value in _ALIASES:
        return _ALIASES[value]
    if value in _KEYS:
        return value
    if value.startswith("hunt:") or value in _SITES or value.startswith("hunt_") and value[5:] in _SITES:
        return "hunt"
    if value.startswith("build:") or value.startswith("build_") and not value.startswith("build_audit"):
        return "build"
    for key in ("hunt_audit", "build_audit", "sol_review", "sync"):
        if value.startswith((key + ":", key + "_")):
            return key
    return None


def _status(value: Any) -> str:
    value = str(value or "").upper().replace(" ", "_").replace("-", "_")
    if not value or value == "UNVERIFIED":
        return "not_recorded"
    if value.startswith(("NOT_RUN", "NOT_STARTED", "PENDING", "QUEUED", "UNSTARTED")):
        return "not_run"
    if "USER_STOP" in value or "PARTIAL" in value or "SHORTFALL" in value or "WITH_FAILURE" in value or "WITH_SOURCE" in value or "WARNING" in value:
        return "partial"
    if "HELD" in value or "BLOCKED" in value:
        return "held"
    if any(word in value for word in ("FAILED", "ERROR", "EXPIRED", "CANCEL", "TIMED_OUT", "TIMEOUT")):
        return "failed"
    if value in ("RUNNING", "ACTIVE", "IN_PROGRESS", "STARTED", "START"):
        return "running"
    if value in ("READY", "APPROVED"):
        return "ready"
    if value.startswith(("DONE", "COMPLETE", "PASSED", "PASS", "SUCCESS", "VERIFIED", "REVIEWED", "SENT", "ARTIFACT_READY")):
        return "done"
    return "partial"


def _count(row: dict, metric: str) -> int | None:
    # Finalization receipts nest Hunt totals under "hunt".
    for group in (row, row.get("counts"), row.get("job_outcome"), row.get("current_screening"), row.get("hunt")):
        if isinstance(group, dict):
            for key in _COUNT_KEYS[metric]:
                if (value := _number(group.get(key), integer=True)) is not None:
                    return value
    return None


def _tokens(value: Any) -> dict | None:
    if isinstance(value, (int, float)):
        total = _number(value, integer=True)
        return {"total_tokens": total} if total is not None else None
    if not isinstance(value, dict):
        return None
    aliases = {"total_tokens": ("total_tokens", "totalTokens"),
               "input_tokens": ("input_tokens", "inputTokens", "prompt_tokens"),
               "output_tokens": ("output_tokens", "outputTokens", "completion_tokens"),
               "cached_input_tokens": ("cached_input_tokens", "cachedInputTokens"),
               "reasoning_output_tokens": ("reasoning_output_tokens", "reasoningOutputTokens"),
               "cache_write_input_tokens": ("cache_write_input_tokens", "cacheWriteInputTokens")}
    result = {}
    for name, keys in aliases.items():
        for key in keys:
            if (number := _number(value.get(key), integer=True)) is not None:
                result[name] = number
                break
    return result or None


def _empty_usage() -> dict:
    return {"status": "unavailable", "exact_tokens": None, "recorded_tokens": None,
            "token_scope": None, "account_delta_pp": None, "window": None,
            "provider": None, "windows": [], "account_observations": [], "approximate": None,
            "usage_scope": None, "usage_counter_basis": None, "usage_thread_id": None,
            "usage_observed_at": None, "usage_binding_verified": False,
            "note": "Usage was not saved for this scope."}


def _quota_observations(value: Any, prefix: str = "") -> list[dict]:
    """Keep single quota snapshots explicit; remaining values are never inverted."""
    if not isinstance(value, dict):
        return []
    for key in ("limits", "result"):
        if isinstance(value.get(key), dict):
            return _quota_observations(value[key], prefix)
    provider = _identifier(value.get("provider"))
    result = []
    bundles = value.get("rateLimitsByLimitId")
    if isinstance(bundles, dict):
        for name, item in list(bundles.items())[:10]:
            result.extend(_quota_observations(item, f"{prefix}{str(name)[:60]}:"))
        return result
    limits = value.get("rateLimits")
    if isinstance(limits, dict):
        result.extend(_quota_observations(limits, prefix))
        return result
    children = [(key, value.get(key)) for key in ("primary", "secondary") if isinstance(value.get(key), dict)]
    if children:
        for name, item in children:
            result.extend(_quota_observations(item, f"{prefix}{name}:"))
        return result
    used = _number(value.get("usedPercent", value.get("used_percent")))
    remaining = _number(value.get("remainingPercent", value.get("remaining_percent")))
    fraction = _number(value.get("remainingFraction", value.get("remaining_fraction", value.get("remainingfraction"))))
    if used is not None and used > 100 or remaining is not None and remaining > 100 or fraction is not None and fraction > 1:
        return []
    status = value.get("quota_status")
    status = status if status in ("available", "unavailable", "ambiguous") else "available" if any(v is not None for v in (used, remaining, fraction)) else "unavailable"
    basis = _identifier(value.get("quota_basis"))
    window = _identifier(value.get("window"))
    duration = _number(value.get("windowDurationMins", value.get("window_minutes")), integer=True)
    reset = _number(value.get("resetsAt", value.get("reset_at")), integer=True)
    if not any(v is not None for v in (used, remaining, fraction)) and status == "unavailable" and not basis:
        return []
    return [{"provider": provider, "quota_status": status, "quota_basis": basis,
             "used_percent": used, "remaining_percent": remaining,
             "remaining_fraction": fraction, "window": window or (prefix.rstrip(":") or None),
             "window_duration_minutes": duration, "reset_at": reset,
             "reset_time_iso": _first_time(value, ("resetTimeISO", "reset_time_iso")),
             "observed_at": _first_time(value, ("observedAt", "observed_at"))}]


def _meter_windows(value: Any) -> dict[str, dict]:
    """Project only numerical account fields; leave auth and account data out."""
    result: dict[str, dict] = {}
    if not isinstance(value, dict):
        return result
    if isinstance(value.get("limits"), dict):
        return _meter_windows(value["limits"])
    if isinstance(value.get("result"), dict):
        return _meter_windows(value["result"])
    bundles = value.get("rateLimitsByLimitId")
    provider = _identifier(value.get("provider"))
    if isinstance(bundles, dict) and bundles:
        for limit_id, bundle in list(bundles.items())[:10]:
            if isinstance(bundle, dict):
                for key, item in _meter_windows({"rateLimits": bundle, "provider": provider or "codex"}).items():
                    result[str(limit_id)[:60] + ":" + key] = item
        return result
    limits = value.get("rateLimits")
    container = limits if isinstance(limits, dict) else value
    for lane in ("primary", "secondary"):
        window = container.get(lane)
        if not isinstance(window, dict):
            continue
        used = _number(window.get("usedPercent", window.get("used_percent")))
        duration = _number(window.get("windowDurationMins", window.get("window_minutes")), integer=True)
        reset = _number(window.get("resetsAt", window.get("reset_at")), integer=True)
        if used is None or used > 100:
            continue
        result[lane] = {"provider": provider or ("codex" if isinstance(limits, dict) else None),
                        "window_duration_minutes": duration, "reset_at": reset,
                        "window": f"{duration if duration is not None else 'unknown'} min · reset {reset}" if reset is not None else None,
                        "used_percent": used}
    if not result:
        used = _number(value.get("used_percent", value.get("usedPercent")))
        duration = _number(value.get("window_minutes", value.get("windowDurationMins")), integer=True)
        reset = _number(value.get("reset_at", value.get("resetsAt")), integer=True)
        if used is not None and used <= 100:
            result["primary"] = {"provider": provider, "window_duration_minutes": duration,
                                 "reset_at": reset, "window": _identifier(value.get("window")), "used_percent": used}
    return result


def _usage(row: dict) -> dict:
    result = _empty_usage()
    container = row.get("usage") if isinstance(row.get("usage"), dict) else row
    usage_scope = row.get("usage_scope", container.get("usage_scope"))
    counter_basis = row.get("usage_counter_basis", container.get("usage_counter_basis"))
    usage_thread_id = row.get("usage_thread_id", container.get("usage_thread_id"))
    receipt_thread_id = row.get("thread_id", container.get("thread_id"))
    observed_at = _first_time(row, ("usage_observed_at",)) or _first_time(container, ("usage_observed_at",))
    appserver_worker = counter_basis == "codex_appserver_thread_token_usage"
    rollout_worker = (counter_basis == "codex_rollout_token_count" and
                      row.get("_native_usage_sidecar_validated") is _NATIVE_USAGE_VALIDATION)
    if (usage_scope == "worker_thread_cumulative" and (appserver_worker or rollout_worker) and
            isinstance(usage_thread_id, str) and usage_thread_id.strip() and
            isinstance(receipt_thread_id, str) and usage_thread_id.strip() == receipt_thread_id.strip() and
            observed_at):
        token_container = container.get("thread_cumulative_token_usage_at_observation")
        if not isinstance(token_container, dict):
            token_container = container.get("usage") if isinstance(container.get("usage"), dict) else container
        if isinstance(token_container.get("total"), dict):
            token_container = token_container["total"]
        reported = (_tokens(token_container) if appserver_worker else _tokens(container.get("recorded_tokens")))
        if reported and reported.get("total_tokens") is not None:
            result.update(status="tokens_recorded", recorded_tokens=reported,
                          token_scope="thread_cumulative", approximate=False,
                          usage_scope="worker_thread_cumulative",
                          usage_counter_basis=counter_basis,
                          usage_thread_id=usage_thread_id.strip()[:200],
                          usage_observed_at=observed_at, usage_binding_verified=True,
                          note=("Bound worker-thread cumulative counter includes cached input; task-wide coverage is incomplete."
                                if rollout_worker else
                                "Final native counter from a bound worker thread; it is not an exact whole-task cost."))
            if rollout_worker:
                result["usage_source_integrity_verified"] = True
                result["cache_included"] = True
            return result
    exact = _tokens(container.get("exact_token_usage", container.get("exact_tokens")))
    if exact is None and container.get("token_scope") in ("request", "task", "stage", "job"):
        exact = _tokens(container.get("tokens", container.get("token_usage")))
    recorded = _tokens(container.get("token_usage"))
    scope = _identifier(container.get("token_scope"))
    if exact is not None:
        result.update(status="exact_tokens", exact_tokens=exact, token_scope=scope or "recorded_request")
    elif recorded is not None:
        result.update(status="tokens_recorded", recorded_tokens=recorded,
                      token_scope=scope or "scope_not_recorded")
    # Codex thread/tokenUsage events are cumulative. They are not a per-stage
    # request total, even if the surrounding receipt was saved for one stage.
    token_total = container.get("token_usage")
    if isinstance(token_total, dict) and isinstance(token_total.get("total"), dict):
        result.update(status="tokens_recorded", exact_tokens=None,
                      recorded_tokens=_tokens(token_total["total"]), token_scope="thread_cumulative")
    cumulative = _tokens(container.get("thread_cumulative_token_usage_at_observation"))
    if cumulative is not None:
        result.update(status="tokens_recorded", exact_tokens=None, recorded_tokens=cumulative,
                      token_scope="thread_cumulative")
    if isinstance(container.get("account_observation"), dict):
        result["account_observations"] = _quota_observations(container["account_observation"])

    before, after = container.get("usage_before"), container.get("usage_after")
    if isinstance(before, dict) and isinstance(after, dict):
        first, last = _meter_windows(before), _meter_windows(after)
        for key in sorted(set(first) | set(last)):
            old, new = first.get(key, {}), last.get(key, {})
            status, delta = "ambiguous", None
            if old and new and old.get("reset_at") is not None and new.get("reset_at") is not None:
                if old["reset_at"] != new["reset_at"]:
                    status = "reset"
                elif (old.get("window_duration_minutes") == new.get("window_duration_minutes") and
                      old.get("provider") == new.get("provider")):
                    delta = new["used_percent"] - old["used_percent"]
                    status = "observed" if delta >= 0 else "reset"
                    if delta < 0:
                        delta = None
            result["windows"].append({"id": key, **{k: new.get(k) or old.get(k) for k in
                ("provider", "window", "window_duration_minutes", "reset_at")},
                "before_percent": old.get("used_percent"), "after_percent": new.get("used_percent"),
                "account_delta_pp": delta, "status": status, "approximate": True})
    else:
        before_keys = ("usage_before", "baseline_used_percent", "start_used_percent", "initial_percent", "baseline")
        after_keys = ("usage_after", "current_used_percent", "end_used_percent", "finish_used_percent", "last_observed_percent", "current")
        old = next((n for key in before_keys if (n := _number(container.get(key))) is not None), None)
        new = next((n for key in after_keys if (n := _number(container.get(key))) is not None), None)
        primary = container.get("primary")
        if new is None and isinstance(primary, dict):
            new = _number(primary.get("usedPercent"))
        window = _identifier(container.get("usage_window", container.get("window")))
        reset = _number(container.get("window_reset_at", container.get("reset_at", container.get("reset"))), integer=True)
        duration = _number(container.get("window_minutes", container.get("windowDurationMins")), integer=True)
        if isinstance(primary, dict):
            reset = reset if reset is not None else _number(primary.get("resetsAt"), integer=True)
            duration = duration if duration is not None else _number(primary.get("windowDurationMins"), integer=True)
        if window is None and reset is not None:
            window = f"{duration if duration is not None else 'unknown'} min · reset {reset}"
        provider = _identifier(container.get("provider"))
        if provider is None and window and window.startswith("codex:"):
            provider = "codex"
        if old is not None and new is not None and old <= 100 and new <= 100:
            delta = new - old
            status = "observed" if window and delta >= 0 else "reset" if delta < 0 else "ambiguous"
            gate = container.get("gate")
            if container.get("reset") is True or isinstance(gate, dict) and gate.get("failure_class") == "usage_reset":
                status = "reset"
            if str(container.get("usage_state", "")).lower() in ("ambiguous", "unavailable"):
                status = str(container["usage_state"]).lower()
            result["windows"].append({"id": "recorded", "provider": provider, "window": window,
                "window_duration_minutes": duration, "reset_at": reset,
                "before_percent": old, "after_percent": new,
                "account_delta_pp": delta if status == "observed" else None,
                "status": status, "approximate": True})
    if result["windows"]:
        window = next((w for w in result["windows"] if w["id"].endswith("primary")), result["windows"][0])
        result.update(account_delta_pp=window["account_delta_pp"], window=window["window"],
                      provider=window["provider"], approximate=True)
        if result["exact_tokens"] is None:
            result["status"] = "account_observed" if window["status"] == "observed" else window["status"]
        result["note"] = "Account-wide change is approximate; it can include other activity and delayed reporting. Zero change does not mean free usage."
    elif result["exact_tokens"] is not None:
        result["note"] = "Exact token fields saved in the receipt for this scope."
    elif result["recorded_tokens"] is not None:
        result["note"] = "Saved token total; its scope is cumulative or unspecified and is not an exact cost for this step."
    elif result["account_observations"]:
        result["note"] = "A single quota snapshot was saved; it is not a before-and-after usage delta."
    return result


def _unique_worker_usages(rows: list[dict]) -> list[dict]:
    """Keep the latest bound snapshot once per native thread identity."""
    by_thread: dict[str, dict] = {}
    for usage in rows:
        if not isinstance(usage, dict) or usage.get("usage_binding_verified") is not True:
            continue
        thread_id = usage.get("usage_thread_id")
        if not isinstance(thread_id, str) or not thread_id.strip():
            continue
        prior = by_thread.get(thread_id)
        usage_total = (usage.get("recorded_tokens", {}).get("total_tokens")
                       if isinstance(usage.get("recorded_tokens"), dict) else None)
        prior_total = (prior.get("recorded_tokens", {}).get("total_tokens")
                       if isinstance(prior, dict) and isinstance(prior.get("recorded_tokens"), dict) else None)
        newer = prior is None or (_time(usage.get("usage_observed_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= (
            _time(prior.get("usage_observed_at")) or datetime.min.replace(tzinfo=timezone.utc))
        nondecreasing = (isinstance(usage_total, int) and not isinstance(usage_total, bool) and
                         (not isinstance(prior_total, int) or isinstance(prior_total, bool) or usage_total >= prior_total))
        if newer and nondecreasing:
            by_thread[thread_id] = usage
    return list(by_thread.values())


def _model(row: dict, source: str, *, configured: bool = False) -> dict | None:
    context = row.get("turn_context")
    if isinstance(context, dict):
        row, source, configured = context, "runtime_context", False
    explicit = row.get("model_used") or row.get("actual_model")
    value = explicit or row.get("model") or row.get("model_requested")
    model = _identifier(value)
    if model is None:
        return None
    effort = _identifier(row.get("actual_reasoning_effort") or row.get("reasoning_effort") or row.get("effort"))
    if not explicit and row.get("model_requested") and not row.get("model"):
        configured = True
    return {"model": model, "effort": effort,
            "source": "configured_contract" if configured else source,
            "confirmed": not configured,
            "note": "Assigned route; execution model was not saved." if configured else "Named in the saved execution receipt."}


def _model_fields(models: list[dict]) -> dict:
    unique = []
    for model in models:
        if model and model not in unique:
            unique.append(model)
    actual = [row for row in unique if row["confirmed"]]
    selected = actual or unique
    names, efforts = {m["model"] for m in selected}, {m["effort"] for m in selected}
    return {"model": next(iter(names)) if len(names) == 1 else "Multiple models" if names else None,
            "effort": next(iter(efforts)) if len(efforts) == 1 else "mixed" if efforts else None,
            "model_source": "recorded" if actual else "configured" if selected else "not_recorded",
            "models": unique}


def _failure(row: dict, stage: str, source: str) -> dict | None:
    named = row.get("failure") if isinstance(row.get("failure"), dict) else row
    kind = _identifier(named.get("failure_class") or named.get("error_class") or named.get("failure_code"))
    state = _status(row.get("status", row.get("outcome")))
    if kind is None and state not in ("failed", "held", "partial"):
        return None
    if kind is None and "USER_STOP" in str(row.get("status", "")).upper():
        kind = "user_stopped"
    # Canned wording prevents raw command errors, email bodies, reasoning or
    # credential-bearing provider messages leaking through a reason field.
    note = _FAILURE_NOTES.get(kind, "The saved step was held or failed." if state in ("failed", "held") else "The saved step has a partial outcome.")
    code = _number(named.get("returncode", named.get("exit_code")), integer=True)
    return {"stage": stage, "class": kind or "recorded_" + state, "message": note,
            "worker_id": _safe_text(row.get("worker_id")),
            "at": _first_time(row, ("actual_work_stopped_at", "finished_at", "ended_at", "recorded_at", "at")),
            "exit_code": code, "source": source}


def _dedup(values: list[dict]) -> list[dict]:
    result, keys = [], set()
    for value in values:
        key = (value.get("stage"), value.get("class"), value.get("worker_id"), value.get("at"))
        if key not in keys:
            result.append(value)
            keys.add(key)
    return result


class _Reader:
    """A fresh per-request budget also tolerates partially written receipts."""
    def __init__(self, data: Any, base: Path):
        self.data, self.base = data, base
        self.bytes, self.files, self.truncated, self.transient = 0, 0, False, False
        self.cache: dict[Path, Any] = {}
        self.hashes: dict[Path, str] = {}

    def read(self, path: Path, *, lines: bool = False) -> Any:
        if path in self.cache:
            return self.cache[path]
        try:
            if path.is_symlink():
                return None
            safe = self.data._safe(path, self.base)
            size = safe.stat().st_size
            if size > _MAX_FILE or self.bytes + size > _MAX_BYTES or self.files >= _MAX_FILES:
                self.truncated = True
                return None
            self.bytes += size
            self.files += 1
            raw_bytes = safe.read_bytes()
            self.hashes[path] = hashlib.sha256(raw_bytes).hexdigest()
            raw = raw_bytes.decode("utf-8")
            if lines:
                rows = raw.splitlines()
                if len(rows) > _MAX_ROWS:
                    self.truncated = True
                    return None
                value = [json.loads(line) for line in rows if line.strip()]
            else:
                try:
                    value = json.loads(raw)
                except ValueError:
                    # Build ledgers are JSONL despite their historical .json suffix.
                    if not path.name.endswith(".ledger.json"):
                        self.transient = True
                        return None
                    value = [json.loads(line) for line in raw.splitlines() if line.strip()]
                    if len(value) > _MAX_ROWS:
                        self.truncated = True
                        return None
            self.cache[path] = value
            return value
        except (OSError, UnicodeError, ValueError, TypeError, StudioDataError):
            try:
                if path.exists() and not path.is_symlink():
                    self.transient = True
            except OSError:
                pass
            return None

    def first(self, names: tuple[str, ...]) -> tuple[dict, str]:
        for name in names:
            value = self.read(self.base / name)
            if isinstance(value, dict):
                return value, name
        return {}, ""

    def entries(self, path: Path) -> list[Path]:
        try:
            if path.is_symlink():
                return []
            values = list(self.data._safe(path, self.base).iterdir())
            if len(values) > _MAX_ENTRIES:
                self.truncated = True
            return sorted(values, key=lambda p: p.name)[:_MAX_ENTRIES]
        except (OSError, StudioDataError):
            return []


class RunHistory:
    def __init__(self, data: Any):
        self.data = data

    def verified_read_events(self, run_dir: Path) -> dict[str, Any]:
        """Return a bounded minimum of screened reads tied to original full ads."""
        from hashlib import sha256

        base = self.data._safe(run_dir, self.data.evidence)
        local_agents = Path(__file__).resolve().parents[1] / "Master" / "auto" / "local"
        if str(local_agents) not in sys.path:
            sys.path.insert(0, str(local_agents))
        from full_ad_evidence import inspect_full_ad_evidence

        events: dict[str, dict[str, Any]] = {}
        partial_days: set[str] = set()
        incomplete = False
        rows_seen = files_seen = bytes_seen = 0
        sites = ("hellowork", "france_travail", "linkedin", "apec", "wttj", "indeed")

        def read(path: Path, name: str) -> bytes | None:
            nonlocal files_seen, bytes_seen, incomplete
            limit = _READ_CAPTURE_FILE_LIMITS.get(name, _MAX_FILE)
            try:
                safe = self.data._safe(path, base)
                if not safe.is_file() or path.is_symlink():
                    return None
                size = safe.stat().st_size
                files_seen += 1
                if (size > limit or files_seen > _READ_CAPTURE_MAX_FILES or
                        bytes_seen + size > _READ_CAPTURE_MAX_BYTES):
                    incomplete = True
                    return None
                raw = safe.read_bytes()
                bytes_seen += len(raw)
                return raw
            except (OSError, StudioDataError):
                return None

        def json_file(path: Path | None, name: str) -> dict | list | None:
            raw = read(path, name) if path else None
            if raw is None:
                return None
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeError, ValueError):
                return None

        def fingerprint(ad: dict, body: str) -> str:
            clean = lambda value: " ".join(str(value or "").split())
            fields = {key: clean(ad.get(key)) for key in ("title", "company", "contract", "location")}
            fields["body"] = clean(body)
            packed = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            return sha256(packed).hexdigest()

        for site in sites:
            lane = base / "hunt" / site
            if not lane.is_dir():
                lane = base / site
            index_path = lane / "full_ads.json"
            if not index_path.exists() or index_path.is_symlink():
                continue
            indexed = json_file(index_path, "ad.json")
            rows = indexed if isinstance(indexed, list) else indexed.get("ads") if isinstance(indexed, dict) else None
            if not isinstance(rows, list):
                incomplete = True
                continue
            for row in rows:
                rows_seen += 1
                if rows_seen > _READ_CAPTURE_MAX_ROWS:
                    incomplete = True
                    break
                if not isinstance(row, dict):
                    incomplete = True
                    continue
                paths = row.get("paths") if isinstance(row.get("paths"), dict) else {}
                capture_id = row.get("capture_id")
                at = row.get("captured_at_utc")
                canonical = _analytics_url(row.get("canonical_url"))
                captured = _time(at)
                day = captured.astimezone(_PARIS).date().isoformat() if captured else None
                if not isinstance(capture_id, str) or not canonical or not day:
                    incomplete = True
                    continue

                def path_for(key: str) -> Path | None:
                    name = paths.get(key)
                    if not isinstance(name, str) or not name:
                        return None
                    candidate = lane / name
                    try:
                        safe = self.data._safe(candidate, base)
                        return safe if safe.is_relative_to(lane.resolve()) else None
                    except (OSError, StudioDataError):
                        return None

                capture_path = path_for("capture_json")
                ad_path = path_for("ad_json")
                raw_path = path_for("raw_html")
                body_path = path_for("body_text") or path_for("body")
                screen_path = ad_path.parent / "screening.json" if ad_path else None
                capture = json_file(capture_path, "capture.json")
                ad = json_file(ad_path, "ad.json")
                screen = json_file(screen_path, "screening.json")
                if not (isinstance(capture, dict) and isinstance(ad, dict) and
                        isinstance(screen, dict) and raw_path and body_path):
                    incomplete = True
                    partial_days.add(day)
                    continue
                raw, body_bytes = read(raw_path, "raw.html"), read(body_path, "body.txt")
                if raw is None or body_bytes is None:
                    incomplete = True
                    partial_days.add(day)
                    continue
                try:
                    raw_text, body = raw.decode("utf-8"), body_bytes.decode("utf-8")
                except UnicodeError:
                    incomplete = True
                    partial_days.add(day)
                    continue
                raw_hash, body_hash = sha256(raw).hexdigest(), sha256(body_bytes).hexdigest()
                raw_ad, raw_screen = read(ad_path, "ad.json"), read(screen_path, "screening.json")
                if raw_ad is None or raw_screen is None:
                    incomplete = True
                    partial_days.add(day)
                    continue
                metadata_ok = (
                    row.get("capture_kind") == "verbatim_full_detail" and
                    capture.get("status") == "captured_full_detail" and
                    capture.get("capture_id") == capture_id == ad.get("capture_id") == screen.get("capture_id") and
                    capture.get("site") == site == row.get("site") and
                    _analytics_url(capture.get("canonical_url")) == canonical ==
                        _analytics_url(ad.get("canonical_url")) == _analytics_url(screen.get("canonical_url")) and
                    capture.get("captured_at_utc") == at and
                    row.get("raw_sha256") == capture.get("raw_sha256") == raw_hash and
                    row.get("body_sha256") == capture.get("body_sha256") == body_hash and
                    capture.get("description_source") == ad.get("description_source") and
                    ad.get("description_source") in {"jobposting_description", "saved_original_dom"} and
                    isinstance(ad.get("description"), str) and
                    " ".join(ad["description"].split()) == " ".join(body.split())
                )
                quoted = screen.get("decision", {}).get("quotes") if isinstance(screen.get("decision"), dict) else None
                checked = _time(screen.get("checked_at_utc"))
                profile_hash = capture.get("profile_sha256")
                rules_hash = capture.get("rules_sha256")
                bindings_ok = (
                    isinstance(profile_hash, str) and re.fullmatch(r"[0-9a-fA-F]{64}", profile_hash) is not None and
                    isinstance(rules_hash, str) and re.fullmatch(r"[0-9a-fA-F]{64}", rules_hash) is not None and
                    screen.get("profile_sha256") == profile_hash and
                    screen.get("rules_sha256") == rules_hash
                )
                screened_ok = (
                    screen.get("body_fingerprint") == fingerprint(ad, body) and
                    bindings_ok and
                    isinstance(quoted, list) and bool(quoted) and all(
                        isinstance(q, str) and q.strip() and " ".join(q.split()) in " ".join(body.split())
                        for q in quoted) and checked is not None and captured is not None and checked >= captured
                )
                if not (metadata_ok and screened_ok):
                    incomplete = True
                    partial_days.add(day)
                    continue
                # Reuse only an identical validator input. Files and all receipt
                # bindings above are still read and verified on every refresh.
                proof_key = (raw_hash, body_hash, sha256(json.dumps(ad, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
                with self.data._read_proof_lock:
                    proof = self.data._read_proof_cache.get(proof_key)
                if proof is None:
                    proof = inspect_full_ad_evidence(ad, raw_text, body)
                    with self.data._read_proof_lock:
                        if len(self.data._read_proof_cache) >= 512:
                            self.data._read_proof_cache.pop(next(iter(self.data._read_proof_cache)))
                        self.data._read_proof_cache[proof_key] = proof
                reasons = set(proof.get("reasons") or [])
                # Requirement-quote packaging is downstream of reading; any raw
                # page, identity, availability or capture defect still blocks.
                body_ok = (proof.get("eligible_full_read") is True or
                           reasons == {"duties_or_requirements_not_literal_original_body"})
                if not (metadata_ok and screened_ok and body_ok):
                    incomplete = True
                    partial_days.add(day)
                    continue
                prior = events.get(canonical)
                if prior is None or (captured and captured < _time(prior["captured_at"])):
                    events[canonical] = {"url": canonical, "canonical_url": canonical,
                        "captured_at": at, "capture_id": capture_id, "source": site,
                        "body_sha256": body_hash,
                        "ad_sha256": sha256(raw_ad).hexdigest(),
                        "screening_sha256": sha256(raw_screen).hexdigest()}
            if rows_seen > _READ_CAPTURE_MAX_ROWS:
                break
        return {"events": list(events.values()), "partial": incomplete or bool(partial_days),
                "partial_dates": sorted(partial_days), "rows_seen": rows_seen,
                "files_seen": files_seen, "bytes_seen": bytes_seen}

    def runs(self, inventory: dict | None = None) -> dict:
        """Return the normal run catalog, optionally enriching a fresh inventory.

        ``StudioData.overview`` already reads the bounded run inventory for its
        dashboard. Callers that need the full history projection in the same
        response can pass that exact result here and avoid scanning it twice.
        Enrichment and saved-snapshot recovery still run exactly as they do on
        the standalone ``/api/runs`` route.
        """
        inventory = self.data.runs() if inventory is None else inventory
        if (not isinstance(inventory, dict) or not isinstance(inventory.get("items"), list)
                or any(not isinstance(row, dict) for row in inventory["items"])):
            raise ValueError("Run inventory must contain a list of saved run records.")
        items = []
        for row in inventory["items"]:
            item = self.enrich(row, detail=False)
            transient = bool(item.pop("_snapshot_transient", False))
            item = self._with_snapshot(item, detail=False, transient=transient)
            items.append(item)
        live_ids = {row.get("id") for row in items}
        items.extend(self._recover_snapshots(live_ids))
        items.sort(key=lambda row: (_time(row.get("started_at")) or _time(row.get("date")) or
                                    datetime.min.replace(tzinfo=timezone.utc), str(row.get("id", ""))), reverse=True)
        return {**inventory, "items": items, "total": len(items)}

    def run(self, run_id: str) -> dict:
        # StudioData remains the sole authority for resolving IDs and containment.
        try:
            source = self.data.run(run_id)
        except StudioDataError as exc:
            if exc.status != 404:
                raise
            saved = self._load_snapshot(run_id)
            if not saved:
                raise
            item = saved.get("detail") or saved.get("summary")
            if not isinstance(item, dict):
                raise
            result = dict(item)
            result["snapshot_status"] = "recovered"
            result["snapshot_recorded_at"] = saved.get("recorded_at")
            result["snapshot_warning"] = "Original run receipts are unavailable; this view uses the last saved Studio snapshot."
            result["observations"] = list(result.get("observations", [])) + ["Original run receipts were unavailable; recovered from the local Studio history snapshot."]
            return result
        item = self.enrich(source, detail=True)
        transient = bool(item.pop("_snapshot_transient", False))
        return self._with_snapshot(item, detail=True, transient=transient)

    def _snapshot_root(self, *, create: bool) -> Path | None:
        path = self.data.local / "history" / "runs"
        parts = (self.data.local, self.data.local / "history", path)
        try:
            if any(part.is_symlink() for part in parts):
                return None
            if create:
                path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not path.is_dir() or any(part.is_symlink() for part in parts):
                return None
            if create:
                os.chmod(path.parent, 0o700)
                os.chmod(path, 0o700)
            safe = self.data._safe(path, self.data.local)
            return safe if safe == path else None
        except (OSError, StudioDataError):
            return None

    @staticmethod
    def _snapshot_filename(run_id: str) -> str:
        return hashlib.sha256(run_id.encode("utf-8")).hexdigest() + ".json"

    def _load_snapshot(self, run_id: str) -> dict | None:
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,180}", run_id):
            return None
        root = self._snapshot_root(create=False)
        if root is None:
            return None
        path = root / self._snapshot_filename(run_id)
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
                return None
            safe = self.data._safe(path, root)
            saved = json.loads(safe.read_text(encoding="utf-8"))
            if (not isinstance(saved, dict) or saved.get("schema_version") != 1 or
                    saved.get("run_id") != run_id):
                return None
            return self._sanitize_saved_snapshot(saved)
        except (OSError, UnicodeError, ValueError, StudioDataError):
            return None

    @staticmethod
    def _sanitize_saved_snapshot(saved: dict) -> dict:
        """Never restore rollout counters from cached projections; revalidate live proofs first."""
        def clean(value: Any) -> Any:
            if isinstance(value, dict):
                if value.get("usage_counter_basis") == "codex_rollout_token_count":
                    return _DROP_SAVED_VALUE
                result = {}
                for key, child in value.items():
                    if key == "_native_usage_sidecar_validated":
                        continue
                    sanitized = clean(child)
                    if sanitized is not _DROP_SAVED_VALUE:
                        result[key] = sanitized
                return result
            if isinstance(value, list):
                result = []
                for child in value:
                    sanitized = clean(child)
                    if sanitized is not _DROP_SAVED_VALUE:
                        result.append(sanitized)
                return result
            return value
        sanitized = clean(saved)
        return sanitized if isinstance(sanitized, dict) else {}

    @staticmethod
    def _snapshot_projection(item: dict) -> dict:
        """Store only normalized history facts, never reports, inbox or logs."""
        basic = ("id", "date", "kind", "status", "raw_status", "started_at", "ended_at", "updated_at",
                 "source", "policy_version", "workflow_name", "workflow_mode", "scheduled_at",
                 "startup_delay_seconds", "deadline", "historical", "duration_seconds")
        result = {key: item.get(key) for key in basic if key in item}
        result["summary"] = "Saved Studio run history snapshot."
        metric_keys = ("jobs_found", "jobs_proposed", "jobs_shortlisted", "ads_reviewed", "cvs_built", "cvs_failed",
                       "rows_synced", "roles_synced", "failure_count")
        metrics = item.get("summary_metrics")
        if isinstance(metrics, dict):
            result["summary_metrics"] = {key: metrics.get(key) for key in metric_keys if key in metrics}
        timing = item.get("timing")
        if isinstance(timing, dict):
            result["timing"] = {key: timing.get(key) for key in
                                ("started_at", "finished_at", "duration_seconds", "elapsed_seconds", "timing_status", "note")
                                if key in timing}
        stages = item.get("stage_metrics")
        if isinstance(stages, list):
            keep = ("id", "label", "status", "raw_status", "started_at", "finished_at", "duration_seconds",
                    "elapsed_seconds", "timing_status", "jobs_found", "jobs_proposed", "jobs_shortlisted", "ads_reviewed",
                    "cvs_built", "rows_synced", "roles_synced", "model", "model_source", "usage",
                    "usage_counters")
            result["stage_metrics"] = []
            for row in stages:
                if not isinstance(row, dict):
                    continue
                projected = {key: row.get(key) for key in keep if key in row}
                if isinstance(projected.get("usage"), dict) and projected["usage"].get(
                        "usage_counter_basis") == "codex_rollout_token_count":
                    projected.pop("usage", None)
                if isinstance(projected.get("usage_counters"), list):
                    projected["usage_counters"] = [counter for counter in projected["usage_counters"]
                        if isinstance(counter, dict) and counter.get("usage_counter_basis") != "codex_rollout_token_count"]
                result["stage_metrics"].append(projected)
        usage = item.get("usage")
        if isinstance(usage, dict) and usage.get("usage_counter_basis") != "codex_rollout_token_count":
            usage_keys = ("status", "exact_tokens", "recorded_tokens", "token_scope", "account_delta_pp",
                          "window", "provider", "approximate", "note", "usage_scope",
                          "usage_counter_basis", "usage_thread_id", "usage_observed_at",
                          "usage_binding_verified")
            result["usage"] = {key: usage.get(key) for key in usage_keys if key in usage}
            if isinstance(usage.get("windows"), list):
                window_keys = ("id", "provider", "window", "window_duration_minutes", "reset_at",
                               "before_percent", "after_percent", "account_delta_pp", "status", "approximate")
                result["usage"]["windows"] = [{key: row.get(key) for key in window_keys if key in row}
                                                for row in usage["windows"] if isinstance(row, dict)]
            if isinstance(usage.get("account_observations"), list):
                quota_keys = ("provider", "quota_status", "quota_basis", "used_percent", "remaining_percent",
                              "remaining_fraction", "window", "window_duration_minutes", "reset_at",
                              "reset_time_iso", "observed_at")
                result["usage"]["account_observations"] = [
                    {key: row.get(key) for key in quota_keys if key in row}
                    for row in usage["account_observations"] if isinstance(row, dict)]
        jobs = item.get("jobs")
        if isinstance(jobs, list):
            keep = ("company", "title", "role_path", "role_id", "status", "cv_built", "duration_seconds", "source", "continuation_id")
            result["jobs"] = [{key: _safe_text(row.get(key)) if key in ("company", "title", "role_path", "status", "source")
                                else row.get(key) for key in keep if key in row}
                               for row in jobs if isinstance(row, dict)]
        builds = item.get("builds")
        if isinstance(builds, list):
            keep = ("role", "name", "company", "status", "artifact_status", "artifact_recorded", "started_at",
                    "completed_at", "duration_seconds", "timing_status", "repair_count", "attempt_count", "source")
            result["builds"] = [{key: _safe_text(row.get(key)) if key in ("role", "name", "company", "status", "artifact_status", "source")
                                  else row.get(key) for key in keep if key in row}
                                 for row in builds if isinstance(row, dict)]
        continuations = item.get("continuations")
        if isinstance(continuations, list):
            keep = ("id", "parent_run_id", "status", "raw_status", "role_path", "started_at", "global_deadline",
                    "dispatch_cutoff", "completed_at", "remaining_stages", "build_status", "build_audit_status",
                    "approval_status", "sync_status", "completion_status", "stages", "counts", "next_action", "role_id")
            result["continuations"] = []
            for row in continuations:
                if not isinstance(row, dict):
                    continue
                saved = {key: row.get(key) for key in keep if key in row and key not in ("stages", "counts")}
                if isinstance(row.get("stages"), dict):
                    saved["stages"] = {key: _safe_text(value) for key, value in row["stages"].items()
                                       if isinstance(key, str) and isinstance(value, str)}
                if isinstance(row.get("counts"), dict):
                    saved["counts"] = {key: value for key, value in row["counts"].items()
                                        if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool) and value >= 0}
                result["continuations"].append(saved)
        milestones = item.get("role_milestones")
        if isinstance(milestones, list):
            keep = ("kind", "stage", "worker_id", "role_id", "role", "at", "status",
                    "evidence_sha256", "predecessor_sha256", "failure_class", "current")
            result["role_milestones"] = [{key: row.get(key) for key in keep if key in row}
                                          for row in milestones if isinstance(row, dict)]
        for key in ("observations",):
            if isinstance(item.get(key), list):
                result[key] = [_safe_text(value) for value in item[key] if isinstance(value, str) and _safe_text(value)]
        if item.get("_jobs_authoritative") is True:
            # Internal merge hint only. _write_snapshot consumes it before the
            # projection is persisted.
            result["_jobs_authoritative"] = True
        return result

    @staticmethod
    def _merge_snapshot_values(old: Any, new: Any, key: str = "") -> Any:
        if new is None:
            return old
        if isinstance(old, dict) and isinstance(new, dict):
            result = dict(old)
            for child, value in new.items():
                result[child] = RunHistory._merge_snapshot_values(old.get(child), value, child)
            return result
        if isinstance(old, list) and isinstance(new, list):
            if not new:
                return old
            identity = {"stage_metrics": "id", "jobs": "role_path", "builds": "role", "continuations": "id"}.get(key)
            if identity:
                prior = {row.get(identity): row for row in old if isinstance(row, dict) and row.get(identity)}
                merged, seen = [], set()
                for row in new:
                    if not isinstance(row, dict) or not row.get(identity):
                        merged.append(row)
                        continue
                    ident = row[identity]
                    seen.add(ident)
                    merged.append(RunHistory._merge_snapshot_values(prior.get(ident), row, identity))
                merged.extend(row for row in old if isinstance(row, dict) and row.get(identity) not in seen)
                return merged
        return new

    def _write_snapshot(self, item: dict, *, detail: bool, transient: bool) -> dict | None:
        run_id = item.get("id")
        existing = self._load_snapshot(run_id) if isinstance(run_id, str) else None
        if transient:
            return existing
        root = self._snapshot_root(create=True)
        if root is None:
            return existing
        projection = self._snapshot_projection(item)
        authoritative_jobs = projection.pop("_jobs_authoritative", False) is True
        saved = existing or {"schema_version": 1, "run_id": run_id, "summary": {}, "detail": None}
        summary = self._merge_snapshot_values(saved.get("summary"), projection)
        full = self._merge_snapshot_values(saved.get("detail"), projection) if detail else saved.get("detail")
        if authoritative_jobs:
            # A hash-bound sealed accepted-role receipt is the current source of
            # shortlist identities. Reconcile old normalized rows against that
            # set so a stale alias cannot survive as a second shortlisted job.
            # Matching rows retain recorded build facts; absent rows are dropped.
            summary["jobs"] = self._merge_authoritative_jobs(
                (saved.get("summary") or {}).get("jobs"), projection.get("jobs"))
            if detail:
                full["jobs"] = self._merge_authoritative_jobs(
                    (saved.get("detail") or {}).get("jobs"), projection.get("jobs"))
        updated_view = {"schema_version": 1, "run_id": run_id, "summary": summary, "detail": full}
        fingerprint = hashlib.sha256(json.dumps(updated_view, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if existing and existing.get("fingerprint") == fingerprint:
            return existing
        recorded_at = datetime.now(timezone.utc).isoformat()
        payload = {**updated_view, "recorded_at": recorded_at, "fingerprint": fingerprint}
        path = root / self._snapshot_filename(run_id)
        temp_path = None
        try:
            if path.is_symlink() or self.data._safe(path, root) != path:
                return existing
            fd, name = tempfile.mkstemp(prefix=".history-", suffix=".tmp", dir=str(root))
            temp_path = Path(name)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
            temp_path = None
            try:
                directory_fd = os.open(root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
            os.chmod(root, 0o700)
            return payload
        except (OSError, StudioDataError, TypeError, ValueError):
            return existing
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @classmethod
    def _merge_authoritative_jobs(cls, old: Any, new: Any) -> list[dict]:
        if not isinstance(new, list):
            return old if isinstance(old, list) else []
        accepted_paths = {row.get("role_path") for row in new
                          if isinstance(row, dict) and isinstance(row.get("role_path"), str)}
        prior = [row for row in old if isinstance(row, dict) and row.get("role_path") in accepted_paths] \
            if isinstance(old, list) else []
        merged = cls._merge_snapshot_values(prior, new, "jobs")
        return merged if isinstance(merged, list) else []

    def _with_snapshot(self, item: dict, *, detail: bool, transient: bool) -> dict:
        existing = self._load_snapshot(str(item.get("id", "")))
        saved = self._write_snapshot(item, detail=detail, transient=transient)
        if transient and saved:
            # A malformed or truncated current read cannot replace good local
            # evidence; serve the last durable view and make the limitation clear.
            view = saved.get("detail") or saved.get("summary") or item
        else:
            view = item
        result = {**view, "snapshot_status": "saved" if saved else "unavailable",
                  "snapshot_recorded_at": saved.get("recorded_at") if saved else None}
        result.pop("_jobs_authoritative", None)
        if transient:
            result["snapshot_warning"] = ("Run receipts changed or were incomplete during this read; the previous saved snapshot was kept."
                                           if saved else "Run receipts were incomplete during this read; no complete local snapshot is available.")
        elif saved is None:
            result["snapshot_warning"] = "A local history snapshot could not be saved."
        return result

    def _recover_snapshots(self, live_ids: set) -> list[dict]:
        root = self._snapshot_root(create=False)
        if root is None:
            return []
        result = []
        try:
            paths = sorted(root.glob("*.json"), key=lambda path: path.name)[:1000]
        except OSError:
            return []
        for path in paths:
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
                    continue
                safe = self.data._safe(path, root)
                saved = json.loads(safe.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError, StudioDataError):
                continue
            if not isinstance(saved, dict) or saved.get("schema_version") != 1:
                continue
            saved = self._sanitize_saved_snapshot(saved)
            run_id = saved.get("run_id")
            if (not isinstance(run_id, str) or run_id in live_ids or
                    path.name != self._snapshot_filename(run_id)):
                continue
            item = saved.get("detail") or saved.get("summary")
            if not isinstance(item, dict) or item.get("id") != run_id:
                continue
            recovered = {**item, "snapshot_status": "recovered", "snapshot_recorded_at": saved.get("recorded_at"),
                         "snapshot_warning": "Original run receipts are unavailable; this view uses the last saved Studio snapshot."}
            result.append(recovered)
        return result

    def _continuations(self, parent_run_id: str) -> list[dict]:
        manual_root = self.data.auto / "evidence" / "manual"
        listing = _Reader(self.data, manual_root)
        queue_reader = _Reader(self.data, self.data.auto)
        queue = queue_reader.read(self.data.auto / "local" / "completion-queue.json")
        queue_items = queue.get("items", []) if isinstance(queue, dict) else []
        queue_items = [row for row in queue_items if isinstance(row, dict)] if isinstance(queue_items, list) else []

        def canonical_role(value: Any, *, legacy: bool = False) -> str | None:
            if not isinstance(value, str) or not value.strip():
                return None
            normalized = value.replace("\\", "/")
            try:
                candidate = (self.data.role_root / normalized.removeprefix("Tailored CVs/")
                             if normalized.startswith("Tailored CVs/") else Path(value))
                safe = self.data._safe(candidate, self.data.role_root)
                relative = safe.relative_to(self.data.role_root)
                if relative.parts:
                    return "Tailored CVs/" + relative.as_posix()
            except (OSError, StudioDataError, ValueError):
                pass
            # Legacy fixtures used a synthetic absolute workspace path and did
            # not carry a public source identity or canonical directory.
            return self._role_path(value) if legacy else None

        result = []
        for folder in listing.entries(manual_root):
            if folder.is_symlink() or not folder.is_dir():
                continue
            reader = _Reader(self.data, folder)
            receipt = reader.read(folder / "continuation.json")
            authorization = reader.read(folder / "continuation-authorization.json")
            legacy = isinstance(receipt, dict) and receipt.get("kind") == "authorized_completion_continuation_v1"
            current_authorization = (isinstance(authorization, dict) and
                authorization.get("authorization") == "standingexplicitcompletioncontinuation" and
                authorization.get("no_new_hunt") is True and
                authorization.get("original_batch_deadline_unchanged") is True)
            if not legacy and not current_authorization:
                continue
            identity = authorization if current_authorization else receipt
            if identity.get("parent_run") != parent_run_id:
                continue
            role_path = canonical_role(identity.get("accepted_role_path"), legacy=legacy)
            if not role_path:
                continue
            queue_item = next((row for row in queue_items if row.get("parent_run") == parent_run_id and
                               canonical_role(row.get("accepted_role_path"), legacy=legacy) == role_path), {})

            def first_receipt(*names: str) -> dict:
                return next((row for name in names if isinstance((row := reader.read(folder / name)), dict)), {})

            def status(row: dict) -> str | None:
                raw = row.get("status")
                return _safe_text(str(raw)) if raw is not None else None

            build_receipt = first_receipt("build-completion.v5.json", "build-terminal.json")
            audit_receipt = first_receipt("independent-build-audit-v5/receipt.json",
                                          "independent-build-audit/receipt.json")
            approval_receipt = first_receipt("sol-high-approval.json")
            sync_receipt = first_receipt("sync-v8/sync-receipt.json", "ledger-runtime.json")
            completion_receipt = first_receipt("completion-receipt.json")

            def role_matches(row: dict) -> bool:
                saved_role = row.get("role_path")
                return saved_role is None or canonical_role(saved_role, legacy=legacy) == role_path

            gate_receipts = (build_receipt, audit_receipt, approval_receipt, sync_receipt, completion_receipt)
            if current_authorization and any(not role_matches(row) for row in gate_receipts if row):
                continue

            build_status = status(build_receipt)
            audit_status = status(audit_receipt)
            approval_status = status(approval_receipt)
            sync_status = status(sync_receipt)
            completion_status = status(completion_receipt)
            artifact_recorded = bool(isinstance(build_receipt, dict) and (
                build_receipt.get("artifact_recorded") is True or
                _timestamp(build_receipt.get("artifact_completed_at")) or
                build_receipt.get("artifact_status") in ("recorded", "verified") or
                build_status in ("ARTIFACT_COMPLETE", "ARTIFACT_READY", "DONE", "COMPLETE")))
            remaining = queue_item.get("remaining_stages") if isinstance(queue_item.get("remaining_stages"), list) else []
            raw_queue_status = str(queue_item.get("status") or "")
            queue_status = _status(raw_queue_status)
            complete = (queue_status == "done" and not remaining and artifact_recorded and
                        audit_status in ("PASS", "PASSED", "DONE", "COMPLETE") and
                        approval_status == "APPROVED" and
                        sync_status in ("VERIFIED", "SYNCED", "DONE", "COMPLETE") and
                        completion_status in ("DONE", "COMPLETE"))
            projected_status = ("complete" if complete else "completion_required" if raw_queue_status.casefold() == "completion_required"
                                else "partial" if queue_status == "done" else queue_status)
            counts = {}
            for key, field in (("cvs_built", "completed_cvs"), ("letters_completed", "completed_letters"),
                               ("rows_synced", "rows_synced")):
                value = _number(completion_receipt.get(field), integer=True)
                if value is not None:
                    counts[key] = value
            result.append({"id": folder.name, "parent_run_id": parent_run_id,
                "status": projected_status,
                "raw_status": _safe_text(queue_item.get("status")), "role_path": role_path,
                "role_id": _safe_text(queue_item.get("role_id") or queue_item.get("job_id")),
                "cv_built": True if artifact_recorded else False if isinstance(build_receipt, dict) and _status(build_receipt.get("status")) == "failed" else None,
                "started_at": _first_time(identity, ("actual_start", "started_at")),
                "completed_at": _first_time(completion_receipt, ("completed_at", "finished_at")),
                "global_deadline": _first_time(identity, ("global_deadline",)),
                "dispatch_cutoff": _first_time(identity, ("dispatch_cutoff",)),
                "remaining_stages": [_safe_text(value) for value in remaining if isinstance(value, str)],
                "build_status": build_status, "build_audit_status": audit_status,
                "approval_status": approval_status, "sync_status": sync_status,
                "completion_status": completion_status,
                "stages": {"build": build_status, "build_audit": audit_status,
                           "sol_review": approval_status, "sync": sync_status},
                "counts": counts, "next_action": _safe_text(queue_item.get("next_action"))})
        return sorted(result, key=lambda row: (row.get("started_at") or "", row["id"]))

    @staticmethod
    def _role_path(value: Any) -> str | None:
        if isinstance(value, dict):
            value = value.get("role_path") or value.get("path") or value.get("role")
        if not isinstance(value, str) or not value.strip():
            return None
        name = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        name = _safe_text(name, 120)
        if not name or name in (".", ".."):
            return None
        return "Tailored CVs/" + name

    def _sealed_roles(self, reader: _Reader) -> dict | None:
        """Read only sealed Hunt Audit decisions, never proposal/ad bodies."""
        audit = reader.base / "hunt_audit"
        folders = [audit]
        folders.extend(path for path in reader.entries(audit)
                       if path.is_dir() and not path.is_symlink())
        names = ("approved_roles.json", "accepted-role-paths.json", "audit_review.json",
                 "weekday_engine_terminal.json")
        for folder in folders:
            for name in names:
                receipt_path = folder / name
                receipt = reader.read(receipt_path)
                if not isinstance(receipt, dict):
                    continue
                status = str(receipt.get("status", "")).upper()
                if receipt.get("sealed") is not True and status not in ("SEALED", "APPROVED"):
                    continue
                paths = receipt.get("accepted_role_paths")
                if not isinstance(paths, list):
                    approved_count = next((_number(receipt.get(key), integer=True) for key in
                        ("approved_roles_n", "roles_promoted_and_audit_approved", "accepted_role_count")
                        if _number(receipt.get(key), integer=True) is not None), None)
                    if approved_count is not None:
                        return {"count": approved_count, "jobs": [], "jobs_authoritative": False}
                    continue
                materialized = self._materialized_role_paths(reader, receipt_path, receipt, paths)
                roles = receipt.get("roles") if isinstance(receipt.get("roles"), list) else []
                by_path = {}
                for row in roles:
                    if not isinstance(row, dict):
                        continue
                    key = self._role_path(row)
                    if key:
                        by_path[key] = row
                decisions = receipt.get("decisions") if isinstance(receipt.get("decisions"), list) else []
                instructions = receipt.get("materialization_instructions")
                candidate_id = instructions.get("candidate_id") if isinstance(instructions, dict) else None
                decision = next((row for row in decisions if isinstance(row, dict)
                                 and row.get("candidate_id") == candidate_id
                                 and str(row.get("status") or row.get("decision") or "").upper()
                                 in ("ACCEPTED", "APPROVED")), None)
                accepted = []
                seen = set()
                for path in paths:
                    source_path = path if isinstance(path, str) else (
                        path.get("role_path") or path.get("path") or path.get("role")
                        if isinstance(path, dict) else None)
                    role_path = materialized.get(source_path) if isinstance(source_path, str) else None
                    role_path = role_path or self._role_path(path)
                    if not role_path or role_path in seen:
                        continue
                    seen.add(role_path)
                    detail = path if isinstance(path, dict) else {}
                    metadata = by_path.get(role_path, {})
                    # A one-role materialization receipt binds its candidate ID to
                    # the sealed decision. Do not infer identity from a proposal
                    # slug or temporary folder basename.
                    accepted_decision = decision if len(paths) == 1 else None
                    role_id = metadata.get("job_id") or metadata.get("role_id")
                    if accepted_decision:
                        role_id = role_id or accepted_decision.get("job_id") or accepted_decision.get("role_id")
                    if isinstance(detail, dict):
                        role_id = role_id or detail.get("job_id") or detail.get("role_id")
                    accepted.append({
                        "role_path": role_path,
                        "role_id": _safe_text(role_id),
                        "company": _safe_text(metadata.get("company") or
                                              (detail.get("company") if isinstance(detail, dict) else None) or
                                              (accepted_decision.get("company") if accepted_decision else None)),
                        "title": _safe_text(metadata.get("title") or metadata.get("job_title") or metadata.get("name") or
                                            (detail.get("title") if isinstance(detail, dict) else None) or
                                            (accepted_decision.get("title") if accepted_decision else None))})
                return {"count": len(accepted), "jobs": accepted, "jobs_authoritative": True,
                        "materialized_paths": materialized, "source_receipt": receipt_path}
        return None

    def _materialized_role_paths(self, reader: _Reader, source_path: Path, receipt: dict,
                                 accepted_paths: list[Any]) -> dict[str, str]:
        """Use only a materialization map hash-bound to this sealed audit receipt."""
        materialization_path = reader.base / "accepted-role-paths.materialized.v1.json"
        materialization = reader.read(materialization_path)
        if not isinstance(materialization, dict) or materialization.get("kind") != "sealed_accepted_role_materialization_v1":
            return {}
        digest = materialization.get("source_handoff_sha256")
        if not isinstance(digest, str) or digest != reader.hashes.get(source_path):
            return {}
        source_handoff = materialization.get("source_handoff")
        if not isinstance(source_handoff, str):
            return {}
        try:
            source_handoff_path = self.data._safe(Path(source_handoff), reader.base)
            actual_source_path = self.data._safe(source_path, reader.base)
        except (OSError, StudioDataError, ValueError):
            return {}
        if source_handoff_path != actual_source_path:
            return {}

        mapping = materialization.get("mapping")
        destinations = materialization.get("accepted_role_paths")
        if not isinstance(mapping, dict) or not isinstance(destinations, list):
            return {}
        source_values = set()
        for value in accepted_paths:
            if isinstance(value, str):
                source_values.add(value)
            elif isinstance(value, dict):
                raw = value.get("role_path") or value.get("path") or value.get("role")
                if isinstance(raw, str):
                    source_values.add(raw)

        approved_destinations = set()
        for destination in destinations:
            if not isinstance(destination, str):
                continue
            try:
                safe = self.data._safe(Path(destination), self.data.role_root)
                if safe != self.data.role_root and safe.is_relative_to(self.data.role_root):
                    approved_destinations.add(safe)
            except (OSError, StudioDataError, ValueError):
                continue

        result = {}
        for source, destination in mapping.items():
            if source not in source_values or not isinstance(destination, str):
                continue
            try:
                safe = self.data._safe(Path(destination), self.data.role_root)
                if safe not in approved_destinations:
                    continue
                relative = safe.relative_to(self.data.role_root)
            except (OSError, StudioDataError, ValueError):
                continue
            if relative.parts and all(part not in (".", "..") for part in relative.parts):
                result[source] = "Tailored CVs/" + relative.as_posix()
        return result

    @staticmethod
    def _apply_role_milestones(jobs: list[dict], milestones: list[dict]) -> list[dict]:
        """Join only the current verified chain to public source identities."""
        result = list(jobs)
        by_id = {row.get("role_id"): row for row in result if row.get("role_id")}
        grouped: dict[str, list[dict]] = {}
        for row in milestones:
            if isinstance(row, dict) and row.get("current") is True:
                grouped.setdefault(row["role_id"], []).append(row)
        stage_order = {"hunt_audit": 0, "artifact": 1, "build_audit": 2, "sol_review": 3, "sync": 4}
        for role_id, rows in grouped.items():
            audit = next((row for row in rows if row["stage"] == "hunt_audit"), None)
            if not audit:
                continue
            job = by_id.get(role_id)
            if job is None:
                job = {"role_id": role_id, "role": audit["role"], "company": None,
                       "title": audit["role"], "role_path": None, "status": "shortlisted",
                       "cv_built": None, "duration_seconds": None}
                result.append(job)
                by_id[role_id] = job
            job["role"] = audit["role"]
            job["milestones"] = sorted(rows, key=lambda row: (row.get("at", ""), stage_order[row["stage"]]))
            if audit.get("status") != "accepted":
                if job.get("status") in {"complete", "sol_approved", "build_audited", "cv_built"}:
                    job["previous_outcome"] = {"status": job.get("status"), "cv_built": job.get("cv_built"),
                                                "continuation_id": job.get("continuation_id")}
                job["status"] = "hunt_audit_" + audit["status"]
                continue
            last = max(rows, key=lambda row: stage_order[row["stage"]])
            stage, status = last["stage"], last["status"]
            if stage == "hunt_audit":
                job["status"], job["cv_built"] = "shortlisted", None
            elif stage == "artifact":
                job["status"], job["cv_built"] = "cv_built", True
            elif stage == "build_audit":
                job["status"] = "build_audited" if status == "passed" else "build_audit_" + status
                if status == "passed":
                    job["cv_built"] = True
            elif stage == "sol_review":
                job["status"] = "sol_approved" if status == "approved" else "sol_review_" + status
            else:
                job["status"] = "complete" if status == "synced" else "sync_" + status
            if any(row["stage"] == "artifact" and row["status"] == "done" for row in rows):
                job["cv_built"] = True
        return sorted(result, key=lambda row: (row.get("company") or "", row.get("title") or row.get("role") or row.get("role_path") or ""))

    @staticmethod
    def _job_outcomes(accepted: list[dict], builds: list[dict], continuations: list[dict]) -> list[dict]:
        jobs: dict[str, dict] = {}
        by_id: dict[str, dict] = {}
        for row in accepted:
            if not isinstance(row, dict) or not row.get("role_path"):
                continue
            jobs[row["role_path"]] = {**row, "status": "shortlisted", "cv_built": None,
                                      "duration_seconds": None}
            if row.get("role_id"):
                by_id[str(row["role_id"])] = jobs[row["role_path"]]
        for build in builds:
            existing = by_id.get(str(build.get("role_id"))) if build.get("role_id") else None
            role_path = build.get("role_path")
            if not isinstance(role_path, str) or not role_path.startswith("Tailored CVs/"):
                role_path = RunHistory._role_path(build.get("role") or build.get("name"))
            if not role_path and existing is None:
                continue
            job = existing or jobs.setdefault(role_path, {"role_path": role_path, "role_id": build.get("role_id"),
                                              "company": _safe_text(build.get("company")),
                                              "title": _safe_text(build.get("name")),
                                              "status": "shortlisted", "cv_built": None,
                                              "duration_seconds": None})
            if build.get("role_id"):
                job["role_id"] = build["role_id"]
                by_id[str(build["role_id"])] = job
            if not job.get("company"):
                job["company"] = _safe_text(build.get("company"))
            if not job.get("title"):
                job["title"] = _safe_text(build.get("name"))
            built = build.get("artifact_recorded") is True or _timestamp(build.get("completed_at")) is not None
            job["cv_built"] = True if built else False if build.get("status") == "failed" else job.get("cv_built")
            job["duration_seconds"] = _number(build.get("duration_seconds"))
            if built:
                job["status"] = "cv_built"
            elif build.get("status") == "failed":
                job["status"] = "build_failed"
        for continuation in continuations:
            existing = by_id.get(str(continuation.get("role_id"))) if continuation.get("role_id") else None
            role_path = RunHistory._role_path(continuation.get("role_path"))
            if not role_path and existing is None:
                continue
            job = existing or jobs.setdefault(role_path, {"role_path": role_path, "role_id": continuation.get("role_id"),
                                              "company": None, "title": None,
                                              "status": "shortlisted", "cv_built": None,
                                              "duration_seconds": None})
            if continuation.get("role_id"):
                job["role_id"] = continuation["role_id"]
                by_id[str(continuation["role_id"])] = job
            if continuation.get("cv_built") is not None:
                job["cv_built"] = continuation["cv_built"]
            job["continuation_id"] = continuation.get("id")
            status = continuation.get("status")
            if status == "completion_required":
                job["status"] = "completion_required"
            elif status == "complete":
                job["status"] = "complete"
        return sorted(jobs.values(), key=lambda row: (row.get("company") or "", row.get("title") or row.get("role") or row.get("role_path") or ""))

    def _authorized_build_role_paths(self, reader: _Reader, sealed_roles: dict | None) -> dict[str, str]:
        """Bind a build worker's short alias through sealed materialization evidence."""
        if not sealed_roles or not sealed_roles.get("jobs_authoritative"):
            return {}
        source_receipt = sealed_roles.get("source_receipt")
        if not isinstance(source_receipt, Path):
            return {}
        source_digest = reader.hashes.get(source_receipt)
        materialization = reader.read(reader.base / "accepted-role-paths.materialized.v1.json")
        if not isinstance(materialization, dict) or materialization.get("kind") != "sealed_accepted_role_materialization_v1":
            return {}
        if materialization.get("source_handoff_sha256") != source_digest:
            return {}
        try:
            source_handoff = self.data._safe(Path(materialization.get("source_handoff", "")), reader.base)
            if source_handoff != self.data._safe(source_receipt, reader.base):
                return {}
        except (OSError, StudioDataError, ValueError):
            return {}

        accepted_paths = set(sealed_roles.get("materialized_paths", {}).values())
        if not accepted_paths:
            return {}
        result: dict[str, str] = {}
        ambiguous: set[str] = set()
        for folder in reader.entries(reader.base / "build"):
            if folder.is_symlink() or not folder.is_dir():
                continue
            authorization = reader.read(folder / "dispatch-authorization.json")
            contract = reader.read(folder / "contract.json")
            if not isinstance(authorization, dict) or not isinstance(contract, dict):
                continue
            embedded = authorization.get("accepted_materialization")
            if not isinstance(embedded, dict) or any(
                    embedded.get(key) != materialization.get(key)
                    for key in ("kind", "source_handoff", "source_handoff_sha256", "mapping", "accepted_role_paths")):
                continue
            canonical = authorization.get("canonical_role_path")
            if not isinstance(canonical, str):
                continue
            try:
                safe_canonical = self.data._safe(Path(canonical), self.data.role_root)
                relative = safe_canonical.relative_to(self.data.role_root)
            except (OSError, StudioDataError, ValueError):
                continue
            normalized = "Tailored CVs/" + relative.as_posix()
            if normalized not in accepted_paths:
                continue
            assigned = contract.get("assigned_role_paths")
            if not isinstance(assigned, list):
                continue
            contract_targets = set()
            for entry in assigned:
                value = entry.get("path") if isinstance(entry, dict) else entry
                if not isinstance(value, str):
                    continue
                try:
                    contract_targets.add(self.data._safe(Path(value), self.data.role_root))
                except (OSError, StudioDataError, ValueError):
                    continue
            if safe_canonical not in contract_targets:
                continue
            contract_id = contract.get("contract_id")
            if not isinstance(contract_id, str) or not contract_id:
                continue
            if contract_id in result and result[contract_id] != normalized:
                ambiguous.add(contract_id)
            result[contract_id] = normalized
        for contract_id in ambiguous:
            result.pop(contract_id, None)
        return result

    @staticmethod
    def _policy4_role_milestones(history_metrics: dict) -> list[dict]:
        """Project identity-bound receipts, invalidating stale positive stages."""
        raw = history_metrics.get("role_milestones", []) if isinstance(history_metrics, dict) else []
        if not isinstance(raw, list):
            return []
        allowed = {"hunt_audit": {"accepted", "rejected", "held"}, "artifact": {"done"},
                   "build_audit": {"passed", "failed", "held", "partial"},
                   "sol_review": {"approved", "rejected", "held"},
                   "sync": {"synced", "failed", "held", "pending", "partial"}}
        success = {"hunt_audit": "accepted", "artifact": "done", "build_audit": "passed",
                   "sol_review": "approved", "sync": "synced"}
        result, labels, current = [], {}, {}
        ordered = sorted(enumerate(raw), key=lambda item: (_timestamp(item[1].get("at")) or "", item[0])
                         if isinstance(item[1], dict) else ("", item[0]))
        for raw_index, row in ordered:
            if not isinstance(row, dict) or row.get("kind") != "role_milestone_receipt":
                continue
            stage, status, role_id = row.get("stage"), row.get("status"), row.get("role_id")
            if (stage not in allowed or status not in allowed[stage] or not isinstance(role_id, str) or
                    not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", role_id)):
                continue
            role = _safe_text(row.get("role"), 160)
            at, worker = _timestamp(row.get("at")), _identifier(row.get("worker_id"))
            if not role or "[address omitted]" in role or "[reference omitted]" in role or not at or not worker:
                continue
            if role_id in labels and labels[role_id] != role:
                continue
            labels[role_id] = role
            evidence, predecessor = row.get("evidence_sha256"), row.get("predecessor_sha256")
            valid_sha = lambda value: isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
            if (status == success[stage] or stage == "hunt_audit" and status in ("accepted", "rejected")) and not valid_sha(evidence):
                continue
            if evidence is not None and not valid_sha(evidence):
                continue
            if predecessor is not None and not valid_sha(predecessor):
                continue
            failure = _identifier(row.get("failure_class"))
            if status in ("held", "failed") and not failure:
                continue
            state = current.setdefault(role_id, {})
            prior = None
            if stage != "hunt_audit":
                prior_stage = {"artifact": "hunt_audit", "build_audit": "artifact",
                               "sol_review": "build_audit", "sync": "sol_review"}[stage]
                prior = state.get(prior_stage)
                needed = {"artifact": "accepted", "build_audit": "done",
                          "sol_review": "passed", "sync": "approved"}[stage]
                if not prior or prior["status"] != needed or prior["at"] > at or prior["role"] != role:
                    continue
                if predecessor is not None and predecessor != prior.get("evidence_sha256"):
                    continue
                if status == success[stage] and predecessor != prior.get("evidence_sha256"):
                    continue
            projected = {"kind": "role_milestone_receipt", "stage": stage, "worker_id": worker,
                         "role_id": role_id, "role": role, "at": at, "status": status,
                         "_index": len(result)}
            if valid_sha(evidence):
                projected["evidence_sha256"] = evidence
            if predecessor:
                projected["predecessor_sha256"] = predecessor
            if failure:
                projected["failure_class"] = failure
            result.append(projected)
            if stage == "hunt_audit":
                current[role_id] = {"hunt_audit": projected}
            else:
                if stage == "artifact":
                    state["artifact"] = projected
                    state.pop("build_audit", None)
                    state.pop("sol_review", None)
                    state.pop("sync", None)
                elif stage == "build_audit":
                    state["build_audit"] = projected
                    state.pop("sol_review", None)
                    state.pop("sync", None)
                elif stage == "sol_review":
                    state["sol_review"] = projected
                    state.pop("sync", None)
                else:
                    state["sync"] = projected
        for row in result:
            row["current"] = any(row is value for value in current.get(row["role_id"], {}).values())
            row.pop("_index", None)
        return result

    @staticmethod
    def _metric_stage_projection(history_metrics: dict, stages: dict, policy_version: Any) -> None:
        records = history_metrics.get("records", []) if isinstance(history_metrics, dict) else []
        if not isinstance(records, list):
            records = []
        by_stage: dict[str, list[dict]] = {}
        for row in records:
            if isinstance(row, dict) and row.get("kind") == "stage_owner_receipt" and row.get("stage") in stages:
                by_stage.setdefault(row["stage"], []).append(row)
        count_mapping = {"hunt": ("jobs_found", "jobs_proposed", "ads_reviewed"),
                         "hunt_audit": ("jobs_shortlisted",), "build": ("cvs_built",),
                         "sync": (("roles_synced",) if policy_version == 4 else ("rows_synced",))}
        for stage_id, rows in by_stage.items():
            stage = stages[stage_id]
            statuses = [str(row.get("status", "")).lower() for row in rows]
            lanes = {str(row.get("lane_id") or row.get("source") or "").casefold().replace("-", "_") for row in rows}
            if "running" in statuses:
                stage.update(status="running", raw_status="RUNNING")
            elif stage_id == "hunt" and policy_version == 4 and not {
                    "hellowork", "france_travail", "linkedin", "apec", "wttj"}.issubset(lanes):
                stage.update(status="partial", raw_status="PARTIAL")
            elif statuses and all(value == "done" for value in statuses):
                stage.update(status="done", raw_status="DONE")
            elif statuses and len(set(statuses)) == 1:
                stage.update(status=_status(statuses[0]), raw_status=statuses[0].upper())
            elif statuses:
                stage.update(status="partial", raw_status="MIXED_SAVED_OUTCOMES")
            models = [_model(row, "stage_owner_receipt") for row in rows]
            stage.update(_model_fields(stage["models"] + [model for model in models if model]))
            for metric in count_mapping.get(stage_id, ()):
                values = [_count(row, metric) for row in rows]
                if values and all(value is not None for value in values):
                    stage[metric] = sum(values)
            if stage_id == "hunt_audit" and stage["jobs_shortlisted"] is None:
                role_timeline = RunHistory._policy4_role_milestones(history_metrics)
                latest_decisions = {row["role_id"]: row for row in role_timeline
                                    if row["stage"] == "hunt_audit" and row.get("current") is True}
                accepted = {role_id for role_id, row in latest_decisions.items() if row["status"] == "accepted"}
                if latest_decisions and all(row["status"] in ("accepted", "rejected") for row in latest_decisions.values()):
                    stage["jobs_shortlisted"] = len(accepted)
            if stage_id == "build" and stage["cvs_built"] is None and policy_version == 4:
                role_timeline = RunHistory._policy4_role_milestones(history_metrics)
                completed = {row["role_id"] for row in role_timeline if row["stage"] == "artifact" and
                             row["status"] == "done" and row.get("current") is True}
                if completed:
                    stage["cvs_built"] = len(completed)
            if stage_id == "sync" and stage["roles_synced"] is None and policy_version == 4:
                role_timeline = RunHistory._policy4_role_milestones(history_metrics)
                synced = {row["role_id"] for row in role_timeline if row["stage"] == "sync" and
                          row["status"] == "synced" and row.get("current") is True}
                if synced:
                    stage["roles_synced"] = len(synced)
            starts = [_first_time(row, ("started_at",)) for row in rows if _first_time(row, ("started_at",))]
            ends = [_first_time(row, ("finished_at",)) for row in rows if _first_time(row, ("finished_at",))]
            if starts or ends:
                stage.update(RunHistory._timing(min(starts, key=_time) if starts else None,
                    max(ends, key=_time) if ends and len(ends) == len(rows) else None, stage["status"]))
            usages = [_usage(row) for row in rows]
            usages = [item for item in usages if item["status"] != "unavailable" or item["account_observations"]]
            if len(usages) == 1:
                stage["usage"] = usages[0]
            elif len(usages) > 1:
                stage["usage"] = {**_empty_usage(), "status": "partial",
                                   "account_observations": [observation for item in usages for observation in item["account_observations"]],
                                   "note": "Usage snapshots are saved per owner receipt; overlapping account windows are not summed."}
            stage["usage_counters"] = _unique_worker_usages(usages)

        # New run metrics keep one final native snapshot per worker thread.
        # Preserve these counters even when the matching owner receipt was not
        # included; older unqualified cumulative observations remain excluded.
        snapshots = history_metrics.get("worker_usage_snapshots")
        if isinstance(snapshots, list):
            counters_by_stage: dict[str, list[dict]] = {}
            for row in snapshots:
                if not isinstance(row, dict):
                    continue
                stage_id = _stage(row.get("stage"))
                if stage_id not in stages:
                    continue
                usage = _usage(row)
                if usage.get("usage_binding_verified"):
                    counters_by_stage.setdefault(stage_id, []).append(usage)
            for stage_id, counters in counters_by_stage.items():
                stage = stages[stage_id]
                stage["usage_counters"] = _unique_worker_usages(
                    stage.get("usage_counters", []) + counters)

    def enrich(self, run: dict, path: Path | None = None, *, detail: bool = True) -> dict:
        run = dict(run)
        stages = {key: {"id": key, "label": label, "status": "not_recorded", "raw_status": None,
                        **_model_fields([]), "started_at": None, "finished_at": None,
                        "duration_seconds": None, "elapsed_seconds": None, "timing_status": "unavailable",
                        "usage": _empty_usage(), "jobs_found": None, "jobs_proposed": None,
                        "jobs_shortlisted": None, "cvs_built": None, "ads_reviewed": None,
                        "rows_synced": None, "roles_synced": None,
                        "observations": [], "failures": [], "attempts": []}
                  for key, label, _route in _STAGES}
        reader = None
        metrics: dict[str, Any] = {key: None for key in _COUNT_KEYS}
        failures: list[dict] = []
        observations: list[str] = []
        builds: list[dict] = []
        jobs: list[dict] = []
        continuations: list[dict] = []
        role_milestones: list[dict] = []
        history_metrics: dict[str, Any] = {}
        sealed_roles: dict | None = None
        usage = _empty_usage()
        timing = self._timing(run.get("started_at"), run.get("ended_at"), run.get("status"))
        if run.get("kind") == "legacy_stage":
            metrics, builds, usage = self._legacy(run, stages, detail)
        else:
            try:
                candidate = Path(path) if path is not None else self.data.evidence / str(run["id"])
                if candidate.is_symlink():
                    raise StudioDataError("That run is outside the history workspace.", "unsafe_path", 403)
                base = self.data._safe(candidate, self.data.evidence)
                reader = _Reader(self.data, base)
                terminal, terminal_name = reader.first(_RUN_TERMINALS)
                manifest, manifest_name = reader.first(_RUN_MANIFESTS)
                closed, closed_name = reader.first(("batch_closed.json", "batch-closed.json"))
                outcomes = reader.read(base / "stage-outcomes.json")
                outcomes = outcomes if isinstance(outcomes, dict) else {}
                history_metrics = reader.read(base / "stage-metrics.json")
                history_metrics = history_metrics if isinstance(history_metrics, dict) and history_metrics.get("kind") == "scheduled_run_metrics_v1" else {}
                # Native rollout counters are owned by a private Studio sidecar.
                # Merge only after its envelope and counter proofs validate; the
                # pipeline's immutable stage-metrics receipt is never rewritten.
                session_root = getattr(getattr(self.data._live, "reader", None), "sessions_dir", None)
                native_rows = _load_native_usage_snapshot(self.data.local, str(run.get("id", "")),
                    workspace=self.data.workspace, sessions_root=session_root)
                if native_rows:
                    history_metrics = dict(history_metrics)
                    prior_snapshots = history_metrics.get("worker_usage_snapshots")
                    prior_snapshots = prior_snapshots if isinstance(prior_snapshots, list) else []
                    history_metrics["worker_usage_snapshots"] = prior_snapshots + [
                        {**row, "_native_usage_sidecar_validated": _NATIVE_USAGE_VALIDATION}
                        for row in native_rows]
                if not terminal:
                    terminal = _pre_dispatch_failure(reader.read(base / "launch-failure.json"), manifest, history_metrics)
                    if terminal:
                        terminal_name = "launch-failure.json"
                        if failure := _failure(terminal, "pipeline", terminal_name):
                            failures.append(failure)
                policy_version = manifest.get("policy_version") if isinstance(manifest.get("policy_version"), int) else history_metrics.get("policy_version")
                run.update({"policy_version": policy_version,
                            "workflow_name": history_metrics.get("workflow_name") or run.get("workflow_name"),
                            "workflow_mode": history_metrics.get("workflow_mode") or run.get("workflow_mode"),
                            "scheduled_at": history_metrics.get("scheduled_at"),
                            "startup_delay_seconds": _number(history_metrics.get("startup_delay_seconds"))})
                if policy_version == 4:
                    role_milestones = self._policy4_role_milestones(history_metrics)
                self._metric_stage_projection(history_metrics, stages, policy_version)
                if terminal_name != "launch-failure.json":
                    if failure := _failure(history_metrics, "pipeline", "stage-metrics.json"):
                        failures.append(failure)
                self._stage_entries(manifest, manifest_name, stages, configured=True)
                unstarted = [r for r in history_metrics.get("records", []) if isinstance(r, dict)
                             and r.get("status") == "not_run" and not r.get("started_at")]
                self._stage_entries({"stages": unstarted}, "stage-metrics.json", stages)
                self._stage_entries(outcomes, "stage-outcomes.json", stages)
                self._stage_entries(terminal, terminal_name, stages)
                self._stage_entries(closed, closed_name, stages)
                for row in (terminal, closed, outcomes, manifest):
                    for key in metrics:
                        if key == "jobs_shortlisted":
                            continue
                        if metrics[key] is None:
                            metrics[key] = _count(row, key)
                sealed_roles = self._sealed_roles(reader)
                accepted_milestones = {row["role_id"]: row for row in role_milestones
                                       if row["stage"] == "hunt_audit" and row["status"] == "accepted" and row.get("current") is True}
                if policy_version == 4 and (accepted_milestones or role_milestones):
                    metrics["jobs_shortlisted"] = len(accepted_milestones)
                    if sealed_roles is not None:
                        sealed_jobs = [row for row in sealed_roles["jobs"] if row.get("role_id") in accepted_milestones]
                        jobs = self._job_outcomes(sealed_jobs, [], [])
                        for role_id, milestone in accepted_milestones.items():
                            if not any(job.get("role_id") == role_id for job in jobs):
                                jobs.append({"role_id": role_id, "role": milestone["role"],
                                    "company": None, "title": milestone["role"], "role_path": None,
                                    "status": "shortlisted", "cv_built": None, "duration_seconds": None})
                    else:
                        jobs = [{"role_id": role_id, "role": row["role"], "company": None,
                                 "title": row["role"], "role_path": None, "status": "shortlisted",
                                 "cv_built": None, "duration_seconds": None}
                                for role_id, row in accepted_milestones.items()]
                    jobs = self._apply_role_milestones(jobs, role_milestones)
                elif sealed_roles is not None:
                    metrics["jobs_shortlisted"] = sealed_roles["count"]
                    jobs = self._job_outcomes(sealed_roles["jobs"], [], [])
                elif stages["hunt_audit"]["status"] == "not_run":
                    metrics["jobs_shortlisted"] = 0
                started = _first_time(manifest, ("batch_started_at", "invocation_started_at", "started_at", "actual_start")) or _first_time(history_metrics, ("started_at",)) or run.get("started_at")
                ended = _first_time(terminal, _RUN_END_KEYS) or _first_time(closed, ("closed_at", "finished_at")) or _first_time(history_metrics, ("finished_at",)) or run.get("ended_at")
                recorded_time = terminal.get("time") if isinstance(terminal.get("time"), dict) else {}
                timing = self._timing(started, ended, run.get("status"), recorded_seconds=terminal.get("pipeline_wall_seconds", recorded_time.get("pipeline_wall_seconds")))
                usage = _usage(terminal)
                if usage["status"] == "unavailable":
                    usage = self._root_usage(reader)
                if usage["status"] == "unavailable":
                    usage = _usage(history_metrics)
                if detail:
                    records, ledgers = self._records(reader)
                    self._integrate(records, stages)
                    authorized_paths = self._authorized_build_role_paths(reader, sealed_roles)
                    builds = self._builds(ledgers, stages["build"]["attempts"], authorized_paths)
                    if policy_version == 4 and role_milestones:
                        jobs = self._apply_role_milestones(jobs, role_milestones)
                        artifact_ids = {row["role_id"] for row in role_milestones if row["stage"] == "artifact" and
                                        row["status"] == "done" and row.get("current") is True}
                        if artifact_ids:
                            metrics["cvs_built"] = len(artifact_ids)
                            stages["build"]["cvs_built"] = len(artifact_ids)
                        synced_ids = {row["role_id"] for row in role_milestones if row["stage"] == "sync" and
                                      row["status"] == "synced" and row.get("current") is True}
                        if synced_ids:
                            metrics["roles_synced"] = len(synced_ids)
                            stages["sync"]["roles_synced"] = len(synced_ids)
                    if builds and any(b.get("artifact_recorded") or b["status"] == "failed" for b in builds):
                        metrics["cvs_built"] = sum(bool(b.get("artifact_recorded")) for b in builds)
                        metrics["cvs_failed"] = sum(b["status"] == "failed" for b in builds)
                        stages["build"]["cvs_built"] = metrics["cvs_built"]
                    elif stages["build"].get("cvs_built") is not None and metrics["cvs_built"] is None:
                        metrics["cvs_built"] = stages["build"]["cvs_built"]
                    for key in ("jobs_found", "ads_reviewed"):
                        if metrics[key] is None:
                            for owner in (("hunt",) if key == "jobs_found" else ("hunt",)):
                                if stages[owner][key] is not None:
                                    metrics[key] = stages[owner][key]
                                    break
                    if metrics["jobs_proposed"] is None:
                        metrics["jobs_proposed"] = stages["hunt"]["jobs_proposed"]
                    if policy_version != 4 and metrics["rows_synced"] is None:
                        metrics["rows_synced"] = stages["sync"]["rows_synced"]
                    elif policy_version == 4:
                        metrics["rows_synced"] = None
                    continuations = self._continuations(str(run["id"]))
                    base_jobs = jobs if policy_version == 4 and (accepted_milestones or role_milestones) else self._job_outcomes(sealed_roles["jobs"] if sealed_roles else [], [], [])
                    jobs = self._job_outcomes(base_jobs, builds, continuations)
                    if policy_version == 4 and role_milestones:
                        jobs = self._apply_role_milestones(jobs, role_milestones)
                if reader.truncated:
                    observations.append("Some large or numerous receipts were omitted; missing metrics remain unavailable.")
            except (StudioDataError, OSError, KeyError):
                observations.append("Detailed receipts were unavailable within the run workspace.")
        for stage in stages.values():
            if stage["status"] == "not_recorded":
                stage["observations"].append("No receipt saved for this step.")
            if stage["model_source"] == "configured":
                stage["observations"].append("Model is the saved assignment; execution was not confirmed.")
            failures.extend(stage["failures"])
        for build in builds:
            failures.extend(build["failures"])
        failures = _dedup(failures)
        if run.get("kind") == "claude_sheet":
            # 19 Oct: Claude/Gemini sheet runs carry their own counts (screening ACCEPTED, approved CVs) from report.json;
            # the Codex evidence readers above find nothing for them, so keep the run's own numbers.
            for key, value in (run.get("summary_metrics") or {}).items():
                if value is not None and metrics.get(key) is None:
                    metrics[key] = value
        metrics["failure_count"] = len(failures)
        metrics["failure_count_note"] = "Saved issue records; this does not prove that unlogged steps had no failures."
        metrics["jobs_found_note"] = "Saved raw opportunity or proposal count; audited accepted roles are counted separately."
        metrics["jobs_proposed_note"] = "Raw site proposals; proposals are not independently accepted roles."
        metrics["jobs_shortlisted_note"] = "Sealed independent Hunt Audit accepted-role count; missing means no sealed count was saved."
        metrics["duration_seconds"] = timing["duration_seconds"]
        if usage.get("approximate"):
            observations.append(usage["note"])
        if timing["duration_seconds"] is None:
            observations.append("A completed whole-run duration was not saved.")
        models = [m for row in stages.values() for m in row["models"]]
        result = {**run, "stage_metrics": list(stages.values()), "builds": builds,
                "jobs": jobs, "continuations": continuations,
                "role_milestones": role_milestones,
                "summary_metrics": metrics, "usage": usage, "timing": timing,
                "duration_seconds": timing["duration_seconds"], "observations": list(dict.fromkeys(observations)),
                "failures": failures, "models": _model_fields(models)["models"]}
        if sealed_roles is not None and sealed_roles.get("jobs_authoritative") is True:
            result["_jobs_authoritative"] = True
        if reader is not None:
            result["_snapshot_transient"] = reader.transient or reader.truncated
        return result

    @staticmethod
    def _timing(start: Any, end: Any, status: Any, *, observed: Any = None,
                recorded_seconds: Any = None, live: bool = False) -> dict:
        start, end = _timestamp(start), _timestamp(end)
        duration = _seconds(start, end)
        timing_status, elapsed = "completed" if duration is not None else "unavailable", None
        if duration is None and _number(recorded_seconds) is not None:
            duration, timing_status = recorded_seconds, "recorded_duration"
        if start and end and duration is None:
            timing_status = "invalid_clock"
        elif start and not end and duration is None:
            if _status(status) == "running":
                observation = datetime.now(timezone.utc).isoformat() if live else _timestamp(observed)
                elapsed = _seconds(start, observation)
                timing_status = "running" if live else "saved_elapsed" if elapsed is not None else "unfinished"
            else:
                timing_status = "unfinished"
        return {"started_at": start, "finished_at": end, "duration_seconds": duration,
                "elapsed_seconds": elapsed, "timing_status": timing_status,
                "note": "Elapsed at the saved observation; completion not recorded." if timing_status == "saved_elapsed" else
                        "Elapsed so far; this is not a completed duration." if timing_status == "running" else
                        "Clock order is invalid; no duration inferred." if timing_status == "invalid_clock" else
                        "Completed duration from saved timestamps." if timing_status == "completed" else
                        "Duration stated in the saved receipt." if timing_status == "recorded_duration" else
                        "Completion time was not saved."}

    def _stage_entries(self, row: dict, source: str, stages: dict, *, configured: bool = False) -> None:
        entries = row.get("stages") or row.get("stage_status") or {}
        if isinstance(entries, list):
            entries = {str(item.get("id") or item.get("name") or item.get("stage")): item
                       for item in entries if isinstance(item, dict)}
        if not isinstance(entries, dict):
            entries = {}
        entries = {**{key: value for key, value in row.items() if _stage(key)}, **entries}
        for name, value in entries.items():
            stage = _stage(name)
            if stage is None:
                continue
            value = value if isinstance(value, dict) else {"status": value}
            raw = value.get("status", value.get("outcome", value.get("six_lanes")))
            if raw is not None and not configured:
                stages[stage].update(status=_status(raw), raw_status=_safe_text(str(raw)))
            model = _model(value, "stage_receipt", configured=configured or _status(raw) in ("not_run", "not_recorded", "ready"))
            if model:
                stages[stage].update(_model_fields(stages[stage]["models"] + [model]))
            for metric in _STAGE_COUNTS:
                if (count := _count(value, metric)) is not None:
                    stages[stage][metric] = count
            start = _first_time(value, ("worker_started_at", "started_at", "stage_started_at"))
            end = _first_time(value, ("actual_work_stopped_at", "finished_at", "ended_at", "worker_finished_at"))
            if start or end:
                stages[stage].update(self._timing(start, end, raw, observed=value.get("recorded_at"), recorded_seconds=value.get("duration_seconds", value.get("elapsed_seconds")) if end else None))
            usage = _usage(value)
            if usage["status"] != "unavailable":
                stages[stage]["usage"] = usage
            if not configured and (failure := _failure(value, stage, source)):
                stages[stage]["failures"].append(failure)

    def _root_usage(self, reader: _Reader) -> dict:
        for name in ("usage-final.json", "parent-final-usage.json", "usage-guard-observation.json", "usage.json"):
            row = reader.read(reader.base / name)
            if isinstance(row, dict) and (usage := _usage(row))["status"] != "unavailable":
                return usage
        before = reader.read(reader.base / "usage-before.json")
        after = reader.read(reader.base / "usage-after.json")
        return _usage({"usage_before": before, "usage_after": after})

    def _records(self, reader: _Reader) -> tuple[list[tuple[dict, str]], list[tuple[list[dict], str]]]:
        records: list[tuple[dict, str]] = []
        ledgers: list[tuple[list[dict], str]] = []
        roots = [reader.base]
        for path in reader.entries(reader.base):
            if path.is_dir() and not path.is_symlink() and (path.name in _SITES | {"inbox", "native-workers", "build-ledgers", "build-contracts"} or
                    path.name.startswith(("build-", "build_", "hunt_audit", "sol_review", "sync"))):
                roots.append(path)
                if path.name == "native-workers":
                    roots.extend(p for p in reader.entries(path) if p.name in _SITES and p.is_dir() and not p.is_symlink())
        for root in roots:
            for path in reader.entries(root):
                if path.is_symlink() or not path.is_file():
                    continue
                name = path.name
                is_ledger = name.endswith(".ledger.json") or bool(re.fullmatch(r"build-\d+(?:\.[\w-]+)?\.jsonl", name))
                allowed = (is_ledger or name in _RUN_MANIFESTS or name in ("hunt.records.json", "site-lane-outcomes.json") or name.startswith("build.records") and name.endswith(".json") or
                           bool(re.search(r"(?:contract(?:-v\d+)?|dispatch|result)\.json$", name)) or
                           bool(re.fullmatch(r"(?:weekday_engine[_-]terminal|worker[_-]terminal|terminal(?:_receipt)?|site_summary|runtime_model_evidence|worker_contract|usage|usage_meter|build[_-]stage|builds|stage[_-]metrics|worker[_-]metrics)(?:[._-](?:v\d+|integrated-v\d+|amendment|custom.failure))?\.json", name)) or
                           root.name == "inbox" and bool(re.fullmatch(r"inbox_receipt(?:\.v\d+)?\.json", name)))
                if not allowed:
                    continue
                value = reader.read(path, lines=path.suffix == ".jsonl")
                source = str(path.relative_to(reader.base))
                if is_ledger and isinstance(value, list):
                    ledgers.append(([row for row in value if isinstance(row, dict)], source))
                    continue
                self._expand(value, source, root.name, records)
        return records, ledgers

    def _expand(self, value: Any, source: str, parent: str, records: list) -> None:
        if isinstance(value, list):
            for row in value[:_MAX_ROWS]:
                if isinstance(row, dict):
                    self._expand(row, source, parent, records)
            return
        if not isinstance(value, dict):
            return
        if source == "stage-metrics.json" and value.get("status") == "not_run" and not value.get("started_at") and not value.get("kind"):
            return  # Seven initial placeholders are stage states, not worker attempts.
        stage = _stage(value.get("stage") or value.get("worker_id") or value.get("site")) or _stage(parent) or _stage(Path(source).name.split(".")[0])
        if stage and (value.get("status") is not None or value.get("kind") in _ASSIGNMENTS or source.endswith("usage.json") or "runtime_model_evidence" in source or "site_summary" in source):
            # Only these records enter the projection. Their raw content is never returned.
            records.append(({**value, "_stage": stage}, source))
        for key in ("contracts", "workers", "records", "builds", "stage_metrics", "lanes"):
            child = value.get(key)
            if isinstance(child, dict):
                child = list(child.values())
            if isinstance(child, list):
                self._expand(child, source, parent, records)

    def _integrate(self, records: list[tuple[dict, str]], stages: dict) -> None:
        groups: dict[tuple, dict] = {}
        contract_groups: dict[str, tuple] = {}
        for row, source in records:
            if row.get("kind") not in _ASSIGNMENTS:
                continue
            stage = row["_stage"]
            start = _first_time(row, ("worker_started_at", "invocation_started_at"))
            worker = _safe_text(row.get("worker_id")) or stage
            key = (stage, worker, start)
            group = groups.setdefault(key, {"contract": {}, "records": [], "source": source})
            group["contract"] = row
            if row.get("contract_id"):
                contract_groups[str(row["contract_id"])] = key
        for row, source in records:
            if row.get("kind") in _ASSIGNMENTS:
                continue
            stage = row["_stage"]
            worker = _safe_text(row.get("worker_id")) or stage
            if worker in _SITES:
                worker = "hunt:" + worker
            start = _first_time(row, ("worker_started_at", "started_at", "discovery_started_at", "stage_started_at"))
            key = contract_groups.get(str(row.get("contract_id")))
            if key is None:
                matches = [k for k in groups if k[0] == stage and k[1] == worker and (start is None or k[2] == start)]
                # Site summaries often use active-discovery rather than dispatch
                # start; bind only when there is one unambiguous worker contract.
                if not matches:
                    matches = [k for k in groups if k[0] == stage and k[1] == worker]
                key = matches[0] if len(matches) == 1 else (stage, worker, start)
            group = groups.setdefault(key, {"contract": {}, "records": [], "source": source})
            group["records"].append((row, source))
        for key, group in groups.items():
            stage, worker, _clock = key
            attempt = self._attempt(stage, worker, group)
            if attempt:
                stages[stage]["attempts"].append(attempt)
        for stage in stages.values():
            attempts = stage["attempts"]
            if not attempts:
                continue
            actual = [a for a in attempts if a["dispatched"]]
            if actual:
                active = [a for a in actual if a["status"] == "running"]
                by_worker = {}
                for attempt in sorted(actual, key=lambda a: a["started_at"] or ""):
                    family = re.sub(r"(?i)(?::|[_-])recovery\d*$", "", attempt["worker_id"])
                    by_worker[family] = attempt
                latest = list(by_worker.values())
                statuses = {a["status"] for a in latest}
                status = "running" if active else next(iter(statuses)) if len(statuses) == 1 else "partial"
                stage.update(status=status, raw_status=latest[-1]["raw_status"] if len(latest) == 1 else "MIXED_SAVED_OUTCOMES")
                starts = [a["started_at"] for a in actual if a["started_at"]]
                ends = [a["finished_at"] for a in actual if a["finished_at"]]
                start = min(starts, key=_time) if starts else None
                end = max(ends, key=_time) if ends and all(a["finished_at"] for a in actual) else None
                stage.update(self._timing(start, end, status))
                if len(actual) > 1:
                    stage["observations"].append("Stage wall time includes saved parallel lanes or repairs; individual clocks are below.")
                for metric in _STAGE_COUNTS:
                    counts = [a.get(metric) for a in latest]
                    if counts and all(value is not None for value in counts):
                        stage[metric] = sum(counts)
                    elif any(value is not None for value in counts):
                        stage["observations"].append(f"Some {metric.replace('_', ' ')} counts were not recorded; no total inferred.")
                usages = [a["usage"] for a in actual if a["usage"]["status"] != "unavailable"]
                if len(usages) == 1:
                    stage["usage"] = usages[0]
                elif len(usages) > 1:
                    stage["usage"] = {**_empty_usage(), "status": "partial", "note": "Usage is saved per attempt below; overlapping account windows and repeated snapshots are not summed."}
                stage["usage_counters"] = _unique_worker_usages(
                    stage.get("usage_counters", []) + [a["usage"] for a in actual])
            stage.update(_model_fields(stage["models"] + [m for a in attempts for m in a["models"]]))
            stage["failures"] = _dedup(stage["failures"] + [f for a in attempts for f in a["failures"]])

    def _attempt(self, stage: str, worker: str, group: dict) -> dict:
        contract, records = group["contract"], group["records"]
        models = []
        if model := _model(contract, "configured_contract", configured=True):
            models.append(model)
        # Versioned terminal amendments supersede their original for status,
        # while the immutable dispatch clock always comes from the contract.
        def order(item: tuple) -> tuple:
            row, source = item
            revised = re.findall(r"(?:[._-]v|recovery)(\d+)", source)
            end = _first_time(row, ("actual_work_stopped_at", "finished_at", "worker_finished_at", "recorded_at", "at"))
            return (end or "", max(map(int, revised)) if revised else 0, source)
        records = sorted(records, key=order)
        terminal = [(r, s) for r, s in records if r.get("status") is not None and
                    "site_summary" not in s and "usage" not in Path(s).name and "runtime_model_evidence" not in s]
        row, source = terminal[-1] if terminal else records[-1] if records else ({}, group["source"])
        raw = row.get("status", row.get("outcome"))
        status = _status(raw)
        evidence_only = row.get("kind") == "weekday_stage_runtime_receipt" or row.get("result_type") == "command_execution_evidence"
        provider_only = row.get("normal_stage_completion") is False
        if evidence_only and status == "done" or provider_only and status == "done":
            status = "partial"
        dispatched = bool(records and any(_first_time(r, ("started_at", "worker_started_at", "discovery_started_at")) or r.get("actual_agent_id") or r.get("kind") == "weekday_worker_terminal" for r, _s in records))
        start = _first_time(contract, ("worker_started_at", "invocation_started_at")) if dispatched else None
        if start is None and dispatched:
            start = next((_first_time(r, ("worker_started_at", "started_at", "discovery_started_at")) for r, _s in records if _first_time(r, ("worker_started_at", "started_at", "discovery_started_at"))), None)
        end = _first_time(row, ("actual_work_stopped_at", "finished_at", "worker_finished_at", "ended_at", "discovery_stopped_at")) if terminal else None
        observed = _first_time(row, ("recorded_at", "observed_at", "at"))
        metrics = {key: None for key in _STAGE_COUNTS}
        usage, failures, notes, roles = _empty_usage(), [], [], []
        for entry, entry_source in records:
            actual_model = _model(entry, "runtime_context" if "runtime_model_evidence" in entry_source else "worker_receipt",
                                  configured=bool(entry.get("model_requested") and not entry.get("model_used")) or _status(entry.get("status")) in ("not_run", "ready"))
            if actual_model:
                models.append(actual_model)
            for metric in metrics:
                if (count := _count(entry, metric)) is not None:
                    metrics[metric] = count
            if (saved_usage := _usage(entry))["status"] != "unavailable" or saved_usage["account_observations"]:
                usage = saved_usage
            if failure := _failure(entry, stage, entry_source):
                failures.append(failure)
            for value in entry.get("assigned_role_paths", []) if isinstance(entry.get("assigned_role_paths"), list) else []:
                value = value.get("path") if isinstance(value, dict) else value
                if isinstance(value, str) and (label := _safe_text(Path(value).name)):
                    roles.append(label)
            if isinstance(entry.get("role"), str) and (label := _safe_text(entry["role"])):
                roles.append(label)
        for value in contract.get("assigned_role_paths", []) if isinstance(contract.get("assigned_role_paths"), list) else []:
            value = value.get("path") if isinstance(value, dict) else value
            if isinstance(value, str) and (label := _safe_text(Path(value).name)):
                roles.append(label)
        if not records:
            status, raw = "not_recorded", None
            notes.append("A frozen assignment exists; dispatch and completion were not recorded.")
        if evidence_only:
            notes.append("Command execution receipt only; it does not approve or complete this pipeline step.")
        if provider_only:
            notes.append("Provider execution receipt only; owner stage validation is still required.")
        if any(r.get("resumed_at") or r.get("reset_at") for r, _s in records):
            notes.append("Saved resumption retains the original dispatch clock.")
        completed = next((_first_time(r, ("artifact_completed_at", "artifact_complete_at")) for r, _s in reversed(records) if _first_time(r, ("artifact_completed_at", "artifact_complete_at"))), None)
        if not roles and stage == "build" and worker.startswith("build:"):
            if label := _safe_text(re.sub(r":recovery\d*$", "", worker.split(":", 1)[1])):
                roles.append(label)
        public_source = contract.get("source") or row.get("source")
        public_source = _identifier(public_source) if isinstance(public_source, str) and "@" not in public_source and "://" not in public_source else None
        role_id = next((value for item, _item_source in records
                        if isinstance(value := item.get("job_id") or item.get("role_id"), str) and
                        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", value)), None)
        role_label = next((_safe_text(item.get("role"), 160) for item, _item_source in records
                           if isinstance(item.get("role"), str) and _safe_text(item.get("role"), 160)), None)
        company = next((_safe_text(item.get("company"), 120) for item, _item_source in records
                        if isinstance(item.get("company"), str) and _safe_text(item.get("company"), 120)), None)
        title = next((_safe_text(item.get("title"), 120) for item, _item_source in records
                      if isinstance(item.get("title"), str) and _safe_text(item.get("title"), 120)), None)
        return {"worker_id": worker, "contract_id": _identifier(contract.get("contract_id") or row.get("contract_id")),
                "status": status, "raw_status": _safe_text(str(raw)) if raw is not None else None,
                "dispatched": dispatched, "clock_started_at": _first_time(contract, ("worker_started_at", "invocation_started_at")),
                **self._timing(start, end, raw, observed=observed), **_model_fields(models), **metrics,
                "usage": usage, "roles": list(dict.fromkeys(roles)), "source": source,
                "source_lane": public_source, "lane_id": _identifier(contract.get("lane_id") or row.get("lane_id")),
                "queued_at": _first_time(contract, ("queued_at",)),
                "dispatch_delay_seconds": _number(contract.get("dispatch_delay_seconds")),
                "role_id": role_id, "role": role_label, "company": company, "title": title,
                "artifact_completed_at": completed,
                "observations": notes, "failures": _dedup(failures)}

    def _builds(self, ledgers: list[tuple[list[dict], str]], attempts: list[dict],
                authorized_role_paths: dict[str, str] | None = None) -> list[dict]:
        by_role: dict[str, list[dict]] = {}
        paths_by_role: dict[str, str] = {}
        ambiguous_roles: set[str] = set()
        for attempt in attempts:
            canonical = (authorized_role_paths or {}).get(attempt.get("contract_id"))
            if not canonical:
                continue
            for role in attempt.get("roles", []):
                if role in paths_by_role and paths_by_role[role] != canonical:
                    ambiguous_roles.add(role)
                paths_by_role[role] = canonical
        for role in ambiguous_roles:
            paths_by_role.pop(role, None)
        for rows, source in ledgers:
            init = next((r for r in rows if r.get("event") == "run_started"), {})
            if not init:
                continue
            start = _first_time(init, ("started_at",))
            roles = {r["role"] for r in rows if isinstance(r.get("role"), str)}
            for role in roles:
                label = _safe_text(role)
                if not label:
                    continue
                owned = [r for r in rows if r.get("role") == role]
                intake = next((r for r in owned if r.get("event") == "role_intake"), {})
                artifact = next((r for r in owned if r.get("event") == "artifact_recorded"), {})
                failed = next((r for r in owned if r.get("event") == "build_failed"), {})
                # A repair ledger cannot inherit another attempt's earlier clock.
                matching = [a for a in attempts if label in a["roles"] and a["started_at"] == start]
                if len(matching) == 1 and matching[0]["started_at"]:
                    start = matching[0]["started_at"]
                role_ids = {a.get("role_id") for a in matching if a.get("role_id")}
                role_id = next(iter(role_ids)) if len(role_ids) == 1 else None
                completed = _first_time(artifact, ("artifact_completed_at", "completed_at", "at"))
                failure = _failure({**failed, "status": "FAILED", "failure_class": "build_failed", "worker_id": label}, "build", source) if failed else None
                verified = any(r.get("event") == "verified" and r.get("completion_stage") == "artifact" for r in rows)
                models = [_model(init, "build_start_receipt")]
                piece = {"role": label, "role_id": role_id,
                         "name": _safe_text(intake.get("job_title")) or label,
                         "company": _safe_text(intake.get("company")), "source": source,
                         "status": "done" if completed else "failed" if failed else "partial",
                         "artifact_status": "verified" if verified and artifact else "recorded" if artifact else "not_recorded",
                         "artifact_recorded": bool(artifact),
                         "completed_at": completed, "started_at": start,
                         "duration_seconds": _seconds(start, completed),
                         "timing_status": "completed" if _seconds(start, completed) is not None else "unavailable",
                         **_model_fields([m for m in models if m]),
                         "attempt_count": _number(artifact.get("attempts", failed.get("attempts")), integer=True),
                         "usage": _empty_usage(), "failures": [failure] if failure else [],
                         "observations": ["CV build time ends at the saved artifact event; later audit and sync are excluded."] if completed else ["Artifact completion was not recorded."]}
                if start and completed and _seconds(start, completed) is None:
                    piece["timing_status"] = "invalid_clock"
                by_role.setdefault(label, []).append(piece)
        # Worker receipts with explicit artifact_completed_at remain useful when
        # there is no ledger. A generic worker finish is never substituted.
        for attempt in attempts:
            for role in attempt["roles"]:
                if role not in by_role:
                    completed = attempt.get("artifact_completed_at")
                    duration = _seconds(attempt["started_at"], completed)
                    by_role.setdefault(role, []).append({"role": role, "role_id": attempt.get("role_id"),
                        "name": attempt.get("title") or role, "company": attempt.get("company"),
                        "status": "done" if completed else attempt["status"], "artifact_status": "recorded" if completed else "not_recorded", "artifact_recorded": bool(completed), "started_at": attempt["started_at"],
                        "completed_at": completed, "duration_seconds": duration, "timing_status": "completed" if duration is not None else "unavailable",
                        **{k: attempt[k] for k in ("model", "effort", "model_source", "models", "usage", "source", "failures")},
                        "attempt_count": None, "observations": ["Artifact completion was not recorded; worker finish is not a CV build time."]})
        builds = []
        for role, pieces in sorted(by_role.items()):
            pieces.sort(key=lambda b: (_time(b["started_at"]) or datetime.min.replace(tzinfo=timezone.utc), b["source"]))
            complete = [b for b in pieces if b["completed_at"]]
            chosen = complete[0] if complete else pieces[-1]
            builds.append({**chosen, "role_path": paths_by_role.get(role) or self._role_path(chosen.get("role") or role),
                           "attempts": pieces, "repair_count": max(0, len(pieces) - 1),
                           "failures": _dedup([f for p in pieces for f in p["failures"]])})
        return builds

    def _legacy(self, run: dict, stages: dict, detail: bool) -> tuple[dict, list[dict], dict]:
        old = next((row for row in run.get("stages", []) if isinstance(row, dict)), {})
        key = _stage(old.get("id"))
        metrics = {metric: _count(run, metric) for metric in _COUNT_KEYS}
        metrics["jobs_shortlisted"] = None  # Legacy stage summaries do not prove sealed Hunt Audit approval.
        if key is None:
            return metrics, [], _empty_usage()
        start, end = run.get("started_at"), run.get("ended_at")
        model = _model(old, "legacy_state")
        stages[key].update(status=_status(run.get("raw_status")), raw_status=_safe_text(run.get("raw_status")),
                           **self._timing(start, end, run.get("raw_status")), **_model_fields([model] if model else []))
        stages[key]["observations"].append("Historical single-stage run; the other six steps were not recorded as part of this run.")
        for metric in ("jobs_found", "cvs_built", "ads_reviewed"):
            stages[key][metric] = metrics[metric]
        if failure := _failure({"status": run.get("raw_status")}, key, str(run.get("source") or "legacy_state")):
            stages[key]["failures"].append(failure)
        usage, builds = _empty_usage(), []
        if detail:
            # Resolve only the selected legacy state record, never a different
            # build run or another day's inventory of PDFs.
            source = run.get("source")
            if isinstance(source, str):
                reader = _Reader(self.data, self.data.auto / "state")
                state = reader.read(self.data.root / source)
                current = state.get("stages", {}).get(old.get("id")) if isinstance(state, dict) else None
                if isinstance(current, dict):
                    rows = [r for r in current.get("previous_runs", []) if isinstance(r, dict)] + [current]
                    record = next((r for r in rows if str(run["id"]).endswith("-" + str(r.get("run", 1)))), {})
                    usage = _usage(record)
                    stages[key]["usage"] = usage
            if key == "build":
                try:
                    base = self.data._safe(self.data.evidence / str(run["date"]), self.data.evidence)
                    ledger_reader = _Reader(self.data, base)
                    number = str(run["id"]).rsplit("-", 1)[-1]
                    path = base / f"build-{number}.jsonl"
                    rows = ledger_reader.read(path, lines=True)
                    if isinstance(rows, list):
                        builds = self._builds([(rows, path.name)], [])
                        if builds:
                            metrics["cvs_built"] = sum(bool(b["completed_at"]) for b in builds)
                            stages[key]["cvs_built"] = metrics["cvs_built"]
                except (StudioDataError, OSError):
                    pass
        return metrics, builds, usage
