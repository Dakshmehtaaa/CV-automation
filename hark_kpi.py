"""Local cache of Hark's daily "Audit KPIs" tab, for the Studio Home feed dashboard.

Studio's Python backend cannot call the Google Sheets connector. A Claude session (or the
sheet pipeline) reads the "Audit KPIs" tab with the connector and pipes the raw rows into
``python3 hark_kpi.py import``; this module normalises them and saves one small JSON file
under ``Studio/.local``. Studio only ever reads that file. Nothing here contacts Google,
starts a pipeline, or touches the sheet.

The tab is hand-maintained by Hark, so cells are free text ("7 min 37 s", "12:50 (approx)",
"10 (6 rows + 4 Runs cells)", "not audited ..."). Parsing is tolerant: anything that cannot be
read becomes ``None`` (unknown), never a guessed number.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import datetime, timezone

KIND = "hark_kpi_audit_v1"
SNAPSHOT = Path(__file__).resolve().parent / ".local" / "hark-kpi-audit.json"
SOURCE = "Hark sheet · Audit KPIs tab"
# 20 Oct 2026: the Firecrawl Job Feed sheet keeps the same "Audit KPIs" layout (written by firecrawl_hunt.py
# through make_header/make_row below), so one parser serves both feeds. Studio Home toggles Hark / Firecrawl / both.
FIRECRAWL_SNAPSHOT = SNAPSHOT.parent / "firecrawl-kpi-audit.json"
SOURCES = {"hark": (SNAPSHOT, SOURCE), "firecrawl": (FIRECRAWL_SNAPSHOT, "Firecrawl sheet · Audit KPIs tab")}
SUMMARY_HEADER = ("audit_date", "audited_at_paris", "automation_score_pct", "schedules_completed", "schedules_completed_pct",
                  "new_rows_total", "jds_captured_total", "jd_capture_pct", "rows_with_audit_errors",
                  "row_accuracy_pct_before_fixes", "errors_found_total", "errors_fixed_total", "emails_with_message_id",
                  "email_delivery_pct", "onhold_fixed_total", "onhold_unresolved_total", "schedule_changes", "notes")
FEED_LABELS = ("On time", "Status", "Start", "Stop", "Duration", "Scraped", "Added", "Duplicates skipped",
               "Full job descriptions", "JD capture %", "Audit errors (found / fixed)", "Accuracy %",
               "ON HOLD (fixed / still open)", "Email report")
SUMMARY_COLUMNS = 17  # audit_date .. schedule_changes; column 18 is the free-text notes
FEED_FIELDS = ("on_time", "status", "start", "stop", "duration", "scraped", "added", "duplicates",
               "jds", "jd_pct", "errors", "accuracy", "onhold", "email")
_STATUS = {"complete": "complete", "partial": "partial", "no run": "no_run", "not scheduled yet": "not_scheduled",
           "failed": "failed", "skipped": "skipped", "missed": "no_run", "late": "late"}
_SLUGS = {"linkedin sweep": "linkedin_sweep", "jobs that make sense": "jobs_that_make_sense",
          "france travail": "france_travail"}


def slug(name: str) -> str:
    key = name.strip().casefold()
    return _SLUGS.get(key) or re.sub(r"[^a-z0-9]+", "_", key).strip("_")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _blank(value) -> bool:
    return _text(value).casefold() in {"", "—", "-", "–", "n/a", "na", "none"}


def number(value):
    """First number in a cell ('85.6%', '97.9', '10 (6 rows + ...)'), or None."""
    if _blank(value):
        return None
    found = re.search(r"-?\d+(?:[.,]\d+)?", _text(value))
    return float(found.group().replace(",", ".")) if found else None


def whole(value):
    n = number(value)
    return None if n is None else int(round(n))


def percent(value):
    text = _text(value)
    if re.search(r"not audited", text, re.I):
        return None
    n = number(value)
    return None if n is None or not 0 <= n <= 100 else round(n, 1)


def fraction(value):
    """'4/5' or '10 / 8' -> (4, 5). 'not audited ...' or '—' -> (None, None)."""
    found = re.match(r"\s*(\d+)\s*/\s*(\d+)", _text(value))
    return (int(found.group(1)), int(found.group(2))) if found else (None, None)


def unresolved(value):
    """'1 (UNEP row 392 ...)' -> 1; 'LI 1 / HW 4 / AP 1 unresolved' -> 6; text only -> None."""
    text = _text(value)
    named = re.findall(r"\b[A-Z]{2}\s+(\d+)\b", text)
    if len(named) > 1:
        return sum(int(n) for n in named)
    return whole(text)


def clock(value):
    found = re.search(r"(\d{1,2})[:h](\d{2})", _text(value))
    if not found:
        return None
    hour, minute = int(found.group(1)), int(found.group(2))
    return f"{hour:02d}:{minute:02d}" if hour < 24 and minute < 60 else None


def minutes(value):
    """Duration cell -> minutes: '7 min 37 s', '44 min', '0 min 53 s', '00:00:53', '~3 min'."""
    text = _text(value)
    hms = re.fullmatch(r"(\d+):(\d{2}):(\d{2})", text)
    if hms:
        h, m, s = (int(g) for g in hms.groups())
        return round(h * 60 + m + s / 60, 1)
    mins = re.search(r"(\d+(?:[.,]\d+)?)\s*min", text)
    secs = re.search(r"(\d+)\s*s\b", text)
    if not mins and not secs:
        return None
    whole_minutes = float(mins.group(1).replace(",", ".")) if mins else 0.0
    return round(whole_minutes + (int(secs.group(1)) / 60 if secs else 0), 1)


def _gap(start, stop):
    if not start or not stop:
        return None
    a = int(start[:2]) * 60 + int(start[3:])
    b = int(stop[:2]) * 60 + int(stop[3:])
    return float(b - a) if b >= a else None


def feed_names(header) -> list[str]:
    names: list[str] = []
    for cell in header[SUMMARY_COLUMNS + 1:]:
        name = _text(cell).split(" · ")[0].strip()
        if " · " in _text(cell) and name and name not in names:
            names.append(name)
    return names


def _feed_blocks(tail: list, count: int):
    """Find each feed's 14-cell block by its status token, so a stray extra or missing cell in a
    hand-edited row cannot shift every later feed. Falls back to fixed 14-cell blocks."""
    anchors = [i for i, cell in enumerate(tail) if _text(cell).casefold() in _STATUS]
    if len(anchors) == count:
        out = []
        for i in anchors:
            block = tail[max(i - 1, 0): i + 13]
            if i == 0:
                block = [""] + block
            out.append((block + [""] * 14)[:14])
        return out, False
    fixed = [(tail[k * 14:(k + 1) * 14] + [""] * 14)[:14] for k in range(count)]
    return fixed, True


def _feed(block: list) -> dict:
    cell = dict(zip(FEED_FIELDS, block))
    status = _STATUS.get(_text(cell["status"]).casefold(), "other" if not _blank(cell["status"]) else "no_run")
    start, stop = clock(cell["start"]), clock(cell["stop"])
    duration = minutes(cell["duration"])
    if duration is None:
        duration = _gap(start, stop)
    jds_have, jds_of = fraction(str(cell["jds"]).replace(" of ", "/"))
    err_found, err_fixed = fraction(cell["errors"])
    hold_fixed, hold_open = fraction(cell["onhold"])
    text_email = _text(cell["email"])
    approx = any(re.search(r"approx|~|audit\)", _text(cell[k])) for k in ("start", "stop", "duration"))
    ran = status in {"complete", "partial", "failed", "late", "other"}
    return {
        "status": status, "ran": ran,
        "on_time": None if _blank(cell["on_time"]) else _text(cell["on_time"]).casefold().startswith("y"),
        "start": start, "stop": stop, "duration_min": duration, "approx_time": approx,
        "scraped": whole(cell["scraped"]), "added": whole(cell["added"]), "duplicates": whole(cell["duplicates"]),
        "jds": jds_have, "jds_of": jds_of, "jd_pct": percent(cell["jd_pct"]),
        "errors_found": err_found, "errors_fixed": err_fixed, "accuracy_pct": percent(cell["accuracy"]),
        "audited": err_found is not None or percent(cell["accuracy"]) is not None,
        "onhold_fixed": hold_fixed, "onhold_open": hold_open,
        "email_sent": None if _blank(text_email) else text_email.casefold().startswith("sent"),
        "email_time": clock(text_email),
    }


def email_intake(notes: str) -> dict:
    """Mailbox counts Hark mentions in the free-text note. Absent counts stay None (not reported)."""
    def count(pattern):
        found = re.search(pattern, notes, re.I)
        return int(found.group(1)) if found else None
    return {"rejections": count(r"(\d+)\s+(?:new\s+)?rejections?"),
            "jobs_added": count(r"(\d+)\s+alert\s+jobs?\s+added"),
            "duplicates": count(r"(\d+)\s+dup"),
            "reported": bool(re.search(r"email intake|rejection|alert job", notes, re.I))}


def make_header(feeds) -> list[str]:
    """Audit KPIs header row for a feed list, e.g. ["Tier 1 ATS", "Tier 2 Niche boards", ...]."""
    return list(SUMMARY_HEADER) + [f"{feed} · {label}" for feed in feeds for label in FEED_LABELS]


def make_row(day: dict, feeds: dict) -> list[str]:
    """One Audit KPIs row that parse() reads back. `day` keys are SUMMARY_HEADER names; `feeds` maps each
    feed name (header order) to a dict of FEED_FIELDS. Unknown values stay "—" (never a guessed number)."""
    def cell(value):
        return "—" if value is None or value == "" else str(value)
    row = [cell(day.get(k)) for k in SUMMARY_HEADER]
    for block in feeds.values():
        row += [cell(block.get(k)) for k in FEED_FIELDS]
    return row


def parse(values, source: str = SOURCE) -> dict:
    """Raw tab values (header row first) -> normalised daily records, newest last."""
    if not isinstance(values, list) or len(values) < 2 or not isinstance(values[0], list):
        raise ValueError("Expected the Audit KPIs tab as a list of rows with the header first")
    names = feed_names(values[0])
    if not names:
        raise ValueError("Header has no 'Feed · Field' columns")
    days: dict[str, dict] = {}
    for row in values[1:]:
        if not isinstance(row, list) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", _text(row[0] if row else "")):
            continue
        row = list(row) + [""] * (SUMMARY_COLUMNS + 1)
        done, planned = fraction(row[3])
        sent, mails = fraction(row[12])
        if sent is None and whole(row[12]) is not None:  # Hark sometimes writes only the count ("5", "4")
            sent, delivery = whole(row[12]), percent(row[13])
            mails = round(sent * 100 / delivery) if delivery else planned
        blocks, fallback = _feed_blocks(row[SUMMARY_COLUMNS + 1:], len(names))
        notes, changes = _text(row[17]), None if _blank(row[16]) else _text(row[16])
        if re.match(r"on time \w \|", notes, re.I):  # early rows: a one-line run summary sits in the notes column
            notes, changes = (changes or ""), None
        found = whole(row[10])
        days[_text(row[0])] = {
            "date": _text(row[0]), "audited_at": _text(row[1]),
            "automation_score_pct": percent(row[2]),
            "schedules_completed": done, "schedules_planned": planned,
            "new_rows": whole(row[5]), "jds_captured": whole(row[6]), "jd_capture_pct": percent(row[7]),
            "rows_with_audit_errors": whole(row[8]), "row_accuracy_pct": percent(row[9]),
            "errors_found": found, "errors_fixed": whole(row[11]),
            "report_emails_sent": sent, "report_emails_total": mails, "email_delivery_pct": percent(row[13]),
            "onhold_fixed": whole(row[14]), "onhold_open": unresolved(row[15]),
            "schedule_changes": changes,
            "notes": notes, "email_intake": email_intake(notes),
            "layout_fallback": fallback,
            "feeds": {slug(n): _feed(b) for n, b in zip(names, blocks)},
        }
    if not days:
        raise ValueError("No dated rows found")
    return {"kind": KIND, "source": source,
            "feeds": [{"id": slug(n), "label": n} for n in names],
            "days": [days[k] for k in sorted(days)]}


def write_snapshot(values, path: Path = SNAPSHOT, source: str = SOURCE) -> dict:
    result = parse(values, source)
    result["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=".hark-kpi-", suffix=".tmp")
    with os.fdopen(handle, "w") as out:
        json.dump(result, out, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return result


def load_snapshot(path: Path = SNAPSHOT):
    """What Studio serves: the saved projection, or None when never imported or unreadable."""
    try:
        saved = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict) or saved.get("kind") != KIND or not isinstance(saved.get("days"), list):
        return None
    return saved


def main(argv) -> int:
    args = list(argv[1:])
    feed = "hark"
    if "--source" in args:
        i = args.index("--source")
        feed = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    if not args or args[0] != "import" or feed not in SOURCES:
        print("usage: python3 hark_kpi.py import [--source hark|firecrawl] [rows.json]   "
              "(JSON: list of rows, or {\"values\": [...]}; stdin if no file)")
        return 2
    raw = Path(args[1]).read_text() if len(args) > 1 else sys.stdin.read()
    payload = json.loads(raw)
    values = payload.get("values") if isinstance(payload, dict) else payload
    path, label = SOURCES[feed]
    result = write_snapshot(values, path, label)
    last = result["days"][-1]
    print(f"saved {len(result['days'])} audit days to {path} (latest {last['date']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
