"""Gemini accuracy score for CV Studio (Daksh, 19 Oct 2026).

The daily Claude review (scheduled task "gemini-pipeline-claude-review", 11:45 Paris) checks the 9:00 Gemini
pipeline run and records one row per run:

    python3 Resources/Studio/gemini_accuracy.py record --run-id <RUN> --checked N --kept K \
        [--screening-checked a --screening-kept b] [--cvs-checked c --cvs-kept d] \
        [--high H --medium M --low L] [--note "..."]

Score = kept / checked x 100: the share of Gemini's decisions and CV packages the Claude review kept unchanged
(screening decisions + approved CV packages). Studio reads it with `load()`; file:
Resources/Studio/.local/gemini-accuracy.json -> {"kind": "gemini_accuracy_v1", "runs": [...], "latest": {...}}.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

FILE = Path(__file__).resolve().parent / ".local" / "gemini-accuracy.json"


def _pct(kept: Optional[int], checked: Optional[int]) -> Optional[float]:
    return round(100.0 * kept / checked, 1) if isinstance(kept, int) and isinstance(checked, int) and checked > 0 else None


def load(path: Path = FILE) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    runs = [r for r in data.get("runs", []) if isinstance(r, dict)]
    if not runs:
        return None
    runs.sort(key=lambda r: str(r.get("run_date") or ""))
    scores = [r["score_pct"] for r in runs if isinstance(r.get("score_pct"), (int, float))]
    return {"kind": "gemini_accuracy_v1", "latest": runs[-1], "runs": runs[-30:],
            "average_pct_last_7": round(sum(scores[-7:]) / len(scores[-7:]), 1) if scores else None,
            "definition": "Share of Gemini's screening decisions and approved CV packages that the daily Claude review "
                          "kept unchanged (kept / checked)."}


def record(args) -> Dict[str, Any]:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"kind": "gemini_accuracy_v1", "runs": []}
    row = {"run_id": args.run_id, "run_date": args.run_id[:10], "reviewed_at": datetime.now(timezone.utc).isoformat(),
           "checked": args.checked, "kept": args.kept, "score_pct": _pct(args.kept, args.checked),
           "screening": {"checked": args.screening_checked, "kept": args.screening_kept,
                         "pct": _pct(args.screening_kept, args.screening_checked)},
           "cvs": {"checked": args.cvs_checked, "kept": args.cvs_kept, "pct": _pct(args.cvs_kept, args.cvs_checked)},
           "issues": {"high": args.high, "medium": args.medium, "low": args.low}, "note": args.note or ""}
    data["runs"] = [r for r in data.get("runs", []) if r.get("run_id") != args.run_id] + [row]
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return row


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--run-id", required=True)
    for name in ("checked", "kept"):
        r.add_argument(f"--{name}", type=int, required=True)
    for name in ("screening-checked", "screening-kept", "cvs-checked", "cvs-kept", "high", "medium", "low"):
        r.add_argument(f"--{name}", type=int)
    r.add_argument("--note")
    sub.add_parser("show")
    a = ap.parse_args()
    print(json.dumps(record(a) if a.cmd == "record" else load(), ensure_ascii=False, indent=1))
