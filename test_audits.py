"""Audit summary tests use isolated temp folders only."""
import json
from pathlib import Path
import tempfile
import unittest

from audits import audits_overview


def _layer(index, auditor, score, at, **extra):
    payload = {
        "kind": "chain_audit_v1", "auditor": auditor, "layer_index": index, "at": at,
        "jobs_checked": 35, "clean_jobs": 30 + index, "flow_score": score,
        "layers": {"names": {"passed": 1, "total": 1, "score": 100.0}},
        "fixes": [{"job": "a", "action": "x", "ok": True}, {"job": "b", "action": "y", "ok": False}],
        "unresolved": [{"job": "c", "company": "Acme", "problem": "claim"}],
        "jobs": [{"id": "secret-per-job-detail"}],
    }
    payload.update(extra)
    return payload


class AuditsOverviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        audits = self.root / "audits"
        older = audits / "2026-11-03-run"
        newer = audits / "2026-11-04-run"
        older.mkdir(parents=True)
        newer.mkdir(parents=True)
        (older / "0-script.json").write_text(json.dumps(_layer(0, "script", 97.0, "2026-11-03T06:01:00Z")))
        (older / "1-gemini.json").write_text(json.dumps(_layer(1, "gemini", 98.5, "2026-11-03T06:05:00Z")))
        (older / "2-argon.json").write_text(json.dumps(_layer(2, "argon", 99.1, "2026-11-03T06:09:00Z")))
        (newer / "0-script.json").write_text(json.dumps(_layer(0, "script", 99.8, "2026-11-04T06:01:00Z", email_sent=True)))
        (newer / "1-gemini.json").write_text("{ not json")
        (newer / "2-argon.json").write_text(json.dumps(_layer(2, "argon", 99.9, "2026-11-04T06:09:00Z", flow_score_before=95.0)))
        (audits / "2026-11-05-empty").mkdir()
        (audits / "loose-file.json").write_text("{}")

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_and_ordering(self):
        result = audits_overview(self.root)
        self.assertEqual([run["id"] for run in result["runs"]], ["2026-11-04-run", "2026-11-03-run"])
        newest = result["runs"][0]
        self.assertEqual([layer["index"] for layer in newest["layers"]], [0, 2])
        self.assertEqual(newest["at"], "2026-11-04T06:09:00Z")
        self.assertEqual(newest["layers"][0]["fixes"], 2)
        self.assertEqual(newest["layers"][0]["fixes_ok"], 1)
        self.assertTrue(newest["layers"][0]["email_sent"])
        self.assertEqual(newest["layers"][1]["flow_score_before"], 95.0)
        self.assertEqual(result["latest"]["id"], "2026-11-04-run")

    def test_per_job_detail_is_stripped(self):
        result = audits_overview(self.root)
        self.assertNotIn("jobs", result["runs"][0]["layers"][0])
        self.assertNotIn("secret-per-job-detail", json.dumps(result))

    def test_trend_is_oldest_first_with_final_layer_score(self):
        result = audits_overview(self.root)
        self.assertEqual(result["trend"], [
            {"id": "2026-11-03-run", "at": "2026-11-03T06:09:00Z", "final_score": 99.1},
            {"id": "2026-11-04-run", "at": "2026-11-04T06:09:00Z", "final_score": 99.9},
        ])

    def test_limit_and_missing_root(self):
        self.assertEqual(len(audits_overview(self.root, limit=1)["runs"]), 1)
        empty = audits_overview(self.root / "nowhere")
        self.assertEqual(empty, {"runs": [], "latest": None, "trend": []})


if __name__ == "__main__":
    unittest.main()
