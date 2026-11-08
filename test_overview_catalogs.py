"""Opt-in startup catalogs, using temporary data and no production service."""
from __future__ import annotations

import copy
import http.client
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from applications import Applications
from data import StudioData
from history import RunHistory
from server import StudioHTTPServer
from test_data import make_fixture, write_json
import test_requested_document_aliases as alias_fixtures
from test_server import FixtureJobs


class CatalogJobs(FixtureJobs):
    def run_settings(self):
        return {"model": "fixture-only", "schedule": "fixture-only", "read_only": True}


class OverviewCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="studio-catalog-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = make_fixture(Path(self.temp.name) / "CV")
        self.data = StudioData(self.root)
        self.apps = Applications(self.data)
        self.history = RunHistory(self.data)
        self.jobs = CatalogJobs()
        self.process_patch = patch.object(StudioData, "_active_process", return_value=None)
        self.process_patch.start()
        self.addCleanup(self.process_patch.stop)
        self.server = StudioHTTPServer(("127.0.0.1", 0), self.root, data=self.data,
                                       applications=self.apps, history=self.history, jobs=self.jobs)
        # The separately tested archive worker adds no useful work to these
        # catalog tests. Hold a known fixture snapshot for deterministic reads.
        self.server.analytics_snapshot = {"fixture": True}
        self.server.analytics_checked_at = time.monotonic()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path="/api/overview", *, catalogs=False, method="GET", payload=None,
                headers=None, authenticated=False):
        headers = dict(headers or {})
        if catalogs:
            headers["X-Studio-Catalogs"] = "1"
        if authenticated:
            headers.update({"Origin": self.server.origin, "X-Studio-Token": self.server.token})
        body = None
        if payload is not None:
            body = json.dumps(payload)
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    def overview(self):
        status, _, result = self.request(catalogs=True)
        self.assertEqual(status, 200, result)
        return result

    def test_header_reuses_single_source_scans_and_normal_catalogs(self):
        with patch.object(self.data, "roles", wraps=self.data.roles) as role_scan, \
                patch.object(self.data, "runs", wraps=self.data.runs) as run_scan, \
                patch.object(self.apps, "decorate_list", wraps=self.apps.decorate_list) as decorate, \
                patch.object(self.history, "runs", wraps=self.history.runs) as histories:
            status, headers, result = self.request(catalogs=True)
        self.assertEqual(status, 200, result)
        self.assertEqual(role_scan.call_count, 1)
        self.assertEqual(run_scan.call_count, 1)
        self.assertEqual(decorate.call_count, 1)
        self.assertEqual(histories.call_count, 1)
        self.assertEqual(histories.call_args.kwargs["inventory"]["total"], 2)
        catalogs = result["catalogs"]
        self.assertEqual(catalogs["version"], 1)
        self.assertEqual(catalogs["roles"], self.apps.roles())
        self.assertEqual(catalogs["runs"], self.history.runs())
        self.assertEqual(catalogs["documents"], self.data.documents())
        self.assertEqual(catalogs["settings"], {**self.data.settings(), "run": self.jobs.run_settings()})
        self.assertEqual(result["application_summary"], catalogs["roles"]["application_summary"])
        self.assertEqual(result["token"], self.server.token)
        self.assertEqual(headers["Cache-Control"], "no-store")
        for key in ("roles", "runs", "recent_roles", "recent_runs", "documents", "settings"):
            self.assertNotIn(key, result)
        for key in ("latest_run", "pipeline", "active_pipeline", "live_pipeline", "runtime", "metrics",
                    "dashboard_analytics", "tracker", "master_cvs", "connections"):
            self.assertIn(key, result)

    def test_query_opt_in_matches_header_and_unknown_flags_keep_default(self):
        status, _, query = self.request("/api/overview?catalogs=1")
        self.assertEqual(status, 200, query)
        self.assertEqual(query["catalogs"], self.overview()["catalogs"])
        for path, headers in (("/api/overview?catalogs=0", {}),
                              ("/api/overview?catalogs=true", {}),
                              ("/api/overview", {"X-Studio-Catalogs": "0"})):
            with self.subTest(path=path, headers=headers):
                status, _, result = self.request(path, headers=headers)
                self.assertEqual(status, 200, result)
                self.assertNotIn("catalogs", result)
                self.assertIn("roles", result)

    def test_default_response_retains_legacy_raw_contract(self):
        with patch.object(self.history, "runs", wraps=self.history.runs) as histories:
            status, _, result = self.request()
        self.assertEqual(status, 200, result)
        self.assertEqual(histories.call_count, 0)
        self.assertNotIn("catalogs", result)
        for key in ("roles", "runs", "recent_roles", "recent_runs", "documents", "settings"):
            self.assertIn(key, result)
        self.assertEqual(result["roles"], self.data.roles())
        self.assertTrue(all("application" not in row for row in result["roles"]["items"]))
        self.assertEqual(result["runs"], self.data.runs())
        self.assertEqual(result["recent_roles"], result["roles"]["items"][:6])
        self.assertEqual(result["recent_runs"], result["runs"]["items"][:6])
        self.assertEqual(result["settings"], self.data.settings())
        self.assertEqual(result["application_summary"], self.apps.summary(result["roles"]))

    def test_status_updates_are_fresh_and_stale_revisions_still_fail(self):
        before = next(row for row in self.overview()["catalogs"]["roles"]["items"]
                      if row["company"] == "Another Company")
        old_revision = before["application"]["revision"]
        status, _, saved = self.request("/api/applications/" + before["id"], method="PUT",
                                        payload={"status": "applied", "revision": old_revision}, authenticated=True)
        self.assertEqual(status, 200, saved)
        after = next(row for row in self.overview()["catalogs"]["roles"]["items"] if row["id"] == before["id"])
        self.assertEqual(after["application"], self.apps.role(before["id"])["application"])
        self.assertEqual(after["application"]["status"], "applied")
        self.assertIsNotNone(after["application"]["applied_at"])
        self.assertNotEqual(after["application"]["revision"], old_revision)
        store = self.data.local / "applications.json"
        saved_bytes = store.read_bytes()
        status, _, rejected = self.request("/api/applications/" + before["id"], method="PUT",
                                           payload={"status": "inactive", "revision": old_revision}, authenticated=True)
        self.assertEqual(status, 409, rejected)
        self.assertEqual(rejected["error"]["code"], "stale_revision")
        self.assertEqual(store.read_bytes(), saved_bytes)

    def test_run_inventory_keeps_enrichment_recovery_and_receipts(self):
        run_path = self.data.evidence / "2020-02-01-weekday"
        write_json(run_path / "terminal.json", {"status": "COMPLETED_UNAPPROVED",
                   "finished_at": "2020-02-01T09:20:00+00:00", "counts": {"ads_reviewed": 12}})
        originals = {path: path.read_bytes() for path in run_path.iterdir() if path.is_file()}
        rows = self.overview()["catalogs"]["runs"]["items"]
        row = next(row for row in rows if row["id"] == run_path.name)
        self.assertEqual(row["raw_status"], "COMPLETED_UNAPPROVED")
        self.assertEqual(len(row["stage_metrics"]), 7)
        self.assertEqual(row["summary_metrics"]["ads_reviewed"], 12)
        self.assertIsNone(row["usage"]["exact_tokens"])
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        recorded_at = row["snapshot_recorded_at"]
        shutil.rmtree(run_path)
        recovered = next(row for row in self.overview()["catalogs"]["runs"]["items"] if row["id"] == run_path.name)
        self.assertEqual(recovered["snapshot_status"], "recovered")
        self.assertEqual(recovered["snapshot_recorded_at"], recorded_at)
        self.assertEqual(recovered["summary_metrics"]["ads_reviewed"], 12)
        self.assertEqual(self.history.runs()["items"], self.overview()["catalogs"]["runs"]["items"])

    def test_incomplete_source_catalogs_fail_without_a_valid_marker(self):
        baseline = self.data.overview(include_analytics=False)
        for key, bad in (("roles", None), ("runs", {"items": "bad", "total": 0}),
                         ("documents", {"items": [None], "total": 1}),
                         ("roles", {"items": [], "total": True})):
            with self.subTest(key=key, bad=bad):
                malformed = copy.deepcopy(baseline)
                if bad is None:
                    malformed.pop(key)
                else:
                    malformed[key] = bad
                with patch.object(self.server, "overview_snapshot", return_value=malformed):
                    status, _, result = self.request(catalogs=True)
                self.assertEqual(status, 500, result)
                self.assertEqual(result["error"]["code"], "invalid_overview_catalogs")
                self.assertNotIn("catalogs", result)

    def test_bad_decorated_catalog_or_settings_cannot_advertise_version_one(self):
        for target, method, value in ((self.apps, "decorate_list", {"items": [], "total": 0}),
                                      (self.history, "runs", {"items": [None], "total": 1}),
                                      (self.data, "settings", [])):
            with self.subTest(method=method), patch.object(target, method, return_value=value):
                status, _, result = self.request(catalogs=True)
                self.assertGreaterEqual(status, 400, result)
                self.assertNotIn("catalogs", result)

    def test_reader_errors_preserve_status_and_do_not_return_partial_catalogs(self):
        store = self.data.local / "applications.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("{fixture corrupt", encoding="utf-8")
        before = store.read_bytes()
        status, _, result = self.request(catalogs=True)
        self.assertEqual(status, 409, result)
        self.assertEqual(result["error"]["code"], "invalid_application_store")
        self.assertEqual(store.read_bytes(), before)
        self.assertNotIn("catalogs", result)
        store.unlink()
        with patch.object(self.history, "runs", side_effect=RuntimeError("fixture history failed")):
            status, _, result = self.request(catalogs=True)
        self.assertEqual(status, 500, result)
        self.assertEqual(result["error"]["code"], "internal_error")
        self.assertNotIn("catalogs", result)

    def test_opt_in_keeps_host_origin_and_mutation_gates(self):
        for headers in ({"Host": "foreign.example"}, {"Origin": "https://foreign.example"},
                        {"Sec-Fetch-Site": "cross-site"}):
            status, _, result = self.request(catalogs=True, headers=headers)
            self.assertEqual(status, 403, result)
        self.jobs.items.append({"id": "fixture-owned-job", "status": "running"})
        result = self.overview()
        self.assertEqual(result["runtime"]["jobs"][0]["id"], "fixture-owned-job")
        status, _, response = self.request("/api/documents/search", method="PUT", catalogs=True,
                                           payload={"content": "changed", "revision": "fixture"}, authenticated=True)
        self.assertEqual(status, 409, response)
        self.assertEqual(response["error"]["code"], "pipeline_active")
        status, _, response = self.request("/api/settings", method="PUT", catalogs=True, payload={"theme": "dark"})
        self.assertEqual(status, 403, response)


class CatalogAliasTests(unittest.TestCase):
    """Existing requested-approval fixture exercised through the opt-in route."""

    def test_consolidated_catalog_retains_status_alias_and_revalidates_approval(self):
        fixture = alias_fixtures.AliasIntegrationTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.apps.set_status(fixture.old["id"], "applied")
        fixture.persist()
        application_bytes = (fixture.data.local / "applications.json").read_bytes()
        server = StudioHTTPServer(("127.0.0.1", 0), fixture.workspace, data=fixture.data, applications=fixture.apps,
                                  jobs=CatalogJobs(), history=RunHistory(fixture.data))
        server.analytics_snapshot = {"fixture": True}
        server.analytics_checked_at = time.monotonic()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def catalog():
                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
                try:
                    connection.request("GET", "/api/overview", headers={"X-Studio-Catalogs": "1"})
                    response = connection.getresponse()
                    result = json.loads(response.read())
                    self.assertEqual(response.status, 200, result)
                    return result["catalogs"]["roles"]
                finally:
                    connection.close()
            with patch.object(fixture.data, "roles", wraps=fixture.data.roles) as scan:
                first = catalog()
            self.assertEqual(scan.call_count, 1)
            self.assertEqual(first, fixture.apps.roles())
            self.assertEqual(first["total"], 1)
            self.assertEqual(first["items"][0]["id"], fixture.old["id"])
            self.assertEqual(first["items"][0]["document_folder"], fixture.new["folder"])
            self.assertEqual(first["items"][0]["application"]["status"], "applied")
            self.assertTrue(first["items"][0]["approved_document_alias"])
            fixture.approval_path.write_text("{}")
            changed = catalog()
            self.assertFalse(any(row.get("approved_document_alias") for row in changed["items"]))
            self.assertEqual((fixture.data.local / "applications.json").read_bytes(), application_bytes)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
