"""Independent adversarial tests for token-usage trust boundaries."""
import unittest
import history
import json
import copy
import tempfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
import native_usage
import test_native_usage as usage_fixtures
from native_usage import collect_run, NativeUsageError


class NativeUsageIndependentTests(unittest.TestCase):
    def test_stored_metrics_cannot_self_assert_rollout_validation(self):
        row = {
            "stage": "hunt", "worker_id": "hunt:hellowork",
            "thread_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "usage_thread_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "usage_scope": "worker_thread_cumulative",
            "usage_counter_basis": "codex_rollout_token_count",
            "usage_observed_at": "2026-10-07T09:20:00Z",
            "recorded_tokens": {"input_tokens": 999000, "output_tokens": 1000,
                                "total_tokens": 1000000},
            "_native_usage_sidecar_validated": True,
            "usage_binding_verified": True,
            "usage_source_integrity_verified": True,
        }
        usage = history._usage(row)
        self.assertFalse(usage["usage_binding_verified"])
        self.assertIsNot(usage.get("usage_source_integrity_verified"), True)

    def test_non_token_event_cannot_supply_token_counter(self):
        f = usage_fixtures.NativeUsageTests(); f.setUp()
        try:
            f._append_event("2026-10-07T09:20:00Z", f.counter(100, 80, 20, 10),
                            type="agent_message")
            result = collect_run(f.workspace, f.RUN_ID, sessions_root=f.sessions,
                                 now=f.NOW, allow_noncurrent=True)
            self.assertEqual(result["worker_usage_snapshots"], [])
        finally:
            f.tearDown()

    def test_duplicate_canonical_worker_task_with_different_uuid_is_held(self):
        f = usage_fixtures.NativeUsageTests(); f.setUp()
        try:
            f._append_event("2026-10-07T09:20:00Z", f.counter(100, 80, 20, 10), type="token_count")
            second = f.day / "rollout-zz-duplicate.jsonl"
            metadata = f._metadata(thread_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                parent_id=f.OWNER_ID, agent_path=f.TASK_PATH, depth=2)
            usage_fixtures.write_json(second, metadata)
            with second.open("ab") as stream:
                stream.write(json.dumps({"type":"event_msg", "timestamp":"2026-10-07T09:21:00Z",
                    "payload":{"type":"token_count", "info":{"total_token_usage":
                        f.counter(200, 180, 30, 15)}}}).encode() + b"\n")
            try:
                result = collect_run(f.workspace, f.RUN_ID, sessions_root=f.sessions,
                                     now=f.NOW, allow_noncurrent=True)
            except NativeUsageError:
                return
            self.assertEqual(result["worker_usage_snapshots"], [])
        finally:
            f.tearDown()

    def test_recomputed_sidecar_cannot_forge_counts_time_or_event_coordinates(self):
        f = usage_fixtures.NativeUsageTests(); f.setUp()
        try:
            f._append_event("2026-10-07T09:20:00Z", f.counter(100, 80, 20, 10), type="token_count")
            payload = collect_run(f.workspace, f.RUN_ID, sessions_root=f.sessions,
                                  now=f.NOW, allow_noncurrent=True)
            local = f.workspace / "Resources/Studio/.local"
            native_usage.write_snapshot(local, payload)
            with patch.object(native_usage, "_latest_counter", side_effect=AssertionError("loader must seek original line")):
                self.assertIsNotNone(native_usage.load_snapshot(local, f.RUN_ID,
                    workspace=f.workspace, sessions_root=f.sessions))
            mutations = [
                lambda row: row.update(recorded_tokens=f.counter(999000, 80, 1000, 10)),
                lambda row: row.update(usage_observed_at="2026-10-07T09:21:00Z"),
                lambda row: row.update(source_event_offset=row["source_event_offset"] + 1),
                lambda row: row.update(source_event_length=row["source_event_length"] - 1),
            ]
            for mutate in mutations:
                changed = copy.deepcopy(payload)
                mutate(changed["worker_usage_snapshots"][0])
                native_usage.write_snapshot(local, changed)
                self.assertIsNone(native_usage.load_snapshot(local, f.RUN_ID,
                    workspace=f.workspace, sessions_root=f.sessions))
        finally:
            f.tearDown()

    def test_transient_saved_projection_cannot_restore_verified_rollout_counter(self):
        forged = {"usage_scope": "worker_thread_cumulative",
            "usage_counter_basis": "codex_rollout_token_count",
            "usage_binding_verified": True, "usage_source_integrity_verified": True,
            "usage_thread_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "usage_observed_at": "2026-10-07T09:20:00Z",
            "recorded_tokens": {"total_tokens": 1000000}}
        saved = {"schema_version": 1, "run_id": "2026-10-07-fixture",
                 "summary": {"id": "2026-10-07-fixture",
            "stage_metrics": [{"id": "hunt", "usage_counters": [forged]}]},
            "recorded_at": "2026-10-07T09:21:00Z"}
        target = object.__new__(history.RunHistory)
        target.data = SimpleNamespace(_safe=lambda path, root: path)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / target._snapshot_filename(saved["run_id"])).write_text(json.dumps(saved))
            with patch.object(target, "_snapshot_root", return_value=root):
                result = target._with_snapshot({"id": "2026-10-07-fixture"},
                                               detail=False, transient=True)
                recovered = target._recover_snapshots(set())
                self.assertEqual(len(recovered), 1)
                self.assertEqual(recovered[0]["stage_metrics"][0]["usage_counters"], [])
        for stage in result.get("stage_metrics", []):
            for usage in stage.get("usage_counters", []):
                self.assertFalse(usage.get("usage_counter_basis") == "codex_rollout_token_count"
                    and usage.get("usage_source_integrity_verified") is True)

    def test_loader_rejects_duplicate_worker_and_mismatched_owner_cohort_before_event_reads(self):
        f = usage_fixtures.NativeUsageTests(); f.setUp()
        try:
            f._append_event("2026-10-07T09:20:00Z", f.counter(100, 80, 20, 10), type="token_count")
            payload = collect_run(f.workspace, f.RUN_ID, sessions_root=f.sessions,
                                  now=f.NOW, allow_noncurrent=True)
            local = f.workspace / "Resources/Studio/.local"
            changed = copy.deepcopy(payload)
            duplicate = copy.deepcopy(changed["worker_usage_snapshots"][0])
            duplicate.update(thread_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                             usage_thread_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd")
            changed["worker_usage_snapshots"].append(duplicate)
            mismatch = copy.deepcopy(payload)
            mismatch["owner_thread_id"] = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
            mixed = copy.deepcopy(changed)
            mixed["worker_usage_snapshots"][1].update(worker_id="hunt:other",
                dispatch_owner_relpath="hunt/other", owner_thread_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
            for unsafe in (changed, mismatch, mixed):
                target = local / "history/native-usage" / (f.RUN_ID + ".json")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(native_usage._canonical({"payload": unsafe,
                    "sha256": native_usage._sha(native_usage._canonical(unsafe))}) + b"\n")
                with patch.object(native_usage, "_verify_source_row", side_effect=AssertionError("must reject cohort first")):
                    self.assertIsNone(native_usage.load_snapshot(local, f.RUN_ID,
                        workspace=f.workspace, sessions_root=f.sessions))
        finally:
            f.tearDown()

    def test_loader_reopens_current_event_metadata_request_and_ack_bytes(self):
        f = usage_fixtures.NativeUsageTests(); f.setUp()
        try:
            offset, raw = f._append_event("2026-10-07T09:20:00Z",
                f.counter(100, 80, 20, 10), type="token_count")
            payload = collect_run(f.workspace, f.RUN_ID, sessions_root=f.sessions,
                                  now=f.NOW, allow_noncurrent=True)
            local = f.workspace / "Resources/Studio/.local"
            native_usage.write_snapshot(local, payload)
            owner_rollout = f.day / ("rollout-owner-" + f.OWNER_ID + ".jsonl")
            paths = [f.rollout, owner_rollout,
                f.codex_dir / "stage-dispatch-request.json",
                f.codex_dir / "codex-app-spawn-tool-result.json"]
            for source in paths:
                original = source.read_bytes()
                try:
                    # Whitespace alone changes the hash-bound metadata/receipt bytes.
                    source.write_bytes(b" " + original)
                    self.assertIsNone(native_usage.load_snapshot(local, f.RUN_ID,
                        workspace=f.workspace, sessions_root=f.sessions))
                finally:
                    source.write_bytes(original)
            original = f.rollout.read_bytes()
            try:
                changed = bytearray(original)
                changed[offset + raw.index(b"100")] = ord("9")
                f.rollout.write_bytes(changed)
                self.assertIsNone(native_usage.load_snapshot(local, f.RUN_ID,
                    workspace=f.workspace, sessions_root=f.sessions))
            finally:
                f.rollout.write_bytes(original)
            self.assertIsNotNone(native_usage.load_snapshot(local, f.RUN_ID,
                workspace=f.workspace, sessions_root=f.sessions))
        finally:
            f.tearDown()


if __name__ == "__main__":
    unittest.main()
