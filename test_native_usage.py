"""Isolated fixtures for bounded native worker-token collection."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import copy
import unittest

from native_usage import NativeUsageError, collect_run, load_snapshot, write_snapshot


def write_json(path: Path, value: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
    path.write_bytes(raw)
    return raw


def pointer(path: Path, raw: bytes) -> dict:
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


class NativeUsageTests(unittest.TestCase):
    RUN_ID = "2026-10-07-fixture"
    NOW = datetime(2026, 10, 7, 11, 0, tzinfo=timezone.utc)
    OWNER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    THREAD_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    OWNER_PATH = "/root/fixture-coordinator"
    TASK_NAME = "fixture-hunt-worker"
    TASK_PATH = OWNER_PATH + "/" + TASK_NAME
    WORKER_ID = "hunt:hellowork"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-usage-")
        self.base = Path(self.temp.name)
        self.workspace = self.base / "CV"
        self.workspace.mkdir()
        self.run_root = self.workspace / "Resources/Master/auto/evidence/runs" / self.RUN_ID
        self.owner_dir = self.run_root / "hunt/hellowork"
        self.codex_dir = self.owner_dir / ".codex-app"
        self.codex_dir.mkdir(parents=True)
        self.sessions = self.base / "sessions"
        self.day = self.sessions / "2026/10/07"
        self.day.mkdir(parents=True)
        self._create_dispatch_chain()
        self._create_metadata()
        self._write_metrics()

    def tearDown(self):
        self.temp.cleanup()

    def _create_dispatch_chain(self):
        names = {
            "stage-dispatch-request.json": {
                "kind": "codex_app_subagent_dispatch_request", "run_id": self.RUN_ID,
                "stage": "hunt", "worker_id": self.WORKER_ID,
                "task_name": self.TASK_NAME, "requested_task_name": self.TASK_NAME,
                "workspace": str(self.owner_dir.resolve()), "prompt": "PRIVATE PROMPT TEST STRING",
                "worker_started_at": "2026-10-07T09:00:00Z"},
            "codex-app-spawn-submission-marker.json": {
                "kind": "codex_app_spawn_submission_marker_v1", "run_id": self.RUN_ID,
                "worker_id": self.WORKER_ID, "submitted_call": "collaboration.spawn_agent",
                "clock_basis": "coordinator_observed_immediately_before_spawn_agent_call",
                "observed_at": "2026-10-07T09:00:01Z"},
            "codex-app-spawn-tool-args.json": {
                "task_name": self.TASK_NAME, "message": "PRIVATE PROMPT TEST STRING"},
            "codex-app-spawn-tool-result.json": {"task_name": self.TASK_PATH},
        }
        raw_by_name = {name: write_json(self.codex_dir / name, value)
                       for name, value in names.items()}
        request_path = self.codex_dir / "stage-dispatch-request.json"
        names["codex-app-spawn-submission-marker.json"]["dispatch_request"] = pointer(
            request_path, raw_by_name["stage-dispatch-request.json"])
        raw_by_name["codex-app-spawn-submission-marker.json"] = write_json(
            self.codex_dir / "codex-app-spawn-submission-marker.json",
            names["codex-app-spawn-submission-marker.json"])
        pointers = {
            "dispatch_request": pointer(request_path, raw_by_name["stage-dispatch-request.json"]),
            "submission_marker": pointer(self.codex_dir / "codex-app-spawn-submission-marker.json",
                                          raw_by_name["codex-app-spawn-submission-marker.json"]),
            "spawn_tool_args": pointer(self.codex_dir / "codex-app-spawn-tool-args.json",
                                        raw_by_name["codex-app-spawn-tool-args.json"]),
            "raw_ack": pointer(self.codex_dir / "codex-app-spawn-tool-result.json",
                                raw_by_name["codex-app-spawn-tool-result.json"]),
            "spawn_tool_result": pointer(self.codex_dir / "codex-app-spawn-tool-result.json",
                                          raw_by_name["codex-app-spawn-tool-result.json"]),
        }
        receipt = {"kind": "codex_app_native_agent_receipt_v1", "run_id": self.RUN_ID,
                   "stage": "hunt", "worker_id": self.WORKER_ID,
                   "task_name": self.TASK_PATH, "actual_agent_id": self.TASK_PATH,
                   "workspace": str(self.owner_dir.resolve()),
                   "prompt_submitted_at": "2026-10-07T09:00:01Z", **pointers}
        write_json(self.codex_dir / "native-agent-receipt.json", receipt)

    def _metadata(self, *, thread_id, parent_id, agent_path, depth, parent_agent_path=None):
        parent_agent_path = parent_agent_path or agent_path.rpartition("/")[0]
        return {"type": "session_meta", "timestamp": "2026-10-07T08:59:00Z", "payload": {
            "id": thread_id, "parent_thread_id": parent_id,
            "cwd": str(self.workspace.resolve()), "agent_path": agent_path,
            "source": {"subagent": {"thread_spawn": {
                "parent_thread_id": parent_id, "agent_path": agent_path, "depth": depth}}}}}

    def _create_metadata(self):
        owner = self._metadata(thread_id=self.OWNER_ID,
            parent_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc", agent_path=self.OWNER_PATH, depth=1)
        worker = self._metadata(thread_id=self.THREAD_ID, parent_id=self.OWNER_ID,
            agent_path=self.TASK_PATH, depth=2)
        write_json(self.day / f"rollout-owner-{self.OWNER_ID}.jsonl", owner)
        self.rollout = self.day / f"rollout-worker-{self.THREAD_ID}.jsonl"
        write_json(self.rollout, worker)

    def _write_metrics(self, *, finish="2026-10-07T10:00:00Z", batch_finish=None):
        write_json(self.run_root / "stage-metrics.json", {"kind": "scheduled_run_metrics_v1",
            "finished_at": batch_finish,
            "records": [{"stage": "hunt", "worker_id": self.WORKER_ID,
                         "started_at": "2026-10-07T09:00:00Z", "finished_at": finish}]})

    @staticmethod
    def counter(inputs, cached, outputs, reasoning):
        return {"input_tokens": inputs, "cached_input_tokens": cached,
                "cache_write_input_tokens": 0, "output_tokens": outputs,
                "reasoning_output_tokens": reasoning, "total_tokens": inputs + outputs}

    def _append_event(self, at, counts, **extra):
        value = {"type": "event_msg", "timestamp": at,
                 "payload": {"type": "token_count", "info": {"total_token_usage": counts}, **extra}}
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
        with self.rollout.open("ab") as stream:
            offset = stream.tell()
            stream.write(raw)
        return offset, raw

    def test_collects_latest_scoped_counter_once_and_writes_private_sidecar(self):
        self._append_event("2026-10-07T09:10:00Z", self.counter(100, 80, 20, 10))
        latest_offset, latest_raw = self._append_event(
            "2026-10-07T09:20:00Z", self.counter(200, 170, 30, 15),
            text="PRIVATE SOURCE BODY TEST STRING")
        payload = collect_run(self.workspace, self.RUN_ID, sessions_root=self.sessions,
                              now=self.NOW, allow_noncurrent=True)
        self.assertEqual(payload["coverage"]["status"], "partial")
        self.assertEqual(payload["coverage"]["recorded_worker_threads"], 1)
        self.assertIsNone(payload["whole_task_exact_tokens"])
        self.assertEqual(len(payload["worker_usage_snapshots"]), 1)
        row = payload["worker_usage_snapshots"][0]
        self.assertEqual(row["recorded_tokens"]["total_tokens"], 230)
        self.assertEqual(row["source_event_offset"], latest_offset)
        self.assertEqual(row["source_event_length"], len(latest_raw))
        self.assertEqual(row["source_event_sha256"], hashlib.sha256(latest_raw).hexdigest())
        self.assertTrue(row["cache_included"])
        local = self.workspace / "Resources/Studio/.local"
        sidecar = write_snapshot(local, payload)
        self.assertEqual(sidecar.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sidecar.parent.stat().st_mode & 0o777, 0o700)
        contents = sidecar.read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE PROMPT TEST STRING", contents)
        self.assertNotIn("PRIVATE SOURCE BODY TEST STRING", contents)
        self.assertNotIn("rollout_path", contents)
        self.assertEqual(len(load_snapshot(local, self.RUN_ID, workspace=self.workspace,
                                           sessions_root=self.sessions)), 1)
        self.assertEqual(load_snapshot(local, self.RUN_ID, workspace=self.workspace,
            sessions_root=self.sessions)[0]["recorded_tokens"]["total_tokens"], 230)
        duplicate = copy.deepcopy(payload)
        duplicate["worker_usage_snapshots"].append(dict(duplicate["worker_usage_snapshots"][0]))
        with self.assertRaises(NativeUsageError):
            write_snapshot(local, duplicate)
        mixed_owner = copy.deepcopy(payload)
        mixed = dict(mixed_owner["worker_usage_snapshots"][0])
        mixed["usage_thread_id"] = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        mixed["thread_id"] = mixed["usage_thread_id"]
        mixed["owner_thread_id"] = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        mixed["owner_agent_path"] = "/root/other-coordinator"
        mixed_owner["worker_usage_snapshots"].append(mixed)
        with self.assertRaises(NativeUsageError):
            write_snapshot(local, mixed_owner)

    def test_optional_cache_write_counter_may_be_absent(self):
        counts = self.counter(100, 80, 20, 10)
        del counts["cache_write_input_tokens"]
        self._append_event("2026-10-07T09:10:00Z", counts)
        payload = collect_run(self.workspace, self.RUN_ID, sessions_root=self.sessions,
                              now=self.NOW, allow_noncurrent=True)
        self.assertEqual(payload["worker_usage_snapshots"][0]["recorded_tokens"], counts)

    def test_late_cleanup_counter_is_kept_until_actual_completion_not_dispatch_deadline(self):
        self._write_metrics(finish="2026-10-07T10:00:00Z", batch_finish="2026-10-07T10:04:30Z")
        self._append_event("2026-10-07T09:20:00Z", self.counter(100, 90, 10, 5))
        offset, raw = self._append_event("2026-10-07T10:04:11Z", self.counter(300, 280, 40, 20))
        result = collect_run(self.workspace, self.RUN_ID, sessions_root=self.sessions,
                             now=self.NOW, allow_noncurrent=True)
        row = result["worker_usage_snapshots"][0]
        self.assertEqual(row["source_event_offset"], offset)
        self.assertEqual(row["source_event_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(row["recorded_tokens"]["total_tokens"], 340)
        local = self.workspace / "Resources/Studio/.local"
        write_snapshot(local, result)
        self.assertEqual(load_snapshot(local, self.RUN_ID, workspace=self.workspace,
            sessions_root=self.sessions)[0]["recorded_tokens"]["total_tokens"], 340)

    def test_ack_hash_mismatch_fails_closed(self):
        ack = self.codex_dir / "codex-app-spawn-tool-result.json"
        ack.write_text('{"task_name":"/root/coordinator/other"}\n', encoding="utf-8")
        with self.assertRaises(NativeUsageError):
            collect_run(self.workspace, self.RUN_ID, sessions_root=self.sessions,
                        now=self.NOW, allow_noncurrent=True)

    def test_last_token_only_and_bad_counters_remain_unknown(self):
        self._append_event("2026-10-07T09:10:00Z", {}, last_token_usage=self.counter(200, 100, 20, 10))
        result = collect_run(self.workspace, self.RUN_ID, sessions_root=self.sessions,
                             now=self.NOW, allow_noncurrent=True)
        self.assertEqual(result["worker_usage_snapshots"], [])
        self.assertEqual(result["coverage"]["status"], "unavailable")

    def test_event_after_actual_finish_is_not_counted(self):
        self._write_metrics(finish="2026-10-07T10:00:00Z", batch_finish="2026-10-07T10:00:00Z")
        self._append_event("2026-10-07T10:00:01Z", self.counter(200, 180, 20, 10))
        result = collect_run(self.workspace, self.RUN_ID, sessions_root=self.sessions,
                             now=self.NOW, allow_noncurrent=True)
        self.assertEqual(result["worker_usage_snapshots"], [])

    def test_run_date_must_be_current_for_real_collection(self):
        with self.assertRaises(NativeUsageError):
            collect_run(self.workspace, self.RUN_ID, sessions_root=self.sessions,
                        now=datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc))

    def test_tampered_sidecar_hash_is_rejected(self):
        self._append_event("2026-10-07T09:10:00Z", self.counter(100, 80, 20, 10))
        payload = collect_run(self.workspace, self.RUN_ID, sessions_root=self.sessions,
                              now=self.NOW, allow_noncurrent=True)
        local = self.workspace / "Resources/Studio/.local"
        sidecar = write_snapshot(local, payload)
        wrapper = json.loads(sidecar.read_text(encoding="utf-8"))
        wrapper["payload"]["worker_usage_snapshots"][0]["recorded_tokens"]["total_tokens"] = 999
        sidecar.write_text(json.dumps(wrapper), encoding="utf-8")
        self.assertIsNone(load_snapshot(local, self.RUN_ID, workspace=self.workspace,
                                        sessions_root=self.sessions))


if __name__ == "__main__":
    unittest.main()
