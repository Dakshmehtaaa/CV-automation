"""Read-only summary of chain audit runs saved under Master/auto/evidence/audits."""
from __future__ import annotations

import json
from pathlib import Path

AUDIT_KIND = "chain_audit_v1"
LAYER_FILE_GLOB = "[0-9]-*.json"


def _load_layer(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("kind") != AUDIT_KIND:
        return None
    index = raw.get("layer_index")
    if not isinstance(index, int) or isinstance(index, bool):
        return None
    fixes = raw.get("fixes") if isinstance(raw.get("fixes"), list) else []
    unresolved = raw.get("unresolved") if isinstance(raw.get("unresolved"), list) else []
    return {
        "index": index,
        "auditor": raw.get("auditor") or "",
        "at": raw.get("at") or "",
        "flow_score": raw.get("flow_score"),
        "flow_score_before": raw.get("flow_score_before"),
        "jobs_checked": raw.get("jobs_checked"),
        "clean_jobs": raw.get("clean_jobs"),
        "layers": raw.get("layers") if isinstance(raw.get("layers"), dict) else {},
        "fixes": len(fixes),
        "fixes_ok": sum(1 for fix in fixes if isinstance(fix, dict) and fix.get("ok") is True),
        "unresolved": [item for item in unresolved if isinstance(item, dict)],
        "email_sent": raw.get("email_sent") is True,
    }


def _run_dirs(audits_root: Path) -> list[Path]:
    dirs = [path for path in audits_root.iterdir() if path.is_dir()]
    return sorted(dirs, key=lambda path: (path.name, path.stat().st_mtime), reverse=True)


def audits_overview(evidence_root: Path, limit: int = 30) -> dict:
    audits_root = Path(evidence_root) / "audits"
    runs: list[dict] = []
    if audits_root.is_dir():
        for run_dir in _run_dirs(audits_root):
            layers = []
            for layer_file in sorted(run_dir.glob(LAYER_FILE_GLOB)):
                layer = _load_layer(layer_file)
                if layer is not None:
                    layers.append(layer)
            if not layers:
                continue
            layers.sort(key=lambda layer: layer["index"])
            runs.append({
                "id": run_dir.name,
                "at": max(layer["at"] for layer in layers),
                "layers": layers,
            })
            if len(runs) >= limit:
                break
    for run in runs:
        for layer in run["layers"]:
            layer.pop("at", None)
    trend = []
    for run in reversed(runs):
        final = run["layers"][-1]
        trend.append({"id": run["id"], "at": run["at"], "final_score": final.get("flow_score")})
    return {"runs": runs, "latest": runs[0] if runs else None, "trend": trend}
