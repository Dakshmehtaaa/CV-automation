from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data import StudioData
import test_data as fixtures


class NativeFinishProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = fixtures.make_fixture(Path(self.temp.name) / "CV")
        self.data = StudioData(self.root)
        self.run = self.root / "Resources/Master/auto/evidence/runs/2026-10-07-fixture-native"
        self.now = datetime.now(timezone.utc)
        self.start = (self.now - timedelta(minutes=10)).isoformat()
        self.end = (self.now - timedelta(minutes=1)).isoformat()
        self.finish = {"run_id": self.run.name, "owner_token": "owner", "status": "PARTIAL",
            "approved": False, "abort": True, "started_at": self.start, "at": self.end,
            "stage_ledger": self.write_ref("stage-ledger.json", {"kind": "verified_pipeline_stage_ledger",
                "run_id": self.run.name, "owner_token": "owner", "started_at": self.start, "sources": []})}
        self.release = {"schema_version": 1, "kind": "codex_app_safe_owner_release_v1",
            "run_id": self.run.name, "owner_token": "owner", "status": "OWNERSHIP_RELEASED_UNAPPROVED",
            "flow_decision": {"action": "abort", "status": "PARTIAL"},
            "verified_at": self.end, "stage_ledger": self.finish["stage_ledger"],
            "finish_request": self.write_ref("finish-request.json", self.finish)}
        self.manifest = {"kind": "fresh_pipeline_launch", "run_id": self.run.name,
            "owner_token": "owner", "status": "OWNERSHIP_RELEASED_UNAPPROVED",
            "owner_release_status": "OWNERSHIP_RELEASED_UNAPPROVED", "started_at": self.start,
            "finished_at": self.end, "batch_deadline": (self.now + timedelta(minutes=30)).isoformat(),
            "owner_release_receipt": self.write_ref("native-app/safe-owner-release-receipt.json", self.release)}
        fixtures.write_json(self.run / "launch-receipt.json", self.manifest)
        self.addCleanup(patch.stopall)
        patch.object(self.data, "live_pipeline", return_value={"active": False}).start()
        patch.object(self.data, "_active_process", return_value=None).start()

    def write_ref(self, name, value):
        path = self.run / name
        fixtures.write_json(path, value)
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def reseal(self):
        self.release["finish_request"] = self.write_ref("finish-request.json", self.finish)
        self.manifest["owner_release_receipt"] = self.write_ref("native-app/safe-owner-release-receipt.json", self.release)
        fixtures.write_json(self.run / "launch-receipt.json", self.manifest)

    def test_real_bound_abort_closes_window_and_preserves_partial(self):
        self.assertFalse(self.data.active_pipeline()["active"])
        summary = self.data._run_summary(self.run)
        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["ended_at"], self.end)

    def test_actual_process_still_wins_over_saved_closure(self):
        with patch.object(self.data, "_active_process", return_value=12345):
            self.assertEqual(self.data.active_pipeline()["pid"], 12345)

    def test_changed_release_finish_or_ledger_keeps_window_reserved(self):
        for name in ("native-app/safe-owner-release-receipt.json", "finish-request.json", "stage-ledger.json"):
            with self.subTest(name=name):
                path = self.run / name
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                self.assertTrue(self.data.active_pipeline()["active"])
                path.write_bytes(original)

    def test_rehashed_wrong_owner_start_clock_or_approval_refused(self):
        original = dict(self.finish)
        for field, value in (("owner_token", "other"), ("started_at", self.end),
            ("approved", True), ("abort", False), ("at", (self.now + timedelta(minutes=1)).isoformat())):
            with self.subTest(field=field):
                self.finish = {**original, field: value}
                self.reseal()
                self.assertTrue(self.data.active_pipeline()["active"])
        self.finish = original
        self.reseal()

    def test_standalone_finish_without_bound_release_stays_reserved(self):
        self.manifest.pop("owner_release_receipt")
        fixtures.write_json(self.run / "launch-receipt.json", self.manifest)
        self.assertTrue(self.data.active_pipeline()["active"])

    def test_rehashed_foreign_ledger_and_false_completed_state_refused(self):
        foreign = {"kind": "verified_pipeline_stage_ledger", "run_id": "other",
            "owner_token": "other", "started_at": self.start}
        self.finish["stage_ledger"] = self.write_ref("stage-ledger.json", foreign)
        self.release["stage_ledger"] = self.finish["stage_ledger"]
        self.reseal()
        self.assertTrue(self.data.active_pipeline()["active"])
        self.finish["stage_ledger"] = self.write_ref("stage-ledger.json", {
            "kind": "verified_pipeline_stage_ledger", "run_id": self.run.name,
            "owner_token": "owner", "started_at": self.start})
        self.release["stage_ledger"] = self.finish["stage_ledger"]
        self.release["status"] = "COMPLETED_SAFE"
        self.manifest.update(status="COMPLETED_SAFE", owner_release_status="COMPLETED_SAFE")
        self.reseal()
        self.assertTrue(self.data.active_pipeline()["active"])


if __name__ == "__main__":
    unittest.main()
