"""Isolated run-history fixtures. No models, inboxes, trackers or live jobs."""
from __future__ import annotations

import json
import hashlib
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data import StudioData, StudioDataError
from history import RunHistory, _usage
from native_usage import _identity, write_snapshot


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="studio-history-fixture-")
        self.root = Path(self.temp.name) / "CV"
        self.resources = self.root / "Resources"
        self.resources.mkdir(parents=True)
        self.data = StudioData(self.root)
        self.history = RunHistory(self.data)
        self.batch = self.resources / "Master/auto/evidence/runs/2020-02-01-weekday"
        write(self.batch / "batch-manifest.json", {
            "batch_id": self.batch.name, "policy_version": 3,
            "batch_started_at": "2020-02-01T09:00:00.123456+00:00"})

    def tearDown(self):
        self.temp.cleanup()

    def run_detail(self):
        return self.history.run(self.batch.name)

    def startup_failure(self):
        (self.batch / "batch-manifest.json").unlink()
        manifest = {"kind": "fresh_pipeline_launch", "run_id": self.batch.name,
                    "status": "PENDING_SUBMISSION", "policy_version": 4,
                    "started_at": "2020-02-01T09:00:00+00:00",
                    "first_submission_at": None, "supervisor_pid": None}
        write(self.batch / "launch-receipt.json", manifest)
        write(self.batch / "stage-metrics.json", {"kind": "scheduled_run_metrics_v1",
              "policy_version": 4, "status": "running", "started_at": manifest["started_at"],
              "records": [{"stage": stage, "worker_id": stage, "status": "not_run",
                           "started_at": None, "finished_at": None}
                          for stage in ("inbox", "hunt", "hunt_audit", "build", "build_audit", "sol_review", "sync")]})
        failure = {"status": "HELD_PRE_DISPATCH", "recorded_at": "2020-02-01T09:00:56+00:00",
                   "reason": "runtime_symlink:Master/auto/local/pipeline_runtime.py",
                   "workers_dispatched": 0, "downstream_status": "NOT_RUN"}
        write(self.batch / "launch-failure.json", failure)
        return manifest, failure

    def test_pre_dispatch_failure_supersedes_pending_without_rewriting_receipts(self):
        self.startup_failure()
        originals = {path: path.read_bytes() for path in self.batch.iterdir() if path.is_file()}
        result = self.run_detail()
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["raw_status"], "HELD_PRE_DISPATCH")
        self.assertEqual(result["duration_seconds"], 56)
        self.assertTrue(all(stage["status"] == "not_run" for stage in result["stage_metrics"]))
        self.assertTrue(all(stage["started_at"] is None for stage in result["stage_metrics"]))
        self.assertEqual(result["summary_metrics"]["failure_count"], 1)
        self.assertEqual(result["failures"][0]["class"], "launch_failed")
        self.assertEqual(result["failures"][0]["source"], "launch-failure.json")
        self.assertEqual(result["usage"]["status"], "unavailable")
        self.assertIn("launch-failure.json", [row["name"] for row in result["files"]])
        self.assertEqual(originals, {path: path.read_bytes() for path in originals})

    def test_stale_startup_failure_cannot_override_submission_or_real_terminal(self):
        manifest, failure = self.startup_failure()
        for field, value in (("first_submission_at", "2020-02-01T09:00:01+00:00"), ("supervisor_pid", 123)):
            with self.subTest(field=field):
                write(self.batch / "launch-receipt.json", {**manifest, field: value})
                result = self.run_detail()
                self.assertNotEqual(result["raw_status"], "HELD_PRE_DISPATCH")
                self.assertFalse(any(row["class"] == "launch_failed" for row in result["failures"]))
        write(self.batch / "launch-receipt.json", manifest)
        write(self.batch / "terminal.json", {"status": "COMPLETED_UNAPPROVED", "finished_at": "2020-02-01T09:10:00+00:00"})
        self.assertEqual(self.run_detail()["raw_status"], "COMPLETED_UNAPPROVED")

    def test_closed_startup_metrics_record_one_failure_and_no_started_stages(self):
        manifest, failure = self.startup_failure()
        manifest["status"] = "PRE_DISPATCH_FAILED"
        failure["status"] = "PRE_DISPATCH_FAILED"
        failure["launcher_exit_code"] = 2
        write(self.batch / "launch-receipt.json", manifest)
        write(self.batch / "launch-failure.json", failure)
        metrics = json.loads((self.batch / "stage-metrics.json").read_text())
        metrics.update(status="failed", finished_at=failure["recorded_at"])
        write(self.batch / "stage-metrics.json", metrics)
        result = self.run_detail()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["summary_metrics"]["failure_count"], 1)
        self.assertEqual(result["failures"][0]["exit_code"], 2)
        self.assertTrue(all(stage["status"] == "not_run" for stage in result["stage_metrics"]))

    def test_invalid_startup_failure_observations_do_not_close_pending_run(self):
        manifest, failure = self.startup_failure()
        for patch_values in ({"workers_dispatched": True}, {"workers_dispatched": 1},
                             {"recorded_at": "2019-01-01T00:00:00+00:00"}, {"downstream_status": "DONE"}):
            with self.subTest(patch_values=patch_values):
                write(self.batch / "launch-failure.json", {**failure, **patch_values})
                self.assertNotEqual(self.run_detail()["raw_status"], "HELD_PRE_DISPATCH")
        write(self.batch / "launch-failure.json", failure)
        metrics = json.loads((self.batch / "stage-metrics.json").read_text())
        for row in ({"status": "running", "started_at": manifest["started_at"]}, {"status": "done"}, "invalid"):
            with self.subTest(metric_row=row):
                write(self.batch / "stage-metrics.json", {**metrics, "records": [row]})
                self.assertNotEqual(self.run_detail()["raw_status"], "HELD_PRE_DISPATCH")

    def test_seven_steps_missing_values_are_not_zeros(self):
        result = self.run_detail()
        self.assertEqual([s["id"] for s in result["stage_metrics"]],
                         ["inbox", "hunt", "hunt_audit", "build", "build_audit", "sol_review", "sync"])
        self.assertTrue(all(s["status"] == "not_recorded" for s in result["stage_metrics"]))
        self.assertTrue(all(s["model"] is None for s in result["stage_metrics"]))
        self.assertIsNone(result["summary_metrics"]["cvs_built"])
        self.assertIsNone(result["duration_seconds"])
        write(self.batch / "stage-outcomes.json", {"hunt": "NOT_RUN", "hunt_audit": "NOT_RUN", "counts": {"full_ads": 0}})
        result = self.run_detail()
        self.assertEqual(result["summary_metrics"]["ads_reviewed"], 0)
        self.assertIsNone(result["summary_metrics"]["jobs_found"])
        self.assertEqual(result["summary_metrics"]["jobs_shortlisted"], 0)
        self.assertEqual(result["stage_metrics"][1]["status"], "not_run")
        self.assertEqual(result["snapshot_status"], "saved")
        self.assertTrue((self.resources / "Studio/.local/history/runs").is_dir())

    def test_verified_full_read_survives_only_downstream_quote_packaging_failure(self):
        lane = self.batch / "hunt" / "hellowork"
        capture_id = "fixture-full-read-001"
        url = "https://jobs.example.test/role"
        captured_at = "2020-02-01T09:10:00+00:00"
        body = ("Type de contrat : CDI. CDI à temps plein en France. "
                "Vos missions : Consolider les données avec les équipes. "
                "Le profil recherché : Anglais professionnel demandé.")
        posting = {"@context": "https://schema.org", "@type": "JobPosting",
                   "url": url, "title": "Chargé de projet RSE",
                   "employmentType": "FULL_TIME", "description": body}
        raw = ('<html><head><link rel="canonical" href="' + url + '">'
               '<script type="application/ld+json">' + json.dumps(posting, ensure_ascii=False) +
               '</script></head><body><h1>Chargé de projet RSE</h1><section class="job-description">' +
               body + '</section><a href="/apply">Postuler</a></body></html>').encode()
        body_bytes = body.encode()
        raw_hash, body_hash = hashlib.sha256(raw).hexdigest(), hashlib.sha256(body_bytes).hexdigest()
        capture = {"capture_id": capture_id, "site": "hellowork", "status": "captured_full_detail",
                   "canonical_url": url, "captured_at_utc": captured_at, "raw_sha256": raw_hash,
                   "body_sha256": body_hash, "description_source": "jobposting_description",
                   "profile_sha256": "a" * 64, "rules_sha256": "b" * 64}
        ad = {"capture_id": capture_id, "canonical_url": url, "title": "Chargé de projet RSE",
              "company": "Example", "contract": "CDI", "location": "Paris",
              "description": body, "description_source": "jobposting_description",
              "literal_duties_quotes": ["Not a literal body quote"],
              "literal_mandatory_requirement_quotes": ["Also not literal"],
              "positive_active_apply_quote": "Postuler",
              "capture": {"kind": "verbatim_full_detail", "captured_at": captured_at,
                          "raw_sha256": raw_hash}}
        fields = {key: " ".join(str(ad[key]).split()) for key in ("title", "company", "contract", "location")}
        fields["body"] = " ".join(body.split())
        fingerprint = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                                ensure_ascii=False).encode()).hexdigest()
        row_paths = {"raw_html": f"captures/{capture_id}/raw.html",
                     "body_text": f"captures/{capture_id}/body.txt",
                     "ad_json": f"captures/{capture_id}/ad.json",
                     "capture_json": f"captures/{capture_id}/capture.json"}
        row = {"capture_id": capture_id, "site": "hellowork", "capture_kind": "verbatim_full_detail",
               "canonical_url": url, "captured_at_utc": captured_at, "raw_sha256": raw_hash,
               "body_sha256": body_hash, "paths": row_paths}
        write(lane / "full_ads.json", {"ads": [row]})
        directory = lane / "captures" / capture_id
        directory.mkdir(parents=True)
        (directory / "raw.html").write_bytes(raw)
        (directory / "body.txt").write_bytes(body_bytes)
        write(directory / "ad.json", ad)
        write(directory / "capture.json", capture)
        write(directory / "screening.json", {
            "capture_id": capture_id, "canonical_url": url,
            "checked_at_utc": "2020-02-01T09:11:00+00:00",
            "profile_sha256": capture["profile_sha256"], "rules_sha256": capture["rules_sha256"],
            "body_fingerprint": fingerprint,
            "decision": {"verdict": "reviewed", "quotes": ["Anglais professionnel demandé."]}})

        result = self.history.verified_read_events(self.batch)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["captured_at"], captured_at)
        self.assertFalse(result["partial"])
        self.assertEqual(result["partial_dates"], [])

        # The parsed proof is reusable, but a changed advert must be rechecked.
        import full_ad_evidence
        with patch.object(full_ad_evidence, "inspect_full_ad_evidence",
                          wraps=full_ad_evidence.inspect_full_ad_evidence) as inspect:
            self.assertEqual(self.history.verified_read_events(self.batch)["events"], result["events"])
            self.assertEqual(inspect.call_count, 0)
            changed_ad = dict(ad, positive_active_apply_quote="Not an Apply button")
            write(directory / "ad.json", changed_ad)
            changed = self.history.verified_read_events(self.batch)
            self.assertEqual(inspect.call_count, 1)
            self.assertEqual(changed["events"], [])
            write(directory / "ad.json", ad)

        screen_path = directory / "screening.json"
        screen = json.loads(screen_path.read_text())
        capture_path = directory / "capture.json"
        capture = json.loads(capture_path.read_text())
        capture.pop("profile_sha256")
        capture.pop("rules_sha256")
        screen.pop("profile_sha256")
        screen.pop("rules_sha256")
        write(capture_path, capture)
        write(screen_path, screen)
        missing_bindings = self.history.verified_read_events(self.batch)
        self.assertEqual(missing_bindings["events"], [])
        self.assertTrue(missing_bindings["partial"])

        capture["profile_sha256"], capture["rules_sha256"] = "a" * 64, "b" * 64
        screen["profile_sha256"], screen["rules_sha256"] = "a" * 64, "b" * 64
        write(capture_path, capture)
        write(screen_path, screen)
        screen = json.loads((directory / "screening.json").read_text())
        screen["body_fingerprint"] = "0" * 64
        write(screen_path, screen)
        rejected = self.history.verified_read_events(self.batch)
        self.assertEqual(rejected["events"], [])
        self.assertTrue(rejected["partial"])

    def test_actual_model_is_separate_from_frozen_assignment(self):
        contract = {"kind": "weekday_worker_contract", "stage": "hunt", "worker_id": "hunt:hellowork",
                    "contract_id": "abc123", "model": "gemini-3.8-flash-high", "reasoning_effort": "high",
                    "worker_started_at": "2020-02-01T09:01:00+00:00", "immutable": True}
        write(self.batch / "hellowork/contract.json", contract)
        stage = self.run_detail()["stage_metrics"][1]
        self.assertEqual(stage["model_source"], "configured")
        self.assertIsNone(stage["started_at"])
        write(self.batch / "hellowork/weekday_engine_terminal.json", {
            "kind": "weekday_worker_terminal", "stage": "hunt", "worker_id": "hunt:hellowork",
            "contract_id": "abc123", "status": "DONE", "model": "gpt-6-luna", "reasoning_effort": "max",
            "started_at": "2020-02-01T09:01:00+00:00", "finished_at": "2020-02-01T09:21:00+00:00"})
        write(self.batch / "hellowork/site_summary.json", {"site": "hellowork", "worker_id": "hunt:hellowork",
            "contract_id": "abc123", "status": "DONE", "unique_full_ad_count": 20, "shortlisted": 2})
        stage = self.run_detail()["stage_metrics"][1]
        self.assertEqual((stage["model"], stage["effort"], stage["model_source"]), ("gpt-6-luna", "max", "recorded"))
        self.assertEqual((stage["ads_reviewed"], stage["jobs_found"]), (20, 2))
        self.assertTrue(any(not m["confirmed"] for m in stage["models"]))

    def test_latest_hunt_rejection_overrides_a_prior_completion_label(self):
        jobs = [{"role_id": "public:fixture", "status": "complete", "cv_built": True,
                 "continuation_id": "fixture-continuation"}]
        milestones = [{"kind": "role_milestone_receipt", "stage": "hunt_audit", "worker_id": "audit",
                       "role_id": "public:fixture", "role": "Fixture Company — ESG Analyst",
                       "at": "2026-10-03T10:00:00+00:00", "status": "rejected",
                       "evidence_sha256": "a" * 64, "current": True}]
        result = RunHistory._apply_role_milestones(jobs, milestones)
        self.assertEqual(result[0]["status"], "hunt_audit_rejected")
        self.assertEqual(result[0]["previous_outcome"], {
            "status": "complete", "cv_built": True, "continuation_id": "fixture-continuation"})

    def test_cv_clock_stops_at_artifact_and_preserves_repairs(self):
        role = "Fixture Company - ESG analyst"
        start = "2020-02-01T09:10:00.123456+00:00"
        write(self.batch / "build-one.contract.json", {"kind": "weekday_worker_contract", "stage": "build",
            "worker_id": "build:fixture", "contract_id": "build123", "model": "gpt-6-luna",
            "worker_started_at": start, "assigned_role_paths": [{"path": "/old/workspace/Tailored CVs/" + role}]})
        write(self.batch / "build-one/weekday_engine_terminal.json", {"kind": "weekday_worker_terminal",
            "stage": "build", "worker_id": "build:fixture", "contract_id": "build123", "status": "DONE",
            "model": "gpt-6-luna", "reasoning_effort": "max", "started_at": "2020-02-01T09:10:05+00:00",
            "resumed_at": "2020-02-01T09:12:00+00:00", "finished_at": "2020-02-01T09:15:00+00:00"})
        rows = [{"event": "run_started", "started_at": start, "model": "gpt-6-luna"},
                {"event": "role_intake", "role": role, "company": "Fixture Company", "job_title": "ESG analyst"},
                {"event": "artifact_recorded", "role": role, "at": "2020-02-01T09:13:00.987654+00:00", "attempts": 2},
                {"event": "verified", "completion_stage": "artifact", "counts": {"cvs_built": 1}},
                {"event": "sync_approval", "at": "2020-02-01T09:40:00+00:00"},
                {"event": "surface_google_sheets", "role": role, "at": "2020-02-01T09:45:00+00:00", "status": "done"}]
        ledger = self.batch / "build-ledgers/fixture.ledger.json"
        ledger.parent.mkdir()
        ledger.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        repair = [{"event": "run_started", "started_at": "2020-02-01T09:20:00+00:00", "model": "gpt-6-luna"},
                  {"event": "role_intake", "role": role},
                  {"event": "artifact_recorded", "role": role, "at": "2020-02-01T09:21:00+00:00", "attempts": 1}]
        (ledger.parent / "fixture.recovery1.ledger.json").write_text("\n".join(json.dumps(r) for r in repair))
        result = self.run_detail()
        self.assertEqual(result["summary_metrics"]["cvs_built"], 1)
        build = result["builds"][0]
        self.assertEqual(build["started_at"], start)
        self.assertAlmostEqual(build["duration_seconds"], 180.864198, places=6)
        self.assertEqual(build["repair_count"], 1)
        self.assertEqual(build["attempts"][1]["duration_seconds"], 60)
        self.assertEqual(result["stage_metrics"][3]["attempts"][0]["started_at"], start)

    def test_generic_worker_finish_does_not_prove_cv_completion(self):
        write(self.batch / "build-one/terminal.json", {"stage": "build", "worker_id": "build:Fixture role",
            "assigned_role_paths": ["/old/Tailored CVs/Fixture role"], "status": "DONE", "model": "gpt-6-luna",
            "started_at": "2020-02-01T09:01:00+00:00", "finished_at": "2020-02-01T09:04:00+00:00"})
        result = self.run_detail()
        self.assertIsNone(result["summary_metrics"]["cvs_built"])
        self.assertIsNone(result["builds"][0]["duration_seconds"])
        self.assertIsNone(result["builds"][0]["completed_at"])

    def test_provider_or_command_completion_is_not_stage_approval(self):
        write(self.batch / "hunt_audit/terminal.json", {"kind": "weekday_stage_runtime_receipt",
            "stage": "hunt_audit", "worker_id": "hunt_audit", "status": "DONE", "evidence_only": True,
            "started_at": "2020-02-01T09:20:00+00:00", "finished_at": "2020-02-01T09:21:00+00:00"})
        result = self.run_detail()
        self.assertEqual(result["stage_metrics"][2]["status"], "partial")
        self.assertNotEqual(result["status"], "done")

    def test_saved_running_elapsed_is_not_completed_duration(self):
        write(self.batch / "hunt_audit/terminal.json", {"stage": "hunt_audit", "worker_id": "hunt_audit",
            "status": "RUNNING", "started_at": "2020-02-01T09:20:00+00:00",
            "observed_at": "2020-02-01T09:22:00+00:00"})
        stage = self.run_detail()["stage_metrics"][2]
        self.assertIsNone(stage["duration_seconds"])
        self.assertEqual(stage["attempts"][0]["elapsed_seconds"], 120)
        self.assertEqual(stage["attempts"][0]["timing_status"], "saved_elapsed")

    def test_account_usage_zero_is_approximate_and_reset_not_cost(self):
        write(self.batch / "terminal.json", {"status": "HELD", "finished_at": "2020-02-01T09:30:00.123456+00:00",
            "counts": {"cvs_built": 0}, "usage": {"window": "codex:10080:12345",
                "start_used_percent": 10, "end_used_percent": 10, "shared_rounded_meter": True}})
        result = self.run_detail()
        self.assertEqual(result["usage"]["account_delta_pp"], 0)
        self.assertTrue(result["usage"]["approximate"])
        self.assertIsNone(result["usage"]["exact_tokens"])
        self.assertIn("Zero change does not mean", result["usage"]["note"])
        self.assertEqual(result["duration_seconds"], 1800)
        before = {"rateLimits": {"primary": {"usedPercent": 50, "windowDurationMins": 10080, "resetsAt": 12345}}}
        after = {"rateLimits": {"primary": {"usedPercent": 1, "windowDurationMins": 10080, "resetsAt": 54321}}}
        usage = _usage({"usage_before": before, "usage_after": after})
        self.assertEqual(usage["status"], "reset")
        self.assertIsNone(usage["account_delta_pp"])
        cumulative = _usage({"token_usage": {"total": {"inputTokens": 200, "outputTokens": 20, "totalTokens": 220}}})
        self.assertIsNone(cumulative["exact_tokens"])
        self.assertEqual(cumulative["recorded_tokens"]["total_tokens"], 220)
        self.assertFalse(cumulative["usage_binding_verified"])

    def test_bound_worker_thread_usage_is_verified_and_cumulative_only_once(self):
        worker_usage = {
            "usage_scope": "worker_thread_cumulative",
            "usage_counter_basis": "codex_appserver_thread_token_usage",
            "usage_thread_id": "thread-a",
            "usage_observed_at": "2026-10-05T12:00:00Z",
            "thread_cumulative_token_usage_at_observation": {
                "total_tokens": 120, "input_tokens": 90, "output_tokens": 30},
        }
        verified = _usage({"thread_id": "thread-a", "usage": worker_usage})
        self.assertTrue(verified["usage_binding_verified"])
        self.assertEqual(verified["usage_scope"], "worker_thread_cumulative")
        self.assertEqual(verified["usage_thread_id"], "thread-a")
        self.assertEqual(verified["recorded_tokens"]["total_tokens"], 120)
        self.assertEqual(verified["token_scope"], "thread_cumulative")
        self.assertIsNone(verified["exact_tokens"])

        mismatch = _usage({"thread_id": "other-thread", "usage": worker_usage})
        self.assertFalse(mismatch["usage_binding_verified"])
        self.assertIsNone(mismatch["usage_scope"])
        self.assertEqual(mismatch["token_scope"], "thread_cumulative")
        no_timestamp = _usage({"thread_id": "thread-a", "usage": {**worker_usage, "usage_observed_at": None}})
        self.assertFalse(no_timestamp["usage_binding_verified"])
        self.assertIsNone(no_timestamp["usage_scope"])

    def test_run_metrics_keeps_unique_bound_worker_counters_on_light_history(self):
        def snapshot(thread_id, total, observed_at):
            return {"stage": "hunt", "thread_id": thread_id, "usage_thread_id": thread_id,
                    "usage_scope": "worker_thread_cumulative",
                    "usage_counter_basis": "codex_appserver_thread_token_usage",
                    "usage_observed_at": observed_at,
                    "thread_cumulative_token_usage_at_observation": {"total_tokens": total}}

        records = [
            {"kind": "stage_owner_receipt", "stage": "hunt", "lane_id": "hellowork",
             "status": "done", **snapshot("worker-a", 100, "2026-10-05T12:00:00Z")},
            {"kind": "stage_owner_receipt", "stage": "hunt", "lane_id": "hellowork",
             "status": "done", **snapshot("worker-a", 120, "2026-10-05T12:02:00Z")},
            {"kind": "stage_owner_receipt", "stage": "hunt", "lane_id": "hellowork",
             "status": "done", **snapshot("worker-a", 99, "2026-10-05T12:05:00Z")},
            {"kind": "stage_owner_receipt", "stage": "hunt", "lane_id": "linkedin",
             "status": "done", **snapshot("worker-b", 50, "2026-10-05T12:03:00Z")},
        ]
        write(self.batch / "stage-metrics.json", {
            "kind": "scheduled_run_metrics_v1", "policy_version": 4,
            "records": records,
            "worker_usage_snapshots": [snapshot("worker-c", 25, "2026-10-05T12:04:00Z")],
        })
        run = self.history.runs()["items"][0]
        hunt = next(stage for stage in run["stage_metrics"] if stage["id"] == "hunt")
        by_thread = {row["usage_thread_id"]: row["recorded_tokens"]["total_tokens"]
                     for row in hunt["usage_counters"]}
        self.assertEqual(by_thread, {"worker-a": 120, "worker-b": 50, "worker-c": 25})
        saved = self.history._snapshot_projection(run)
        saved_hunt = next(stage for stage in saved["stage_metrics"] if stage["id"] == "hunt")
        self.assertEqual({row["usage_thread_id"] for row in saved_hunt["usage_counters"]},
                         {"worker-a", "worker-b", "worker-c"})

    def test_native_rollout_sidecar_merges_in_memory_and_deduplicates_by_thread(self):
        thread_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        owner_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        old_appserver = {
            "stage": "hunt", "thread_id": thread_id, "usage_thread_id": thread_id,
            "usage_scope": "worker_thread_cumulative",
            "usage_counter_basis": "codex_appserver_thread_token_usage",
            "usage_observed_at": "2020-02-01T09:03:00Z",
            "thread_cumulative_token_usage_at_observation": {
                "total_tokens": 110, "input_tokens": 90, "output_tokens": 20},
        }
        write(self.batch / "stage-metrics.json", {
            "kind": "scheduled_run_metrics_v1", "policy_version": 4,
            "records": [{"stage": "hunt", "worker_id": "hunt:hellowork",
                "started_at": "2020-02-01T09:00:00Z", "finished_at": "2020-02-01T09:06:00Z"}],
            "worker_usage_snapshots": [old_appserver],
        })
        metrics_path = self.batch / "stage-metrics.json"
        original_metrics = metrics_path.read_bytes()
        proof_hash = hashlib.sha256(b"fixture-proof").hexdigest()
        task_name = "/root/fixture-coordinator/fixture-hunt-worker"
        owner_dir = self.batch / "hunt/hellowork"
        codex_dir = owner_dir / ".codex-app"
        request_path = codex_dir / "stage-dispatch-request.json"
        def write_raw(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
            path.write_bytes(raw)
            return raw
        request = {"kind": "codex_app_subagent_dispatch_request", "run_id": self.batch.name,
            "stage": "hunt", "worker_id": "hunt:hellowork", "task_name": "fixture-hunt-worker",
            "requested_task_name": "fixture-hunt-worker", "workspace": str(owner_dir.resolve()),
            "worker_started_at": "2020-02-01T09:00:00Z"}
        request_raw = write_raw(request_path, request)
        request_pointer = {"path": str(request_path), "sha256": hashlib.sha256(request_raw).hexdigest()}
        marker_path = codex_dir / "codex-app-spawn-submission-marker.json"
        marker = {"kind": "codex_app_spawn_submission_marker_v1", "run_id": self.batch.name,
            "worker_id": "hunt:hellowork", "submitted_call": "collaboration.spawn_agent",
            "observed_at": "2020-02-01T09:00:01Z", "dispatch_request": request_pointer}
        marker_raw = write_raw(marker_path, marker)
        args_path = codex_dir / "codex-app-spawn-tool-args.json"
        args_raw = write_raw(args_path, {"task_name": "fixture-hunt-worker"})
        ack_path = codex_dir / "codex-app-spawn-tool-result.json"
        ack_raw = write_raw(ack_path, {"task_name": task_name})
        ptr = lambda path, raw: {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}
        write_raw(codex_dir / "native-agent-receipt.json", {
            "kind": "codex_app_native_agent_receipt_v1", "run_id": self.batch.name,
            "stage": "hunt", "worker_id": "hunt:hellowork", "task_name": task_name,
            "actual_agent_id": task_name, "workspace": str(owner_dir.resolve()),
            "prompt_submitted_at": marker["observed_at"],
            "dispatch_request": request_pointer,
            "submission_marker": ptr(marker_path, marker_raw),
            "spawn_tool_args": ptr(args_path, args_raw), "raw_ack": ptr(ack_path, ack_raw),
            "spawn_tool_result": ptr(ack_path, ack_raw),
        })

        sessions = self.root / "session-fixture"
        session_day = sessions / "2020/02/01"
        session_day.mkdir(parents=True)
        def metadata(thread, parent, agent_path, depth):
            return {"type": "session_meta", "payload": {"id": thread,
                "parent_thread_id": parent, "cwd": str(self.root.resolve()),
                "agent_path": agent_path, "source": {"subagent": {"thread_spawn": {
                    "parent_thread_id": parent, "agent_path": agent_path, "depth": depth}}}}}
        owner_meta = metadata(owner_id, "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                              "/root/fixture-coordinator", 1)
        owner_meta_path = session_day / f"rollout-fixture-{owner_id}.jsonl"
        owner_meta_raw = write_raw(owner_meta_path, owner_meta)
        worker_meta = metadata(thread_id, owner_id, task_name, 2)
        worker_meta_path = session_day / f"rollout-fixture-{thread_id}.jsonl"
        worker_meta_raw = write_raw(worker_meta_path, worker_meta)
        worker_identity = _identity(json.loads(worker_meta_raw))[0]
        owner_identity = _identity(json.loads(owner_meta_raw), require_spawn=False)[0]
        token_event = {"type": "event_msg", "timestamp": "2020-02-01T09:05:00Z",
            "payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": 100, "cached_input_tokens": 80, "cache_write_input_tokens": 0,
                "output_tokens": 25, "reasoning_output_tokens": 10, "total_tokens": 125}}}}
        event_raw = json.dumps(token_event, separators=(",", ":")).encode("utf-8") + b"\n"
        event_offset = len(worker_meta_raw)
        with worker_meta_path.open("ab") as stream:
            stream.write(event_raw)
        self.data = StudioData(self.root, sessions_dir=sessions)
        self.history = RunHistory(self.data)
        row = {
            "stage": "hunt", "worker_id": "hunt:hellowork", "thread_id": thread_id,
            "usage_thread_id": thread_id, "usage_scope": "worker_thread_cumulative",
            "usage_counter_basis": "codex_rollout_token_count", "token_scope": "thread_cumulative",
            "recorded_tokens": {"input_tokens": 100, "cached_input_tokens": 80,
                "cache_write_input_tokens": 0, "output_tokens": 25,
                "reasoning_output_tokens": 10, "total_tokens": 125},
            "usage_observed_at": "2020-02-01T09:05:00Z", "usage_binding_verified": True,
            "usage_source_integrity_verified": True, "cache_included": True,
            "whole_task_exact_tokens": None,
            "source_metadata_line_sha256": hashlib.sha256(worker_meta_raw).hexdigest(),
            "source_identity_sha256": _identity(json.loads(worker_meta_raw))[1],
            "owner_metadata_line_sha256": hashlib.sha256(owner_meta_raw).hexdigest(),
            "owner_identity_sha256": _identity(json.loads(owner_meta_raw), require_spawn=False)[1],
            "source_event_offset": event_offset, "source_event_length": len(event_raw),
            "source_event_sha256": hashlib.sha256(event_raw).hexdigest(),
            "dispatch_request_sha256": request_pointer["sha256"],
            "native_agent_receipt_sha256": hashlib.sha256((codex_dir / "native-agent-receipt.json").read_bytes()).hexdigest(),
            "submission_marker_sha256": hashlib.sha256(marker_raw).hexdigest(),
            "spawn_tool_args_sha256": hashlib.sha256(args_raw).hexdigest(),
            "native_ack_sha256": hashlib.sha256(ack_raw).hexdigest(),
            "dispatch_owner_relpath": "hunt/hellowork",
            "owner_thread_id": owner_id, "owner_agent_path": "/root/fixture-coordinator",
            "coverage_kind": "verified_worker_thread_snapshot",
        }
        payload = {"kind": "native_worker_usage_snapshot_v1", "schema_version": 1,
            "run_id": self.batch.name, "generated_at": "2020-02-01T09:06:00Z",
            "counter_basis": "codex_rollout_token_count", "usage_scope": "worker_thread_cumulative",
            "counter_semantics": "Cumulative worker totals include cached input.",
            "whole_task_exact_tokens": None, "owner_thread_id": owner_id,
            "owner_agent_path": "/root/fixture-coordinator",
            "coverage": {"status": "partial", "assigned_native_workers": 1,
                "metadata_bound_workers": 1, "recorded_worker_threads": 1,
                "missing_worker_counters": 0, "scope_note": "Selected worker threads only."},
            "worker_usage_snapshots": [row]}
        write_snapshot(self.resources / "Studio/.local", payload)

        result = self.run_detail()
        hunt = next(stage for stage in result["stage_metrics"] if stage["id"] == "hunt")
        self.assertEqual(len(hunt["usage_counters"]), 1)
        usage = hunt["usage_counters"][0]
        self.assertEqual(usage["usage_counter_basis"], "codex_rollout_token_count")
        self.assertEqual(usage["recorded_tokens"]["total_tokens"], 125)
        self.assertTrue(usage["usage_source_integrity_verified"])
        projection = self.history._snapshot_projection(result)
        projected_hunt = next(stage for stage in projection["stage_metrics"] if stage["id"] == "hunt")
        self.assertFalse(any(row.get("usage_counter_basis") == "codex_rollout_token_count"
                             for row in projected_hunt.get("usage_counters", [])))
        self.assertEqual(metrics_path.read_bytes(), original_metrics,
                         "The sidecar enriches history in memory without rewriting immutable run metrics")

    def test_failures_expose_named_fields_and_canned_safe_text_only(self):
        secret = "PRIVATE INBOX CONTENT token=secret123 person@example.org"
        write(self.batch / "hunt_audit/terminal.json", {"stage": "hunt_audit", "worker_id": "hunt_audit",
            "status": "FAILED", "started_at": "2020-02-01T09:20:00+00:00", "finished_at": "2020-02-01T09:22:00+00:00",
            "failure": {"failure_class": "clock_exhausted", "reason": secret},
            "commands": [{"stdout": secret}], "reasoning": secret})
        write(self.batch / "inbox/full-messages.json", {"body": secret})
        result = self.run_detail()
        encoded = json.dumps({k: result[k] for k in ("stage_metrics", "builds", "observations", "failures")})
        self.assertNotIn(secret, encoded)
        self.assertNotIn("secret123", encoded)
        self.assertEqual(result["failures"][0]["class"], "clock_exhausted")

    def test_legacy_stage_preserves_other_six_missing_and_its_ledger(self):
        write(self.resources / "Master/auto/state/2020-01-01.json", {"date": "2020-01-01", "stages": {
            "build": {"run": 1, "status": "done", "model": "historic-model", "started_at": "2020-01-01T09:00:00",
                      "ended_at": "2020-01-01T09:03:00", "counts": {"cvs_built": 1}}}})
        result = self.history.run("legacy-2020-01-01-build-1")
        self.assertEqual(len(result["stage_metrics"]), 7)
        self.assertEqual(result["stage_metrics"][3]["model"], "historic-model")
        self.assertEqual(result["stage_metrics"][3]["duration_seconds"], 180)
        self.assertEqual(sum(s["status"] == "not_recorded" for s in result["stage_metrics"]), 6)
        self.assertEqual(result["summary_metrics"]["cvs_built"], 1)

    def test_id_resolution_and_symlink_containment_remain_authoritative(self):
        for value in ("../outside", "/etc/passwd", "unknown"):
            with self.assertRaises(StudioDataError):
                self.history.run(value)
        outside = Path(self.temp.name) / "private.json"
        outside.write_text(json.dumps({"stage": "build", "status": "DONE", "model": "private-model"}))
        (self.batch / "build.dispatch.json").symlink_to(outside)
        self.assertNotIn("private-model", json.dumps(self.run_detail()["stage_metrics"]))

    def test_compact_list_does_not_read_ledgers(self):
        with patch.object(RunHistory, "_records", side_effect=AssertionError("heavy detail read")):
            result = self.history.runs()
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["items"][0]["stage_metrics"]), 7)

    def test_supplied_inventory_preserves_normal_history_without_rescanning(self):
        write(self.batch / "stage-outcomes.json", {"hunt": "NOT_RUN", "counts": {"full_ads": 0}})
        inventory = self.data.runs()
        original = copy.deepcopy(inventory)
        expected = self.history.runs()
        with patch.object(self.data, "runs", side_effect=AssertionError("duplicate inventory scan")):
            result = self.history.runs(inventory=inventory)
        self.assertEqual(result, expected)
        self.assertEqual(inventory, original)
        self.assertEqual(result["items"][0]["stage_metrics"][1]["status"], "not_run")

    def test_supplied_inventory_rejects_malformed_inputs_before_reading(self):
        for inventory in ([], {}, {"items": None}, {"items": [None]}, {"items": ["bad"]}):
            with self.subTest(inventory=inventory), \
                    patch.object(self.history, "enrich", side_effect=AssertionError("unexpected receipt read")):
                with self.assertRaises(ValueError):
                    self.history.runs(inventory=inventory)

    def test_only_sealed_audit_paths_count_as_shortlisted(self):
        write(self.batch / "terminal-report.json", {"counts": {"jobs_found": 7, "jobs_shortlisted": 4}})
        result = self.run_detail()
        self.assertEqual(result["summary_metrics"]["jobs_found"], 7)
        self.assertIsNone(result["summary_metrics"]["jobs_shortlisted"])

        role_path = "Tailored CVs/Fixture Company - ESG analyst"
        write(self.batch / "hunt_audit/approved_roles.json", {
            "kind": "hunt_audit_approved_roles", "status": "SEALED", "sealed": True,
            "accepted_role_paths": ["/private/workspace/" + role_path],
            "roles": [{"role_path": "/private/workspace/" + role_path,
                       "company": "Fixture Company", "title": "ESG analyst", "job_id": "job-123"}]})
        result = self.run_detail()
        self.assertEqual(result["summary_metrics"]["jobs_shortlisted"], 1)
        self.assertEqual(result["jobs"], [{"role_path": role_path, "role_id": "job-123",
            "company": "Fixture Company", "title": "ESG analyst", "status": "shortlisted",
            "cv_built": None, "duration_seconds": None}])
        self.assertNotIn("/private/workspace", json.dumps(result["jobs"]))

    def test_hash_bound_materialization_merges_alias_with_build_and_reconciles_snapshot(self):
        accepted_path = "/private/tmp/run/shortlist-audit/candidates/mail-03"
        role_name = "Résistex - Chargé de Missions RSE - 2020-02-01"
        role_folder = self.data.role_root / role_name
        role_folder.mkdir(parents=True)
        approved_path = self.batch / "hunt_audit/sol-high/approved_roles.json"
        approved = {
            "kind": "hunt_audit_approved_roles", "sealed": True,
            "accepted_role_paths": [accepted_path],
            "materialization_instructions": {
                "candidate_id": "mail-03", "proposal_id": "proposal-id-is-not-an-identity"},
            "decisions": [{"candidate_id": "mail-03", "decision": "ACCEPTED",
                           "company": "Résistex", "title": "Chargé de Missions RSE H/F"}],
        }
        write(approved_path, approved)

        # First read models the old alias-only snapshot saved before the
        # hash-bound materialization receipt was available.
        role = "resistex"
        started_at = "2020-02-01T09:10:00+00:00"
        write(self.batch / "stage-metrics.json", {"kind": "scheduled_run_metrics_v1", "records": [{
            "stage": "build", "worker_id": "build:resistex", "contract_id": "build123",
            "status": "PARTIAL", "started_at": started_at, "finished_at": "2020-02-01T09:12:00+00:00",
            "role": role,
        }]})
        ledger = self.batch / "build-ledgers/resistex.ledger.json"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("\n".join(json.dumps(row) for row in [
            {"event": "run_started", "started_at": started_at, "model": "gpt-6-luna"},
            {"event": "role_intake", "role": role, "company": "Résistex",
             "job_title": "Chargé de Missions RSE H/F"},
            {"event": "artifact_recorded", "role": role, "at": "2020-02-01T09:12:00+00:00"},
            {"event": "verified", "completion_stage": "artifact"},
        ]), encoding="utf-8")
        build_folder = self.batch / "build/resistex"
        write(build_folder / "contract.json", {"contract_id": "build123", "assigned_role_paths": [
            {"path": str(role_folder)}]})

        first = self.run_detail()
        self.assertEqual(first["summary_metrics"]["jobs_shortlisted"], 1)
        self.assertEqual(len(first["jobs"]), 2)
        self.assertTrue(any(row["role_path"] == "Tailored CVs/mail-03" for row in first["jobs"]))

        materialized_path = self.batch / "accepted-role-paths.materialized.v1.json"
        materialization = {
            "kind": "sealed_accepted_role_materialization_v1",
            "source_handoff": str(approved_path),
            "source_handoff_sha256": hashlib.sha256(approved_path.read_bytes()).hexdigest(),
            "mapping": {accepted_path: str(role_folder)},
            "accepted_role_paths": [str(role_folder)],
        }
        write(materialized_path, materialization)
        write(build_folder / "dispatch-authorization.json", {
            "canonical_role_path": str(role_folder),
            "accepted_materialization": materialization,
        })

        result = self.run_detail()
        self.assertEqual(result["summary_metrics"]["jobs_shortlisted"], 1)
        self.assertEqual(len(result["jobs"]), 1)
        self.assertEqual(result["jobs"][0]["role_path"], "Tailored CVs/" + role_name)
        self.assertEqual(result["jobs"][0]["company"], "Résistex")
        self.assertEqual(result["jobs"][0]["title"], "Chargé de Missions RSE H/F")
        self.assertIs(result["jobs"][0]["cv_built"], True)
        self.assertNotIn("_jobs_authoritative", result)

        snapshot_path = self.resources / "Studio/.local/history/runs" / self.history._snapshot_filename(self.batch.name)
        saved = json.loads(snapshot_path.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["summary"]["jobs"]), 1)
        self.assertEqual(len(saved["detail"]["jobs"]), 1)
        self.assertNotIn("mail-03", json.dumps(saved))

    def test_history_snapshot_recovers_after_source_receipts_are_missing(self):
        write(self.batch / "terminal.json", {"status": "DONE", "finished_at": "2020-02-01T09:20:00+00:00",
                                               "counts": {"ads_reviewed": 12}})
        first = self.run_detail()
        recorded_at = first["snapshot_recorded_at"]
        self.assertEqual(first["summary_metrics"]["ads_reviewed"], 12)
        import shutil
        shutil.rmtree(self.batch)

        listing = self.history.runs()
        recovered = next(row for row in listing["items"] if row["id"] == "2020-02-01-weekday")
        self.assertEqual(recovered["snapshot_status"], "recovered")
        self.assertEqual(recovered["snapshot_recorded_at"], recorded_at)
        self.assertEqual(recovered["summary_metrics"]["ads_reviewed"], 12)
        self.assertIn("Original run receipts are unavailable", recovered["snapshot_warning"])
        detail = self.history.run("2020-02-01-weekday")
        self.assertEqual(detail["snapshot_status"], "recovered")
        self.assertEqual(detail["summary_metrics"]["ads_reviewed"], 12)

    def test_transient_parse_does_not_overwrite_last_valid_snapshot(self):
        write(self.batch / "stage-outcomes.json", {"hunt": "DONE", "counts": {"ads_reviewed": 9}})
        first = self.run_detail()
        recorded_at = first["snapshot_recorded_at"]
        self.assertEqual(first["summary_metrics"]["ads_reviewed"], 9)
        (self.batch / "stage-outcomes.json").write_text("{partial", encoding="utf-8")

        result = self.run_detail()
        self.assertEqual(result["snapshot_recorded_at"], recorded_at)
        self.assertEqual(result["summary_metrics"]["ads_reviewed"], 9)
        self.assertIn("previous saved snapshot was kept", result["snapshot_warning"])

    def test_completion_continuation_is_attached_to_parent_job_and_preserves_queue_state(self):
        role_path = "Tailored CVs/Fixture Company - ESG analyst"
        write(self.batch / "hunt_audit/approved_roles.json", {
            "kind": "hunt_audit_approved_roles", "status": "SEALED", "sealed": True,
            "accepted_role_paths": ["/private/workspace/" + role_path],
            "roles": [{"role_path": "/private/workspace/" + role_path,
                       "company": "Fixture Company", "title": "ESG analyst"}]})
        manual = self.resources / "Master/auto/evidence/manual/fixture-continuation"
        write(manual / "continuation.json", {"kind": "authorized_completion_continuation_v1",
            "parent_run": self.batch.name, "accepted_role_path": "/private/workspace/" + role_path,
            "started_at": "2020-02-02T09:00:00+00:00", "global_deadline": "2020-02-02T09:10:00+00:00"})
        write(manual / "build-terminal.json", {"status": "DONE", "artifact_recorded": True})
        write(manual / "independent-build-audit/receipt.json", {"status": "DONE"})
        write(manual / "sol-high-approval.json", {"status": "APPROVED"})
        write(manual / "ledger-runtime.json", {"status": "PENDING"})
        write(self.resources / "Master/auto/local/completion-queue.json", {"items": [{
            "parent_run": self.batch.name, "accepted_role_path": "/private/workspace/" + role_path,
            "status": "completion_required", "remaining_stages": ["sync"], "next_action": "Sync after approved review"}]})
        result = self.run_detail()
        self.assertEqual(result["continuations"][0]["status"], "completion_required")
        self.assertEqual(result["continuations"][0]["remaining_stages"], ["sync"])
        self.assertEqual(result["jobs"][0]["status"], "completion_required")
        self.assertIs(result["jobs"][0]["cv_built"], True)
        encoded = json.dumps({"jobs": result["jobs"], "continuations": result["continuations"]})
        self.assertNotIn("/private/workspace", encoded)

    def test_v5_continuation_projects_verified_gates_separately_from_partial_parent(self):
        role_folder = self.data.role_root / "Fixture Company - ESG analyst"
        role_folder.mkdir(parents=True)
        role_path = str(role_folder)
        relative_role = "Tailored CVs/Fixture Company - ESG analyst"
        write(self.batch / "terminal.json", {"status": "PARTIAL", "batch_started_at": "2020-02-01T09:00:00+00:00"})
        write(self.batch / "hunt_audit/approved_roles.json", {
            "kind": "hunt_audit_approved_roles", "status": "SEALED", "sealed": True,
            "accepted_role_paths": [role_path],
            "roles": [{"role_path": role_path, "company": "Fixture Company", "title": "ESG analyst"}]})
        manual = self.resources / "Master/auto/evidence/manual/fixture-v5-continuation"
        write(manual / "continuation-authorization.json", {
            "authorization": "standingexplicitcompletioncontinuation", "no_new_hunt": True,
            "original_batch_deadline_unchanged": True, "parent_run": self.batch.name,
            "accepted_role_path": role_path, "actual_start": "2020-02-02T09:00:00+00:00"})
        for filename, status in (("build-completion.v5.json", "ARTIFACT_COMPLETE"),
                                 ("independent-build-audit-v5/receipt.json", "PASS"),
                                 ("sol-high-approval.json", "APPROVED"),
                                 ("sync-v8/sync-receipt.json", "VERIFIED"),
                                 ("completion-receipt.json", "DONE")):
            write(manual / filename, {"status": status, "role_path": role_path,
                                      "completed_at": "2020-02-02T09:08:00+00:00",
                                      **({"completed_cvs": 1, "completed_letters": 1, "rows_synced": 1}
                                         if filename == "completion-receipt.json" else {})})
        write(self.resources / "Master/auto/local/completion-queue.json", {"items": [{
            "parent_run": self.batch.name, "accepted_role_path": role_path,
            "status": "complete", "remaining_stages": []}]})

        result = self.run_detail()
        self.assertEqual(result["status"], "partial", "The original run must retain its saved outcome")
        continuation = result["continuations"][0]
        self.assertEqual(continuation["status"], "complete")
        self.assertEqual(continuation["stages"], {"build": "ARTIFACT_COMPLETE", "build_audit": "PASS",
                                                    "sol_review": "APPROVED", "sync": "VERIFIED"})
        self.assertEqual(continuation["counts"], {"cvs_built": 1, "letters_completed": 1, "rows_synced": 1})
        self.assertEqual(continuation["role_path"], relative_role)
        self.assertEqual(result["jobs"][0]["status"], "complete")
        self.assertIs(result["jobs"][0]["cv_built"], True)
        encoded = json.dumps({"jobs": result["jobs"], "continuations": result["continuations"]})
        self.assertNotIn(str(self.root), encoded)


if __name__ == "__main__":
    unittest.main()
