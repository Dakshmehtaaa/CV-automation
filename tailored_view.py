#!/usr/bin/env python3
"""Keep the Tailored CVs folder readable in Finder (Daksh, 12 Oct 2026).

Only the roles still to apply to stay visible: Studio status "Ready to apply" plus roles the
Claude pipeline is still building. Every other top-level entry gets the macOS "hidden" flag,
so it is archived from the Finder view without moving anything. Paths, Studio ids, application
records and pipeline evidence all stay valid. Inside a visible role only the files needed to
apply stay visible (CV, cover letter, motivation, advert).

    python3 Studio/tailored_view.py            # show what would change
    python3 Studio/tailored_view.py --apply    # apply it
    python3 Studio/tailored_view.py --show-all # undo: unhide everything

To see hidden items in Finder: Cmd+Shift+. (period). Studio always lists every role.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
APPLY_FILES = {"CV_Candidate.pdf", "Cover_Letter_Candidate.pdf", "why-this-company.md", "JD.md"}


def _hidden(path: Path) -> bool:
    return bool(os.lstat(path).st_flags & stat.UF_HIDDEN)


def _set_hidden(path: Path, hidden: bool) -> bool:
    flags = os.lstat(path).st_flags
    new = flags | stat.UF_HIDDEN if hidden else flags & ~stat.UF_HIDDEN
    if new == flags:
        return False
    os.chflags(path, new, follow_symlinks=False)
    return True


def keep_folders(root: Path) -> set[str]:
    """Top-level folder names to keep visible."""
    sys.path.insert(0, str(HERE))
    from data import StudioData
    from applications import Applications
    data = StudioData(root)
    roles = Applications(data).decorate_list(data.roles())["items"]
    keep = set()
    for role in roles:
        parts = str(role.get("folder", "")).split("/")
        if len(parts) == 2 and (role.get("application") or {}).get("status") == "ready" and role.get("cv_available"):
            keep.add(parts[1])
    obligations = data.root / "Master/auto/evidence/claude-runs/obligations.json"
    try:
        for item in json.loads(obligations.read_text(encoding="utf-8")).get("items", []):
            folder = Path(item.get("folder", ""))
            if item.get("status") == "ACCEPTED_AWAITING_COMPLETION" and folder.parent.name == "Tailored CVs":
                keep.add(folder.name)
    except (OSError, ValueError):
        pass
    return keep


def tidy(root: Path, *, apply: bool, show_all: bool = False) -> dict:
    base = root / "Tailored CVs"
    keep = set() if show_all else keep_folders(root)
    changed, visible, hidden = [], [], 0
    for entry in sorted(base.iterdir()):
        if entry.name.startswith("."):
            continue
        hide = not show_all and entry.name not in keep
        if hide != _hidden(entry):
            changed.append({"path": entry.name, "hidden": hide})
            if apply:
                _set_hidden(entry, hide)
        if hide:
            hidden += 1
            continue
        visible.append(entry.name)
        if entry.is_dir() and not entry.is_symlink():
            for inner in entry.iterdir():
                if inner.name.startswith("."):
                    continue
                inner_hide = not show_all and inner.name not in APPLY_FILES
                if inner_hide != _hidden(inner):
                    changed.append({"path": f"{entry.name}/{inner.name}", "hidden": inner_hide})
                    if apply:
                        _set_hidden(inner, inner_hide)
    result = {"kind": "tailored_view_v1", "at": datetime.now(timezone.utc).isoformat(), "applied": apply,
              "visible": visible, "hidden_top_level": hidden, "changes": len(changed)}
    if apply and changed:
        receipt = root / "Studio/.local/tailored-view.json"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps({**result, "changed": changed}, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(HERE.parent.parent), help="CV folder (holds Tailored CVs)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--show-all", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not (root / "Tailored CVs").is_dir():
        root = root.parent if (root.parent / "Tailored CVs").is_dir() else root
    print(json.dumps(tidy(root, apply=args.apply or args.show_all, show_all=args.show_all), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
