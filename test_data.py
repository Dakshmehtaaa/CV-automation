"""Fixture-only regression tests for Studio's workspace adapter.

No test reads or writes the real profile, tracker, scheduler or pipeline state.
``make_fixture`` is also useful for the HTTP/browser acceptance tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from data import StudioData, StudioDataError, _analytics_platform, _office_location_label, _web_url


PROFILE = "# PROFILE\n\n## 1. Identity\nExample Candidate\n\n## 15. Approved CV bank\nGenerated words.\n"
SEARCH = ("# SEARCH\n\n## Current shortlisting rule\nPreserve evidence gates.\n\n"
          "## 1. Order of priority\nExample companies.\n\n"
          "## 2. Language preference\nPreserve language gate.\n\n"
          "## 3. Where to look\nExisting sources.\n\n"
          "## 4. Filters\nFrance only.\n\n## 5. The routine\nNo applications or outreach.\n")
PDF = b"%PDF-1.4\n% Fixture only\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_fixture(root: Path, *, reverse_alias: bool = True) -> Path:
    """Create a tiny invented CV workspace and return its visible root."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    resources = root / "Resources"
    resources.mkdir(exist_ok=True)
    (resources / "PROFILE.md").write_text(PROFILE, encoding="utf-8")
    (resources / "SEARCH.md").write_text(SEARCH, encoding="utf-8")
    (resources / "LAYOUT.md").write_text("# Layout\nOne page.\n", encoding="utf-8")
    write_json(resources / "Master/cv/bank.json", {"version": 2, "identity": {"name": "Example Candidate"}})
    routing = resources / "Master/auto/local/ROUTING.md"
    routing.parent.mkdir(parents=True, exist_ok=True)
    routing.write_text("# Routing\nExisting pipeline gates.\n", encoding="utf-8")
    roles = root / "Tailored CVs" if reverse_alias else resources / "Tailored CVs"
    roles.mkdir(exist_ok=True)
    if reverse_alias:
        (resources / "Tailored CVs").symlink_to("../Tailored CVs", target_is_directory=True)
    folder = roles / "Already applied" / "_Applied" / "Example Company"
    folder.mkdir(parents=True)
    (folder / "JD.md").write_text("# Sustainability analyst\n\n**Company:** Example Company\n**Location:** Paris\n**Contract:** CDI\n**Fit:** 4/5\n**Source:** https://example.org/jobs/1\n", encoding="utf-8")
    (folder / "CV_Candidate.pdf").write_bytes(PDF)
    (folder / "Cover_Letter_Candidate.pdf").write_bytes(PDF)
    write_json(folder / "build-spec.json", {"cvs": [{"language": "EN"}]})
    (folder / "why-this-company.md").write_text("Fixture company motivation.", encoding="utf-8")
    (roles / "Example Company").symlink_to("Already applied/_Applied/Example Company", target_is_directory=True)
    (roles / "_Applied").symlink_to("Already applied/_Applied", target_is_directory=True)
    source_only = roles / "Another Company"
    source_only.mkdir()
    (source_only / "JD.md").write_text("# ESG associate\nCompany: Another Company\nLocation: Lyon\n", encoding="utf-8")
    for language in ("EN", "FR"):
        (root / f"Candidate - CV ({language}).pdf").write_bytes(PDF)
    run = resources / "Master/auto/evidence/runs/2020-02-01-weekday"
    write_json(run / "batch-manifest.json", {"batch_id": run.name, "batch_started_at": "2020-02-01T09:00:00+00:00", "policy_version": 3})
    write_json(run / "stage-outcomes.json", {"capacity_admission": "HELD", "hunt": "NOT_RUN", "build": "NOT_RUN",
                                            "reason": "Fixture capacity route unavailable.", "counts": {"full_ads": 0}})
    write_json(run / "site-lane-outcomes.json", {"lanes": [{"worker_id": "hunt:hellowork", "status": "HELD_UNSTARTED",
                "full_live_ads_reviewed": 0, "target": 20, "reason": "Fixture route unavailable."}]})
    write_json(run / "pipeline-inventory-final.json", {"values": [
        ["Company", "Job profile", "Applied", "Status", "CV file", "Lang", "Location"],
        ["Example Company", "Sustainability analyst", "Yes", "Awaiting reply", "Tailored CVs/_Applied/Example Company/CV_Candidate.pdf", "EN", "Paris"]]})
    write_json(resources / "Master/auto/state/2020-01-01.json", {"date": "2020-01-01", "stages": {
        "hunt": {"status": "done", "started_at": "2020-01-01T09:00:00", "ended_at": "2020-01-01T09:30:00", "run": 1,
                 "summary": "Historical fixture", "model": "fixture-model"}}})
    return root


class StudioDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cv-studio-data-test-")
        self.root = make_fixture(Path(self.temporary.name) / "CV")
        self.resources = self.root / "Resources"
        self.data = StudioData(self.root)
        self.inspect_process = self.data._active_process
        self.process_patch = patch.object(StudioData, "_active_process", return_value=None)
        self.process_patch.start()

    def tearDown(self):
        self.process_patch.stop()
        self.temporary.cleanup()

    def assert_error(self, code: str, function, *args):
        with self.assertRaises(StudioDataError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def _dashboard_role(self, folder_name: str, company: str, title: str, location: str,
                        source_url: str, *, cv: bool = True) -> Path:
        folder = (self.root / "Tailored CVs" / folder_name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "JD.md").write_text(
            f"# {title}\n\nCompany: {company}\nLocation: {location}\nSource URL: {source_url}\n",
            encoding="utf-8")
        if cv:
            (folder / "CV_Candidate.pdf").write_bytes(PDF)
        return folder

    @staticmethod
    def _dashboard_ad_record(url: str, slug: str) -> dict:
        return {"url": url, "body_path": f"metadata-only/{slug}.html",
                "body_sha256": "a" * 64, "counted_unique_full_ad": True}

    def _dashboard_read_batch(self, accepted_role_path: str, urls_by_site: dict[str, list[str]]) -> None:
        run = self.resources / "Master/auto/evidence/runs/2020-02-02-dashboard"
        write_json(run / "batch-manifest.json", {
            "batch_id": run.name, "batch_started_at": "2020-02-02T09:00:00+00:00", "policy_version": 3})
        write_json(run / "terminal.json", {
            "status": "DONE", "batch_started_at": "2020-02-02T09:00:00+00:00",
            "finished_at": "2020-02-02T09:15:00+00:00", "counts": {"ads_reviewed": 3}})
        lanes = []
        for site_id in ("hellowork", "france_travail", "linkedin", "apec", "wttj"):
            urls = urls_by_site.get(site_id, [])
            lanes.append({"worker_id": "hunt:" + site_id, "status": "DONE",
                          "full_live_ads_reviewed": len(urls), "target": 20})
            write_json(run / site_id / "full_ads.index.json", {
                "full_live_ads_reviewed": len(urls),
                "records": [self._dashboard_ad_record(url, f"{site_id}-{i}")
                            for i, url in enumerate(urls)]})
        write_json(run / "site-lane-outcomes.json", {"lanes": lanes})
        write_json(run / "hunt_audit" / "approved_roles.json", {
            "sealed": True, "accepted_role_paths": [{"role_path": accepted_role_path,
                "company": "Example Company", "title": "Sustainability analyst"}]})

    def test_native_hunt_display_uses_sealed_six_lanes_and_preserves_old_run(self):
        config={"routing_revision":"2026-10-05-luna-max", "routes":{
            "hunt":{"model":"gpt-6-luna","effort":"max"},
            "hunt_audit":{"model":"gpt-6.1-sol","effort":"medium"}}}
        fresh=self.resources/"Master/auto/evidence/runs/2026-10-05-native-fixture"
        write_json(fresh/"launch-receipt.json", {"policy_version":4,"config":config})
        rows=self.data._sources(fresh)
        self.assertEqual([r["id"] for r in rows], ["hellowork","france_travail","linkedin","apec","wttj","indeed"])
        self.assertTrue(all(r["target"]==20 for r in rows))
        stages=self.data._stages({}, {}, {"config":config}, policy_version=4)
        self.assertEqual(stages[0]["id"],"hunt")
        self.assertEqual(next(r for r in stages if r["id"]=="hunt")["model"],"GPT-6 Luna · MAX")
        self.assertEqual(next(r for r in stages if r["id"]=="hunt_audit")["model"],"GPT-6.1 Sol · Medium")
        old=self.resources/"Master/auto/evidence/runs/2026-10-01-old-fixture"
        write_json(old/"batch-manifest.json", {"policy_version":4})
        history=self.data._sources(old)
        self.assertEqual(len(history),5)
        self.assertTrue(all(r["target"]==31 for r in history))

    def test_read_methods_have_no_local_write_side_effects(self):
        overview = self.data.overview()
        self.data.settings()
        self.data.document("profile")
        self.data.run(overview["latest_run"]["id"])
        self.assertFalse((self.resources / "Studio/.local").exists())

    def test_overview_reuses_one_live_observation(self):
        with patch.object(self.data, "live_pipeline", wraps=self.data.live_pipeline) as observe:
            self.data.overview()
        self.assertEqual(observe.call_count, 1)

    def test_reverse_alias_and_nested_collections_are_read_once(self):
        roles = self.data.roles()
        self.assertEqual(roles["total"], 2)
        example = next(r for r in roles["items"] if r["company"] == "Example Company")
        self.assertTrue(example["cv_available"])
        self.assertEqual(example["collection"], "applied")
        self.assertEqual(example["language"], "EN")
        self.assertEqual(example["tracker_status"], "Awaiting reply")
        self.assertTrue(example["applied"])
        self.assertEqual(StudioData(self.resources).roles()["total"], 2)
        detail = self.data.role(example["id"])
        self.assertIn("Sustainability analyst", detail["jd"])
        self.assertTrue(detail["documents"])

    def test_jd_compatibility_link_requires_exact_manifest_and_hash(self):
        folder = self.root / "Tailored CVs/Already applied/_Applied/Example Company"
        legacy = folder / "JD.md"
        original = legacy.read_bytes()
        support_dir = folder / "Example Company support"
        support_dir.mkdir()
        support = support_dir / "JD.md"
        support.write_bytes(original)
        legacy.unlink()
        legacy.symlink_to("Example Company support/JD.md")
        write_json(self.resources / "Master/auto/local/pipeline-repair/tidy_roles_manifest.json", {
            "schema_version": 1, "manifest_id": "fixture-tidy-v1", "root_relative": "Tailored CVs",
            "roles": [{"role_path": "Already applied/_Applied/Example Company", "support_dir": "Example Company support",
                       "files": [{"kind": "job_description", "original_path": "Already applied/_Applied/Example Company/JD.md",
                                  "legacy_link_path": "Already applied/_Applied/Example Company/JD.md",
                                  "legacy_link_target": "Example Company support/JD.md",
                                  "support_path": "Example Company support/JD.md",
                                  "sha256": hashlib.sha256(original).hexdigest(), "size_bytes": len(original)}]}]})

        roles = self.data.roles()
        self.assertEqual(roles["total"], 2, "A nested support folder must not appear as a separate role")
        item = next(row for row in roles["items"] if row["company"] == "Example Company")
        self.assertIn("Sustainability analyst", self.data.role(item["id"])["jd"])
        dashboard = self.data._dashboard_jd_metadata(item)
        self.assertTrue(dashboard["jd_role_evidence"])
        self.assertEqual(dashboard["source_url"], "https://example.org/jobs/1")
        self.assertEqual(dashboard["office_location"], "Paris")

        legacy.unlink()
        legacy.symlink_to("Example Company support/other.md")
        self.assertIsNone(self.data._role_jd_text(folder))
        self.assertFalse(self.data._dashboard_jd_metadata(item)["jd_role_evidence"])
        legacy.unlink()
        legacy.symlink_to("Example Company support/JD.md")
        support.write_text("# Changed after manifest\nCompany: Spoofed\n", encoding="utf-8")
        self.assertIsNone(self.data._role_jd_text(folder))
        self.assertFalse(self.data._dashboard_jd_metadata(item)["jd_role_evidence"])

    def test_status_separates_artifact_presence_from_approval(self):
        roles = self.data.roles()["items"]
        self.assertEqual({r["status"] for r in roles}, {"artifact_present", "source_only"})
        self.assertFalse(any(r["status"] in ("ready", "approved", "done") for r in roles))
        self.assertTrue(all(r.get("approval") is None for r in roles))

    def test_latest_batch_is_separate_from_historical_tracker_and_stage(self):
        overview = self.data.overview()
        self.assertEqual(overview["latest_run"]["id"], "2020-02-01-weekday")
        self.assertEqual(overview["latest_run"]["status"], "held")
        self.assertTrue(overview["tracker"]["historical"])
        self.assertFalse(overview["tracker"]["live"])
        self.assertEqual(overview["tracker"]["date"], "2020-02-01")
        self.assertEqual(next(s for s in overview["pipeline"]["stages"] if s["id"] == "hunt")["status"], "not_run")
        self.assertEqual({m["id"]: m["value"] for m in overview["metrics"]}["applied"], 1)

    def test_missing_stage_receipt_is_distinct_from_explicit_not_run(self):
        stages = self.data._stages({"stages": {
            "hunt": {"status": "NOT_RUN"},
            "build": {"status": "QUEUED"},
            "build_audit": {"status": "UNSTARTED"},
        }}, {}, {})
        by_id = {stage["id"]: stage for stage in stages}
        for stage_id in ("hunt", "build", "build_audit"):
            self.assertEqual(by_id[stage_id]["status"], "not_run")
        for stage_id in ("inbox", "hunt_audit", "sol_review", "sync"):
            self.assertEqual(by_id[stage_id]["status"], "not_recorded")
            self.assertIn("No stage receipt", by_id[stage_id]["detail"])
        self.assertTrue(all(stage["status"] == "not_recorded" for stage in self.data._stages({}, {}, {})))

    def test_policy4_route_and_hunt_target_require_explicit_policy(self):
        unknown = self.resources / "Master/auto/evidence/runs/2020-02-02-unknown-policy"
        unknown_metrics = {"kind": "scheduled_run_metrics_v1", "records": []}
        write_json(unknown / "stage-metrics.json", unknown_metrics)
        self.assertTrue(all(row["target"] is None for row in self.data._sources(unknown)))
        unknown_stages = self.data._stages({}, {}, {}, history_metrics=unknown_metrics, policy_version=None)
        self.assertTrue(all(row["model"] is None for row in unknown_stages))
        self.assertIn("No stage receipt", next(row for row in unknown_stages if row["id"] == "hunt")["detail"])

        policy4 = self.resources / "Master/auto/evidence/runs/2020-02-03-policy4"
        policy4_metrics = {"kind": "scheduled_run_metrics_v1", "policy_version": 4, "records": []}
        write_json(policy4 / "stage-metrics.json", policy4_metrics)
        self.assertTrue(all(row["target"] == 31 for row in self.data._sources(policy4)))
        current_stages = self.data._stages({}, {}, {}, history_metrics=policy4_metrics, policy_version=4)
        self.assertEqual(next(row for row in current_stages if row["id"] == "hunt_audit")["model"],
                         "GPT-6.1 Sol · High")
        self.assertEqual(next(row for row in current_stages if row["id"] == "sync")["label"], "CV Studio sync")

    def test_missing_receipts_do_not_create_zero_counts(self):
        overview = self.data.overview()
        sources = {s["id"]: s for s in overview["pipeline"]["sources"]}
        self.assertEqual(sources["hellowork"]["screened"], 0)
        self.assertIsNone(sources["linkedin"]["screened"])
        (self.resources / "Master/auto/evidence/runs/2020-02-01-weekday/pipeline-inventory-final.json").unlink()
        self.assertIsNone(next(m for m in self.data.overview()["metrics"] if m["id"] == "applied")["value"])

    def test_recorded_lane_count_has_receipt_detail_without_a_reason(self):
        path = self.resources / "Master/auto/evidence/runs/2020-02-01-weekday/site-lane-outcomes.json"
        write_json(path, {"lanes": [
            {"worker_id": "hunt:hellowork", "status": "DONE", "full_live_ads_reviewed": 20, "target": 20},
            {"worker_id": "hunt:apec", "status": "PARTIAL", "full_live_ads_reviewed": 0, "target": 20},
        ]})
        sources = {s["id"]: s for s in self.data.overview()["pipeline"]["sources"]}
        for key in ("hellowork", "apec"):
            self.assertIn("recorded in the saved lane receipt", sources[key]["detail"])
            self.assertNotIn("No lane count", sources[key]["detail"])
        self.assertEqual(sources["hellowork"]["status"], "done")
        self.assertEqual(sources["hellowork"]["screened"], 20)
        self.assertIn("No lane count", sources["linkedin"]["detail"])

    def test_assets_are_opaque_pdf_keys(self):
        role = next(r for r in self.data.roles()["items"] if r["cv_available"])
        asset = role["artifacts"][0]
        self.assertEqual(asset["url"], "/api/assets?key=" + asset["key"])
        self.assertEqual(self.data.asset_path(asset["key"]).read_bytes(), PDF)
        for key in ("../PROFILE.md", "/etc/passwd", "..%2f..%2fsecret", "f" * 24):
            self.assert_error("not_found", self.data.asset_path, key)

    def test_only_exact_master_pdfs_are_exposed(self):
        (self.root / "private.pdf").write_bytes(PDF)
        masters = self.data.overview()["master_cvs"]
        self.assertEqual(len(masters), 2)
        self.assertEqual(self.data.asset_path(masters[0]["key"]).parent, self.root)
        key = hashlib.sha256(b"private.pdf").hexdigest()[:24]
        self.assert_error("not_found", self.data.asset_path, key)

    def test_wrong_pdf_magic_is_rejected(self):
        role = next(r for r in self.data.roles()["items"] if r["cv_available"])
        asset = role["artifacts"][0]
        self.data.asset_path(asset["key"]).write_text("This is not a PDF.")
        self.assert_error("invalid_pdf", self.data.asset_path, asset["key"])

    def test_asset_symlink_escape_is_rejected_after_key_was_issued(self):
        role = next(r for r in self.data.roles()["items"] if r["cv_available"])
        asset = role["artifacts"][0]
        path = self.data.asset_path(asset["key"])
        outside = Path(self.temporary.name) / "private.pdf"
        outside.write_bytes(PDF)
        path.unlink()
        path.symlink_to(outside)
        self.assert_error("not_found", self.data.asset_path, asset["key"])

    def test_external_role_symlink_is_never_traversed(self):
        outside = Path(self.temporary.name) / "Outside Company"
        outside.mkdir()
        (outside / "JD.md").write_text("Private unrelated material")
        (self.root / "Tailored CVs/Outside").symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.data.roles()["total"], 2)

    def test_reverse_alias_cannot_register_arbitrary_directory(self):
        outside = Path(self.temporary.name) / "Secret"
        outside.mkdir()
        (outside / "JD.md").write_text("Secret")
        alias = self.resources / "Tailored CVs"
        alias.unlink()
        alias.symlink_to(outside, target_is_directory=True)
        self.assertEqual(StudioData(self.root).roles()["total"], 0)

    def test_unknown_document_and_role_ids_are_not_paths(self):
        for key in ("../../PROFILE.md", "PROFILE.md", "/etc/passwd"):
            self.assert_error("not_found", self.data.document, key)
            self.assert_error("not_found", self.data.role, key)
            self.assert_error("not_found", self.data.run, key)

    def test_save_creates_original_backup_and_journal(self):
        document = self.data.document("profile")
        edited = document["content"].replace("Example Candidate", "Example Candidate, Paris")
        saved = self.data.save_document("profile", edited, document["revision"])
        self.assertTrue(saved["saved"])
        self.assertEqual((self.resources / "PROFILE.md").read_text(), edited)
        backup = self.resources / saved["backup"]
        self.assertEqual(backup.read_text(), PROFILE)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.resources / "Studio/.local").stat().st_mode), 0o700)
        journal = [json.loads(line) for line in (self.resources / "Studio/.local/edit-history.jsonl").read_text().splitlines()]
        self.assertEqual([e["status"] for e in journal], ["prepared", "saved"])
        self.assertEqual(journal[-1]["after"], saved["revision"])

    def test_stale_edit_never_overwrites_newer_source(self):
        document = self.data.document("profile")
        external_edit = PROFILE.replace("Example Candidate", "Changed elsewhere")
        (self.resources / "PROFILE.md").write_text(external_edit)
        self.assert_error("stale_revision", self.data.save_document, "profile", PROFILE + "Draft\n", document["revision"])
        self.assertEqual((self.resources / "PROFILE.md").read_text(), external_edit)

    def test_profile_mirror_and_search_gates_are_protected(self):
        profile = self.data.document("profile")
        self.assert_error("protected_section", self.data.save_document, "profile", PROFILE.replace("Generated words.", "New unapproved words."), profile["revision"])
        search = self.data.document("search")
        self.assert_error("protected_section", self.data.save_document, "search", SEARCH.replace("France only.", "Anywhere."), search["revision"])
        edited = SEARCH.replace("Example companies.", "Different employer priorities.")
        self.assertTrue(self.data.save_document("search", edited, search["revision"])["saved"])

    def test_bank_is_read_only(self):
        doc = self.data.document("bank")
        self.assertFalse(doc["editable"])
        self.assert_error("read_only", self.data.save_document, "bank", "{}", doc["revision"])

    def test_noop_save_does_not_create_backup(self):
        doc = self.data.document("profile")
        saved = self.data.save_document("profile", doc["content"], doc["revision"])
        self.assertFalse(saved["saved"])
        self.assertTrue(saved["unchanged"])
        self.assertFalse((self.resources / "Studio/.local/backups").exists())

    def test_crlf_revision_round_trip(self):
        path = self.resources / "PROFILE.md"
        path.write_bytes(PROFILE.replace("\n", "\r\n").encode())
        doc = self.data.document("profile")
        self.assertIn("\r\n", doc["content"])
        saved = self.data.save_document("profile", doc["content"].replace("Example Candidate", "Updated Candidate"), doc["revision"])
        self.assertTrue(saved["saved"])

    def test_open_current_batch_locks_source_edits_without_claiming_worker_verified(self):
        now = datetime.now(timezone.utc)
        run = self.resources / "Master/auto/evidence/runs" / (now.strftime("%Y-%m-%d") + "-fixture-active")
        write_json(run / "batch-manifest.json", {"batch_started_at": now.isoformat(), "batch_deadline_at": (now + timedelta(minutes=30)).isoformat()})
        active = self.data.active_pipeline()
        self.assertTrue(active["active"])
        self.assertTrue(active["uncertain"])
        self.assertEqual(active["evidence"], "open_batch_window")
        doc = self.data.document("profile")
        self.assertTrue(doc["editing_paused"])
        self.assert_error("pipeline_active", self.data.save_document, "profile", PROFILE.replace("Example", "Updated"), doc["revision"])
        self.assertEqual((self.resources / "PROFILE.md").read_text(), PROFILE)
        self.assertFalse((self.resources / "Studio/.local").exists())
        write_json(run / "terminal.json", {"status": "HELD", "finished_at": now.isoformat()})
        self.assertFalse(self.data.active_pipeline()["active"])

    def test_pre_dispatch_failure_releases_only_the_unstarted_window(self):
        now = datetime.now(timezone.utc)
        run = self.resources / "Master/auto/evidence/runs" / (now.strftime("%Y-%m-%d") + "-startup-failed")
        manifest = {"kind": "fresh_pipeline_launch", "started_at": (now - timedelta(seconds=5)).isoformat(),
                    "status": "PENDING_SUBMISSION", "supervisor_pid": None, "first_submission_at": None}
        write_json(run / "launch-receipt.json", manifest)
        write_json(run / "launch-failure.json", {"status": "HELD_PRE_DISPATCH", "recorded_at": now.isoformat(),
                   "workers_dispatched": 0, "downstream_status": "NOT_RUN"})
        with patch.object(self.data, "_active_process", return_value=None), patch.object(self.data, "live_pipeline", return_value={"active": False}):
            self.assertFalse(self.data.active_pipeline()["active"])
            write_json(run / "launch-receipt.json", {**manifest, "supervisor_pid": 123})
            self.assertTrue(self.data.active_pipeline()["active"])
        write_json(run / "launch-receipt.json", manifest)
        with patch.object(self.data, "_active_process", return_value=12345), patch.object(self.data, "live_pipeline", return_value={"active": False}):
            self.assertEqual(self.data.active_pipeline()["pid"], 12345)

    def test_open_window_accepts_policy3_global_deadline_alias(self):
        now = datetime.now(timezone.utc)
        run = self.resources / "Master/auto/evidence/runs" / (now.strftime("%Y-%m-%d") + "-fixture-global-deadline")
        write_json(run / "batch-manifest.json", {
            "started_at": now.isoformat(), "global_deadline": (now + timedelta(minutes=50)).isoformat()})
        active = self.data.active_pipeline()
        self.assertTrue(active["active"])
        self.assertEqual(active["evidence"], "open_batch_window")
        self.assertEqual(active["run_id"], run.name)
        self.assertEqual(active["deadline"], (now + timedelta(minutes=50)).isoformat())

    def test_stale_running_record_is_not_live(self):
        write_json(self.resources / "Master/auto/state/2020-01-02.json", {"date": "2020-01-02", "stages": {"hunt": {
            "status": "running", "started_at": "2020-01-02T09:00:00", "run": 1}}})
        self.assertFalse(self.data.active_pipeline()["active"])
        stale = next(r for r in self.data.runs()["items"] if r["id"] == "legacy-2020-01-02-hunt-1")
        self.assertEqual(stale["status"], "partial")
        self.assertEqual(stale["raw_status"], "running")

    def test_verified_local_worker_locks_editing(self):
        with patch.object(self.data, "_active_process", return_value=12345):
            active = self.data.active_pipeline()
            self.assertTrue(active["active"])
            self.assertFalse(active["uncertain"])
            self.assertEqual(active["pid"], 12345)

    def test_process_inspection_failure_is_an_explicit_hold(self):
        with patch("data.subprocess.run", side_effect=OSError("ps unavailable")):
            result = self.inspect_process()
        self.assertTrue(result["active"])
        self.assertTrue(result["uncertain"])
        with patch("data.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "denied")):
            self.assertTrue(self.inspect_process()["edit_locked"])

    def test_relative_worker_argv_uses_read_only_cwd_verification(self):
        ps = subprocess.CompletedProcess([], 0, "23456 python3 Master/auto/local/stage_runtime.py run --contract fixture.json\n", "")
        cwd = subprocess.CompletedProcess([], 0, "p23456\nn" + str(self.root) + "\n", "")
        with patch("data.subprocess.run", side_effect=[ps, cwd]) as run:
            found = self.inspect_process()
        self.assertEqual(found["pid"], 23456)
        self.assertFalse(found["uncertain"])
        self.assertEqual(run.call_args_list[1].args[0][:2], ["lsof", "-a"])

    def test_unresolved_relative_worker_remains_uncertain(self):
        ps = subprocess.CompletedProcess([], 0, "23456 python3 Master/auto/local/terminal_agents.py run\n", "")
        with patch("data.subprocess.run", side_effect=[ps, OSError("cannot inspect cwd")]):
            found = self.inspect_process()
        self.assertTrue(found["active"])
        self.assertTrue(found["uncertain"])

    def test_shell_or_search_text_is_not_a_running_pipeline(self):
        ps = subprocess.CompletedProcess([], 0, '23456 rg stage_runtime.py /workspace\n23457 zsh -c "python3 Master/auto/local/stage_runtime.py"\n23458 python3 -c "print(\"terminal_agents.py\")"\n', "")
        with patch("data.subprocess.run", return_value=ps) as run:
            self.assertIsNone(self.inspect_process())
        self.assertEqual(run.call_count, 1)

    def test_settings_are_local_only_and_validated(self):
        settings = self.data.save_settings({"theme": "dark", "density": "compact", "refresh_seconds": 30})
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(settings["scope"], "local_ui")
        self.assertFalse(settings["schedule"]["available"])
        for payload in ({"model": "new-model"}, {"theme": "rainbow"}, {"refresh_seconds": True}, {"refresh_seconds": 0}):
            self.assert_error("invalid_request", self.data.save_settings, payload)
        self.assertEqual(self.data.settings()["refresh_seconds"], 30)

    def test_local_directory_symlink_cannot_redirect_writes(self):
        studio = self.resources / "Studio"
        studio.mkdir()
        (studio / ".local").symlink_to(self.resources, target_is_directory=True)
        self.assert_error("unsafe_path", self.data.save_settings, {"theme": "dark"})
        self.assertFalse((self.resources / "settings.json").exists())
        self.assertEqual((self.resources / "PROFILE.md").read_text(), PROFILE)

    def test_journal_symlink_cannot_modify_another_local_file(self):
        local = self.resources / "Studio/.local"
        local.mkdir(parents=True)
        protected = local / "unrelated.json"
        protected.write_text("original")
        (local / "edit-history.jsonl").symlink_to(protected)
        doc = self.data.document("profile")
        self.assert_error("unsafe_path", self.data.save_document, "profile", PROFILE.replace("Example", "Updated"), doc["revision"])
        self.assertEqual(protected.read_text(), "original")
        self.assertEqual((self.resources / "PROFILE.md").read_text(), PROFILE)

    def test_schedule_reads_only_public_configuration_fields(self):
        path = Path(self.temporary.name) / "automation.toml"
        path.write_text('name = "Fixture weekday schedule"\nstatus = "ACTIVE"\nrrule = "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=14;BYMINUTE=30"\nmodel = "fixture-model"\nreasoning_effort = "high"\nprompt = "PRIVATE PROMPT"\n')
        data = StudioData(self.root, schedule_path=path)
        schedule = data.settings()["schedule"]
        self.assertEqual(schedule["label"], "Weekdays at 14:30 · Paris")
        self.assertEqual(schedule["model"], "fixture-model")
        self.assertNotIn("PRIVATE", json.dumps(schedule))
        self.assertNotIn("rrule", schedule)

    def test_private_inbox_contents_are_not_part_of_run_detail(self):
        run = self.resources / "Master/auto/evidence/runs/2020-02-01-weekday"
        write_json(run / "inbox/full-messages.json", {"private": "SECRET INBOX BODY"})
        detail = self.data.run(run.name)
        self.assertNotIn("SECRET", json.dumps(detail))
        self.assertFalse(any("inbox" in f["path"] for f in detail["files"]))

    def test_source_url_is_an_exact_safe_field_or_intentional_markdown_link(self):
        for value in ("https://example.org/jobs/1", "[Read advert](https://example.org/jobs/1)"):
            self.assertEqual(_web_url(value), "https://example.org/jobs/1")
        for value in ("javascript:https://example.org/jobs/1", "See https://example.org/jobs/1 later",
                      "https://user:password@example.org/jobs/1", "https://example.org/?access_token=private",
                      "https://example.org/?api%5Fkey=private", "https://example.org:99999/jobs/1",
                      "https://example.org/\\unsafe", "https://example.org/\nsecret"):
            self.assertIsNone(_web_url(value), value)

    def test_malformed_partial_receipt_does_not_crash_reads(self):
        path = self.resources / "Master/auto/evidence/runs/2020-02-01-weekday/terminal.json"
        path.write_text('{"status":')
        self.assertEqual(self.data.overview()["latest_run"]["status"], "held")

    def test_saved_advert_inventory_read_is_read_only_when_missing(self):
        before = set(self.resources.rglob("*"))
        counts = self.data._saved_advert_counts()
        self.assertFalse(counts["available"])
        self.assertIsNone(counts["unique_postings"])
        self.assertEqual(before, set(self.resources.rglob("*")))

    def test_dashboard_saved_adverts_refresh_without_changing_read_or_shortlist_counts(self):
        # Use the real deterministic importer on a temporary public-only collection.
        self.data._saved_advert_counts()  # Load the shared pure inventory library.
        from advert_inventory import sync_run
        import hashlib

        directory = self.resources / "Master/auto/evidence/manual/inventory-fixture"
        lane = directory / "sol/indeed"
        lane.mkdir(parents=True)
        body = lane / "body.txt"
        body.write_text("Saved duties and requirements for this public advert.")
        row = {"id": "abc123", "canonical_url": "https://fr.indeed.com/viewjob?jk=abc123&from=share",
               "body_path": str(body), "body_sha256": hashlib.sha256(body.read_bytes()).hexdigest(),
               "captured_at_utc": "2026-10-07T13:00:00Z"}
        write_json(lane / "adverts.json", [row, dict(row)])
        first = self.data.overview()["dashboard_analytics"]
        sync_run(self.resources, directory)
        second = self.data.overview()["dashboard_analytics"]
        self.assertTrue(second["saved_adverts"]["available"])
        self.assertEqual(second["saved_adverts"]["unique_postings"], 1)
        self.assertEqual(second["total_shortlisted"], first["total_shortlisted"])
        self.assertEqual(second["unique_jobs_read"], first["unique_jobs_read"])
        self.assertEqual(next(row for row in second["saved_adverts"]["by_website"]
                              if row["website"] == "indeed")["unique_postings"], 1)
        sync_run(self.resources, directory)
        third = self.data.overview()["dashboard_analytics"]["saved_adverts"]
        self.assertEqual(third["unique_postings"], 1)
        self.assertEqual(third["saved_captures"], second["saved_adverts"]["saved_captures"])

    def test_dashboard_analytics_uses_saved_acceptances_indexes_and_role_jds(self):
        roles = self.root / "Tailored CVs"
        example = roles / "Already applied" / "_Applied" / "Example Company"
        (example / "JD.md").write_text(
            "# Sustainability analyst\nCompany: Example Company\nLocation: Paris (75020)\n"
            "Source URL: https://www.linkedin.com/jobs/view/alpha?trk=share\n", encoding="utf-8")
        self._dashboard_role("Nanterre role", "Nanterre Group", "ESG analyst", "Nanterre (92000)",
            "https://www.apec.fr/candidat/emploi/role-2")
        self._dashboard_role("Marseille role", "Marseille Group", "CSR lead", "Marseille 13008",
            "https://www.hellowork.com/fr-fr/emplois/role-3.html")
        # Same saved advert in a second package is one role by canonical URL.
        self._dashboard_role("Example Company duplicate", "Example Company", "Sustainability analyst", "Paris 75020",
            "https://linkedin.com/jobs/view/alpha?utm_source=share")
        # A generic starter template has a PDF but no JD-backed company/title.
        self._dashboard_role("Germany (General EN)", "", "", "", "", cv=True)
        (roles / "Germany (General EN)" / "JD.md").write_text("# General CV\nLocation: Germany\n", encoding="utf-8")
        (self.resources / "Master/auto/state/2020-01-01.json").unlink()
        prior_outcomes = self.resources / "Master/auto/evidence/runs/2020-02-01-weekday/stage-outcomes.json"
        prior = json.loads(prior_outcomes.read_text(encoding="utf-8"))
        prior["hunt_audit"] = "NOT_RUN"
        write_json(prior_outcomes, prior)

        accepted_url = "https://linkedin.com/jobs/view/alpha?utm_source=share"
        apec_url = "https://www.apec.fr/candidat/emploi/role-2"
        hw_url = "https://www.hellowork.com/fr-fr/emplois/role-3.html"
        self._dashboard_read_batch(str(example.relative_to(roles.parent)), {
            "linkedin": [accepted_url], "apec": [apec_url], "hellowork": [hw_url]})

        analytics = self.data.overview()["dashboard_analytics"]
        self.assertEqual(analytics["total_shortlisted"], 3)
        self.assertEqual(analytics["shortlisted_companies"], 3)
        self.assertEqual(analytics["unique_jobs_read"], 3)
        self.assertEqual(analytics["shortlist_rate_pct"], 100.0)
        sources = {row["id"]: row for row in analytics["sources"]}
        self.assertEqual(sources["linkedin"]["shortlisted"], 1)
        self.assertEqual(sources["linkedin"]["rate_pct"], 100.0)
        self.assertEqual(sources["apec"]["unique_jobs_read"], 1)
        self.assertEqual(sources["hellowork"]["shortlisted"], 1)
        self.assertEqual({row["label"] for row in analytics["locations"]},
                         {"Paris (20e)", "Nanterre (92)", "Marseille (13)"})
        self.assertEqual(analytics["missing_evidence"]["legacy_cv_packages_without_jd_role_evidence"], 1)

    def test_dashboard_projects_only_audit_acceptances_bound_to_queue_readback(self):
        directory = self.resources / "Master/auto/evidence/manual/2026-10-05-fixture"
        items = [
            {"proposal_index": 0, "company": "Panzani", "job_title": "RSE project lead",
             "url": "https://www.hellowork.com/fr-fr/emplois/one.html?utm_source=x",
             "decision": "ACCEPTED_FOR_WAITING_QUEUE"},
            {"proposal_index": 1, "company": "HEXAOM", "job_title": "RSE missions",
             "url": "https://fr.linkedin.com/jobs/view/two", "decision": "ACCEPTED_FOR_WAITING_QUEUE"},
            {"proposal_index": 2, "company": "Unreviewed", "job_title": "Proposal",
             "url": "https://example.org/proposal", "decision": "PROPOSED"},
        ]
        audit = {"kind": "requested_11_proposals_independent_hunt_audit",
                 "created_at_utc": "2026-10-05T18:00:00Z", "accepted_for_waiting_queue": 2,
                 "profile": {"sha256": "a" * 64}, "rules": {"sha256": "b" * 64},
                 "acceptance_indices": [0, 1], "items": items}
        audit_path = directory / "proposal-audit.json"
        write_json(audit_path, audit)
        readback = {"kind": "parent_review_and_studio_waiting_queue_readback",
                    "checked_at": "2026-10-05T18:08:51.566470+00:00",
                    "audit_sha256": hashlib.sha256(audit_path.read_bytes()).hexdigest(),
                    "accepted": [
                        {"queue_id": "queue-one", "duplicate": False,
                         "url": "https://hellowork.com/fr-fr/emplois/one.html"},
                        {"queue_id": "queue-two", "duplicate": False,
                         "url": "https://fr.linkedin.com/jobs/view/two"},
                    ], "total_queue_items": 3, "original_user_item_preserved": "existing"}
        write_json(directory / "parent-acceptance-and-queue-readback.json", readback)
        events = self.data._dashboard_manual_shortlists()
        self.assertEqual(len(events), 2)
        self.assertEqual({event["company"] for event in events}, {"Panzani", "HEXAOM"})
        self.assertTrue(all(event["accepted_at"] == readback["checked_at"] for event in events))

        audit["profile"].pop("sha256")
        audit["rules"].pop("sha256")
        write_json(audit_path, audit)
        readback["audit_sha256"] = hashlib.sha256(audit_path.read_bytes()).hexdigest()
        write_json(directory / "parent-acceptance-and-queue-readback.json", readback)
        self.assertEqual(self.data._dashboard_manual_shortlists(), [])

        audit["profile"]["sha256"], audit["rules"]["sha256"] = "a" * 64, "b" * 64
        write_json(audit_path, audit)
        readback["audit_sha256"] = hashlib.sha256(audit_path.read_bytes()).hexdigest()
        readback["accepted"][1]["duplicate"] = True
        write_json(directory / "parent-acceptance-and-queue-readback.json", readback)
        self.assertEqual(self.data._dashboard_manual_shortlists(), [])

    def test_dashboard_shows_known_minimum_when_saved_history_is_incomplete(self):
        self.test_dashboard_analytics_uses_saved_acceptances_indexes_and_role_jds()
        incomplete_run = self.resources / "Master/auto/evidence/runs/2020-02-03-dashboard-gap"
        write_json(incomplete_run / "batch-manifest.json", {"batch_started_at": "2020-02-03T09:00:00+00:00"})
        write_json(incomplete_run / "terminal.json", {"status": "DONE", "counts": {"ads_reviewed": 0}})
        analytics = self.data._dashboard_analytics(self.data.roles(), self.data.runs()["items"])
        self.assertGreater(analytics["missing_evidence"]["runs_without_sealed_shortlist_count"], 0)
        self.assertIsNone(analytics["total_shortlisted"])
        self.assertGreater(analytics["known_shortlisted"], 0)
        self.assertFalse(analytics["total_shortlisted_complete"])
        self.assertIsNone(analytics["shortlisted_companies"])
        self.assertGreater(analytics["known_shortlisted_companies"], 0)
        self.assertFalse(analytics["shortlisted_companies_complete"])
        self.assertIsNone(analytics["shortlist_rate_pct"])
        self.assertTrue(analytics["sources"])
        self.assertTrue(all(row["rate_pct"] is None for row in analytics["sources"]))

    def test_dashboard_analytics_withholds_rates_when_read_urls_are_missing(self):
        data = self.resources / "Master/auto/evidence/runs/2020-02-02-missing-index"
        prior_outcomes = self.resources / "Master/auto/evidence/runs/2020-02-01-weekday/stage-outcomes.json"
        prior = json.loads(prior_outcomes.read_text(encoding="utf-8"))
        prior["hunt_audit"] = "NOT_RUN"
        write_json(prior_outcomes, prior)
        write_json(data / "batch-manifest.json", {"batch_started_at": "2020-02-02T09:00:00+00:00"})
        write_json(data / "terminal.json", {"status": "DONE", "counts": {"ads_reviewed": 7}})
        write_json(data / "site-lane-outcomes.json", {"lanes": [
            {"worker_id": "hunt:hellowork", "status": "DONE", "full_live_ads_reviewed": 7}]})
        analytics = self.data.overview()["dashboard_analytics"]
        self.assertIsNone(analytics["unique_jobs_read"])
        self.assertIsNone(analytics["shortlist_rate_pct"])
        self.assertIsNone(analytics["total_shortlisted"])
        self.assertGreater(analytics["missing_evidence"]["read_count_without_indexed_urls"], 0)
        self.assertGreater(analytics["missing_evidence"]["runs_without_complete_read_coverage"], 0)
        self.assertGreater(analytics["missing_evidence"]["runs_without_sealed_shortlist_count"], 0)

    def test_dashboard_source_and_location_normalizers_are_exact_and_noninventive(self):
        self.assertEqual(_analytics_platform("https://jobs.linkedin.com/view/1"), "linkedin")
        self.assertIsNone(_analytics_platform("https://notlinkedin.com/view/1"))
        self.assertIsNone(_analytics_platform("https://linkedin.com.evil.test/view/1"))
        self.assertIsNone(_analytics_platform("ftp://linkedin.com/view/1"))
        self.assertEqual(_office_location_label("Paris 75020"), "Paris (20e)")
        self.assertEqual(_office_location_label("Nanterre (92000)"), "Nanterre (92)")
        self.assertEqual(_office_location_label("75 - PARIS"), "Paris (75)")
        self.assertEqual(_office_location_label("13 - Marseille"), "Marseille (13)")
        self.assertEqual(_office_location_label("Marseille"), "Marseille")
        self.assertIsNone(_office_location_label(""))


if __name__ == "__main__":
    unittest.main()
