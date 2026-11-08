"""Exercise the real embedded Prospector against isolated fictional data."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from prospector_fixture import make_prospector_fixture
import test_server as studio_tests


class ProspectIntegrationTests(unittest.TestCase):
    setUp = studio_tests.HTTPTests.setUp
    tearDown = studio_tests.HTTPTests.tearDown
    request = studio_tests.HTTPTests.request

    def prepare(self):
        self.bundle = make_prospector_fixture(self.root)
        self.environment = patch.dict(os.environ, {"PROSPECTOR_OFFLINE": "1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_real_assets_namespace_settings_write_and_record_preservation(self):
        self.prepare()
        self.assertIsNone(self.server.prospector.process)
        self.assertEqual(self.request("/api/health")[0], 200)
        self.assertIsNone(self.server.prospector.process)
        status, headers, body = self.request("/prospect")
        self.assertEqual(status, 308)
        self.assertEqual(headers["Location"], "/prospect/")
        for path in ("/prospect/", "/prospect/styles.css", "/prospect/js/main.js",
                     "/prospect/js/api.js", "/prospect/api/catalog"):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200, (path, body))
            self.assertEqual(headers["X-Frame-Options"], "SAMEORIGIN")
            self.assertIn("frame-ancestors 'self'", headers["Content-Security-Policy"])
        process = self.server.prospector.process
        status, _, body = self.request("/prospect/api/config/settings")
        settings = json.loads(body)
        self.assertEqual(status, 200)
        settings["followup_days"] = 12
        self.assertEqual(self.request("/prospect/api/config/settings", "PUT", settings)[0], 403)
        self.assertEqual(self.request("/prospect/api/config/settings", "PUT", settings,
                                     {"X-Prospector": "1", "Origin": self.server.origin})[0], 200)
        saved = json.loads((self.bundle / "config/settings.json").read_text())
        self.assertEqual(saved["followup_days"], 12)
        self.assertEqual(self.request("/api/health")[0], 200)
        self.assertIs(self.server.prospector.process, process)
        self.assertTrue((self.bundle / "data/leads/fixture-expired.json").is_file())
        self.server.prospector.close()
        self.assertIsNotNone(process.poll())

    def test_proxy_security_and_unsupported_requests_do_not_start_a_service(self):
        for path in ("/prospect/", "/prospect/api/state"):
            for headers in ({"Host": "evil.test"}, {"Origin": "http://evil.test"},
                            {"Sec-Fetch-Site": "cross-site"}):
                self.assertEqual(self.request(path, headers=headers)[0], 403)
        for verb in ("PUT", "PATCH", "DELETE", "POST"):
            self.assertEqual(self.request("/prospect/api/config/settings", verb, {})[0], 403)
        self.assertEqual(self.request("/prospect/api/state", headers={
            "Content-Length": "1048577"})[0], 413)
        self.assertEqual(self.request("/prospect/api/state", headers={
            "Content-Length": "-1"})[0], 400)
        self.assertIsNone(self.server.prospector.process)

    def test_delete_patch_query_routing_and_existing_studio_guard(self):
        self.prepare()
        (self.bundle / "data/candidates").mkdir()
        candidate = self.bundle / "data/candidates/fictional.json"
        candidate.write_text('{"id":"fictional","mode":"job","name":"Fixture"}')
        headers = {"X-Prospector": "1", "Origin": self.server.origin}
        status, _, body = self.request("/prospect/api/candidates?mode=job")
        self.assertEqual(status, 200, body)
        self.assertIn(b"fictional", body)
        self.assertEqual(self.request("/prospect/api/leads/missing", "PATCH", {}, headers)[0], 404)
        self.assertEqual(self.request("/prospect/api/candidates/fictional", "DELETE",
                                     headers=headers)[0], 200)
        self.assertFalse(candidate.exists())
        self.assertEqual(self.request("/api/settings", "PUT", {}, headers)[0], 403)
        status, _, body = self.request("/prospect/../private.txt")
        self.assertEqual(status, 404)
        self.assertNotIn(b"must not escape", body)

    def test_missing_bundle_is_a_retryable_service_error(self):
        status, _, body = self.request("/prospect/")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"]["code"], "prospector_unavailable")

    def test_prospect_errors_never_log_named_record_ids(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.request("/prospect/api/leads/fictional-private-name")
        self.assertIn("/prospect/", output.getvalue())
        self.assertNotIn("fictional-private-name", output.getvalue())

    def test_second_writer_is_refused_and_original_service_stays_healthy(self):
        from prospector import ProspectorService
        self.prepare()
        self.assertEqual(self.request("/prospect/api/catalog")[0], 200)
        original = self.server.prospector.process
        second = ProspectorService(self.root)
        try:
            with self.assertRaises(RuntimeError):
                second.ensure_started()
            self.assertEqual(self.request("/prospect/api/catalog")[0], 200)
            self.assertIsNone(original.poll())
        finally:
            second.close()

    def test_service_releases_ownership_after_parent_crash(self):
        self.prepare()
        code = ("from prospector import ProspectorService; import json,os,sys; "
                "service=ProspectorService(sys.argv[1]); "
                "print(json.dumps({'port':service.ensure_started()}),flush=True); "
                "os._exit(0)")
        result = subprocess.run([sys.executable, "-c", code, str(self.root)],
                                cwd=Path(__file__).parent, capture_output=True, text=True,
                                timeout=10, check=True)
        port = json.loads(result.stdout)["port"]
        stopped = False
        for _ in range(40):
            try:
                connection = socket.create_connection(("127.0.0.1", port), timeout=0.2)
                connection.close()
            except OSError:
                stopped = True
                break
            time.sleep(0.1)
        self.assertTrue(stopped, "Child must release its socket after Studio crashes")
        self.assertEqual(self.request("/prospect/api/catalog")[0], 200)


if __name__ == "__main__":
    unittest.main()
