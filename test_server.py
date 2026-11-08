"""HTTP boundaries tested against isolated fixtures, never production jobs."""
import http.client
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from applications import Applications
from company_logos import CompanyLogoLibrary
from data import StudioData
from runtime import JobManager
from server import StudioHTTPServer
from test_data import make_fixture


class FixtureData:
    def __init__(self, root):
        self.root = root
        self.settings_value = {"theme": "light", "density": "comfortable", "refresh_seconds": 15}
        self.saved = []
        self.pdf = root / "fixture.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\nfixture only\n%%EOF")

    def overview(self):
        return {"workspace": {"name": "Fixture"}, "pipeline": {}, "roles": {"items": [], "total": 0}}

    def active_pipeline(self):
        return {"active": False, "reason": "Fixture idle"}

    def roles(self):
        return {"items": [], "total": 0}

    def documents(self):
        return {"items": [{"id": "search", "title": "Search brief"}], "total": 1}

    def document(self, key):
        if key != "search":
            raise KeyError(key)
        return {"id": key, "content": "fixture", "revision": "r1"}

    def save_document(self, key, content, revision):
        self.saved.append((key, content, revision))
        return {"id": key, "content": content, "revision": "r2"}

    def settings(self):
        return self.settings_value

    def save_settings(self, payload):
        self.settings_value.update(payload)
        return self.settings_value

    def asset_path(self, key):
        if key != "fixture":
            raise KeyError(key)
        return self.pdf


class FixtureJobs:
    def __init__(self):
        self.items = []

    def capabilities(self):
        return {"pipeline": {"available": False, "reason": "Fixture only"}}

    def list(self):
        return {"items": self.items, "total": len(self.items)}

    def start(self, kind):
        if kind != "check":
            raise ValueError("Unknown job kind")
        job = {"id": "fixture-job", "status": "running", "kind": kind}
        self.items.append(job)
        return job

    def manual_jobs(self):
        return {"items": [], "total": 0, "counts": {"queued": 0}}

    def enqueue_manual_job(self, payload):
        self.manual_payload = payload
        return {"item": {"id": "a" * 16, "status": "queued"}, "duplicate": False}

    def start_manual_build(self, ident):
        self.manual_build_id = ident
        return {"id": "b" * 16, "kind": "manual_build", "status": "queued"}


class FixtureApplications:
    def __init__(self, data):
        self.data = data

    def roles(self):
        return self.data.roles()

    def summary(self):
        return {"total": 0, "ready": 0, "applied": 0, "inactive": 0, "pending": 0}

    def contacts_catalog(self, jobs):
        return {"items": [], "total_contacts": 0}


class FixtureHistory:
    def runs(self):
        return {"items": [], "total": 0}

    def run(self, ident):
        raise KeyError(ident)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = FixtureData(self.root)
        self.jobs = FixtureJobs()
        self.logo_root = self.root / "company-logos"
        self.logo_root.mkdir()
        logo = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><path d="M0 0h8v8H0z" fill="#246b50"/></svg>'
        (self.logo_root / "example.svg").write_bytes(logo)
        (self.logo_root / "manifest.json").write_text(json.dumps([{
            "company": "Example Company", "categories": ["shortlist"], "path": "example.svg",
            "format": "svg", "bytes": len(logo), "sha256": hashlib.sha256(logo).hexdigest(),
            "verification_status": "verified_safe_transparent_svg",
        }]), encoding="utf-8")
        self.server = StudioHTTPServer(("127.0.0.1", 0), self.root, data=self.data, jobs=self.jobs,
                                       applications=FixtureApplications(self.data), history=FixtureHistory(),
                                       company_logos=CompanyLogoLibrary(self.logo_root))
        static = self.root / "static"
        static.mkdir()
        (static / "index.html").write_text("<!doctype html><title>Fixture</title>")
        (self.root / "private.txt").write_text("must not escape")
        self.server.static_dir = static
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, path, method="GET", payload=None, headers=None, authenticated=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        hdr = dict(headers or {})
        if authenticated:
            hdr.update({"Origin": self.server.origin, "X-Studio-Token": self.server.token})
        body = None
        if payload is not None:
            body = json.dumps(payload)
            hdr.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=body, headers=hdr)
        response = conn.getresponse()
        result = (response.status, dict(response.getheaders()), response.read())
        conn.close()
        return result

    def test_overview_and_health_are_live_and_private(self):
        status, headers, body = self.request("/api/overview")
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertEqual(value["token"], self.server.token)
        self.assertFalse(value["runtime"]["active_pipeline"]["active"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, _, body = self.request("/api/health")
        self.assertEqual(json.loads(body)["app"], "cv-studio")
        self.assertNotIn(self.server.token, body.decode())

    def test_manual_queue_and_contacts_routes_are_local(self):
        for route in ("/api/manual-jobs", "/api/contacts", "/api/jd-insights"):
            status, headers, body = self.request(route)
            self.assertEqual(status, 200, body)
            self.assertEqual(headers["Cache-Control"], "no-store")
        payload = {"url": "https://example.com/job/1", "jd": "", "title": "Analyst", "company": "Example"}
        status, _, _ = self.request("/api/manual-jobs", "POST", payload)
        self.assertEqual(status, 403)
        status, _, body = self.request("/api/manual-jobs", "POST", payload, authenticated=True)
        self.assertEqual(status, 201, body)
        self.assertEqual(self.jobs.manual_payload, payload)
        status, _, body = self.request("/api/manual-jobs/" + "a" * 16 + "/build", "POST", {}, authenticated=True)
        self.assertEqual(status, 202, body)
        self.assertEqual(self.jobs.manual_build_id, "a" * 16)
        self.assertEqual(json.loads(body)["job"]["kind"], "manual_build")
        status, _, _ = self.request("/api/manual-jobs/" + "a" * 16 + "/build", "POST", {"model": "other"}, authenticated=True)
        self.assertEqual(status, 400)

    def test_company_logo_routes_serve_only_verified_local_assets(self):
        status, headers, body = self.request("/api/company-logos")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "private, max-age=300")
        catalog = json.loads(body)
        self.assertEqual(catalog["total"], 1)
        item = catalog["items"][0]
        self.assertEqual(item["company"], "Example Company")
        self.assertNotIn("path", item)
        self.assertNotIn("source_url", item)

        status, headers, content = self.request(item["url"])
        self.assertEqual(status, 200)
        self.assertEqual(content, (self.logo_root / "example.svg").read_bytes())
        self.assertEqual(headers["Content-Type"], "image/svg+xml; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "private, max-age=86400")
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        status, cached_headers, cached_body = self.request(item["url"], headers={"If-None-Match": headers["ETag"]})
        self.assertEqual((status, cached_body), (304, b""))
        self.assertEqual(cached_headers["ETag"], headers["ETag"])
        self.assertEqual(self.request("/api/company-logos/../private.txt")[0], 404)

    def test_foreign_origin_and_dns_rebinding_rejected(self):
        self.assertEqual(self.request("/api/overview", headers={"Origin": "https://foreign.example"})[0], 403)
        self.assertEqual(self.request("/api/overview", headers={"Host": "foreign.example"})[0], 403)
        self.assertEqual(self.request("/api/overview", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_mutations_require_token_and_origin(self):
        self.assertEqual(self.request("/api/settings", "PUT", {"theme": "dark"})[0], 403)
        self.assertEqual(self.data.settings()["theme"], "light")
        self.assertEqual(self.request("/api/settings", "PUT", {"theme": "dark"}, authenticated=True)[0], 200)
        self.assertEqual(self.data.settings()["theme"], "dark")

    def test_file_or_link_navigation_only_allows_static_document_entry(self):
        navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                      "Sec-Fetch-Dest": "document"}
        self.assertEqual(self.request("/", headers=navigation)[0], 200)
        self.assertEqual(self.request("/index.html", "HEAD", headers=navigation)[0], 200)
        self.assertEqual(self.request("/api/overview", headers=navigation)[0], 403)
        self.assertEqual(self.request("/", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request("/", headers={**navigation, "Sec-Fetch-Dest": "iframe"})[0], 403)
        self.assertEqual(self.request("/api/settings", "PUT", {"theme": "dark"},
                                      headers=navigation, authenticated=True)[0], 403)

    def test_path_escape_and_unregistered_assets_not_served(self):
        self.assertEqual(self.request("/%2e%2e/private.txt")[0], 404)
        self.assertEqual(self.request("/.secret")[0], 404)
        self.assertEqual(self.request("/api/assets?key=../private.txt")[0], 404)
        (self.server.static_dir / "escape.txt").symlink_to(self.root / "private.txt")
        self.assertEqual(self.request("/escape.txt")[0], 404)

    def test_pdf_preview_ranges_and_download(self):
        status, headers, body = self.request("/api/assets?key=fixture", headers={"Range": "bytes=0-7"})
        self.assertEqual((status, body), (206, b"%PDF-1.4"))
        self.assertEqual(headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertEqual(self.request("/api/assets?key=fixture", headers={"Range": "bytes=1000-2000"})[0], 416)
        self.assertEqual(self.request("/api/assets?key=fixture", headers={"Range": "bytes=-0"})[0], 416)
        self.assertIn("attachment", self.request("/api/assets?key=fixture&download=1")[1]["Content-Disposition"])

    def test_active_studio_job_blocks_document_mutation(self):
        self.assertEqual(self.request("/api/jobs", "POST", {"kind": "check"}, authenticated=True)[0], 202)
        status, _, _ = self.request("/api/documents/search", "PUT", {"content": "change", "revision": "r1"}, authenticated=True)
        self.assertEqual(status, 409)
        self.assertEqual(self.data.saved, [])

    def test_document_save_delivers_revision(self):
        status, _, body = self.request("/api/documents/search", "PUT", {"content": "new text", "revision": "r1"}, authenticated=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["revision"], "r2")
        self.assertEqual(self.data.saved, [("search", "new text", "r1")])

    def test_unknown_actions_cannot_execute_commands(self):
        status, _, _ = self.request("/api/jobs", "POST", {"kind": "shell", "command": "touch nope"}, authenticated=True)
        self.assertEqual(status, 400)
        self.assertEqual(self.jobs.items, [])
        self.assertEqual(self.request("/api/anything", "POST", {}, authenticated=True)[0], 404)


class LocalApplyHTTPTests(unittest.TestCase):
    """Exercise real HTTP routing with local data and fixture-only task results."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cv-studio-apply-http-")
        self.root = make_fixture(Path(self.tmp.name) / "CV")
        self.data = StudioData(self.root)
        source_url = "https://example.org/team"
        quote = "Alex Martin, Talent Partner at Example Company, alex.martin@example.org"
        self.apps = Applications(self.data, evidence_reader=lambda _url: {
            "title": "Example Company team page", "text": quote,
            "links": ["mailto:alex.martin@example.org", "https://www.linkedin.com/in/alex-martin-fixture"],
            "contexts": [{"tag": "article", "text": quote, "identity_frame": True,
                          "links": ["mailto:alex.martin@example.org", "https://www.linkedin.com/in/alex-martin-fixture"]}],
        })
        # The fixture URL is safe by construction; no DNS or network source is consulted.
        self.apps._public_source_url = lambda value: value if value == source_url else (_ for _ in ()).throw(ValueError("unexpected fixture URL"))

        motivation = (
            "Je souhaite rejoindre Example Company pour contribuer aux missions de ce poste de Sustainability analyst. "
            "Ce rôle m’intéresse par son approche concrète et la possibilité de travailler avec les équipes sur des projets utiles. "
            "Je serais heureux d’apprendre auprès de votre équipe et d’apporter une contribution sérieuse, adaptée aux priorités de l’entreprise. "
            "Je souhaite échanger sur la manière dont mes expériences peuvent soutenir vos objectifs, avec humilité et curiosité, dans un cadre de travail collectif."
        )

        def fixture_runner(_manager, job, _control, _packet):
            if job["kind"] == "application_motivation":
                return {"payload": {"text": motivation}, "model_used": "gpt-6.1-sol",
                        "requested_effort": "low", "actual_effort": None,
                        "requested_service_tier": "priority", "actual_service_tier": None}
            if job.get("application_mode") == "review":
                return {"payload": {"decisions": [{"candidate_id": candidate["candidate_id"],
                            "decision": "approve", "reason": "The public role evidence fits this saved job."}
                            for candidate in _packet["candidates"]]},
                        "model_used": "gpt-6.1-sol", "actual_model": "gpt-6.1-sol", "actual_effort": None}
            return {"payload": {"people": [{
                "name": "Alex Martin", "title": "Talent Partner", "organization": "Example Company",
                "email": "alex.martin@example.org",
                "email_evidence": {"source_url": source_url, "quote": quote},
                "linkedin_url": "https://www.linkedin.com/in/alex-martin-fixture",
                "linkedin_evidence": {"source_url": source_url, "quote": quote},
            }]}, "model_used": "gpt-6.1-sol", "requested_effort": "high", "actual_effort": None}

        self.jobs = JobManager(self.root, self.data, application_runner=fixture_runner)
        self.roles = {role["company"]: role for role in self.data.roles()["items"]}
        self.role_id = self.roles["Example Company"]["id"]
        self.server = StudioHTTPServer(("127.0.0.1", 0), self.root, data=self.data, jobs=self.jobs,
                                       applications=self.apps, history=FixtureHistory())
        static = self.root / "static"
        static.mkdir()
        (static / "index.html").write_text("<!doctype html><title>Fixture</title>")
        self.server.static_dir = static
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.jobs.close()
        self.tmp.cleanup()

    def request(self, path, method="GET", payload=None, authenticated=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        headers = {}
        if authenticated:
            headers.update({"Origin": self.server.origin, "X-Studio-Token": self.server.token})
        body = None if payload is None else json.dumps(payload)
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        status, value = response.status, response.read()
        conn.close()
        return status, json.loads(value) if value else {}

    def wait_job(self, job_id, *, contacts=False):
        path = f"/api/contact-jobs/{job_id}" if contacts else f"/api/jobs/{job_id}"
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, job = self.request(path)
            self.assertEqual(status, 200)
            if job.get("status") in {"completed", "failed", "interrupted", "stopped"} and job.get("finished_at"):
                return job
            time.sleep(.02)
        self.fail("Fixture application task did not finish: " + job_id)

    def test_apply_http_endpoints_return_saved_preview_and_verified_fixture_contacts(self):
        status, options = self.request("/api/apply-options")
        self.assertEqual(status, 200)
        self.assertIn("contacting", {row["id"] for row in options["statuses"]})
        self.assertEqual(options["contact_discovery"]["provider"], "sol")
        self.assertEqual(options["contact_discovery"]["model"], "gpt-6.1-sol")

        path = f"/api/applications/{self.role_id}"
        status, missing = self.request(path + "/motivation?language=fr")
        self.assertEqual((status, missing["status"]), (200, "missing"))
        status, started = self.request(path + "/motivation", "POST", {"language": "fr"}, authenticated=True)
        self.assertEqual(status, 202)
        motivation_job = self.wait_job(started["job_id"])
        self.assertEqual(motivation_job["status"], "completed", motivation_job.get("summary"))
        self.assertEqual(motivation_job["requested_effort"], "low")
        self.assertEqual(motivation_job["requested_service_tier"], "priority")
        self.assertIsNone(motivation_job["actual_effort"])
        status, preview = self.request(path + "/motivation?language=fr")
        self.assertEqual((status, preview["status"], preview["saved"]), (200, "ready", True))
        self.assertIn("Example Company", preview["text"])

        status, started = self.request(path + "/contacts", "POST", {"provider": "luna"}, authenticated=True)
        self.assertEqual(status, 202)
        contacts_job = self.wait_job(started["job_id"], contacts=True)
        self.assertEqual(contacts_job["status"], "completed")
        self.assertEqual(contacts_job["requested_model"], "gpt-6.1-sol")
        self.assertEqual(contacts_job["requested_effort"], "high")
        self.assertNotIn("contact_workflow", contacts_job)
        status, contacts = self.request(path + "/contacts")
        self.assertEqual(status, 200)
        self.assertEqual(contacts["total"], 1)
        self.assertTrue(contacts["contacts"][0]["email"]["verified"])
        self.assertTrue(contacts["contacts"][0]["linkedin"]["verified"])
        self.assertEqual(contacts["job"]["id"], started["job_id"])
        self.assertTrue(contacts["search_completed"])
        total_jobs = self.jobs.list()["total"]
        self.assertEqual(self.request(path + "/contacts", "POST", {"provider": "workflow"}, authenticated=True)[0], 400)
        status, saved = self.request(path + "/contacts", "POST", {"provider": "luna"}, authenticated=True)
        self.assertEqual(status, 200)
        self.assertEqual(saved["contacts"], contacts["contacts"])
        self.assertEqual(self.jobs.list()["total"], total_jobs, "Completed searches must not dispatch again")
        status, catalog = self.request("/api/contacts")
        self.assertEqual(status, 200)
        self.assertEqual(catalog["total_contacts"], 1)
        self.assertEqual(catalog["items"][0]["role_id"], self.role_id)

    def test_apply_generation_requires_exact_mutation_body_and_local_token(self):
        path = f"/api/applications/{self.role_id}/motivation"
        self.assertEqual(self.request(path, "POST", {"language": "fr"})[0], 403)
        self.assertEqual(self.request(path, "POST", {"language": "fr", "text": "invented"}, authenticated=True)[0], 400)
        self.assertEqual(self.jobs.list()["total"], 0)


if __name__ == "__main__":
    unittest.main()
