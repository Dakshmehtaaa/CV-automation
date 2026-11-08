"""Local-only collection and acceptance projections for the supplied Hark feed.

This module reads saved connector snapshots and completed screening receipts.
It never contacts Google, visits an advert, starts a pipeline, or writes state.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo


SPREADSHEET_ID = "YOUR_HARK_SPREADSHEET_ID"
SHEET_ID = 0
# 21 Oct (Daksh): the Firecrawl Job Feed sheet is screened by the same pipeline; its rows count too.
FIRECRAWL_SPREADSHEET_ID = "YOUR_FIRECRAWL_SPREADSHEET_ID"
FEEDS = {SPREADSHEET_ID: "hark", FIRECRAWL_SPREADSHEET_ID: "firecrawl"}
MAX_SOURCES = 150
MAX_BYTES = 25_000_000
MAX_ROWS = 10_000
_TRACKING = {"trk", "trackingid", "refid", "ref_id", "source", "src"}


def _time(value):
    try:
        if not isinstance(value, str):
            return None
        raw = value.strip().replace("Z", "+00:00")
        paris = raw.endswith(" Paris")
        raw = raw.removesuffix(" Paris").replace(" UTC", "+00:00")
        stamp = datetime.fromisoformat(raw)
        if paris and stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ZoneInfo("Europe/Paris"))
        return stamp if stamp.tzinfo is not None else None
    except (AttributeError, ValueError, TypeError):
        return None


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def canonical_url(value):
    if not isinstance(value, str) or re.search(r"[\s<>\\\x00-\x1f]", value):
        return ""
    try:
        p = urlsplit(value)
        host = (p.hostname or "").casefold().removeprefix("www.")
        if p.scheme not in {"http", "https"} or not host or p.username or p.password:
            return ""
        port = p.port
        if port and port not in {80, 443}:
            host += f":{port}"
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.casefold().startswith("utm_") and k.casefold() not in _TRACKING]
        return urlunsplit(("https", host, re.sub(r"/{2,}", "/", p.path or "/").rstrip("/") or "/",
                           urlencode(sorted(query)), ""))
    except (ValueError, UnicodeError):
        return ""


def _posting_key(url):
    p = urlsplit(url)
    host = p.hostname or ""
    sites = (("linkedin.com", "linkedin", r"/jobs/view/(?:[^/?]*-)?(\d+)(?:/|$)"),
             ("hellowork.com", "hellowork", r"/emplois/(\d+)\.html"),
             ("hellowork.fr", "hellowork", r"/emplois/(\d+)\.html"),
             ("francetravail.fr", "france_travail", r"/detail/([\w-]+)"),
             ("pole-emploi.fr", "france_travail", r"/detail/([\w-]+)"),
             ("apec.fr", "apec", r"detail-offre/([\w-]+)"))
    for domain, site, pattern in sites:
        if host == domain or host.endswith("." + domain):
            found = re.search(pattern, p.path)
            if found:
                return "posting:" + site + ":" + found[1]
    if host == "indeed.com" or host.endswith(".indeed.com") or host == "indeed.fr" or host.endswith(".indeed.fr"):
        params = dict(parse_qsl(p.query))
        key = params.get("jk") or params.get("vjk")
        if key:
            return "posting:indeed:" + key
    return ""


def identity_keys(record):
    """Union URLs and stable posting IDs; a row position is never an identity."""
    supplied_keys = record.get("identity_keys")
    keys = {k for k in supplied_keys if isinstance(k, str)} if isinstance(supplied_keys, list) else set()
    urls = [record.get(k) for k in ("canonical_url", "url", "link", "source_url", "final_url")]
    aliases = record.get("alias_urls")
    if isinstance(aliases, str):
        try:
            aliases = json.loads(aliases)
        except ValueError:
            aliases = re.findall(r"https?://[^\s,;\"\[\]]+", aliases)
    if isinstance(aliases, list):
        urls.extend(aliases)
    for raw in urls:
        url = canonical_url(raw)
        if url:
            keys.add("url:" + url)
            if posting := _posting_key(url):
                keys.add(posting)
    raw_id = str(record.get("job_id") or "").strip()
    if raw_id:
        keys.add("job:" + raw_id)
        prefix, _, item = raw_id.partition("-")
        site = {"LI": "linkedin", "HW": "hellowork", "FT": "france_travail", "AP": "apec"}.get(prefix)
        if site and item:
            keys.add("posting:" + site + ":" + item)
    return {key for key in keys if isinstance(key, str) and key}


_FR_WORDS = frozenset("le la les des une un du de et en pour dans avec sur vous nous votre notre est sont poste missions profil entreprise équipe sera être au aux ce cette qui que par".split())
_EN_WORDS = frozenset("the and of to in for with you we our your is are will be role team experience skills work about as an on at by this that".split())


def guess_advert_language(text: str) -> str | None:
    """Sheet-built roles carry no 'Advert language' line: guess French or English from the advert text."""
    words = re.findall(r"[a-zà-ÿ']+", (text or "").lower())
    if len(words) < 40:
        return None
    fr = sum(1 for word in words if word in _FR_WORDS)
    en = sum(1 for word in words if word in _EN_WORDS)
    if max(fr, en) < 8 or abs(fr - en) < max(fr, en) * 0.25:
        return None
    return "fr" if fr > en else "en"


def merge_events(events, date_field="captured_at"):
    """Merge aliases transitively, retaining the earliest supported event date."""
    groups = []
    for event in events:
        if not isinstance(event, dict):
            continue
        keys = identity_keys(event)
        if not keys:
            continue
        matches = [i for i, group in enumerate(groups) if keys & set(group["identity_keys"])]
        target = dict(event, identity_keys=sorted(keys))
        for i in reversed(matches):
            prior = groups.pop(i)
            keys.update(prior["identity_keys"])
            old, new = _time(prior.get(date_field)), _time(target.get(date_field))
            if old and (not new or old < new):
                target[date_field] = prior[date_field]
            for field in ("canonical_url", "url", "job_id", "company", "title", "language"):
                target[field] = target.get(field) or prior.get(field)
            target["bases"] = sorted((set(target.get("bases") or [target.get("basis", "")]) |
                                      set(prior.get("bases") or [prior.get("basis", "")])) - {""})
            target["sources"] = sorted((set(target.get("sources") or [target.get("source", "")]) |
                                        set(prior.get("sources") or [prior.get("source", "")])) - {""})
        target["identity_keys"] = sorted(keys)
        target["identity"] = next((k for k in sorted(keys) if k.startswith("posting:")), sorted(keys)[0])
        groups.append(target)
    return groups


def public_capture_signature(root):
    path = Path(root) / ".local/public-hunt/advert-inventory.sqlite3"
    stats = []
    for candidate in (path, path.with_name(path.name + "-wal")):
        try:
            stat = candidate.stat()
            stats.append((str(candidate), stat.st_size, stat.st_mtime_ns))
        except OSError:
            stats.append((str(candidate), None))
    return tuple(stats)


def public_capture_events(root):
    """Read existing inventory IDs only; never create or rescan the inventory."""
    root = Path(root).resolve()
    path = root / ".local/public-hunt/advert-inventory.sqlite3"
    empty = {"available": False, "events": [], "omitted_jobs": 0, "read_failed": False}
    failed = {**empty, "read_failed": True}
    if not path.exists() and not path.is_symlink():
        return empty
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
        return failed
    if any(p.is_symlink() for p in path.parents if p != root and p.is_relative_to(root)):
        return failed
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1) as connection:
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute("SELECT posting,website,canonical_url,MIN(captured_at) "
                "FROM captures WHERE length(body_sha256)=64 GROUP BY posting,website,canonical_url "
                "ORDER BY posting LIMIT ?", (MAX_ROWS + 1,)).fetchall()
    except (OSError, sqlite3.Error):
        return failed
    events = []
    for posting, site, raw_url, at in rows[:MAX_ROWS]:
        url = canonical_url(raw_url)
        if not url or not isinstance(posting, str) or not posting.startswith(str(site) + ":"):
            continue
        events.append({"canonical_url": url, "url": url, "identity_keys": ["posting:" + posting],
                       "captured_at": at if _time(at) else None, "source": site,
                       "basis": "indexed_saved_public_capture"})
    return {"available": True, "events": merge_events(events), "omitted_jobs": max(0, len(rows) - MAX_ROWS), "read_failed": False}


class HarkMetrics:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.evidence = self.root / "Master/auto/evidence"
        self.paths = []
        self.omitted = 0
        for folder, patterns in ((self.evidence / "sheet-intake", ("snapshot.json", "final-snapshot.json", "screening.json", "worker-result.json", "native-dispatch.json", "native-terminal.json")),
                                 (self.evidence / "runs", ("inputs/sheet-feed.json", "inputs/sheet-screening.json")),
                                 (self.evidence / "claude-runs", ("run.json", "inputs/snapshot.json", "ledger.jsonl", "agents/screening/attempt-*/*.json"))):
            if not folder.is_dir() or folder.is_symlink():
                continue
            dirs = sorted((p for p in folder.iterdir() if p.is_dir() and not p.is_symlink()), reverse=True)
            self.omitted += max(0, len(dirs) - MAX_SOURCES)
            for directory in dirs[:MAX_SOURCES]:
                for pattern in patterns:
                    self.paths.extend(directory.glob(pattern))
        cache = self.root / "Studio/.local/hark-feed/live-source.json"
        if cache.exists():
            self.paths.append(cache)
        self.paths = sorted(set(self.paths))

    def _bytes(self, path):
        path = Path(path)
        if not path.is_absolute():
            path = self.root / path
        if not path.resolve().is_relative_to(self.root) or path.is_symlink() or not path.is_file():
            raise ValueError("Unsafe saved sheet evidence")
        for parent in path.parents:
            if parent == self.root:
                break
            if parent.is_symlink():
                raise ValueError("Unsafe saved sheet evidence parent")
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("Saved sheet evidence exceeds the projection bound")
        return path.read_bytes()

    def _read(self, path):
        value = json.loads(self._bytes(path))
        if not isinstance(value, dict):
            raise ValueError("Expected saved sheet evidence object")
        return value

    def _bound(self, reference):
        if not isinstance(reference, dict):
            raise ValueError("Missing saved sheet evidence reference")
        raw = self._bytes(reference.get("path", ""))
        if hashlib.sha256(raw).hexdigest() != reference.get("sha256"):
            raise ValueError("Saved sheet evidence bytes changed")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Saved sheet evidence object missing")
        return value

    def signature(self):
        stats = []
        for path in self.paths:
            try:
                stat = path.stat()
                stats.append((str(path), stat.st_size, stat.st_mtime_ns))
            except OSError:
                stats.append((str(path), None))
        code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        return (code_hash, self.omitted, tuple(stats))

    @staticmethod
    def _rows(value):
        rows = value.get("rows")
        return (isinstance(rows, list) and len(rows) <= MAX_ROWS and
                all(isinstance(row, dict) and ("fields" not in row or isinstance(row["fields"], dict)) for row in rows))

    @staticmethod
    def _snapshot(value):
        return (value.get("kind") == "sheet_feed_snapshot" and value.get("schema_version") == 1 and
                value.get("spreadsheet_id") in FEEDS and value.get("sheet_id") == SHEET_ID and
                value.get("source_basis") == "user_provided_sheet_jd" and value.get("live_vacancy_verified") is False and
                _time(value.get("captured_at_utc")) is not None and
                HarkMetrics._rows(value))

    @staticmethod
    def _row(row, *, compact=False, feed="hark"):
        fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
        supplied = {**fields, **row}
        jd = row.get("jd", fields.get("JD (full text)", ""))
        if compact:
            has_text = type(row.get("jd_characters")) is int and row["jd_characters"] > 0
        else:
            has_text = isinstance(jd, str) and bool(jd.strip())
            if row.get("jd_sha256") and row["jd_sha256"] != hashlib.sha256(str(jd).encode()).hexdigest():
                raise ValueError("Saved supplied JD bytes differ from their recorded hash")
        if not has_text:
            return None
        supplied["link"] = row.get("link") or fields.get("Link")
        urls = [canonical_url(supplied.get(k)) for k in ("canonical_url", "link", "source_url", "final_url")]
        url = next((v for v in urls if v), "")
        stamp = next((_time(supplied.get(k)) for k in ("checked_at_utc", "checked_at_paris") if _time(supplied.get(k))), None)
        at = stamp.astimezone(timezone.utc).isoformat() if stamp else None
        event = {"canonical_url": url or None, "url": url or None, "job_id": supplied.get("job_id"),
                 "identity_keys": sorted(identity_keys(supplied)), "captured_at": at,
                 "source": feed, "source_site": supplied.get("source_site"),
                 "source_basis": "user_provided_sheet_jd", "live_vacancy_verified": False,
                 "basis": "supplied_sheet_jd_text", "row_key": row.get("row_key"),
                 "company": row.get("company"), "title": row.get("title"),
                 "language": None if compact else guess_advert_language(jd)}
        return event if event["identity_keys"] else None

    def _accepted_rows(self, snapshot, result, at, basis):
        if result.get("snapshot_sha256") != _sha(snapshot) or _time(at) is None:
            raise ValueError("Screening result is not bound to its saved sheet snapshot")
        rows = {(r.get("row_number"), r.get("row_key")): r for r in snapshot["rows"] if isinstance(r, dict)}
        decisions = result.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError("Screening decisions missing")
        events = []
        for decision in decisions:
            if not isinstance(decision, dict) or decision.get("status") != "ACCEPTED":
                continue
            row = rows.get((decision.get("row_number"), decision.get("row_key")))
            if row is None or row.get("content_sha256") != decision.get("content_sha256"):
                raise ValueError("Accepted row differs from its saved source identity")
            if event := self._row(row, feed=FEEDS.get(snapshot.get("spreadsheet_id"), "hark")):
                events.append({**event, "accepted_at": at, "basis": basis})
        return events

    def _native_acceptances(self, screening):
        if (screening.get("kind") != "sheet_screening_v1" or screening.get("status") != "DONE" or
                not screening.get("audit_executor_id") or screening.get("audit_executor_id") == screening.get("intake_executor_id") or
                screening.get("terminal_status") != "completed" or screening.get("pending_tool_calls") is not False):
            raise ValueError("No completed independent sheet screening")
        snapshot = self._bound(screening.get("snapshot"))
        result = self._bound(screening.get("audit_result"))
        terminal = self._bound(screening.get("native_terminal"))
        dispatch = self._bound(screening.get("native_dispatch"))
        auditor = screening["audit_executor_id"]
        if (not self._snapshot(snapshot) or result.get("kind") != "sheet_screening_result_v1" or
                any(result.get(k) != screening.get(k) for k in ("decisions", "snapshot_sha256", "audit_executor_id", "intake_executor_id", "finished_at_utc")) or
                terminal.get("audit_result") != screening.get("audit_result") or
                dispatch.get("raw_result", {}).get("task_name") != auditor):
            raise ValueError("Screening receipt binding is incomplete")
        agents = terminal.get("raw_result", {}).get("agents", [])
        matches = [a for a in agents if isinstance(a, dict) and a.get("agent_name") == auditor]
        if len(matches) != 1 or not matches[0].get("agent_status", {}).get("completed"):
            raise ValueError("Screening completion missing")
        times = [_time(dispatch.get(k)) for k in ("submitted_at", "ack_at")]
        times += [_time(screening.get("finished_at_utc")), _time(terminal.get("observed_at_utc"))]
        if any(t is None for t in times) or times != sorted(times):
            raise ValueError("Screening clocks missing or reversed")
        return self._accepted_rows(snapshot, result, screening["finished_at_utc"], "independent_sheet_screening")

    def _claude_acceptances(self, directory):
        header = self._read(directory / "run.json")
        if header.get("kind") != "claude_sheet_run_v1" or header.get("fixture") is True:
            return []
        ledger = [json.loads(line) for line in self._bytes(directory / "ledger.jsonl").splitlines() if line.strip()]
        prev = None
        for i, entry in enumerate(ledger):
            core = {k: v for k, v in entry.items() if k != "sha256"}
            if entry.get("seq") != i or entry.get("prev_sha256") != prev or _sha(core) != entry.get("sha256"):
                raise ValueError("Saved sheet ledger chain changed")
            prev = entry["sha256"]
        frozen = next((r["data"] for r in ledger if r.get("event") == "snapshot_frozen"), None)
        if frozen is None:
            return []
        snapshot = self._bound(frozen.get("snapshot"))
        if not self._snapshot(snapshot) or frozen.get("semantic_sha256") != _sha(snapshot):
            raise ValueError("Ledger snapshot differs from its saved input")
        events = []
        for entry in ledger:
            receipt = entry.get("data", {})
            if entry.get("event") != "stage_completed" or receipt.get("stage") != "screening" or receipt.get("verdict") != "DONE":
                continue
            result = self._bound(receipt.get("outputs", {}).get("result"))
            actor = receipt.get("agent_id")
            if str(actor or "").startswith("gemini:") or actor == "runner":
                # 18 Oct: Gemini screening (gemini_pipeline.py) and runner-only screening have no Claude worker
                # completion; the result file is hash-bound above and the ledger chain is verified.
                if (result.get("kind") not in {"claude_sheet_screening_result_v1", "claude_sheet_screening_merged_v1"}
                        or not _time(entry.get("at"))):
                    raise ValueError("Screening ledger lacks a bound Gemini/runner result")
                events.extend(self._accepted_rows(snapshot, result, entry["at"], "completed_sheet_screening_ledger"))
                continue
            completion = self._bound(receipt.get("completion"))
            ack = next((r["data"] for r in ledger if r.get("event") == "agent_acknowledged" and
                        r.get("data", {}).get("request_id") == receipt.get("request_id")), {})
            if (not actor or actor != completion.get("agent_id") or actor != ack.get("agent_id") or
                    completion.get("kind") != "claude_agent_completion_v1" or not completion.get("final_text") or
                    result.get("kind") not in {"claude_sheet_screening_result_v1", "claude_sheet_screening_merged_v1"} or
                    not _time(completion.get("finished_at"))):
                raise ValueError("Screening ledger lacks bound worker completion")
            events.extend(self._accepted_rows(snapshot, result, completion["finished_at"], "completed_sheet_screening_ledger"))
        return events

    def _sheet_row_events(self, directories):
        """6 Nov: every new row of the Hark Jobs sheet is a collected job. Each morning run saves the whole Jobs A:E
        column read (inputs/gemini-driver/Jobs_A1-E<N>.json); a link first seen in a run that is not the oldest saved
        read is dated at that run's start. Rows also in a snapshot keep Hark's earlier check time (merge keeps the
        earliest date). Undated careers-page rows and rows Hark marked itself are no longer left out."""
        reads = []
        for directory in directories:
            try:
                header = self._read(directory / "run.json")
            except (OSError, ValueError):
                continue
            if header.get("kind") != "claude_sheet_run_v1" or header.get("fixture") is True or not _time(header.get("started_at")):
                continue
            for path in sorted(directory.glob("inputs/gemini-driver/Jobs_A1-E*.json")):
                try:
                    value = json.loads(self._bytes(path))
                except (OSError, ValueError):
                    continue
                rows = value.get("values") if isinstance(value, dict) else value
                if isinstance(rows, list):
                    reads.append((_time(header["started_at"]), header["started_at"], rows))
        reads.sort(key=lambda item: item[0])
        seen, events = set(), []
        for index, (_stamp, started, rows) in enumerate(reads):
            for row in rows[1:]:
                if not isinstance(row, list) or len(row) < 3:
                    continue
                url = canonical_url(row[2])
                if not url or url in seen:
                    continue
                seen.add(url)
                if index == 0:
                    continue  # the oldest saved read is the baseline: its rows are older than the saved history
                events.append({"canonical_url": url, "url": url, "captured_at": started, "source": "hark",
                               "company": row[3] if len(row) > 3 else None, "title": row[4] if len(row) > 4 else None,
                               "basis": "hark_sheet_new_row", "source_basis": "user_provided_sheet_row",
                               "live_vacancy_verified": False})
        return events

    def load(self):
        events, accepted, sources = [], [], []
        invalid = 0
        current = None
        seen_screenings = set()
        for path in self.paths:
            if path.name not in {"snapshot.json", "final-snapshot.json", "sheet-feed.json", "live-source.json", "screening.json", "sheet-screening.json"}:
                continue
            try:
                value = self._read(path)
                if self._snapshot(value) or (value.get("kind") == "studio_hark_feed_cache_v1" and
                        value.get("spreadsheet_id") == SPREADSHEET_ID and value.get("sheet_id") == SHEET_ID and
                        value.get("source_basis") == "user_provided_sheet_jd" and value.get("live_vacancy_verified") is False and
                        self._rows(value) and _time(value.get("captured_at_utc"))):
                    compact = value["kind"] == "studio_hark_feed_cache_v1"
                    feed = FEEDS.get(value.get("spreadsheet_id"), "hark")
                    projected = [e for r in value["rows"] if isinstance(r, dict) and (e := self._row(r, compact=compact, feed=feed))]
                    events.extend(projected)
                    sources.append({"path": str(path.relative_to(self.root)), "captured_at_utc": value["captured_at_utc"],
                                    "row_count": len(value["rows"]), "jd_text_rows": len(projected)})
                    if feed == "hark" and (current is None or _time(value["captured_at_utc"]) > _time(current["captured_at_utc"]) or
                            compact and _time(value["captured_at_utc"]) == _time(current["captured_at_utc"])):
                        current = value
                elif value.get("kind") == "sheet_screening_v1":
                    digest = _sha(value)
                    if digest not in seen_screenings:
                        accepted.extend(self._native_acceptances(value))
                        seen_screenings.add(digest)
                elif value.get("kind") in {"sheet_feed_snapshot", "studio_hark_feed_cache_v1"}:
                    raise ValueError("Invalid saved sheet source structure")
            except (OSError, ValueError, TypeError, AttributeError, KeyError):
                invalid += 1
        claude_dirs = {p.parent for p in self.paths if p.name == "run.json" and p.parent.parent.name == "claude-runs"}
        for directory in sorted(claude_dirs):
            try:
                accepted.extend(self._claude_acceptances(directory))
            except (OSError, ValueError, TypeError, AttributeError, KeyError):
                invalid += 1
        events.extend(self._sheet_row_events(sorted(claude_dirs)))
        merged = merge_events(events)
        shortlists = merge_events(accepted, "accepted_at")
        marker_counts = {}
        for row in (current or {}).get("rows", []):
            marker = str(row.get("screened") or row.get("fields", {}).get("Screened (YES/NO)") or "blank")
            marker_counts[marker] = marker_counts.get(marker, 0) + 1
        metadata = {"available": bool(sources), "captured_at_utc": (current or {}).get("captured_at_utc"),
                    "source_url": (current or {}).get("source_url"), "source_basis": "user_provided_sheet_jd",
                    "live_vacancy_verified": False, "row_count": len((current or {}).get("rows", [])) if current else None,
                    "jd_text_rows": next((s["jd_text_rows"] for s in reversed(sources) if s["captured_at_utc"] == current.get("captured_at_utc")), None) if current else None,
                    "unique_jobs": len(merged) if sources else None, "shortlisted_jobs": len(shortlists),
                    "screening_markers": marker_counts, "invalid_saved_sources": invalid, "omitted_source_directories": self.omitted,
                    "sources": sources, "undated_jobs": sum(not _time(e.get("captured_at")) for e in merged),
                    "note": "Supplied Hark JD text is collected source material. YES means screened; only saved independent ACCEPTED decisions count as shortlisted. Dates use recorded Hark checks; unknown dates remain excluded from dated views. Job links were not opened."}
        return {"events": merged, "shortlists": shortlists, "metadata": metadata}
