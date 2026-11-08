"""Fixture-only tests for the local manual job queue; no model or network calls."""
from pathlib import Path
import re
import tempfile
import threading
import unittest

from manual_jobs import ManualJobQueue, ManualJobsError


class ManualJobQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cv-studio-manual-jobs-test-")
        self.local = Path(self.temp.name) / "Studio/.local"
        self.queue = ManualJobQueue(self.local)

    def tearDown(self):
        self.temp.cleanup()

    def test_contact_opt_in_default_off_persisted_and_claimed_once(self):
        off = self.queue.enqueue({"url": "https://jobs.example/off"})["item"]
        self.assertFalse(off["find_contacts"])
        self.assertEqual(off["contact_research"]["status"], "off")
        self.assertIsNone(self.queue.claim_contact_research(off["id"], "a" * 16))
        on = self.queue.enqueue({"url": "https://jobs.example/on", "find_contacts": True})["item"]
        self.assertTrue(ManualJobQueue(self.local).get(on["id"])["find_contacts"])
        self.assertIsNotNone(self.queue.claim_contact_research(on["id"], "b" * 16))
        self.assertIsNone(self.queue.claim_contact_research(on["id"], "c" * 16))
        self.assertTrue(self.queue.snapshot_pending()["items"][1]["item"]["find_contacts"])
        duplicate = self.queue.enqueue({"url": "https://jobs.example/on"})["item"]
        self.assertTrue(duplicate["find_contacts"])
        self.assertEqual(duplicate["contact_research"]["job_id"], "b" * 16)
        with self.assertRaises(ManualJobsError):
            self.queue.enqueue({"url": "https://jobs.example/wrong", "find_contacts": "yes"})

    def test_release_returns_an_unbuilt_job_to_the_queue_for_its_owner_only(self):
        item = self.queue.enqueue({"url": "https://jobs.example/quota"})["item"]
        self.queue.claim(item["id"], "job-1")
        with self.assertRaises(ManualJobsError):
            self.queue.release(item["id"], "job-2", "wrong owner")
        released = self.queue.release(item["id"], "job-1", "Not built yet: quota used up.")
        self.assertEqual(released["status"], "queued")
        self.assertNotIn("claim", released)
        self.assertNotIn("build_job_id", released)
        self.assertEqual(self.queue.claim(item["id"], "job-3")["status"], "building")

    def test_contact_choice_can_turn_off_before_dispatch_without_reopting_in(self):
        on = self.queue.enqueue({"url": "https://jobs.example/choice", "find_contacts": True})["item"]
        off = self.queue.enqueue({"url": "https://jobs.example/choice", "find_contacts": False})["item"]
        self.assertFalse(off["find_contacts"])
        self.assertFalse(self.queue.enqueue({"url": "https://jobs.example/choice"})["item"]["find_contacts"])
        self.assertIsNone(self.queue.claim_contact_research(on["id"], "d" * 16))

    def test_enqueue_persists_without_generation_and_listing_hides_jd(self):
        jd = "Senior data analyst role. " * 40
        result = self.queue.enqueue({"url": "https://jobs.example/role/7", "jd": jd,
                                     "title": "Data Analyst", "company": "Example"})
        item = result["item"]
        self.assertFalse(result["duplicate"])
        self.assertRegex(item["id"], re.compile(r"^[a-f0-9]{16}$"))
        self.assertEqual(item["status"], "queued")
        self.assertEqual(item["source_kind"], "url_and_jd")
        self.assertNotIn("jd", item)
        self.assertNotIn("jd", self.queue.list()["items"][0])

        reopened = ManualJobQueue(self.local)
        saved = reopened.get(item["id"])
        self.assertEqual(saved["jd"], jd.strip())
        self.assertEqual(saved["company"], "Example")
        self.assertEqual(reopened.list()["counts"]["queued"], 1)

    def test_deduplicates_canonical_url_and_normalized_jd(self):
        first = self.queue.enqueue({"url": "https://www.example.com/jobs/42?utm_source=mail", "jd": "Unique exact role."})
        same_url = self.queue.enqueue({"url": "https://example.com/jobs/42#description", "jd": "Different pasted text"})
        same_jd = self.queue.enqueue({"jd": "Unique   exact\n role."})
        self.assertFalse(first["duplicate"])
        self.assertTrue(same_url["duplicate"])
        self.assertTrue(same_jd["duplicate"])
        self.assertEqual(first["item"]["id"], same_url["item"]["id"])
        self.assertEqual(first["item"]["id"], same_jd["item"]["id"])
        self.assertEqual(self.queue.list()["total"], 1)

    def test_requires_url_or_description_and_rejects_credential_urls(self):
        for payload in ({}, {"url": "https://jobs.example/?access_token=secret"},
                        {"url": "https://user:pass@jobs.example/role"}):
            with self.subTest(payload=payload), self.assertRaises(ManualJobsError):
                self.queue.enqueue(payload)
        saved = self.queue.enqueue({"jd": "Pasted job description"})
        self.assertEqual(saved["item"]["source_kind"], "jd")

    def test_snapshot_is_frozen_and_later_queue_items_are_not_included(self):
        first = self.queue.enqueue({"url": "https://jobs.example/one"})["item"]
        snapshot = self.queue.snapshot_pending()
        second = self.queue.enqueue({"url": "https://jobs.example/two"})["item"]
        self.assertEqual([row["id"] for row in snapshot["items"]], [first["id"]])
        self.queue.claim(first["id"], "pipeline-run-1", owner_kind="pipeline")
        self.assertEqual(snapshot["items"][0]["item"]["status"], "queued")
        self.assertEqual(self.queue.get(second["id"])["status"], "queued")

    def test_claim_and_finish_require_the_same_owner(self):
        item = self.queue.enqueue({"jd": "Role description"})["item"]
        claimed = self.queue.claim(item["id"], "job-1")
        self.assertEqual(claimed["status"], "building")
        with self.assertRaises(ManualJobsError):
            self.queue.finish(item["id"], "other-job", "completed", "Incorrect owner")
        finished = self.queue.finish(item["id"], "job-1", "held", "Sol review requested changes.",
                                     audit={"status": "hold"})
        self.assertEqual(finished["status"], "held")
        self.assertEqual(self.queue.list()["counts"]["held"], 1)
        with self.assertRaises(ManualJobsError):
            self.queue.claim(item["id"], "job-2")

    def test_held_jd_can_be_retried_with_new_url_without_rewriting_history(self):
        jd = "Unique held role description and source evidence. " * 20
        original = self.queue.enqueue({"jd": jd})["item"]
        self.queue.claim(original["id"], "job-held")
        held = self.queue.finish(original["id"], "job-held", "held", "URL required.")

        retry = self.queue.enqueue({"url": "https://jobs.example/retry", "jd": jd})
        self.assertFalse(retry["duplicate"])
        self.assertTrue(retry["updated"])
        self.assertEqual(retry["retry_of"], original["id"])
        self.assertEqual(retry["item"]["retry_of"], original["id"])
        self.assertEqual(retry["item"]["status"], "queued")
        self.assertEqual(self.queue.get(original["id"]), held)

        repeated = self.queue.enqueue({"url": "https://jobs.example/retry", "jd": jd})
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(repeated["item"]["id"], retry["item"]["id"])
        self.assertEqual(self.queue.list()["total"], 2)

    def test_building_retry_takes_priority_over_older_held_duplicate(self):
        jd = "Same source text for historical and current jobs. " * 20
        old = self.queue.enqueue({"jd": jd})["item"]
        self.queue.claim(old["id"], "job-old")
        self.queue.finish(old["id"], "job-old", "held", "Old attempt held.")
        retry = self.queue.enqueue({"url": "https://jobs.example/current", "jd": jd})["item"]
        self.queue.claim(retry["id"], "job-current")

        duplicate = self.queue.enqueue({"url": "https://jobs.example/current", "jd": jd})
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["item"]["id"], retry["id"])
        self.assertEqual(duplicate["item"]["status"], "building")
        self.assertEqual(self.queue.get(old["id"])["status"], "held")

    def test_completed_outcomes_do_not_get_requeued_by_duplicate_save(self):
        for terminal in ("completed", "built_pending_approval"):
            item = self.queue.enqueue({"url": "https://jobs.example/" + terminal})["item"]
            self.queue.claim(item["id"], "job-" + terminal)
            self.queue.finish(item["id"], "job-" + terminal, terminal,
                              "Saved outcome for " + terminal)
            duplicate = self.queue.enqueue({"url": "https://jobs.example/" + terminal})
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(duplicate["item"]["id"], item["id"])
            self.assertEqual(self.queue.get(item["id"])["status"], terminal)

    def test_pending_approval_is_counted_separately_from_completed(self):
        item = self.queue.enqueue({"url": "https://jobs.example/pending"})["item"]
        self.queue.claim(item["id"], "job-pending")
        self.queue.finish(item["id"], "job-pending", "built_pending_approval",
                          "Sol audit passed; final pipeline approval is pending.")
        counts = self.queue.list()["counts"]
        self.assertEqual(counts["built_pending_approval"], 1)
        self.assertEqual(counts["completed"], 0)

    def test_concurrent_duplicate_saves_keep_one_item(self):
        results = []
        errors = []

        def save():
            try:
                results.append(self.queue.enqueue({"url": "https://jobs.example/shared"}))
            except Exception as exc:  # surfaced below with the collected fixture error
                errors.append(exc)

        threads = [threading.Thread(target=save) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        self.assertEqual(self.queue.list()["total"], 1)
        self.assertEqual(sum(not result["duplicate"] for result in results), 1)


if __name__ == "__main__":
    unittest.main()
