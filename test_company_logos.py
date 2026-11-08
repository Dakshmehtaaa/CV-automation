"""Local-only validation of the manifest-backed logo library."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

STUDIO_ROOT = Path(__file__).resolve().parent
if str(STUDIO_ROOT) not in sys.path:
    sys.path.insert(0, str(STUDIO_ROOT))

import tempfile
import unittest

from company_logos import CompanyLogoError, CompanyLogoLibrary


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><path d="M0 0h8v8H0z" fill="#246b50"/></svg>'


def entry(company: str, path: str, content: bytes = SVG, **overrides) -> dict:
    return {"company": company, "categories": ["shortlist"], "path": path, "format": "svg",
            "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
            "verification_status": "verified_safe_transparent_svg", **overrides}


class CompanyLogoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cv-studio-logo-")
        self.root = Path(self.temp.name) / "company-logos"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_manifest(self, rows: list[dict]) -> None:
        (self.root / "manifest.json").write_text(json.dumps(rows), encoding="utf-8")

    def test_catalog_is_allowlisted_and_returns_verified_metadata_only(self):
        logo = self.root / "shortlist" / "example.svg"
        logo.parent.mkdir()
        logo.write_bytes(SVG)
        self.write_manifest([entry("Example Company", "shortlist/example.svg", role_id="a" * 24,
                                   role_ids=["b" * 24, "a" * 24, "invalid-role-id"],
                                   aliases=["Example Corp", "Example Co", "Example Company"]),
                             {"company": "No asset", "path": None, "format": "svg", "verification_status": "missing"}])
        library = CompanyLogoLibrary(self.root)
        catalog = library.catalog()
        self.assertEqual(catalog["total"], 1)
        item = catalog["items"][0]
        self.assertEqual(item["company"], "Example Company")
        self.assertEqual(item["role_ids"], sorted(["a" * 24, "b" * 24]))
        self.assertEqual(item["aliases"], ["Example Company", "Example Corp", "Example Co"])
        self.assertNotIn("source_url", item)
        self.assertNotIn("path", item)
        content, mime, etag = library.asset(item["id"])
        self.assertEqual(content, SVG)
        self.assertEqual(mime, "image/svg+xml; charset=utf-8")
        self.assertEqual(etag, '"' + hashlib.sha256(SVG).hexdigest() + '"')
        with self.assertRaises(CompanyLogoError):
            library.asset("../outside")

    def test_symlink_and_manifest_path_escape_are_omitted(self):
        outside = Path(self.temp.name) / "outside.svg"
        outside.write_bytes(SVG)
        link = self.root / "logos.svg"
        link.symlink_to(outside)
        self.write_manifest([entry("Escaping path", "../outside.svg"), entry("Symlink", "logos.svg")])
        self.assertEqual(CompanyLogoLibrary(self.root).catalog()["total"], 0)

    def test_hash_and_svg_security_are_checked_at_serve_time(self):
        logo = self.root / "safe.svg"
        logo.write_bytes(SVG)
        library = CompanyLogoLibrary(self.root)
        self.write_manifest([entry("Example", "safe.svg")])
        item = library.catalog()["items"][0]
        logo.write_bytes(SVG.replace(b"246b50", b"346b50"))
        with self.assertRaisesRegex(CompanyLogoError, "integrity"):
            library.asset(item["id"])

        unsafe = b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://bad.example/a.png"/></svg>'
        logo.write_bytes(unsafe)
        self.write_manifest([entry("Example", "safe.svg", unsafe)])
        item = library.catalog()["items"][0]
        with self.assertRaisesRegex(CompanyLogoError, "not safe"):
            library.asset(item["id"])

    def test_reviewed_catalog_keeps_palette_transparency_and_plan_a_hint(self):
        source = Path(__file__).parent / "assets" / "company-logos"
        library = CompanyLogoLibrary(source)
        catalog = library.catalog()
        brands = {item["company"]: item for item in catalog["items"]}
        self.assertIn("Plan A", brands)
        self.assertEqual(brands["Plan A"]["render_hint"], "light_ink/use_dark_background")
        self.assertEqual(library.asset(brands["Sphera"]["id"])[1], "image/png")
        self.assertEqual(library.asset(brands["Plan A"]["id"])[1], "image/svg+xml; charset=utf-8")
        pngs = [item for item in catalog["items"] if item["format"] == "png"]
        self.assertGreaterEqual(len(pngs), 12)  # the reviewed catalogue grows as logos are added (29 on 13 Oct)
        for item in pngs:
            with self.subTest(company=item["company"]):
                self.assertEqual(library.asset(item["id"])[1], "image/png")


if __name__ == "__main__":
    unittest.main()
