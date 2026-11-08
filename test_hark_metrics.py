"""Invented local source fixtures; no connectors or production-state writes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from hark_metrics import HarkMetrics, SPREADSHEET_ID, SHEET_ID, _sha, merge_events, public_capture_events


class HarkMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="studio-hark-metrics-")
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, value):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def row(self, id="LI-123", *, text="Saved duties and requirements.", at="2026-10-09 23:45 UTC", url="https://www.linkedin.com/jobs/view/123"):
        fields = {"job_id": id, "Link": url, "canonical_url": url, "checked_at_utc": at,
                  "Company": "Invented Company", "Post": "Invented Analyst", "JD (full text)": text}
        return {"row_number": 2, "row_key": "job:" + id, "job_id": id, "link": url,
                "company": fields["Company"], "title": fields["Post"], "fields": fields,
                "content_sha256": _sha(fields), "jd": text, "jd_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "screened": "YES"}

    def snapshot(self, rows=None, at="2026-10-10T00:01:00Z"):
        return {"kind": "sheet_feed_snapshot", "schema_version": 1, "spreadsheet_id": SPREADSHEET_ID,
                "sheet_id": SHEET_ID, "source_basis": "user_provided_sheet_jd", "live_vacancy_verified": False,
                "captured_at_utc": at, "rows": rows if rows is not None else [self.row()]}

    def cache(self, rows):
        return {**self.snapshot(rows), "kind": "studio_hark_feed_cache_v1"}

    def test_alias_union_and_date_use_source_check_not_snapshot_time(self):
        self.write("Master/auto/evidence/sheet-intake/one/snapshot.json", self.snapshot())
        self.write("Studio/.local/hark-feed/live-source.json", self.cache([
            {"job_id": "LI-123", "link": "https://linkedin.com/jobs/view/analyst-123?trk=a", "screened": "FAILED",
             "jd_characters": 120, "checked_at_utc": "2026-10-09 23:45 UTC"},
            {"job_id": "LI-124", "link": "https://linkedin.com/jobs/view/124", "jd_characters": 0, "screened": "YES"},
        ]))
        value = HarkMetrics(self.root).load()
        self.assertEqual(value["metadata"]["unique_jobs"], 1)
        self.assertEqual(value["metadata"]["jd_text_rows"], 1)
        self.assertEqual(value["events"][0]["captured_at"], "2026-10-09T23:45:00+00:00")
        self.assertEqual(value["metadata"]["shortlisted_jobs"], 0)
        self.assertEqual(value["metadata"]["screening_markers"], {"FAILED": 1, "YES": 1})

    def test_new_saved_snapshot_invalidates_signature_and_adds_rows(self):
        self.write("Studio/.local/hark-feed/live-source.json", self.cache([{ "job_id": "LI-123", "jd_characters": 99 }]))
        first = HarkMetrics(self.root)
        signature = first.signature()
        self.write("Master/auto/evidence/sheet-intake/new/snapshot.json", self.snapshot([self.row("LI-124", url="https://linkedin.com/jobs/view/124")], at="2026-10-10T01:00:00Z"))
        after = HarkMetrics(self.root)
        self.assertNotEqual(signature, after.signature())
        value = after.load()
        self.assertEqual(value["metadata"]["unique_jobs"], 2)
        self.assertEqual(value["metadata"]["captured_at_utc"], "2026-10-10T01:00:00Z")

    def test_corrupt_cache_keeps_other_valid_evidence(self):
        self.write("Master/auto/evidence/sheet-intake/one/snapshot.json", self.snapshot())
        for rows in ([None], [{"fields": None}], [{"fields": "invalid"}], [7]):
            with self.subTest(rows=rows):
                self.write("Studio/.local/hark-feed/live-source.json", self.cache(rows))
                value = HarkMetrics(self.root).load()
                self.assertEqual(value["metadata"]["unique_jobs"], 1)
                self.assertEqual(value["metadata"]["invalid_saved_sources"], 1)
                self.assertEqual(value["metadata"]["shortlisted_jobs"], 0)

    def test_corrupt_cache_alone_is_unavailable(self):
        self.write("Studio/.local/hark-feed/live-source.json", self.cache([None]))
        value = HarkMetrics(self.root).load()
        self.assertFalse(value["metadata"]["available"])
        self.assertIsNone(value["metadata"]["unique_jobs"])

    def test_corrupt_jd_hash_is_unavailable_instead_of_confirmed_zero(self):
        row = self.row()
        row["jd_sha256"] = "0" * 64
        self.write("Master/auto/evidence/sheet-intake/one/snapshot.json", self.snapshot([row]))
        value = HarkMetrics(self.root).load()
        self.assertFalse(value["metadata"]["available"])
        self.assertIsNone(value["metadata"]["unique_jobs"])
        self.assertEqual(value["metadata"]["invalid_saved_sources"], 1)

    def test_native_acceptance_requires_matching_worker_and_terminal_bytes(self):
        base = "Master/auto/evidence/sheet-intake/one/"
        snapshot = self.snapshot()
        ref = self.write(base + "snapshot.json", snapshot)
        decision = {"row_number": 2, "row_key": "job:LI-123", "content_sha256": snapshot["rows"][0]["content_sha256"], "status": "ACCEPTED"}
        result = {"kind": "sheet_screening_result_v1", "snapshot_sha256": _sha(snapshot), "decisions": [decision],
                  "intake_executor_id": "intake", "audit_executor_id": "independent", "finished_at_utc": "2026-10-10T00:03:00Z"}
        result_ref = self.write(base + "worker-result.json", result)
        terminal_ref = self.write(base + "native-terminal.json", {"audit_result": result_ref,
            "observed_at_utc": "2026-10-10T00:04:00Z", "raw_result": {"agents": [{"agent_name": "independent", "agent_status": {"completed": "Done"}}]}})
        dispatch_ref = self.write(base + "native-dispatch.json", {"submitted_at": "2026-10-10T00:02:00Z", "ack_at": "2026-10-10T00:02:01Z", "raw_result": {"task_name": "independent"}})
        screening = {**result, "kind": "sheet_screening_v1", "status": "DONE", "terminal_status": "completed", "pending_tool_calls": False,
                     "snapshot": ref, "audit_result": result_ref, "native_terminal": terminal_ref, "native_dispatch": dispatch_ref}
        self.write(base + "screening.json", screening)
        self.assertEqual(HarkMetrics(self.root).load()["metadata"]["shortlisted_jobs"], 1)
        (self.root / result_ref["path"]).write_text("{}")
        changed = HarkMetrics(self.root).load()
        self.assertEqual(changed["metadata"]["shortlisted_jobs"], 0)
        self.assertEqual(changed["metadata"]["unique_jobs"], 1)

    def test_claude_acceptance_is_bound_to_completed_screening_ledger(self):
        base = "Master/auto/evidence/claude-runs/invented/"
        self.write(base + "run.json", {"kind": "claude_sheet_run_v1", "fixture": False})
        snapshot = self.snapshot()
        snapshot_ref = self.write(base + "inputs/snapshot.json", snapshot)
        row = snapshot["rows"][0]
        result = {"kind": "claude_sheet_screening_result_v1", "snapshot_sha256": _sha(snapshot),
                  "decisions": [{"row_number": 2, "row_key": row["row_key"], "content_sha256": row["content_sha256"], "status": "ACCEPTED"}]}
        result_ref = self.write(base + "agents/screening/attempt-1/screening-result.json", result)
        completion_ref = self.write(base + "agents/screening/attempt-1/completion.json", {"kind": "claude_agent_completion_v1", "agent_id": "worker",
            "final_text": "Screening finished", "finished_at": "2026-10-10T00:03:00Z"})
        entries = [("snapshot_frozen", {"snapshot": snapshot_ref, "semantic_sha256": _sha(snapshot)}),
                   ("agent_acknowledged", {"request_id": "request", "agent_id": "worker"}),
                   ("stage_completed", {"stage": "screening", "verdict": "DONE", "request_id": "request", "agent_id": "worker",
                    "outputs": {"result": result_ref}, "completion": completion_ref})]
        lines, prev = [], None
        for seq, (event, data) in enumerate(entries):
            entry = {"seq": seq, "event": event, "data": data, "prev_sha256": prev}
            entry["sha256"] = _sha(entry)
            prev = entry["sha256"]
            lines.append(json.dumps(entry))
        path = self.root / base / "ledger.jsonl"
        path.write_text("\n".join(lines))
        self.assertEqual(HarkMetrics(self.root).load()["metadata"]["shortlisted_jobs"], 1)
        path.write_text("\n".join(lines).replace('"DONE"', '"FAILED"'))
        changed = HarkMetrics(self.root).load()
        self.assertEqual(changed["metadata"]["shortlisted_jobs"], 0)
        self.assertEqual(changed["metadata"]["unique_jobs"], 1)

    def test_public_inventory_is_read_only_and_dedupes_with_hark(self):
        path = self.root / ".local/public-hunt/advert-inventory.sqlite3"
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE captures(posting TEXT,website TEXT,canonical_url TEXT,captured_at TEXT,body_sha256 TEXT)")
            c.execute("INSERT INTO captures VALUES(?,?,?,?,?)", ("linkedin:123", "linkedin", "https://linkedin.com/jobs/view/123", "2026-10-08T12:00:00Z", "a" * 64))
        before = path.read_bytes()
        value = public_capture_events(self.root)
        self.assertTrue(value["available"])
        self.assertEqual(path.read_bytes(), before)
        merged = merge_events(value["events"] + [{"job_id": "LI-123", "url": "https://linkedin.com/jobs/view/analyst-123", "captured_at": "2026-10-09T12:00:00Z"}])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["captured_at"], "2026-10-08T12:00:00Z")

    def test_lookalike_host_does_not_merge_with_linkedin_id(self):
        merged = merge_events([{"url": "https://linkedin.com/jobs/view/123"},
                               {"url": "https://linkedin.com.evil.example/jobs/view/123"}])
        self.assertEqual(len(merged), 2)

    def test_corrupt_public_inventory_marks_supported_counts_minimum(self):
        from data import StudioData

        data = StudioData(self.root)
        cache = self.root / "Studio/.local/hark-feed/live-source.json"
        self.write(cache, self.cache([{ "job_id": "LI-123", "jd_characters": 99, "checked_at_utc": "2026-10-09T12:00:00Z" }]))
        inventory = self.root / ".local/public-hunt/advert-inventory.sqlite3"
        inventory.parent.mkdir(parents=True)
        damaged = b"corrupt saved inventory fixture"
        inventory.write_bytes(damaged)
        reader = public_capture_events(self.root)
        self.assertTrue(reader["read_failed"])
        value = data._dashboard_analytics([], [])
        self.assertEqual(value["unique_jobs_collected"], 1)
        self.assertTrue(value["collection_events_partial"])
        self.assertTrue(value["collection_sources"]["public_inventory_read_failed"])
        self.assertEqual(inventory.read_bytes(), damaged)

        # A source that never existed is distinct from an unreadable saved one.
        inventory.unlink()
        missing = data._dashboard_analytics([], [])
        self.assertEqual(missing["unique_jobs_collected"], 1)
        self.assertFalse(missing["collection_events_partial"])
        self.assertFalse(missing["collection_sources"]["public_inventory_read_failed"])

        inventory.write_bytes(damaged)
        cache.unlink()
        unsupported = data._dashboard_analytics([], [])
        self.assertIsNone(unsupported["unique_jobs_collected"])
        self.assertTrue(unsupported["collection_events_partial"])
        self.assertEqual(inventory.read_bytes(), damaged)


    def test_new_sheet_rows_are_collected_on_first_run_that_saw_them(self):
        head = ["Screened (YES/NO)", "Pipeline feedback", "Link", "Company", "Post"]
        old = ["NO", "", "https://www.linkedin.com/jobs/view/1", "A", "Old job"]
        new = ["", "", "https://www.welcometothejungle.com/fr/companies/b/jobs/new", "B", "New job"]
        for name, at, rows in (("r1", "2026-11-05T05:00:00+00:00", [head, old]),
                               ("r2", "2026-11-06T05:00:00+00:00", [head, old, new])):
            base = f"Master/auto/evidence/claude-runs/{name}/"
            self.write(base + "run.json", {"kind": "claude_sheet_run_v1", "fixture": False, "started_at": at})
            self.write(base + "inputs/gemini-driver/Jobs_A1-E%d.json" % len(rows), {"values": rows})
        events = HarkMetrics(self.root)._sheet_row_events(sorted((self.root / "Master/auto/evidence/claude-runs").iterdir()))
        self.assertEqual([(e["title"], e["captured_at"]) for e in events], [("New job", "2026-11-06T05:00:00+00:00")])

if __name__ == "__main__":
    unittest.main()
