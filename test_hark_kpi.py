import json
from pathlib import Path
import tempfile
import unittest

import hark_kpi

FEEDS = ["LinkedIn", "HelloWork", "France Travail", "Jobs That Make Sense", "APEC", "LinkedIn sweep"]
FIELDS = ["On time", "Status", "Start", "Stop", "Duration", "Scraped", "Added", "Duplicates skipped",
          "Full job descriptions", "JD capture %", "Audit errors (found / fixed)", "Accuracy %",
          "ON HOLD (fixed / still open)", "Email report"]
HEADER = ["audit_date", "audited_at_paris", "automation_score_pct", "schedules_completed", "schedules_completed_pct",
          "new_rows_total", "jds_captured_total", "jd_capture_pct", "rows_with_audit_errors",
          "row_accuracy_pct_before_fixes", "errors_found_total", "errors_fixed_total", "emails_with_message_id",
          "email_delivery_pct", "onhold_fixed_total", "onhold_unresolved_total", "schedule_changes", "notes"] + \
         [f"{feed} · {field}" for feed in FEEDS for field in FIELDS]
DASH = ["—"] * 12
# Rows as Hark wrote them on 9, 12, 13 and 14 October 2026 (9 Oct has a run summary in the notes
# column, 13 Oct has one stray "—" inside APEC's block). Values are copied from the Audit KPIs tab.
OCT9 = ["2026-10-09", "Fri 2026-10-09 1:15 PM CEST (UTC+02:00)", "85.6%", "4/5", "80%", "160", "134", "83.8%", "34",
        "78.8%", "40", "17", "5/5", "100%", "38", "38", "APEC prompt: added aggregator-card dedupe rule (4 cross-site duplicates today from HELLOWORK-labelled cards)",
        "on time Y | COMPLETE | 00:00:53 | scraped 1461 | new 83",
        "Y", "COMPLETE", "11:00", "11:08", "7 min 37 s", "1612", "34", "1", "34 of 34", "100.0%", "1 / 1", "100%", "0 / 10", "Sent 11:10",
        "Y", "COMPLETE", "11:25", "11:26", "0 min 53 s", "1461", "83", "10", "83 of 83", "100.0%", "10 / 8", "89.2%", "1 / 11", "Sent 11:28",
        "Y", "COMPLETE", "11:50", "12:34", "44 min", "103", "17", "27", "17 of 17", "100.0%", "1 / 1", "100%", "37 / 12", "Sent 12:38",
        "Y", "COMPLETE", "12:40", "12:43", "3 min 14 s", "3", "0", "3", "0 of 0", "n/a", "1 / 1", "n/a", "0 / 0", "Sent 12:44",
        "Y", "PARTIAL", "12:50", "13:04", "15 min", "31", "26", "5", "2 of 26", "7.7%", "27 / 6", "3.8%", "0 / 5", "Sent 13:06",
        "—", "not scheduled yet"] + DASH
OCT12 = ["2026-10-12", "2026-10-12 13:30 CEST", "97.9", "5/5", "100", "71", "71", "100", "6", "91.5", "10",
         "10 (6 rows + 4 Runs cells)", "5", "100", "0", "1 (UNEP row 392, empty LI advert)", "none",
         "LI run wrongly appended then deleted 63 rows (cleaned). Recurring watch: RUNS-TIME approx times (LI, FT, JT, AP), JD-NOTES (APEC 2nd day), HW OFFTOPIC (2nd day, different kind)",
         "Y", "COMPLETE", "11:00", "11:08", "8 min", "1144", "16", "7", "16 of 16", "100.0%", "0 / 0", "100.0%", "0 / 1", "Sent 11:09",
         "Y", "COMPLETE", "11:25", "11:25", "0 min 22 s", "578", "28", "1", "28 of 28", "100.0%", "2 / 2", "92.9%", "0 / 0", "Sent 11:26",
         "Y", "COMPLETE", "11:50", "12:24", "34 min", "25", "16", "3", "16 of 16", "100.0%", "1 / 1", "93.8%", "0 / 0", "Sent 12:24",
         "Y", "COMPLETE", "12:40", "12:42", "1 min 55 s", "21", "2", "0", "2 of 2", "100.0%", "0 / 0", "100.0%", "0 / 0", "Sent 12:42",
         "Y", "COMPLETE", "12:50 (approx)", "13:03 (approx)", "13 min (approx)", "21", "9", "12", "8 of 9", "88.9%", "3 / 3", "66.7%", "0 / 0", "Sent 13:07",
         "—", "not scheduled yet"] + DASH
OCT13 = ["2026-10-13", "2026-10-13 18:35 CEST", "89.0", "4/5", "80", "105", "104", "99.0", "3", "97.1", "3",
         "0 (rows already YES-screened by Daksh pipeline; left untouched)", "4", "80", "0",
         "3 (pipeline ON HOLD rows 501,528,568 not touched)", "none", "APEC did not complete today. Errors found but not fixed because rows were already Screened=YES.",
         "Y", "COMPLETE", "11:00", "11:09", "9 min", "1441", "32", "5", "32 of 32", "100.0%", "0 / 0", "100.0%", "0 / 1", "Sent 11:17",
         "Y", "COMPLETE", "11:25", "11:26", "0 min 54 s", "1021", "52", "5", "52 of 52", "100.0%", "2 / 0", "96.2%", "0 / 2", "Sent",
         "Y", "COMPLETE", "11:50", "12:29", "39 min", "105", "21", "44", "21 of 21", "100.0%", "1 / 0", "95.2%", "3 / 1", "Sent 12:33",
         "Y", "COMPLETE", "12:40", "12:42", "2 min 09 s", "40", "0", "1", "0 of 0", "n/a", "0 / 0", "n/a", "0 / 0", "Sent 12:46",
         "—", "NO RUN"] + ["—"] * 13 + ["not scheduled yet"] + DASH
OCT14 = ["2026-10-14", "2026-10-14 13:35 CEST", "87.2", "4/5", "80", "80", "80", "100", "9", "88.8", "9", "9", "4/5", "80",
         "0", "LI 1 / HW 4 / AP 1 unresolved (run-reported)", "none",
         "Email intake: 0 alert jobs added (OMEVA already row 470; Tikehau needs French; UNEP JD unreachable; others off-topic), 1 dup; 0 new rejections (Jean Hénaff already recorded)",
         "Y", "COMPLETE", "11:00", "11:11", "11 min", "1567", "21", "11", "21 of 21", "100.0%", "3 / 3", "85.7%", "0 / 1", "Sent 11:13",
         "Y", "COMPLETE", "11:25", "~11:28 (audit)", "~3 min (audit)", "818", "36", "0", "36 of 36", "100.0%", "2 / 2", "94.4%", "0 / 4", "Sent 11:26",
         "—", "NO RUN"] + DASH + [
         "Y", "COMPLETE", "12:42", "12:43", "1 min 06 s", "29", "2", "0", "2 of 2", "100.0%", "1 / 1", "50.0%", "0 / 0", "Sent 12:44",
         "Y", "COMPLETE", "12:50", "13:06", "16 min", "30", "21", "9", "21 of 21", "100.0%", "3 / 3", "85.7%", "0 / 1", "Sent 13:12",
         "Y", "COMPLETE", "13:20", "13:38", "18 min", "2386", "9", "10", "9 of 9", "100.0%", "not audited (runs after 13:00 audit)",
         "not audited", "0 / 0", "Sent 13:39"]
ROWS = [HEADER, OCT9, OCT12, OCT13, OCT14]


class HarkKpiTests(unittest.TestCase):
    def setUp(self):
        self.parsed = hark_kpi.parse(ROWS)
        self.days = {d["date"]: d for d in self.parsed["days"]}

    def test_feeds_come_from_the_header_in_order(self):
        self.assertEqual([f["id"] for f in self.parsed["feeds"]],
                         ["linkedin", "hellowork", "france_travail", "jobs_that_make_sense", "apec", "linkedin_sweep"])

    def test_early_row_layout_and_notes(self):
        self.assertTrue(self.days["2026-10-09"]["notes"].startswith("APEC prompt"))
        feeds = self.days["2026-10-09"]["feeds"]
        self.assertEqual((feeds["linkedin"]["start"], feeds["linkedin"]["duration_min"], feeds["linkedin"]["added"]), ("11:00", 7.6, 34))
        self.assertEqual(feeds["apec"]["status"], "partial")
        self.assertEqual(feeds["apec"]["accuracy_pct"], 3.8)
        self.assertEqual(feeds["linkedin_sweep"]["status"], "not_scheduled")

    def test_stray_dash_inside_a_block_is_absorbed(self):
        feeds = self.days["2026-10-13"]["feeds"]
        self.assertEqual(feeds["apec"]["status"], "no_run")
        self.assertFalse(feeds["apec"]["ran"])
        self.assertEqual(feeds["linkedin_sweep"]["status"], "not_scheduled")
        self.assertEqual(feeds["france_travail"]["duration_min"], 39.0)

    def test_free_text_cells(self):
        day = self.days["2026-10-14"]
        self.assertEqual(day["onhold_open"], 6)  # LI 1 / HW 4 / AP 1
        self.assertEqual((day["report_emails_sent"], day["report_emails_total"]), (4, 5))
        self.assertTrue(day["feeds"]["hellowork"]["approx_time"])
        self.assertEqual(day["feeds"]["hellowork"]["duration_min"], 3.0)
        sweep = day["feeds"]["linkedin_sweep"]
        self.assertIsNone(sweep["accuracy_pct"])
        self.assertFalse(sweep["audited"])
        self.assertEqual(sweep["jds"], 9)
        self.assertEqual(self.days["2026-10-13"]["errors_fixed"], 0)
        self.assertEqual(self.days["2026-10-09"]["automation_score_pct"], 85.6)

    def test_email_count_without_total_uses_delivery_percent(self):
        for date, expected in (("2026-10-12", (5, 5)), ("2026-10-13", (4, 5)), ("2026-10-14", (4, 5))):
            day = self.days[date]
            self.assertEqual((day["report_emails_sent"], day["report_emails_total"]), expected, date)

    def test_email_intake_only_when_reported(self):
        self.assertEqual(self.days["2026-10-14"]["email_intake"],
                         {"rejections": 0, "jobs_added": 0, "duplicates": 1, "reported": True})
        self.assertIsNone(self.days["2026-10-13"]["email_intake"]["rejections"])
        self.assertFalse(self.days["2026-10-13"]["email_intake"]["reported"])

    def test_snapshot_round_trip_and_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kpi.json"
            self.assertIsNone(hark_kpi.load_snapshot(path))
            hark_kpi.write_snapshot(ROWS, path)
            self.assertEqual(len(hark_kpi.load_snapshot(path)["days"]), 4)
            path.write_text(json.dumps({"kind": "other"}))
            self.assertIsNone(hark_kpi.load_snapshot(path))
        with self.assertRaises(ValueError):
            hark_kpi.parse([["a"], ["b"]])


class FirecrawlKpiTests(unittest.TestCase):
    """20 Oct 2026: the Firecrawl hunt writes rows with make_header/make_row; the same parser reads them."""
    FEEDS = ["Tier 1 ATS", "Tier 2 Niche boards", "Tier 3 Career pages", "Tier 4 Open web"]

    def rows(self):
        day = {"audit_date": "2026-10-21", "audited_at_paris": "2026-10-21 08:20 CEST", "schedules_completed": "1/1",
               "new_rows_total": 12, "jds_captured_total": 12, "jd_capture_pct": "100.0%", "errors_found_total": 3,
               "notes": "budget: 140 Firecrawl credits, 40 Gemini calls"}
        block = {"on_time": "Y", "status": "COMPLETE", "start": "07:30", "stop": "07:45", "duration": "15 min",
                 "scraped": 30, "added": 5, "duplicates": 9, "jds": "5 of 5", "jd_pct": "100.0%", "errors": "1 / 0"}
        feeds = {name: dict(block) for name in self.FEEDS}
        feeds["Tier 4 Open web"] = {"status": "SKIPPED"}
        return [hark_kpi.make_header(self.FEEDS), hark_kpi.make_row(day, feeds)]

    def test_rows_written_by_the_hunt_parse_back(self):
        parsed = hark_kpi.parse(self.rows(), "Firecrawl sheet · Audit KPIs tab")
        self.assertEqual([f["id"] for f in parsed["feeds"]], ["tier_1_ats", "tier_2_niche_boards", "tier_3_career_pages", "tier_4_open_web"])
        day = parsed["days"][0]
        self.assertEqual((day["new_rows"], day["schedules_completed"], day["errors_found"]), (12, 1, 3))
        self.assertIsNone(day["automation_score_pct"])  # unknown stays unknown
        ats = day["feeds"]["tier_1_ats"]
        self.assertEqual((ats["status"], ats["duration_min"], ats["added"], ats["jds"], ats["jds_of"]), ("complete", 15.0, 5, 5, 5))
        self.assertIsNone(ats["accuracy_pct"])
        self.assertEqual(day["feeds"]["tier_4_open_web"]["status"], "skipped")
        self.assertFalse(day["feeds"]["tier_4_open_web"]["ran"])
        self.assertEqual(parsed["source"], "Firecrawl sheet · Audit KPIs tab")

    def test_import_with_source_writes_the_firecrawl_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "fc.json"
            original = hark_kpi.SOURCES["firecrawl"]
            hark_kpi.SOURCES["firecrawl"] = (target, original[1])
            try:
                rows = Path(tmp) / "rows.json"
                rows.write_text(json.dumps({"values": self.rows()}))
                self.assertEqual(hark_kpi.main(["hark_kpi.py", "import", "--source", "firecrawl", str(rows)]), 0)
                self.assertEqual(hark_kpi.load_snapshot(target)["source"], original[1])
                self.assertEqual(hark_kpi.main(["hark_kpi.py", "import", "--source", "other", str(rows)]), 2)
            finally:
                hark_kpi.SOURCES["firecrawl"] = original


if __name__ == "__main__":
    unittest.main()
