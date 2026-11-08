"""Read the explicit, user-supplied historical Home dashboard adjustments."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_FILE = "dashboard-adjustments.json"
_MAX_BYTES = 16_000
_METRICS = ("unique_full_ads_read", "validated_jds_chosen_for_cv")


def load_dashboard_adjustments(studio_root: Path) -> dict[str, Any]:
    """Return validated adjustment metadata without modifying any file or receipt."""
    path = Path(studio_root) / _FILE
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
            return _unavailable()
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return _unavailable()

    if (not isinstance(raw, dict) or raw.get("schema_version") != 1 or
            raw.get("kind") != "user_supplied_historical_adjustments" or
            not isinstance(raw.get("recorded_counts"), dict)):
        return _unavailable()
    counts = raw["recorded_counts"]
    normalized = {}
    for metric in _METRICS:
        value = counts.get(metric)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return _unavailable()
        normalized[metric] = value

    return {
        "available": True,
        "source": str(raw.get("source") or "user supplied historical estimate"),
        "recorded_on": raw.get("recorded_on") if isinstance(raw.get("recorded_on"), str) else None,
        "event_date": None,
        "source_allocation": None,
        "recorded_counts": normalized,
        "shortlist_definition": str(raw.get("shortlist_definition") or
                                     "Validated job descriptions chosen for a tailored CV."),
        "verified": False,
    }


def _unavailable() -> dict[str, Any]:
    return {
        "available": False,
        "source": None,
        "recorded_on": None,
        "event_date": None,
        "source_allocation": None,
        "recorded_counts": {metric: 0 for metric in _METRICS},
        "shortlist_definition": "Validated job descriptions chosen for a tailored CV.",
        "verified": False,
    }
