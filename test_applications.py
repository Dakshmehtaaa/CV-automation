"""Fixture-only tests for local application choices and their history boundary."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import unittest
from unittest.mock import Mock, call, patch

from applications import Applications, safe_application_url
from data import StudioData, StudioDataError
from test_data import make_fixture, PDF


class ApplicationsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cv-studio-apply-test-")
        self.root = make_fixture(Path(self.temporary.name) / "CV")
        self.resources = self.root / "Resources"
        self.data = StudioData(self.root)
        self.apps = Applications(self.data)
        ready = self.root / "Tailored CVs" / "Ready Example"
        ready.mkdir()
        (ready / "JD.md").write_text("# Fixture role\nCompany: Ready Example\nSource URL: https://www.linkedin.com/jobs/view/123456789?trk=fixture\n")
        (ready / "CV_Candidate.pdf").write_bytes(PDF)
        inactive = self.root / "Tailored CVs" / "Already applied" / "_No longer active" / "Inactive Example"
        inactive.mkdir(parents=True)
        (inactive / "JD.md").write_text("# Closed fixture role\nCompany: Inactive Example\nSource: https://example.org/closed/1\n")
        self.by_company = {r["company"]: r for r in self.data.roles()["items"]}

    def tearDown(self):
        self.temporary.cleanup()

    def role(self, company="Ready Example"):
        return self.apps.role(self.by_company[company]["id"])

    @staticmethod
    def rejection_evidence(**overrides):
        evidence = {"source": "gmail", "message_id": "gmail-message-1",
            "thread_id": "gmail-thread-1",
            "received_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
            "company": "Ready Example", "title": "Fixture role",
            "matched_role_company": "Ready Example", "matched_role_title": "Fixture role",
            "identity_basis": "Master/auto/evidence/owner/applications.json",
            "literal_quote": "Thank you for your interest. We have decided to proceed with another candidate."}
        return {**evidence, **overrides}

    @staticmethod
    def reviewed_contact(*, email=True, linkedin=True, name="Alex Martin",
                         title="Talent Acquisition Partner", organization="Ready Example"):
        return {"name": name, "title": title, "organization": organization,
            "email": ({"value": "alex@ready-example.example", "verified": True,
                       "source_url": "https://people.ready-example.test/team",
                       "checked_at": "2026-10-05T09:00:00+00:00"} if email else None),
            "linkedin": ({"url": "https://www.linkedin.com/in/alex-martin", "verified": True,
                          "source_url": "https://people.ready-example.test/team",
                          "checked_at": "2026-10-05T09:00:00+00:00"} if linkedin else None),
            "provider": "workflow", "model_used": "multi-route",
            "found_at": "2026-10-05T09:00:00+00:00", "verified": True,
            "review_status": "approved", "review_model": "gpt-6.1-sol"}

    @staticmethod
    def reviewed_receipt(count=1):
        return {"status": "completed", "actual_model": "gpt-6.1-sol",
                "verified_count": count, "rejected_count": 0}

    def assert_error(self, code, function, *args):
        with self.assertRaises(StudioDataError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_saved_job_contacts_require_original_advert_employer_and_role_proof(self):
        url = "https://careers.example.test/jobs/fixture"
        gate = patch.object(self.apps, "_public_source_url", side_effect=lambda value: value)
        gate.start(); self.addCleanup(gate.stop)
        self.apps.evidence_reader = lambda source: {"text":"Example Corp seeks Data Analyst", "contexts":[]}
        packet = {"source_url":url}
        payload = {"people":[], "job_evidence":{"source_url":url, "company":"Example Corp", "title":"Data Analyst", "quote":"Example Corp seeks Data Analyst"}}
        proof = self.apps.verify_saved_job_contacts(packet, "luna", "gpt-6-luna", payload)
        self.assertEqual(proof["verified_count"], 0)
        payload["job_evidence"]["source_url"] = "https://careers.example.test/other"
        self.assert_error("job_identity_unverified", self.apps.verify_saved_job_contacts, packet, "luna", "gpt-6-luna", payload)
        payload["job_evidence"]["source_url"] = url
        payload["job_evidence"]["company"] = "Unproven Employer"
        self.assert_error("job_identity_unverified", self.apps.verify_saved_job_contacts, packet, "luna", "gpt-6-luna", payload)

    def test_reads_are_side_effect_free_and_do_not_mutate_role_objects(self):
        raw = self.data.roles()
        before = json.dumps(raw, sort_keys=True)
        decorated = self.apps.decorate_list(raw)
        self.apps.decorate(raw["items"][0])
        self.apps.roles()
        self.apps.summary()
        self.role()
        self.assertEqual(json.dumps(raw, sort_keys=True), before)
        self.assertTrue(all("application" in r for r in decorated["items"]))
        self.assertFalse(self.data.local.exists())

    def test_defaults_distinguish_archives_cvs_and_source_only_roles(self):
        applied = self.role("Example Company")["application"]
        self.assertEqual((applied["status"], applied["source"]), ("applied", "archive"))
        self.assertTrue(applied["canonical_applied"])
        self.assertEqual(applied["canonical_source"], "applied_archive")
        self.assertIsNone(applied["applied_at"])
        self.assertIsNone(applied["rejected_at"])
        self.assertEqual(applied["status_history"], [])
        inactive = self.role("Inactive Example")["application"]
        self.assertEqual((inactive["status"], inactive["source"]), ("inactive", "archive"))
        ready = self.role()["application"]
        self.assertEqual((ready["status"], ready["source"]), ("ready", "folder"))
        self.assertFalse(ready["needs_review"])
        pending = self.role("Another Company")["application"]
        self.assertEqual((pending["status"], pending["label"]), ("pending", "Review first"))
        self.assertTrue(pending["needs_review"])
        self.assertIsNone(pending["apply_url"])

    def test_historical_applied_snapshot_is_preserved_outside_an_archive(self):
        role = dict(self.by_company["Ready Example"], applied=True)
        app = self.apps.decorate(role)["application"]
        self.assertEqual(app["status"], "applied")
        self.assertEqual(app["canonical_source"], "tracker_snapshot")
        self.assertTrue(app["canonical_applied"])

    def test_summary_covers_every_role_and_updates_with_local_choices(self):
        counts = self.apps.summary()
        self.assertEqual({k: counts[k] for k in ("ready", "pending", "applied", "inactive", "total")},
                         {"ready": 1, "pending": 1, "applied": 1, "inactive": 1, "total": 4})
        self.assertEqual(counts["canonical_applied"], 1)
        saved = self.apps.set_status(self.by_company["Ready Example"]["id"], "applied")
        self.assertTrue(saved["saved"])
        counts = self.apps.roles()["application_summary"]
        self.assertEqual((counts["ready"], counts["applied"], counts["local_overrides"]), (0, 2, 1))
        self.assertEqual(counts["canonical_applied"], 1)
        self.assertEqual(self.apps.summary(self.data.roles()["items"]), counts)

    def test_save_persists_and_preserves_original_workspace_facts(self):
        before = {p: p.read_bytes() for p in self.resources.rglob("*") if p.is_file()}
        current = self.role()
        saved = self.apps.set_status(current["id"], "applied", current["application"]["revision"])
        self.assertEqual(saved["scope"], "local")
        self.assertEqual(saved["application"]["source"], "local")
        self.assertEqual(saved["application"]["apply_url"], current["source_url"])
        reread = Applications(self.root).role(current["id"])
        self.assertEqual(reread["application"], saved["application"])
        self.assertEqual(Applications(self.resources).role(current["id"])["application"], saved["application"])
        for path, original in before.items():
            self.assertEqual(path.read_bytes(), original)
        self.assertTrue((self.root / current["folder"]).is_dir())
        self.assertIsNone(self.data.role(current["id"])["applied"])

    def test_local_ready_override_never_removes_canonical_applied_evidence(self):
        original = self.role("Example Company")
        saved = self.apps.set_status(original["id"], "ready", original["application"]["revision"])
        role = saved["role"]
        self.assertEqual(role["application"]["status"], "ready")
        self.assertTrue(role["application"]["local_override"])
        self.assertTrue(role["application"]["canonical_applied"])
        self.assertEqual(role["application"]["canonical_status"], "applied")
        self.assertEqual(role["collection"], "applied")
        self.assertTrue(role["applied"])
        self.assertEqual(role["tracker_status"], original["tracker_status"])

    def test_manual_ready_for_source_only_keeps_review_flag(self):
        original = self.role("Another Company")
        result = self.apps.set_status(original["id"], "ready")
        self.assertEqual(result["application"]["canonical_status"], "pending")
        self.assertTrue(result["application"]["needs_review"])
        self.assertFalse(result["role"]["cv_available"])

    def test_every_change_has_a_private_exact_backup(self):
        role_id = self.by_company["Ready Example"]["id"]
        first = self.apps.set_status(role_id, "applied")
        initial_backup = self.resources / first["backup"]
        self.assertEqual(json.loads(initial_backup.read_bytes()), {"version": 1, "applications": {}})
        before_second = self.apps.path.read_bytes()
        second = self.apps.set_status(role_id, "inactive", first["revision"])
        backup = self.resources / second["backup"]
        self.assertEqual(backup.read_bytes(), before_second)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.apps.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.data.local.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(backup.parent.stat().st_mode), 0o700)

    def test_noop_does_not_create_a_status_file_or_backup(self):
        role = self.role()
        result = self.apps.set_status(role["id"], "ready", role["application"]["revision"])
        self.assertFalse(result["saved"])
        self.assertTrue(result["unchanged"])
        self.assertIsNone(result["backup"])
        self.assertFalse(self.apps.path.exists())
        self.assertFalse((self.data.local / "backups").exists())

    def test_status_dates_and_history_follow_explicit_local_changes(self):
        role_id = self.by_company["Ready Example"]["id"]
        applied = self.apps.set_status(role_id, "applied")
        applied_at = applied["application"]["applied_at"]
        self.assertTrue(applied_at)
        self.assertIsNone(applied["application"]["rejected_at"])
        rejected = self.apps.set_status(role_id, "rejected", applied["revision"])
        rejected_at = rejected["application"]["rejected_at"]
        self.assertTrue(rejected_at)
        self.assertEqual(rejected["application"]["applied_at"], applied_at)
        inactive = self.apps.set_status(role_id, "inactive", rejected["revision"])
        application = inactive["application"]
        self.assertEqual((application["applied_at"], application["rejected_at"]),
                         (applied_at, rejected_at))
        self.assertEqual([event["status"] for event in application["status_history"]],
                         ["applied", "rejected", "inactive"])
        self.assertEqual(application["status_history"][0], {"status": "applied", "at": applied_at})
        self.assertEqual(application["status_history"][1], {"status": "rejected", "at": rejected_at})

    def test_reapplying_preserves_first_applied_date_and_same_status_is_a_noop(self):
        role_id = self.by_company["Ready Example"]["id"]
        first = self.apps.set_status(role_id, "applied")
        first_date = first["application"]["applied_at"]
        ready = self.apps.set_status(role_id, "ready", first["revision"])
        reapplied = self.apps.set_status(role_id, "applied", ready["revision"])
        self.assertEqual(reapplied["application"]["applied_at"], first_date)
        self.assertEqual([event["status"] for event in reapplied["application"]["status_history"]],
                         ["applied", "ready", "applied"])
        before = self.apps.path.read_bytes()
        backup_count = len(list((self.data.local / "backups" / "applications").glob("*.json")))
        unchanged = self.apps.set_status(role_id, "applied", reapplied["revision"])
        self.assertFalse(unchanged["saved"])
        self.assertTrue(unchanged["unchanged"])
        self.assertEqual(self.apps.path.read_bytes(), before)
        self.assertEqual(len(list((self.data.local / "backups" / "applications").glob("*.json"))), backup_count)

    def test_status_dates_and_history_survive_a_fresh_applications_reader(self):
        role_id = self.by_company["Ready Example"]["id"]
        applied = self.apps.set_status(role_id, "applied")
        rejected = self.apps.set_status(role_id, "rejected", applied["revision"])
        reopened = Applications(self.root).role(role_id)["application"]
        self.assertEqual(reopened["applied_at"], applied["application"]["applied_at"])
        self.assertEqual(reopened["rejected_at"], rejected["application"]["rejected_at"])
        self.assertEqual(reopened["status_history"], rejected["application"]["status_history"])

    def test_email_rejection_saves_minimal_evidence_and_preserves_applied_history(self):
        role_id = self.by_company["Ready Example"]["id"]
        applied = self.apps.set_status(role_id, "applied")
        applied_at = applied["application"]["applied_at"]
        evidence = self.rejection_evidence()

        saved = self.apps.record_email_rejection(role_id, evidence, applied["revision"])

        self.assertTrue(saved["saved"])
        self.assertEqual(saved["application"]["status"], "rejected")
        self.assertEqual(saved["application"]["applied_at"], applied_at)
        self.assertEqual(saved["application"]["rejected_at"], evidence["received_at"])
        self.assertNotEqual(saved["application"]["updated_at"], evidence["received_at"])
        self.assertEqual(saved["application"]["email_rejections"], [evidence])
        self.assertEqual(saved["application"]["status_history"], [
            {"status": "applied", "at": applied_at},
            {"status": "rejected", "at": evidence["received_at"]},
        ])
        self.assertEqual(self.apps.role(role_id)["application"]["email_rejections"], [evidence])
        stored = json.loads(self.apps.path.read_text())["applications"][role_id]
        self.assertEqual(stored["email_rejections"], [evidence])
        self.assertNotIn("sender", stored)
        self.assertNotIn("body", stored)
        self.assertEqual(json.loads((self.resources / saved["backup"]).read_text())["applications"][role_id]["status"], "applied")

    def test_email_rejection_is_idempotent_by_message_id_and_conflicts_on_changed_evidence(self):
        role = self.role()
        evidence = self.rejection_evidence()
        first = self.apps.record_email_rejection(role["id"], evidence, role["application"]["revision"])
        before = self.apps.path.read_bytes()
        backup_count = len(list((self.data.local / "backups" / "applications").glob("*.json")))

        duplicate = self.apps.record_email_rejection(role["id"], evidence, first["revision"])
        self.assertFalse(duplicate["saved"])
        self.assertTrue(duplicate["unchanged"])
        self.assertIsNone(duplicate["backup"])
        self.assertEqual(self.apps.path.read_bytes(), before)
        self.assertEqual(len(list((self.data.local / "backups" / "applications").glob("*.json"))), backup_count)

        changed = self.rejection_evidence(literal_quote="A different quote for the same message.")
        error = self.assert_error("email_rejection_conflict", self.apps.record_email_rejection,
            role["id"], changed, first["revision"])
        self.assertEqual(error.status, 409)
        self.assertEqual(self.apps.path.read_bytes(), before)

    def test_email_rejection_rejects_stale_revision_without_changing_status(self):
        role = self.role()
        applied = self.apps.set_status(role["id"], "applied", role["application"]["revision"])
        before = self.apps.path.read_bytes()
        error = self.assert_error("stale_revision", self.apps.record_email_rejection,
            role["id"], self.rejection_evidence(), role["application"]["revision"])
        self.assertEqual(error.status, 409)
        self.assertEqual(self.apps.path.read_bytes(), before)
        self.assertEqual(self.apps.role(role["id"])["application"], applied["application"])

    def test_email_rejection_rejects_malformed_private_or_unbounded_evidence(self):
        role = self.role()
        invalid = (
            self.rejection_evidence(received_at="2026-10-07T10:15:00"),
            self.rejection_evidence(received_at="not-a-time"),
            self.rejection_evidence(received_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()),
            self.rejection_evidence(source="imap"),
            self.rejection_evidence(literal_quote="Reject: candidate@example.com"),
            self.rejection_evidence(literal_quote="x" * 1001),
            self.rejection_evidence(identity_basis="../../private/owner-receipt.json"),
            self.rejection_evidence(matched_role_company="Another Company"),
            self.rejection_evidence(matched_role_title="A different role"),
            self.rejection_evidence(raw_mime="private message content"),
        )
        for evidence in invalid:
            with self.subTest(evidence=evidence):
                self.assert_error("invalid_request", self.apps.record_email_rejection,
                    role["id"], evidence, role["application"]["revision"])
        self.assertFalse(self.data.local.exists())

    def test_email_rejection_does_not_overwrite_a_later_positive_state_or_newer_local_update(self):
        role = self.role()
        interviewing = self.apps.set_status(role["id"], "interviewing", role["application"]["revision"])
        current_evidence = self.rejection_evidence()
        error = self.assert_error("rejection_status_conflict", self.apps.record_email_rejection,
            role["id"], current_evidence, interviewing["revision"])
        self.assertEqual(error.status, 409)
        self.assertEqual(self.apps.role(role["id"])["application"]["status"], "interviewing")

        inactive_role = self.role("Another Company")
        inactive = self.apps.set_status(inactive_role["id"], "inactive", inactive_role["application"]["revision"])
        old_email = self.rejection_evidence(received_at="2020-01-01T10:15:00+02:00",
            company="Another Company", title="ESG associate",
            matched_role_company="Another Company", matched_role_title="ESG associate")
        error = self.assert_error("rejection_status_conflict", self.apps.record_email_rejection,
            inactive_role["id"], old_email, inactive["revision"])
        self.assertEqual(error.status, 409)
        self.assertEqual(self.apps.role(inactive_role["id"])["application"]["status"], "inactive")

    def test_legacy_updated_at_is_read_only_fallback_and_becomes_initial_history(self):
        role_id = self.by_company["Ready Example"]["id"]
        legacy_date = "2026-09-18T09:10:11+02:00"
        legacy = {"version": 1, "applications": {role_id: {
            "status": "applied", "updated_at": legacy_date, "revision": 7,
            "kept_future_field": {"value": "preserve"}}}}
        self.data.local.mkdir(parents=True)
        original = self.apps._encode(legacy)
        self.apps.path.write_bytes(original)
        read = self.apps.role(role_id)["application"]
        self.assertEqual(read["applied_at"], legacy_date)
        self.assertIsNone(read["rejected_at"])
        self.assertEqual(read["status_history"], [])
        self.assertEqual(self.apps.path.read_bytes(), original)

        changed = self.apps.set_status(role_id, "inactive", read["revision"])
        saved = json.loads(self.apps.path.read_text())["applications"][role_id]
        self.assertEqual(saved["applied_at"], legacy_date)
        self.assertEqual(saved["status_history"][0], {"status": "applied", "at": legacy_date})
        self.assertEqual(saved["status_history"][1]["status"], "inactive")
        self.assertEqual(saved["kept_future_field"], {"value": "preserve"})
        self.assertEqual(changed["application"]["applied_at"], legacy_date)

    def test_invalid_optional_dates_and_history_preserve_store_bytes(self):
        role_id = self.by_company["Ready Example"]["id"]
        valid_updated = "2026-10-01T09:00:00+00:00"
        valid_event = {"status": "applied", "at": valid_updated}
        invalid_records = []
        for field, value in (("applied_at", 123), ("rejected_at", None),
                             ("applied_at", "2026-10-01T09:00:00"),
                             ("rejected_at", "not-a-date")):
            record = {"status": "ready", "updated_at": valid_updated, "revision": 1, field: value}
            invalid_records.append(record)
        invalid_records.extend((
            {"status": "ready", "updated_at": valid_updated, "revision": 1, "status_history": "bad"},
            {"status": "ready", "updated_at": valid_updated, "revision": 1,
             "status_history": [{"status": "applied", "at": "2026-10-01T09:00:00"}]},
            {"status": "ready", "updated_at": valid_updated, "revision": 1,
             "status_history": [{"status": "pending", "at": valid_updated},
                                {"status": "pending", "at": valid_updated}]},
            {"status": "ready", "updated_at": valid_updated, "revision": 1,
             "status_history": [valid_event] * 1001},
        ))
        self.data.local.mkdir(parents=True)
        for record in invalid_records:
            with self.subTest(record=record):
                content = self.apps._encode({"version": 1, "applications": {role_id: record}})
                self.apps.path.write_bytes(content)
                self.assert_error("invalid_application_store", self.apps.roles)
                self.assert_error("invalid_application_store", self.apps.set_status, role_id, "applied")
                self.assertEqual(self.apps.path.read_bytes(), content)

    def test_invalid_status_unknown_role_and_bad_revision_fail_before_writes(self):
        for status in ("unknown", "pending", "Applied", "", None, True):
            self.assert_error("invalid_request", self.apps.set_status, self.by_company["Ready Example"]["id"], status)
        for role_id in ("../PROFILE.md", "f" * 24, None):
            error = self.assert_error("not_found", self.apps.set_status, role_id, "applied")
            self.assertEqual(error.status, 404)
        self.assert_error("invalid_request", self.apps.set_status, self.by_company["Ready Example"]["id"], "applied", 1)
        self.assertFalse(self.data.local.exists())

    def test_stale_revision_never_overwrites_newer_status(self):
        role = self.role()
        saved = self.apps.set_status(role["id"], "applied", role["application"]["revision"])
        before = self.apps.path.read_bytes()
        error = self.assert_error("stale_revision", self.apps.set_status, role["id"], "inactive", role["application"]["revision"])
        self.assertEqual(error.status, 409)
        self.assertEqual(self.apps.path.read_bytes(), before)
        self.assertEqual(self.role()["application"], saved["application"])

    def test_saving_another_role_does_not_invalidate_its_revision(self):
        role = self.role()
        self.apps.set_status(self.by_company["Another Company"]["id"], "inactive")
        self.assertTrue(self.apps.set_status(role["id"], "applied", role["application"]["revision"])["saved"])

    def test_concurrent_stale_saves_have_only_one_winner(self):
        role = self.role()
        start = threading.Barrier(2)

        def save(status):
            independent = Applications(self.root)
            start.wait(timeout=5)
            try:
                return independent.set_status(role["id"], status, role["application"]["revision"])["saved"]
            except StudioDataError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(save, ("applied", "inactive")))
        self.assertCountEqual(outcomes, [True, "stale_revision"])
        records = json.loads(self.apps.path.read_text())["applications"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[role["id"]]["revision"], 1)

    def test_concurrent_independent_saves_do_not_lose_statuses(self):
        roles = [self.role(company) for company in self.by_company]
        start = threading.Barrier(len(roles))

        def save(role):
            status = "inactive" if role["application"]["status"] != "inactive" else "applied"
            start.wait(timeout=5)
            return Applications(self.root).set_status(role["id"], status, role["application"]["revision"])

        with ThreadPoolExecutor(max_workers=len(roles)) as pool:
            self.assertTrue(all(r["saved"] for r in pool.map(save, roles)))
        self.assertEqual(set(json.loads(self.apps.path.read_text())["applications"]), {r["id"] for r in roles})

    def test_failed_atomic_replace_preserves_saved_bytes_and_cleans_temporary(self):
        role = self.role()
        saved = self.apps.set_status(role["id"], "applied")
        before = self.apps.path.read_bytes()
        replace = os.replace

        def fail_store_replace(source, destination):
            if Path(destination) == self.apps.path:
                raise OSError("Fixture write failure")
            return replace(source, destination)

        with patch("data.os.replace", side_effect=fail_store_replace):
            self.assert_error("application_store_unavailable", self.apps.set_status, role["id"], "inactive", saved["revision"])
        self.assertEqual(self.apps.path.read_bytes(), before)
        self.assertEqual(list(self.data.local.glob(".applications.json.*.tmp")), [])
        self.assertEqual(self.role()["application"]["status"], "applied")

    def test_corrupt_store_is_preserved_and_never_silently_replaced(self):
        self.data.local.mkdir(parents=True)
        for content in (b'{"version":', b'{}', b'{"version":1,"applications":[]}',
                        json.dumps({"version": 1, "applications": {"a" * 24: {"status": "pending"}}}).encode()):
            self.apps.path.write_bytes(content)
            self.assert_error("invalid_application_store", self.apps.roles)
            self.assert_error("invalid_application_store", self.apps.set_status, self.by_company["Ready Example"]["id"], "applied")
            self.assertEqual(self.apps.path.read_bytes(), content)

    def test_symlink_store_and_backup_cannot_redirect_writes(self):
        self.data.local.mkdir(parents=True)
        unrelated = self.data.local / "unrelated.json"
        unrelated.write_bytes(b"original")
        self.apps.path.symlink_to(unrelated)
        self.assert_error("unsafe_path", self.apps.set_status, self.by_company["Ready Example"]["id"], "applied")
        self.assertEqual(unrelated.read_bytes(), b"original")
        self.apps.path.unlink()
        (self.data.local / "backups").symlink_to(self.resources, target_is_directory=True)
        self.assert_error("unsafe_path", self.apps.set_status, self.by_company["Ready Example"]["id"], "applied")
        self.assertFalse(self.apps.path.exists())

    def test_symlink_local_directory_cannot_redirect_reads_or_writes(self):
        studio = self.resources / "Studio"
        studio.mkdir()
        self.data.local.symlink_to(self.resources, target_is_directory=True)
        self.assert_error("unsafe_path", self.apps.roles)
        self.assert_error("unsafe_path", self.apps.set_status, self.by_company["Ready Example"]["id"], "applied")
        self.assertFalse((self.resources / "applications.json").exists())

    def test_motivation_parser_stops_before_sources_and_keeps_languages_separate(self):
        french = ("UPEC associe formation, recherche et partenaires pour répondre aux défis de société. "
                  "Ce poste relie achats responsables, sensibilisation et plans d’action, avec une forte "
                  "coordination entre métiers. Je souhaite contribuer à ces actions en m’appuyant sur une "
                  "pratique du reporting carbone et du travail collectif.")
        english = ("I am interested in joining Ready Example because this role connects responsible "
                   "purchasing, carbon reporting and practical work with different teams. My previous "
                   "experience helped me organize sustainability data and build clear reporting processes. "
                   "I would like to bring that careful, collaborative approach to the company’s work and "
                   "learn from colleagues who turn plans into measurable day-to-day progress.")
        doc = ("# Why this company\n\n" + french + "\n\n## English\n\n" + english
               + "\n\n## Sources\n\n- Profile evidence: not copy text\nhttps://example.org/source\n")
        parsed = Applications.parse_motivation_document(doc)
        self.assertEqual(parsed["fr"], french)
        self.assertEqual(parsed["en"], english)
        self.assertNotIn("Profile evidence", parsed["fr"] + parsed["en"])
        self.assertNotIn("example.org", parsed["fr"] + parsed["en"])

    def test_generated_motivation_records_luna_high_fast_and_validates_local_readback(self):
        role = self.role()
        packet = self.apps.motivation_packet(role["id"], "fr")
        text = ("Je souhaite rejoindre Ready Example pour contribuer aux missions de ce poste d’analyste. "
                "Ce rôle m’intéresse par son approche concrète et la possibilité de travailler avec les équipes "
                "sur des projets utiles. Je serais heureux d’apprendre auprès de votre équipe et d’apporter une "
                "contribution sérieuse, adaptée aux priorités de l’entreprise. Je souhaite échanger sur la manière "
                "dont mes expériences peuvent soutenir vos objectifs, avec humilité et curiosité, dans un cadre "
                "de travail collectif et ouvert aux apprentissages.")
        saved = self.apps.save_generated_motivation(packet, text, job_id="f" * 16, provider="luna",
            model_used="gpt-6-luna", effort="high", actual_effort=None,
            requested_service_tier="priority", actual_service_tier=None)
        self.assertTrue(saved["saved"])
        self.assertEqual(saved["text"], text)
        self.assertEqual(saved["provenance"]["requested_effort"], "high")
        self.assertIsNone(saved["provenance"]["actual_effort"])
        self.assertEqual(saved["provenance"]["requested_service_tier"], "priority")
        self.assertIsNone(saved["provenance"]["actual_service_tier"])
        self.assertEqual(self.apps.motivation(role["id"], "fr")["text"], text)
        with self.assertRaises(StudioDataError):
            self.apps.save_generated_motivation(packet, text, job_id="e" * 16, provider="luna",
                model_used="gpt-6-luna", effort="max", actual_effort="max",
                requested_service_tier="priority", actual_service_tier="priority")

    def test_saved_why_document_is_language_checked_and_contact_packet_is_role_only(self):
        role = self.role()
        folder = self.root / role["folder"]
        text = ("# Why this company\n\nUPEC associe formation, recherche et partenaires pour répondre "
                "aux défis de société. Ce poste relie suivi carbone, achats responsables, sensibilisation "
                "et plans d’action, avec une forte coordination entre métiers. Je souhaite contribuer à "
                "ces actions en m’appuyant sur une pratique du reporting et du travail collectif.\n\n"
                "## Sources\n- Evidence: internal note\nhttps://example.org/source\n")
        (folder / "why-this-company.md").write_text(text)
        fresh = Applications(StudioData(self.root))
        saved = fresh.motivation(role["id"], "fr")
        self.assertEqual(saved["status"], "ready")
        self.assertEqual(saved["source"], "why-this-company.md")
        self.assertIn("UPEC associe", saved["text"])
        self.assertNotIn("Sources", saved["text"])
        self.assertNotIn("example.org", saved["text"])
        packet = fresh.contact_packet(role["id"])
        self.assertEqual(packet["role_id"], role["id"])
        self.assertEqual(packet["company"], role["company"])
        self.assertIn("Fixture role", packet["jd"])
        self.assertNotIn("profile_evidence", packet)
        self.assertNotIn("PROFILE", packet)

    def test_contacts_need_exact_source_text_and_linkedin_link_before_verified(self):
        role = self.role()
        source_url = "https://people.ready-example.test/team"
        linkedin_url = "https://www.linkedin.com/in/alex-martin"
        quote = "Ready Example — Alex Martin — Talent Acquisition Partner — alex@ready-example.example"
        page = {"url": source_url, "title": "Ready Example team", "text": quote,
                "links": ["mailto:alex@ready-example.example", linkedin_url],
                "contexts": [{"tag": "article", "text": quote, "identity_frame": True,
                              "links": ["mailto:alex@ready-example.example", linkedin_url]}]}
        self.apps.evidence_reader = lambda _url: page
        person = {"name": "Alex Martin", "title": "Talent Acquisition Partner", "organization": "Ready Example",
                  "email": "alex@ready-example.example", "email_evidence": {"source_url": source_url, "quote": quote},
                  "linkedin_url": linkedin_url, "linkedin_evidence": {"source_url": source_url, "quote": quote}}
        with patch.object(self.apps, "_public_source_url", return_value=source_url):
            saved = self.apps.save_contacts(role["id"], "a" * 16, "luna", "gpt-6-luna", {"people": [person]})
        self.assertEqual(saved["verified_count"], 1)
        self.assertTrue(saved["contacts"][0]["email"]["verified"])
        self.assertTrue(saved["contacts"][0]["linkedin"]["verified"])
        self.assertEqual(saved["contacts"][0]["email"]["source_name"], "Ready Example team")
        self.assertEqual(self.apps.contacts(role["id"])["contacts"], saved["contacts"])

    def test_official_company_service_email_is_saved_with_literal_page_proof(self):
        role = self.role()
        source_url = "https://www.ready-example.example/en/customer-service"
        email = "service@ready-example.example"
        quote = "Ready Example — Customer Service — service@ready-example.example"
        page = {"url": source_url, "title": "Ready Example customer service",
            "text": quote, "links": ["mailto:" + email],
            "contexts": [{"tag": "section", "text": quote, "identity_frame": True,
                          "links": ["mailto:" + email]}]}
        self.apps.evidence_reader = lambda _url: page
        payload = {"company_emails": [{"label": "Customer Service", "email": email,
                    "email_evidence": {"source_url": source_url, "quote": quote}}]}
        with patch.object(self.apps, "_public_source_url", return_value=source_url):
            saved = self.apps.save_contacts(role["id"], "9" * 16, "luna", "gpt-6-luna", payload)
        self.assertEqual(saved["verified_count"], 1)
        contact = saved["contacts"][0]
        self.assertEqual(contact["recipient_type"], "company_service")
        self.assertEqual(contact["name"], "Customer Service")
        self.assertEqual(contact["organization"], "Ready Example")
        self.assertTrue(contact["email"]["verified"])
        self.assertEqual(contact["email"]["value"], email)
        self.assertEqual(contact["email"]["source_kind"], "official_company_contact")
        self.assertEqual(saved["review"]["actual_model"], "deterministic-public-source-check-v1")
        reopened = Applications(self.root).contacts(role["id"])
        self.assertEqual(reopened["contacts"], saved["contacts"])

    def test_company_service_email_rejects_guesses_wrong_domains_and_unrelated_labels(self):
        role = self.role()
        source_url = "https://directory.example/ready-example/contact"
        quote = "Ready Example — Customer Service — service@ready-example.example"
        self.apps.evidence_reader = lambda _url: {"url": source_url, "title": "Ready Example contact",
            "text": quote, "links": ["mailto:service@ready-example.example"],
            "contexts": [{"tag": "address", "text": quote, "identity_frame": True,
                          "links": ["mailto:service@ready-example.example"]}]}
        payload = {"people": [], "company_emails": [
            {"label": "Customer Service", "email": "service@ready-example.example",
             "email_evidence": {"source_url": source_url, "quote": quote}},
            {"label": "General contact", "email": "info@ready-example.example",
             "email_evidence": {"source_url": source_url,
                "quote": "Ready Example — General contact — info@ready-example.example"}},
        ]}
        with patch.object(self.apps, "_public_source_url", return_value=source_url):
            proof = self.apps.verify_contacts(role["id"], "luna", "gpt-6-luna", payload)
        self.assertEqual(proof["verified_count"], 0)
        self.assertEqual(proof["rejected_count"], 2)
        self.assertEqual(proof["verified_contacts"], [])

    def test_contact_results_reject_non_luna_model_route(self):
        role = self.role()
        self.assert_error("route_mismatch", self.apps.verify_contacts,
            role["id"], "google", "gemini-3.8-flash-high", {"people": []})

    def test_new_result_without_email_preserves_the_previously_saved_verified_email(self):
        role = self.role()
        first = self.apps.save_reviewed_contacts(role["id"], "1" * 16,
            [self.reviewed_contact()], review=self.reviewed_receipt(),
            provider="workflow", model_used="multi-route")
        self.assertEqual(first["total"], 1)
        new_result = self.reviewed_contact(email=False)
        second = self.apps.save_reviewed_contacts(role["id"], "2" * 16,
            [new_result], review=self.reviewed_receipt(),
            provider="workflow", model_used="multi-route")
        self.assertEqual(second["total"], 1)
        self.assertEqual(second["contacts"][0]["email"]["value"], "alex@ready-example.example")
        self.assertEqual(second["contacts"][0]["job_id"], "2" * 16)
        reopened = Applications(self.root).contacts(role["id"])
        self.assertEqual(reopened["contacts"][0]["email"]["value"], "alex@ready-example.example")

    def test_empty_successful_search_is_durable_and_blocks_duplicate_search(self):
        role = self.role()
        jobs = {"items": [{"id": "f" * 16, "kind": "application_contacts",
                            "role_id": role["id"], "status": "failed",
                            "summary": "A failed attempt can be retried."}]}
        self.assertFalse(self.apps.contact_search_completed(role["id"], jobs))
        saved = self.apps.save_contacts(role["id"], "3" * 16, "luna", "gpt-6-luna",
            {"people": [], "company_emails": []})
        self.assertEqual(saved["contacts"], [])
        self.assertTrue(self.apps.contacts(role["id"])["search_completed"])
        self.assertTrue(self.apps.contact_search_completed(role["id"], jobs))
        store = json.loads(self.apps.contacts_path.read_text())
        self.assertTrue(store["searches"][role["id"]]["search_completed"])
        self.assertEqual(store["searches"][role["id"]]["job_id"], "3" * 16)
        self.assertEqual(store["jobs"]["3" * 16]["provider"], "luna")
        self.assertEqual(store["jobs"]["3" * 16]["model_used"], "gpt-6-luna")
        self.assertEqual(store["jobs"]["3" * 16]["review"]["actual_model"],
            "deterministic-public-source-check-v1")

    def test_contacts_catalog_exposes_active_job_before_results_are_saved(self):
        role = self.role()
        active_states = ("queued", "running", "waiting_input", "stopping")
        for index, status in enumerate(active_states):
            with self.subTest(status=status):
                job = {"id": str(index + 1) * 16, "kind": "application_contacts",
                    "role_id": role["id"], "provider": "luna", "model": "gpt-6-luna",
                    "status": status, "summary": f"Fixture contact job is {status}.",
                    "started_at": f"2026-10-05T09:0{index}:00+00:00"}
                catalog = self.apps.contacts_catalog({"items": [job]})
                self.assertEqual(catalog["total_contacts"], 0)
                row = next(item for item in catalog["items"] if item["role_id"] == role["id"])
                self.assertEqual(row["company"], "Ready Example")
                self.assertEqual(row["contacts"], [])
                self.assertFalse(row["search_completed"])
                self.assertEqual(row["last_job"]["id"], job["id"])
                self.assertEqual(row["last_job"]["status"], status)
                self.assertEqual(row["last_job"]["summary"], job["summary"])
                self.assertEqual(row["last_job"]["created_at"], job["started_at"])
                self.assertFalse(row["last_job"]["search_completed"])

    def test_contacts_catalog_aggregates_roles_and_counts_saved_contacts(self):
        role = self.role()
        empty_role = self.role("Another Company")
        self.apps.save_reviewed_contacts(role["id"], "4" * 16,
            [self.reviewed_contact()], review=self.reviewed_receipt(),
            provider="workflow", model_used="multi-route")
        self.apps.save_reviewed_contacts(empty_role["id"], "5" * 16, [],
            review=self.reviewed_receipt(0), provider="workflow", model_used="multi-route")
        catalog = self.apps.contacts_catalog()
        by_id = {item["role_id"]: item for item in catalog["items"]}
        self.assertEqual(catalog["total_contacts"], 1)
        self.assertEqual(by_id[role["id"]]["company"], "Ready Example")
        self.assertEqual(by_id[role["id"]]["title"], role["title"])
        self.assertEqual(len(by_id[role["id"]]["contacts"]), 1)
        self.assertTrue(by_id[empty_role["id"]]["search_completed"])
        self.assertEqual(by_id[empty_role["id"]]["contacts"], [])

    def test_contacts_recover_only_approved_records_from_backups_and_completed_job_results(self):
        backup_role = self.role()
        result_role = self.role("Another Company")
        backup_job_id = "6" * 16
        backup_contact = self.reviewed_contact()
        backup_contact["job_id"] = backup_job_id
        rejected_job_id = "7" * 16
        rejected_contact = self.reviewed_contact(name="Unapproved Person")
        rejected_contact["email"]["value"] = "unapproved@ready-example.example"
        rejected_contact["job_id"] = rejected_job_id
        backup = {"version": 1,
            "roles": {backup_role["id"]: [backup_contact, rejected_contact]},
            "jobs": {
                backup_job_id: {"role_id": backup_role["id"], "verified_count": 1,
                    "review": self.reviewed_receipt()},
                rejected_job_id: {"role_id": backup_role["id"], "verified_count": 1,
                    "review": {"status": "failed", "actual_model": "gpt-6.1-sol"}},
            }, "searches": {}}
        backup_dir = self.data.local / "backups" / "application-contacts"
        backup_dir.mkdir(parents=True)
        (backup_dir / "20261005T090000000000Z-fixture.json").write_text(json.dumps(backup))
        self.data.local.mkdir(parents=True, exist_ok=True)
        current = {"version": 1, "roles": {backup_role["id"]: []}, "jobs": {}, "searches": {}}
        self.apps.contacts_path.write_bytes(self.apps._encode_json(current))
        current_bytes = self.apps.contacts_path.read_bytes()

        result_job_id = "8" * 16
        result_contact = self.reviewed_contact(name="Jordan Jones", email=False,
            title="People Partner", organization="Another Company")
        result_contact["linkedin"]["url"] = "https://www.linkedin.com/in/jordan-jones"
        result_contact["job_id"] = result_job_id
        jobs = {"items": [{"id": result_job_id, "kind": "application_contacts",
            "role_id": result_role["id"], "status": "completed",
            "result": {"contacts": [result_contact], "review": self.reviewed_receipt(),
                       "verified_count": 1, "summary": "Approved fixture result."}}]}

        recovered_backup = self.apps.contacts(backup_role["id"])
        recovered_result = self.apps.contacts(result_role["id"], jobs)
        self.assertEqual([item["name"] for item in recovered_backup["contacts"]], ["Alex Martin"])
        self.assertEqual(recovered_backup["contacts"][0]["email"]["value"], "alex@ready-example.example")
        self.assertNotIn("unapproved@ready-example.example",
                         json.dumps(recovered_backup["contacts"]))
        self.assertEqual(recovered_result["contacts"][0]["name"], "Jordan Jones")
        self.assertTrue(recovered_result["search_completed"])
        self.assertEqual(self.apps.contacts_path.read_bytes(), current_bytes)

    def test_guessed_email_or_unmatched_person_is_never_marked_verified(self):
        role = self.role()
        source_url = "https://people.ready-example.test/team"
        quote = "Ready Example — Alex Martin — Talent Acquisition Partner — alex@ready-example.example"
        self.apps.evidence_reader = lambda _url: {"url": source_url, "title": "Ready Example team",
            "text": "Ready Example — Alex Martin — Talent Acquisition Partner", "links": [],
            "contexts": [{"tag": "article", "text": "Ready Example — Alex Martin — Talent Acquisition Partner",
                          "links": [], "identity_frame": True}]}
        person = {"name": "Alex Martin", "title": "Talent Acquisition Partner", "organization": "Ready Example",
                  "email": "alex@ready-example.example", "email_evidence": {"source_url": source_url, "quote": quote},
                  "linkedin_url": None, "linkedin_evidence": None}
        with patch.object(self.apps, "_public_source_url", return_value=source_url):
            saved = self.apps.save_contacts(role["id"], "b" * 16, "luna", "gpt-6-luna", {"people": [person]})
        self.assertEqual(saved["verified_count"], 0)
        self.assertEqual(saved["contacts"], [])

    def test_contact_fields_must_share_quote_and_local_person_context(self):
        role = self.role()
        source_url = "https://people.ready-example.test/team"
        linkedin_url = "https://www.linkedin.com/in/alex-martin"
        quote = "Alex Martin — Talent Acquisition Partner — alex@ready-example.example"
        # Company elsewhere on the page is not evidence this person works there.
        self.apps.evidence_reader = lambda _url: {"url": source_url, "title": "Ready Example team",
            "text": "Ready Example Footer Alex Martin Talent Acquisition Partner alex@ready-example.example",
            "links": ["mailto:alex@ready-example.example", linkedin_url],
            "contexts": [
                {"tag": "article", "text": quote, "links": ["mailto:alex@ready-example.example"], "identity_frame": True},
                {"tag": "footer", "text": "Ready Example careers", "links": [linkedin_url]},
            ]}
        person = {"name": "Alex Martin", "title": "Talent Acquisition Partner", "organization": "Ready Example",
                  "email": "alex@ready-example.example", "email_evidence": {"source_url": source_url, "quote": quote},
                  "linkedin_url": linkedin_url, "linkedin_evidence": {"source_url": source_url, "quote": quote}}
        with patch.object(self.apps, "_public_source_url", return_value=source_url):
            saved = self.apps.save_contacts(role["id"], "c" * 16, "luna", "gpt-6-luna", {"people": [person]})
        self.assertEqual(saved["verified_count"], 0)
        self.assertEqual(saved["contacts"], [])

    def test_company_on_another_page_section_does_not_support_the_person(self):
        role = self.role()
        source_url = "https://people.ready-example.test/team"
        quote = "Alex Martin — Talent Acquisition Partner — alex@ready-example.example"
        self.apps.evidence_reader = lambda _url: {"url": source_url, "title": "Ready Example team",
            "text": "Ready Example — Alex Martin — Talent Acquisition Partner — alex@ready-example.example",
            "links": ["mailto:alex@ready-example.example"],
            "contexts": [
                {"tag": "article", "text": "Alex Martin — Talent Acquisition Partner — alex@ready-example.example",
                 "links": ["mailto:alex@ready-example.example"]},
                {"tag": "footer", "text": "Ready Example company careers", "links": []},
            ]}
        person = {"name": "Alex Martin", "title": "Talent Acquisition Partner", "organization": "Ready Example",
                  "email": "alex@ready-example.example", "email_evidence": {"source_url": source_url, "quote": quote}}
        with patch.object(self.apps, "_public_source_url", return_value=source_url):
            saved = self.apps.save_contacts(role["id"], "d" * 16, "luna", "gpt-6-luna", {"people": [person]})
        self.assertEqual(saved["verified_count"], 0)
        self.assertEqual(saved["contacts"], [])

    def test_company_in_same_bounded_person_context_can_support_shorter_quote(self):
        role = self.role()
        source_url = "https://people.ready-example.test/team"
        quote = "Alex Martin — Talent Acquisition Partner — alex@ready-example.example"
        context = "Ready Example — Alex Martin — Talent Acquisition Partner — alex@ready-example.example"
        self.apps.evidence_reader = lambda _url: {"url": source_url, "title": "Ready Example team",
            "text": context, "links": ["mailto:alex@ready-example.example"],
            "contexts": [{"tag": "article", "text": context, "links": ["mailto:alex@ready-example.example"],
                          "identity_frame": True}]}
        person = {"name": "Alex Martin", "title": "Talent Acquisition Partner", "organization": "Ready Example",
                  "email": "alex@ready-example.example", "email_evidence": {"source_url": source_url, "quote": quote}}
        with patch.object(self.apps, "_public_source_url", return_value=source_url):
            saved = self.apps.save_contacts(role["id"], "e" * 16, "luna", "gpt-6-luna", {"people": [person]})
        self.assertEqual(saved["verified_count"], 1)
        self.assertTrue(saved["contacts"][0]["email"]["verified"])

    def test_html_parser_keeps_profile_links_inside_person_context(self):
        from applications import _PublicPage

        parser = _PublicPage()
        parser.feed('''<main><article class="person-card"><h2>Alex Martin</h2>
            <p>Talent Acquisition Partner, Ready Example</p>
            <a href="https://www.linkedin.com/in/alex-martin">LinkedIn profile</a></article>
            <footer><a href="https://www.linkedin.com/in/footer-profile">LinkedIn</a></footer></main>''')
        parser.close()
        person_context = next(row for row in parser.contexts if "alex-martin" in " ".join(row["links"]))
        self.assertIn("Alex Martin", person_context["text"])
        self.assertIn("Ready Example", person_context["text"])
        self.assertNotIn("footer-profile", " ".join(person_context["links"]))

    def test_linkedin_link_cannot_bubble_between_sibling_person_cards(self):
        from applications import _PublicPage

        role = self.role()
        source_url = "https://people.ready-example.test/team"
        alex_url = "https://www.linkedin.com/in/alex-martin"
        bob_url = "https://www.linkedin.com/in/bob-jones"
        alex_quote = "Alex Martin — Talent Acquisition Partner at Ready Example — LinkedIn profile"
        bob_quote = "Bob Jones — Engineering Manager at Ready Example — LinkedIn profile"
        parser = _PublicPage()
        parser.feed(f'''<main><article>
          <div class="person-card"><p>{alex_quote}</p><a href="{alex_url}">LinkedIn profile</a></div>
          <div class="person-card"><p>{bob_quote}</p><a href="{bob_url}">LinkedIn profile</a></div>
        </article></main>''')
        parser.close()
        page = {"url": source_url, "title": "Ready Example team", "text": " ".join(parser.text),
                "links": parser.links, "contexts": parser.contexts}
        self.apps.evidence_reader = lambda _url: page
        mismatched = {"name": "Alex Martin", "title": "Talent Acquisition Partner",
                      "organization": "Ready Example", "linkedin_url": bob_url,
                      "linkedin_evidence": {"source_url": source_url, "quote": alex_quote}}
        valid = {**mismatched, "linkedin_url": alex_url}
        with patch.object(self.apps, "_public_source_url", return_value=source_url):
            self.assertIsNone(self.apps._verified_contact_field(bob_url, mismatched["linkedin_evidence"],
                kind="linkedin", name=mismatched["name"], title=mismatched["title"],
                company="Ready Example", cache={}))
            proof = self.apps._verified_contact_field(alex_url, valid["linkedin_evidence"],
                kind="linkedin", name=valid["name"], title=valid["title"], company="Ready Example", cache={})
        self.assertTrue(proof["verified"])
        self.assertEqual(len(page["contexts"]), 2)
        self.assertEqual(page["contexts"][0]["links"], [alex_url])
        self.assertEqual(page["contexts"][1]["links"], [bob_url])

    def test_generic_article_with_multiple_profiles_is_not_a_person_context(self):
        from applications import _PublicPage

        parser = _PublicPage()
        parser.feed('''<article><div>Alex Martin — Talent Acquisition Partner at Ready Example
          <a href="https://www.linkedin.com/in/alex-martin">LinkedIn</a></div>
          <div>Bob Jones — Engineering Manager at Ready Example
          <a href="https://www.linkedin.com/in/bob-jones">LinkedIn</a></div></article>''')
        parser.close()
        self.assertEqual(parser.contexts, [])

    def test_public_fetch_pins_checked_address_and_does_not_resolve_hostname_again(self):
        response = Mock()
        response.status = 200
        response.getheader.side_effect = lambda key: {
            "Content-Type": "text/html; charset=utf-8", "Content-Length": "78"}.get(key)
        response.read.return_value = b"<article class='person-card'>Ready Example Alex Martin</article>"
        response.close.return_value = None
        connection = Mock()
        connection.getresponse.return_value = response
        sockaddr = ("93.184.216.34", 443)
        resolved = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)
        with patch("applications.socket.getaddrinfo", return_value=[resolved]) as resolver, \
             patch("applications._PinnedHTTPSConnection", return_value=connection) as pinned:
            page = Applications._fetch_public_page("https://people.ready-example.test/team")
        resolver.assert_called_once_with("people.ready-example.test", 443, type=socket.SOCK_STREAM)
        pinned.assert_called_once_with("people.ready-example.test", (socket.AF_INET, socket.SOCK_STREAM,
                                    socket.IPPROTO_TCP, sockaddr), 443, timeout=8)
        connection.request.assert_called_once()
        self.assertIn("Ready Example Alex Martin", page["text"])

    def test_public_fetch_rejects_private_or_mixed_dns_answers_before_connecting(self):
        with patch("applications.socket.getaddrinfo", return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))]), \
             patch("applications._PinnedHTTPSConnection") as pinned:
            with self.assertRaisesRegex(ValueError, "non-public"):
                Applications._fetch_public_page("https://people.ready-example.test/team")
        pinned.assert_not_called()

    def test_public_fetch_revalidates_and_pins_each_redirect_target(self):
        first_response = Mock()
        first_response.status = 302
        first_response.getheader.side_effect = lambda key: "https://public-cdn.example/team" if key == "Location" else None
        second_response = Mock()
        second_response.status = 200
        second_response.getheader.side_effect = lambda key: {
            "Content-Type": "text/plain", "Content-Length": "17"}.get(key)
        second_response.read.return_value = b"Fixture team page"
        first_connection, second_connection = Mock(), Mock()
        first_connection.getresponse.return_value = first_response
        second_connection.getresponse.return_value = second_response
        first = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))
        second = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", 443))
        with patch("applications.socket.getaddrinfo", side_effect=[[first], [second]]) as resolver, \
             patch("applications._PinnedHTTPSConnection", side_effect=[first_connection, second_connection]) as pinned:
            page = Applications._fetch_public_page("https://people.ready-example.test/team")
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual([item.args[0] for item in pinned.call_args_list],
                         ["people.ready-example.test", "public-cdn.example"])
        self.assertEqual(page["url"], "https://public-cdn.example/team")
        first_response.close.assert_called_once()
        second_response.close.assert_called_once()

    def test_public_fetch_rejects_private_redirect_before_second_connection(self):
        response = Mock()
        response.status = 302
        response.getheader.side_effect = lambda key: "https://127.0.0.1/private" if key == "Location" else None
        connection = Mock()
        connection.getresponse.return_value = response
        public = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))
        with patch("applications.socket.getaddrinfo", return_value=[public]), \
             patch("applications._PinnedHTTPSConnection", return_value=connection) as pinned:
            with self.assertRaisesRegex(ValueError, "Direct IP"):
                Applications._fetch_public_page("https://people.ready-example.test/team")
        self.assertEqual(pinned.call_count, 1)
        response.close.assert_called_once()

    def test_status_options_include_new_local_stages_without_making_pending_writable(self):
        options = {row["id"] for row in self.apps.status_options()}
        self.assertTrue({"saved", "contacting", "interviewing", "offer", "rejected", "withdrawn"}.issubset(options))
        self.assertNotIn("pending", options)


class ApplicationURLTests(unittest.TestCase):
    def test_safe_link_is_exact_and_never_fabricated(self):
        for url in ("https://www.linkedin.com/jobs/view/123?trk=original#details",
                    "https://example.org/jobs/42?ref=source&keyword=CSR%20analyst", "http://example.org/job?id=42"):
            self.assertEqual(safe_application_url(url), url)
        for value in (None, 12, "", "Original advert pending", "See https://example.org/jobs/1"):
            self.assertIsNone(safe_application_url(value))

    def test_unsafe_protocols_credentials_and_malformed_urls_are_rejected(self):
        for value in ("javascript:alert(1)", "javascript:https://example.org/jobs/1", "data:text/html,x",
                      "file:///etc/passwd", "ftp://example.org/jobs/1", "//example.org/job", "https:///job",
                      "https://user:secret@example.org/jobs/1", "https://user@example.org/job", "https://example.org:abc/job",
                      "https://example.org:999999/job", "https://[broken/job", "https://example.org\\@evil.org/job",
                      "https://example.org/job\n", "https://example.org/%0d%0aCookie:x", "https://example.org/%5cevil",
                      "https://example.org/job?access_token=secret", "https://example.org/job?PASSWORD=secret",
                      "https://example.org/job#token=secret", "https://example.org/job?api-key=secret"):
            with self.subTest(value=value):
                self.assertIsNone(safe_application_url(value))

    def test_unsafe_role_source_never_reaches_the_apply_link(self):
        with tempfile.TemporaryDirectory(prefix="cv-studio-apply-url-test-") as temporary:
            root = make_fixture(Path(temporary) / "CV")
            apps = Applications(root)
            raw = apps.data.roles()["items"][0]
            raw["source_url"] = "https://person:password@example.org/job"
            self.assertIsNone(apps.decorate(raw)["application"]["apply_url"])


if __name__ == "__main__":
    unittest.main()
