"""Fixture-only checks for read-only JD insight extraction."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from jd_insights import JDInsights


class JDInsightsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.resources = Path(self.temp.name) / "CV" / "Resources"
        self.resources.mkdir(parents=True)
        self.role = self.resources / "Tailored CVs" / "Example Org - ESG Analyst"
        self.role.mkdir(parents=True)
        self.role_text = (
            "# ESG Analyst\n"
            "Company: Example Org\n"
            "Source URL: https://jobs.example.test/role/1?utm_source=fixture\n\n"
            "Required skills: SQL, Power BI, English C1.\n"
            "Experience: 3+ years of experience.\n\n"
            "## Responsibilities\n"
            "- Analyze Scope 3 emissions in Excel.\n"
            "- Coordinate ESG reporting with teams.\n\n"
            "## Profile\n"
            "- Rigor and collaboration.\n"
        )
        (self.role / "JD.md").write_text(self.role_text, encoding="utf-8")
        self.run = self.resources / "Master" / "auto" / "evidence" / "runs" / "2026-10-01-fixture"

    def tearDown(self):
        self.temp.cleanup()

    def add_capture(self, capture_id: str, body: str, *, captured_at: str | None,
                    status: str = "captured_full_detail", url: str = "https://jobs.example.test/role/2") -> Path:
        folder = self.run / "hunt" / "linkedin" / "captures" / "linkedin" / capture_id
        folder.mkdir(parents=True)
        raw = body.encode("utf-8")
        record = {
            "status": status,
            "site": "linkedin",
            "canonical_url": url,
            "captured_at_utc": captured_at,
            "body_sha256": hashlib.sha256(raw).hexdigest(),
            "paths": {"body_text": "body.txt"},
        }
        (folder / "capture.json").write_text(json.dumps(record), encoding="utf-8")
        (folder / "body.txt").write_bytes(raw)
        (folder / "ad.json").write_text(json.dumps({"title": "Data Analyst", "company": "Capture Co"}), encoding="utf-8")
        return folder

    def terms(self, catalog: dict, category: str) -> dict[str, dict]:
        group = next(row for row in catalog["categories"] if row["id"] == category)
        return {row["term"].casefold(): row for row in group["terms"]}

    def test_catalog_groups_literal_quotes_and_keeps_actual_dates_explicit(self):
        body = "Build a Tableau report with French colleagues; 5 years of experience."
        self.add_capture("linkedin-20261001T120000Z-a", body,
                         captured_at="2026-10-01T12:00:00Z")
        self.add_capture("linkedin-unknown-time-b", "Use Python and English.", captured_at=None,
                         url="https://jobs.example.test/role/3")
        self.add_capture("linkedin-incomplete-c", "not a full advert", captured_at=None,
                         status="incomplete", url="https://jobs.example.test/role/4")

        catalog = JDInsights(self.resources).catalog()
        self.assertEqual(catalog, JDInsights(self.resources).catalog())
        self.assertEqual(catalog["coverage"]["role_jds"], 1)
        self.assertEqual(catalog["coverage"]["full_captures"], 2)
        self.assertEqual(catalog["coverage"]["unique_full_capture_urls"], 2)
        self.assertEqual(catalog["coverage"]["undated_documents"], 2)
        self.assertEqual(catalog["coverage"]["omission_counts"]["omitted_non_full_capture"], 1)

        it = self.terms(catalog, "it")
        self.assertIn("sql", it)
        self.assertIn("power bi", it)
        self.assertIn("tableau", it)
        languages = self.terms(catalog, "languages")
        self.assertIn("english", languages)
        self.assertIn("french", languages)
        self.assertIn("scope 3", self.terms(catalog, "other"))
        self.assertTrue(self.terms(catalog, "tasks"))
        self.assertIn("3+ years of experience", self.terms(catalog, "experience_years"))

        by_source = {row["source"]: row for row in catalog["documents"]}
        for category in catalog["categories"]:
            for term in category["terms"]:
                for hit in term["hits"]:
                    doc = by_source[hit["source"]]
                    source_text = self.role_text if doc["kind"] == "role_jd" else (
                        "Build a Tableau report with French colleagues; 5 years of experience."
                        if doc["source_url"].endswith("/role/2") else "Use Python and English."
                    )
                    self.assertIn(hit["quote"], source_text)
                    self.assertIn("source", hit)
                    self.assertNotIn("proficiency", hit)
        dated = next(row for row in catalog["documents"] if row["source_url"].endswith("/role/2"))
        self.assertEqual(dated["captured_at"], "2026-10-01T12:00:00Z")
        self.assertEqual(dated["date_status"], "known")
        role_doc = next(row for row in catalog["documents"] if row["kind"] == "role_jd")
        self.assertIsNone(role_doc["captured_at"])
        self.assertEqual(role_doc["date_status"], "unknown")
        self.assertEqual(role_doc["source_url"], "https://jobs.example.test/role/1")
        self.assertEqual(role_doc["title"], "ESG Analyst")
        self.assertEqual(role_doc["company"], "Example Org")

    def test_body_hash_mismatch_and_symlink_capture_are_not_read(self):
        folder = self.add_capture("linkedin-hash-mismatch", "Python", captured_at="2026-10-01T12:00:00Z")
        (folder / "capture.json").write_text(json.dumps({
            "status": "captured_full_detail", "site": "linkedin",
            "canonical_url": "https://jobs.example.test/bad", "captured_at_utc": "2026-10-01T12:00:00Z",
            "body_sha256": "0" * 64, "paths": {"body_text": "body.txt"},
        }), encoding="utf-8")

        external = Path(self.temp.name) / "external-body.txt"
        external.write_text("Python", encoding="utf-8")
        symlink_dir = self.run / "hunt" / "hellowork" / "captures" / "hellowork" / "hellowork-link"
        symlink_dir.mkdir(parents=True)
        (symlink_dir / "capture.json").write_text(json.dumps({
            "status": "captured_full_detail", "site": "hellowork",
            "canonical_url": "https://jobs.example.test/link", "captured_at_utc": "2026-10-01T12:00:00Z",
            "body_sha256": hashlib.sha256(b"Python").hexdigest(), "paths": {"body_text": "body.txt"},
        }), encoding="utf-8")
        (symlink_dir / "body.txt").symlink_to(external)

        catalog = JDInsights(self.resources).catalog()
        self.assertEqual(catalog["coverage"]["full_captures"], 0)
        self.assertEqual(catalog["coverage"]["omission_counts"]["omitted_body_hash_mismatch"], 1)
        self.assertGreaterEqual(catalog["coverage"]["omission_counts"]["omitted_symlink"], 1)
        self.assertNotIn("https://jobs.example.test/bad", {row["source_url"] for row in catalog["documents"]})


if __name__ == "__main__":
    unittest.main()
