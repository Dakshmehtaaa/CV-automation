"""Independent negative probes for native Studio closure observations."""
import copy
import unittest
from pathlib import Path
import test_native_finish_projection as fixtures


class NativeFinishIndependentTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.NativeFinishProjectionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_rehashed_foreign_ledger_identity_cannot_unlock(self):
        f = self.f
        for field, value in (("kind", "foreign"), ("run_id", "2026-10-07-foreign"),
                             ("owner_token", "foreign"), ("started_at", f.end)):
            with self.subTest(field=field):
                ledger = {"kind": "verified_pipeline_stage_ledger", "run_id": f.run.name,
                          "owner_token": "owner", "started_at": f.start, field: value}
                f.finish["stage_ledger"] = f.write_ref("stage-ledger.json", ledger)
                f.release["stage_ledger"] = f.finish["stage_ledger"]
                f.reseal()
                self.assertTrue(f.data.active_pipeline()["active"])

    def test_release_leaf_symlink_and_symlink_ancestor_cannot_unlock(self):
        f = self.f
        release = f.run / "native-app/safe-owner-release-receipt.json"
        original = release.read_bytes()
        alias = f.run / "real-release.json"
        alias.write_bytes(original)
        release.unlink()
        release.symlink_to(alias)
        self.assertTrue(f.data.active_pipeline()["active"])
        release.unlink(); release.write_bytes(original)
        parent = f.run / "native-app"
        other = f.run / "real-native-app"
        parent.rename(other)
        parent.symlink_to(other, target_is_directory=True)
        self.assertTrue(f.data.active_pipeline()["active"])

    def test_parent_traversal_or_foreign_hash_bound_reference_cannot_unlock(self):
        f = self.f
        original = copy.deepcopy(f.manifest)
        for path in (str(f.run / "native-app/../native-app/safe-owner-release-receipt.json"),
                     str(f.root / "foreign-release.json")):
            f.manifest = copy.deepcopy(original)
            f.manifest["owner_release_receipt"]["path"] = path
            fixtures.fixtures.write_json(f.run / "launch-receipt.json", f.manifest)
            self.assertTrue(f.data.active_pipeline()["active"])


if __name__ == "__main__":
    unittest.main()
