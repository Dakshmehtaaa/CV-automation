#!/usr/bin/env python3
"""Loopback-only HTTP host for CV Studio. No package installation required."""
from __future__ import annotations

import argparse
import copy
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parent.parent
MAX_BODY = 1024 * 1024
VERSION = "1.0.0"


class StudioHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, root=DEFAULT_ROOT, *, data=None, jobs=None, applications=None, desktop=None, history=None,
                 company_logos=None):
        self.root = Path(root).resolve()
        if data is None:
            from data import StudioData
            data = StudioData(self.root)
        if jobs is None:
            from runtime import JobManager
            jobs = JobManager(self.root, data)
        self.data = data
        self.jobs = jobs
        if applications is None:
            from applications import Applications
            applications = Applications(data)
        self.applications = applications
        if hasattr(jobs, "set_application_service"):
            jobs.set_application_service(applications)
        if desktop is None and self.root == DEFAULT_ROOT.resolve():
            from desktop_control import DesktopPipelineController
            desktop = DesktopPipelineController(self.root, descriptor_provider=data.external_stop_descriptor)
        self.desktop = desktop
        if history is None:
            from history import RunHistory
            history = RunHistory(data)
        self.history = history
        self.token = secrets.token_urlsafe(32)
        self.mutation_lock = threading.RLock()
        self.static_dir = HERE / "static"
        if company_logos is None:
            from company_logos import CompanyLogoLibrary
            company_logos = CompanyLogoLibrary(HERE / "assets" / "company-logos")
        self.company_logos = company_logos
        self.jd_insights_lock = threading.Lock()
        self.jd_insights_snapshot = None
        self.jd_insights_checked_at = 0.0
        self.analytics_lock = threading.Lock()
        self.analytics_snapshot = None
        self.analytics_checked_at = None
        self.analytics_running = False
        self.analytics_error = False
        from prospector import ProspectorService
        self.prospector = ProspectorService(self.root)
        super().__init__(address, StudioHandler)
        self.prospector.studio_url = f"http://127.0.0.1:{self.server_address[1]}"

    def server_close(self):
        self.prospector.close()
        super().server_close()

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def overview_snapshot(self):
        """Serve controls and roles promptly; verify archive evidence in one worker."""
        from data import StudioData
        if not isinstance(self.data, StudioData):
            return self.data.overview()
        result = self.data.overview(include_analytics=False)
        with self.analytics_lock:
            if not self.analytics_running and (self.analytics_checked_at is None or time.monotonic() - self.analytics_checked_at >= 60):
                self.analytics_running = True
                roles, runs = result["roles"]["items"], result["runs"]["items"]

                def update():
                    try:
                        snapshot = self.data._dashboard_analytics(roles, runs)
                        with self.analytics_lock:
                            self.analytics_snapshot = snapshot
                            self.analytics_error = False
                    except Exception:
                        with self.analytics_lock:
                            self.analytics_error = True
                    finally:
                        with self.analytics_lock:
                            self.analytics_running = False
                            self.analytics_checked_at = time.monotonic()

                threading.Thread(target=update, name="studio-saved-analytics", daemon=True).start()
            result["dashboard_analytics"] = copy.deepcopy(self.analytics_snapshot) if self.analytics_snapshot is not None else {"loading": not self.analytics_error, "unavailable": self.analytics_error}
            result["dashboard_analytics_refreshing"] = self.analytics_running
            result["dashboard_analytics_error"] = self.analytics_error
        return result


def refresh_tailored_view(root):
    """Keep only roles still to apply visible in Finder after a status change (tailored_view.py)."""
    def run():
        try:
            from tailored_view import tidy
            tidy(Path(root), apply=True)
        except Exception:
            pass  # the Finder view is a convenience; never fail the status save
    threading.Thread(target=run, name="studio-tailored-view", daemon=True).start()


class StudioHandler(BaseHTTPRequestHandler):
    server_version = "CVStudio/1.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, fmt, *args):
        # Do not log content, tokens, document text, or arbitrary query strings.
        if args and isinstance(args[0], str) and "/api/health" in args[0]:
            return
        if len(args) > 1 and str(args[1]).startswith(("4", "5")):
            path = urlsplit(self.path).path
            if path == "/prospect" or path.startswith("/prospect/"):
                path = "/prospect/"
            print(f"HTTP {args[1]} {self.command} {path}", flush=True)

    def _headers(self, status, content_type, length, *, pdf=False, extra=None, cache_control="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", cache_control)
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN" if pdf else "DENY")
        security_policy = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
            + ("frame-ancestors 'self'" if pdf else "frame-ancestors 'none'")
        )
        if extra and isinstance(extra.get("Content-Security-Policy"), str):
            security_policy = extra["Content-Security-Policy"]
        self.send_header("Content-Security-Policy", security_policy)
        if extra:
            for key, value in extra.items():
                if key == "Content-Security-Policy":
                    continue
                self.send_header(key, value)
        self.end_headers()

    def _json(self, value, status=200, *, cache_control="no-store"):
        body = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body), cache_control=cache_control)
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status, message, code="request_failed"):
        self.close_connection = True
        self._json({"error": {"code": code, "message": str(message)}, "message": str(message)}, status)

    def _check_request(self, *, mutation=False):
        port = self.server.server_address[1]
        if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            self._error(403, "Open CV Studio from its local address.", "invalid_host")
            return False
        expected = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        origin = self.headers.get("Origin")
        if origin and origin not in expected:
            self._error(403, "This request came from another website.", "invalid_origin")
            return False
        site = self.headers.get("Sec-Fetch-Site")
        document_entry = (not mutation and self.command in {"GET", "HEAD"}
                          and urlsplit(self.path).path in {"/", "/index.html"}
                          and self.headers.get("Sec-Fetch-Mode") == "navigate"
                          and self.headers.get("Sec-Fetch-Dest") == "document")
        if site in {"cross-site", "same-site"} and not document_entry:
            self._error(403, "Open CV Studio directly to use your workspace.", "cross_site")
            return False
        if mutation:
            token = self.headers.get("X-Studio-Token", "")
            if not origin or not secrets.compare_digest(token, self.server.token):
                self._error(403, "Refresh this page before saving or starting work.", "invalid_token")
                return False
        return True

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid request size.")
        if length <= 0 or length > MAX_BODY:
            raise ValueError("A JSON object of at most 1 MB is required.")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("This request needs JSON content.")
        raw = self.rfile.read(length)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("The request must contain a JSON object.")
        return value

    def _runtime(self):
        active = self.server.data.active_pipeline()
        caps = self.server.jobs.capabilities()
        jobs = self.server.jobs.list()
        stops = self.server.desktop.list() if self.server.desktop else []
        if any(receipt.get("recovery_required") for receipt in stops):
            active = {**active, "uncertain": True, "edit_locked": True,
                      "reason": "Stop was requested. Cleanup needs review before starting another run."}
            caps = {**caps, "can_run": False, "can_check": False, "blocked_reason": active["reason"]}
        descriptor = self.server.data.external_stop_descriptor() if self.server.desktop else None
        control = self.server.desktop.capabilities(descriptor) if self.server.desktop else {"can_stop": False}
        return {"active_pipeline": active, "capabilities": caps,
                "external_control": control, "external_stops": stops,
                "jobs": jobs.get("items", []) if isinstance(jobs, dict) else jobs}

    def _stop_hold(self):
        return bool(self.server.desktop and any(receipt.get("recovery_required") for receipt in self.server.desktop.list()))

    def _settings(self):
        result = self.server.data.settings()
        reader = getattr(self.server.jobs, "run_settings", None)
        if reader:
            result["run"] = reader()
        return result

    @staticmethod
    def _overview_catalog(value, name):
        """Reject incomplete catalogs before advertising the opt-in contract."""
        if (not isinstance(value, dict) or not isinstance(value.get("items"), list)
                or any(not isinstance(row, dict) for row in value["items"])
                or type(value.get("total")) is not int or value["total"] < 0):
            from data import StudioDataError
            raise StudioDataError(f"CV Studio could not read the startup {name} catalog. Try refreshing.",
                                  "invalid_overview_catalogs", 500)
        return value

    def _overview_catalogs(self, result):
        """Reuse this request's inventories; keep normal status/history readers."""
        roles = self._overview_catalog(result.get("roles"), "roles")
        runs = self._overview_catalog(result.get("runs"), "runs")
        documents = self._overview_catalog(result.get("documents"), "documents")
        roles = self._overview_catalog(self.server.applications.decorate_list(roles), "roles")
        runs = self._overview_catalog(self.server.history.runs(inventory=runs), "runs")
        settings = self._settings()
        if not isinstance(roles.get("application_summary"), dict) or not isinstance(settings, dict):
            from data import StudioDataError
            raise StudioDataError("CV Studio could not read its complete startup catalogs. Try refreshing.",
                                  "invalid_overview_catalogs", 500)
        return {"version": 1, "roles": roles, "runs": runs,
                "documents": documents, "settings": settings}

    def _file(self, path, *, pdf=False, download=False):
        path = Path(path)
        if not path.is_file():
            self._error(404, "This file is no longer available.", "not_found")
            return
        if pdf and path.suffix.lower() != ".pdf":
            self._error(403, "Only PDF documents can be opened here.", "unsupported_file")
            return
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        headers = {"Accept-Ranges": "bytes"}
        if pdf:
            mode = "attachment" if download else "inline"
            headers["Content-Disposition"] = f"{mode}; filename*=UTF-8''{quote(path.name)}"
        byte_range = self.headers.get("Range")
        if byte_range:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range.strip())
            if not match or not size or not any(match.groups()):
                self._headers(416, "text/plain", 0, extra={"Content-Range": f"bytes */{size}"})
                return
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last) if last else end, end)
            else:
                start = max(0, size - int(last))
            if start >= size or start > end:
                self._headers(416, "text/plain", 0, extra={"Content-Range": f"bytes */{size}"})
                return
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        content_type = "application/pdf" if pdf else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        if path.suffix in {".js", ".mjs"}:
            content_type = "text/javascript; charset=utf-8"
        self._headers(status, content_type, max(0, end - start + 1), pdf=pdf, extra=headers)
        if self.command != "HEAD":
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    def _static(self, route):
        if route.startswith("/static/"):
            route = route[len("/static"):]
        relative = "index.html" if route == "/" else unquote(route.lstrip("/"))
        candidate = (self.server.static_dir / relative).resolve()
        base = self.server.static_dir.resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            self._error(404, "Page not found.", "not_found")
            return
        if any(part.startswith(".") for part in Path(relative).parts):
            self._error(404, "Page not found.", "not_found")
            return
        self._file(candidate)

    def _prospect(self, parts):
        """Forward only this namespace after Studio's local-origin checks."""
        if parts.path == "/prospect":
            self._headers(308, "text/plain", 0, extra={"Location": "/prospect/"})
            return
        if self.command not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}:
            return self._error(405, "That Prospect action is not supported.")
        if self.headers.get("Transfer-Encoding"):
            return self._error(400, "Chunked requests are not supported.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._error(400, "Invalid request size.")
        if length < 0:
            return self._error(400, "Invalid request size.")
        if length > MAX_BODY:
            return self._error(413, "A request of at most 1 MB is required.")
        if self.command not in {"GET", "HEAD"} and self.headers.get("X-Prospector") != "1":
            return self._error(403, "Refresh Prospect before saving or starting work.")
        body = self.rfile.read(length) if length else None
        connection = None
        try:
            port = self.server.prospector.ensure_started()
            headers = {"Host": f"127.0.0.1:{port}"}
            for name in ("Content-Type", "X-Prospector"):
                if self.headers.get(name):
                    headers[name] = self.headers[name]
            if self.headers.get("Origin"):
                headers["Origin"] = f"http://127.0.0.1:{port}"
            target = parts.path[len("/prospect"):] or "/"
            if parts.query:
                target += "?" + parts.query
            connection = HTTPConnection("127.0.0.1", port, timeout=20)
            connection.request(self.command, target, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            extra = {"Content-Security-Policy": response.getheader("Content-Security-Policy")}
            size = int(response.getheader("Content-Length", "0")) if self.command == "HEAD" else len(payload)
            self._headers(response.status, response.getheader("Content-Type", "application/json"),
                          size, pdf=True, extra=extra)
            if self.command != "HEAD":
                self.wfile.write(payload)
        except (OSError, HTTPException, RuntimeError) as exc:
            self._error(503, str(exc) if isinstance(exc, RuntimeError) else
                        "Prospector is unavailable. Retry or reopen CV Studio.", "prospector_unavailable")
        finally:
            if connection is not None:
                connection.close()

    def _handle(self):
        parts = urlsplit(self.path)
        if parts.path == "/prospect" or parts.path.startswith("/prospect/"):
            if self._check_request():
                return self._prospect(parts)
            return
        mutation = self.command in {"POST", "PUT"}
        if not self._check_request(mutation=mutation):
            return
        parts = urlsplit(self.path)
        route = parts.path.rstrip("/") or "/"
        query = parse_qs(parts.query)
        data, jobs = self.server.data, self.server.jobs
        if self.command in {"GET", "HEAD"}:
            if route == "/api/health":
                return self._json({"app": "cv-studio", "version": VERSION,
                                   "workspace_id": hashlib.sha256(str(self.server.root).encode()).hexdigest()[:16]})
            if route == "/api/overview":
                result = self.server.overview_snapshot()
                if self.headers.get("X-Studio-Catalogs", "").strip() == "1" or query.get("catalogs") == ["1"]:
                    catalogs = self._overview_catalogs(result)
                    summary = catalogs["roles"]["application_summary"]
                    # The opt-in client consumes these catalogs. Remove the raw
                    # duplicates only after all four normal catalogs succeeded.
                    duplicate_keys = {"roles", "runs", "recent_roles", "recent_runs", "documents", "settings"}
                    result = {key: value for key, value in result.items() if key not in duplicate_keys}
                    result["catalogs"] = catalogs
                else:
                    from applications import Applications
                    summary = (self.server.applications.summary(result.get("roles"))
                               if isinstance(self.server.applications, Applications)
                               else self.server.applications.summary())
                result.update(token=self.server.token, runtime=self._runtime(), version=VERSION,
                              application_summary=summary)
                return self._json(result)
            if route == "/api/company-logos":
                return self._json(self.server.company_logos.catalog(), cache_control="private, max-age=300")
            logo_match = re.fullmatch(r"/api/company-logos/([^/]+)", route)
            if logo_match:
                from company_logos import CompanyLogoError
                try:
                    content, mime, etag = self.server.company_logos.asset(unquote(logo_match[1]))
                except CompanyLogoError as error:
                    return self._error(error.status, str(error), error.code)
                headers = {"ETag": etag, "Cross-Origin-Resource-Policy": "same-origin"}
                if mime.startswith("image/svg+xml"):
                    headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
                cache_control = "private, max-age=86400"
                if self.headers.get("If-None-Match") == etag:
                    self._headers(304, mime, 0, cache_control=cache_control, extra=headers)
                    return
                self._headers(200, mime, len(content), cache_control=cache_control, extra=headers)
                if self.command != "HEAD":
                    self.wfile.write(content)
                return
            if route == "/api/runtime":
                return self._json(self._runtime())
            if route == "/api/roles":
                return self._json(self.server.applications.roles())
            if route == "/api/apply-flow":
                from apply_flow import ApplyFlow, ApplyFlowError
                try:
                    return self._json(ApplyFlow(data.local).list())
                except ApplyFlowError as exc:
                    return self._error(500, str(exc), "apply_flow_unreadable")
            if route == "/api/shortlist":
                from shortlist import Shortlist, ShortlistError
                try:
                    return self._json(Shortlist(data.local).list())
                except ShortlistError as exc:
                    return self._error(500, str(exc), "shortlist_unreadable")
            if route == "/api/help-threads":  # 26 Oct: saved Mercury 3 chats, one per job
                from help_threads import HelpThreads, HelpThreadsError
                try:
                    return self._json(HelpThreads(data.local).list())
                except HelpThreadsError as exc:
                    return self._error(500, str(exc), "help_threads_unreadable")
            if route == "/api/contacts":
                return self._json(self.server.applications.contacts_catalog(jobs.list()))
            if route == "/api/manual-jobs":
                return self._json(jobs.manual_jobs())
            if route == "/api/jd-insights":
                from jd_insights import JDInsights
                # Corpus parsing is deterministic but more expensive than reading
                # a saved role. Reuse it across the normal 15-second UI refreshes.
                with self.server.jd_insights_lock:
                    if (self.server.jd_insights_snapshot is None
                            or time.monotonic() - self.server.jd_insights_checked_at >= 60):
                        self.server.jd_insights_snapshot = JDInsights(data.root).catalog()
                        self.server.jd_insights_checked_at = time.monotonic()
                    result = self.server.jd_insights_snapshot
                return self._json(result)
            if route.startswith("/api/roles/"):
                return self._json(self.server.applications.role(unquote(route[len("/api/roles/"):])))
            if route == "/api/audits":
                from audits import audits_overview
                return self._json(audits_overview(data.auto / "evidence"))
            if route == "/api/runs":
                return self._json(self.server.history.runs())
            if route.startswith("/api/runs/"):
                return self._json(self.server.history.run(unquote(route[len("/api/runs/"):])))
            if route == "/api/documents":
                return self._json(data.documents())
            if route.startswith("/api/documents/"):
                return self._json(data.document(unquote(route[len("/api/documents/"):])))
            if route == "/api/settings":
                return self._json(self._settings())
            if route == "/api/jobs":
                return self._json(jobs.list())
            if route == "/api/apply-options":
                if not hasattr(jobs, "application_options"):
                    statuses = self.server.applications.status_options() if hasattr(self.server.applications, "status_options") else []
                    return self._json({"statuses": statuses, "providers": {
                    "luna": {"id": "luna", "model": "gpt-6-luna", "effort": "max", "available": False,
                             "generation_verified": False, "blocked": False,
                             "reason": "Application task routes are unavailable in this Studio process."},
                        "motivation": {"id": "motivation", "provider": "luna", "model": "gpt-6-luna", "effort": "high",
                                       "service_tier": "priority", "available": False,
                                       "generation_verified": False, "blocked": False,
                                       "reason": "Start the explicit motivation request to check the signed-in fast route."},
                        "google": {"id": "google", "model": "gemini-3.8-flash-medium", "effort": "medium", "available": False,
                                   "generation_verified": False, "blocked": True,
                                   "reason": "The Antigravity bridge cannot enforce a read-only public-source tool scope. Google search was not started."}},
                        "contact_discovery": {"provider": "luna", "model": "gpt-6-luna", "effort": "max",
                                              "lanes": 1, "validation": "deterministic_public_evidence"},
                        "metadata_only": True})
                return self._json(jobs.application_options())
            apply_match = re.fullmatch(r"/api/applications/([^/]+)/(motivation|contacts)", route)
            if apply_match:
                role_id = unquote(apply_match[1])
                jobs_view = jobs.list()
                if apply_match[2] == "motivation":
                    languages = query.get("language", [])
                    if len(languages) != 1:
                        return self._error(400, "Choose English or French for the motivation copy.", "invalid_language")
                    return self._json(self.server.applications.motivation(role_id, languages[0], jobs_view))
                return self._json(self.server.applications.contacts(role_id, jobs_view))
            contact_job_match = re.fullmatch(r"/api/contact-jobs/([^/]+)", route)
            if contact_job_match:
                job = jobs.get(unquote(contact_job_match[1]))
                if job.get("kind") not in {"application_contacts", "application_motivation"}:
                    return self._error(404, "This application task was not found.", "not_found")
                return self._json(job)
            if route.startswith("/api/jobs/"):
                return self._json(jobs.get(unquote(route[len("/api/jobs/"):])))
            if route == "/api/assets":
                key = query.get("key", [""])[0]
                return self._file(data.asset_path(key), pdf=True, download=query.get("download") == ["1"])
            if route == "/api/email-contacts":  # 5 Nov: Find email history
                import email_finder
                return self._json({"contacts": email_finder.contacts(), "signature": email_finder.signature()})
            if route.startswith("/api/"):
                return self._error(404, "This action does not exist.", "not_found")
            return self._static(route)
        payload = self._body()
        if self.command == "PUT" and route.startswith("/api/help-threads/"):  # chat save: no mutation lock (a CV edit holds it for minutes)
            from help_threads import HelpThreads, HelpThreadsError
            try:
                return self._json(HelpThreads(data.local).save(unquote(route[len("/api/help-threads/"):]), payload))
            except HelpThreadsError as exc:
                return self._error(400, str(exc), "help_thread_invalid")
        if route == "/api/email-contacts" and self.command == "POST":  # 5 Nov: Find email / Outreach history (found / drafted / emailed / replied)
            import email_finder
            try:
                if isinstance(payload.get("fields"), dict):
                    email_finder.update_contact(str(payload.get("key") or ""), payload["fields"])
                if payload.get("status"):
                    return self._json(email_finder.set_status(str(payload.get("key") or ""), str(payload.get("status"))))
                return self._json(next(r for r in email_finder.contacts() if r.get("key") == payload.get("key")))
            except (email_finder.FinderError, StopIteration) as exc:
                return self._error(400, str(exc) or "Unknown person.", "email_contact_invalid")
        if self.command == "POST" and route in ("/api/find-email", "/api/draft-email", "/api/followup-email"):  # 5 Nov: Outreach, read-only, no mutation lock
            import email_finder
            role = None
            if isinstance(payload.get("role_id"), str) and payload.get("role_id"):
                try:
                    import help_bot
                    role = help_bot.role_files(data, data.role(payload["role_id"]))
                except Exception:
                    role = None
            try:
                if route == "/api/find-email":
                    return self._json(email_finder.find(payload.get("query"), str(payload.get("model") or "sonnet")))
                if route == "/api/followup-email":
                    return self._json(email_finder.followup(str(payload.get("key") or ""), role))
                return self._json(email_finder.draft(payload.get("person") or {}, payload.get("email"), payload.get("note") or "",
                                                     str(payload.get("language") or "fr"), "sonnet",
                                                     str(payload.get("key") or ""), role))
            except email_finder.FinderError as exc:
                return self._error(400, str(exc), "email_finder_unavailable")
        if self.command == "POST" and route == "/api/help-chat":  # read-only Mercury 3 chat: no mutation lock
            import help_bot
            role = help_bot.role_files(data, data.role(payload["role_id"])) if isinstance(payload.get("role_id"), str) and payload.get("role_id") else None
            try:
                result = help_bot.answer(payload.get("messages"), role)
            except help_bot.HelpBotError as exc:
                return self._error(400, str(exc), "help_unavailable")
            instruction = result.get("edit_instruction")
            if instruction:  # 22 Oct: Mercury 3 edited CV, built into a NEW sub-folder; nothing existing is touched
                app = role.get("application") or {} if role else {}
                if not role:
                    result["reply"] += "\n- Pick the job in the dropdown first, then ask again."
                elif role.get("collection", "current") != "current" or app.get("status") in ("applied", "rejected") or app.get("canonical_applied"):
                    # 26 Oct strict rule (Daksh): never touch CVs of jobs already applied to
                    result["reply"] += "\n- Not edited: this job is already applied to (or closed), so its CV stays as sent."
                else:
                    import cv_edit
                    rel = str(role.get("document_folder") or role.get("folder") or "").removeprefix("Tailored CVs/")
                    try:
                        with self.server.mutation_lock:
                            made = cv_edit.make_edited_cv(Path(data.role_root) / rel, instruction)
                        result["reply"] += ("\n- Built: " + made["folder"] + " (2 pages, " + " then ".join(made["languages"]) +
                                            "). The original CV is untouched." +
                                            "".join("\n- ⚠️ " + w for w in made.get("warnings") or []))
                        result["edited_cv"] = made
                    except cv_edit.EditError as exc:
                        result["reply"] += "\n- I could not build it: " + str(exc)
                    except Exception as exc:
                        result["reply"] += "\n- The edit failed before anything was saved: " + str(exc)[:200]
            help_bot.log_exchange(payload.get("messages"), role, result)
            return self._json(result)
        with self.server.mutation_lock:
            if self.command == "POST" and route == "/api/manual-jobs":
                result = jobs.enqueue_manual_job(payload)
                return self._json(result, 200 if result.get("duplicate") else 201)
            contact_choice = re.fullmatch(r"/api/manual-jobs/([a-f0-9]{16})/contact-choice", route)
            if self.command == "POST" and contact_choice:
                if set(payload) != {"find_contacts"}:
                    raise ValueError("Choose yes or no for contact research.")
                return self._json(jobs.set_contact_choice(contact_choice[1], payload["find_contacts"]))
            manual_build = re.fullmatch(r"/api/manual-jobs/([a-f0-9]{16})/build", route)
            if self.command == "POST" and manual_build:
                if payload:
                    raise ValueError("Build now takes the saved job only.")
                if self._stop_hold():
                    return self._error(409, "Cleanup needs review before starting a build.", "cleanup_required")
                job = jobs.start_manual_build(manual_build[1])
                return self._json({"job_id": job.get("id"), "job": job}, 202)
            manual_review = re.fullmatch(r"/api/manual-jobs/([a-f0-9]{16})/review", route)
            if self.command == "POST" and manual_review:
                if not set(payload) <= {"decision", "comment"}:
                    raise ValueError("Review takes a decision and an optional comment only.")
                if self._stop_hold():
                    return self._error(409, "Cleanup needs review before starting a build.", "cleanup_required")
                result = jobs.review_manual_job(manual_review[1], payload.get("decision"), payload.get("comment") or "")
                return self._json(result, 202 if result.get("job") else 200)
            handoff = re.fullmatch(r"/api/applications/([^/]+)/contact-handoff", route)
            if self.command == "POST" and handoff:
                if payload:
                    raise ValueError("The contact handoff takes the saved job only.")
                from contact_handoff import hand_off
                return self._json(hand_off(self.server.applications.role(unquote(handoff[1]))))
            apply_match = re.fullmatch(r"/api/applications/([^/]+)/(motivation|contacts)", route)
            if self.command == "POST" and apply_match:
                role_id = unquote(apply_match[1])
                task = apply_match[2]
                applications = self.server.applications
                if task == "motivation":
                    if set(payload) != {"language"}:
                        raise ValueError("Choose English or French for the motivation copy.")
                    language = applications.normalize_language(payload.get("language"))
                    current = applications.motivation(role_id, language, jobs.list())
                    if current.get("status") == "ready":
                        return self._json(current)
                    packet = applications.motivation_packet(role_id, language)
                    job = jobs.start_application_job("application_motivation", role_id, "luna",
                                                     language=language, packet=packet)
                else:
                    if set(payload) != {"provider"}:
                        raise ValueError("Choose one public contact search route.")
                    if payload.get("provider") != "luna":
                        raise ValueError("Public contact search uses Luna MAX.")
                    current = applications.contacts(role_id, jobs.list())
                    if current.get("search_completed"):
                        return self._json(current)
                    packet = applications.contact_packet(role_id)
                    job = jobs.start_application_job("application_contacts", role_id,
                                                     payload.get("provider"), packet=packet)
                return self._json({"job_id": job.get("id"), "job": job}, 202)
            if self.command == "POST" and route == "/api/pipeline/stop":
                if not self.server.desktop:
                    return self._error(503, "The pipeline chat cannot be controlled from this Studio.", "desktop_unavailable")
                descriptor = data.external_stop_descriptor()
                if not descriptor or set(payload) != {"thread_id", "current_turn_id", "run_id"} or any(payload.get(key) != descriptor.get(key) for key in payload):
                    return self._error(409, "The active run changed. Refresh before stopping it.", "run_changed")
                return self._json(self.server.desktop.stop(descriptor), 202)
            if self.command == "POST" and route == "/api/runtime/check":
                return self._json(jobs.refresh_capabilities(), 202)
            if self.command == "PUT" and route.startswith("/api/apply-flow/"):
                from apply_flow import ApplyFlow, ApplyFlowError
                try:  # 26 Oct: apply later / won't-apply reason; local file only
                    return self._json(ApplyFlow(data.local).update(unquote(route[len("/api/apply-flow/"):]), payload))
                except ApplyFlowError as exc:
                    return self._error(400, str(exc), "apply_flow_invalid")
            if self.command == "PUT" and route.startswith("/api/shortlist/"):
                from shortlist import Shortlist, ShortlistError
                try:  # 23 Oct: jobs put aside to find a contact; local file only
                    return self._json(Shortlist(data.local).update(unquote(route[len("/api/shortlist/"):]), payload))
                except ShortlistError as exc:
                    return self._error(400, str(exc), "shortlist_invalid")
            if self.command == "PUT" and route == "/api/settings":
                data.save_settings(payload)
                return self._json(self._settings())
            if self.command == "PUT" and route == "/api/settings/run":
                return self._json(jobs.save_run_settings(payload))
            if self.command == "POST" and route == "/api/settings/run/reset":
                return self._json(jobs.save_run_settings(payload, reset=True))
            if self.command == "PUT" and route.startswith("/api/applications/"):
                if set(payload) - {"status", "revision"}:
                    raise ValueError("Choose an application status.")
                result = self.server.applications.set_status(unquote(route[len("/api/applications/"):]), payload.get("status"), payload.get("revision"))
                if result.get("saved"):
                    refresh_tailored_view(self.server.root)
                return self._json(result)
            if self.command == "PUT" and route.startswith("/api/documents/"):
                if self._stop_hold():
                    return self._error(409, "Cleanup needs review before changing pipeline inputs.", "cleanup_required")
                if any(j.get("status") in {"queued", "running", "starting", "waiting", "waiting_input", "waiting_for_input", "stopping"}
                       for j in jobs.list().get("items", [])):
                    return self._error(409, "Let the current Studio job finish before editing its inputs.", "pipeline_active")
                return self._json(data.save_document(unquote(route[len("/api/documents/"):]), payload.get("content"), payload.get("revision")))
            if self.command == "POST" and route == "/api/jobs":
                if self._stop_hold():
                    return self._error(409, "Cleanup needs review before starting another run.", "cleanup_required")
                return self._json(jobs.start(payload.get("kind")), 202)
            match = re.fullmatch(r"/api/jobs/([^/]+)/(stop|input)", route)
            if self.command == "POST" and match:
                job_id, action = unquote(match[1]), match[2]
                if action == "stop":
                    return self._json(jobs.stop(job_id))
                return self._json(jobs.respond(job_id, payload.get("request_id"), payload.get("answer")))
        self._error(404, "This action does not exist.", "not_found")

    def _dispatch(self):
        try:
            self._handle()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            if hasattr(exc, "status") and hasattr(exc, "code"):
                self._error(exc.status, str(exc), exc.code)
            elif isinstance(exc, (ValueError, TypeError, json.JSONDecodeError)):
                self._error(400, str(exc), "invalid_request")
            elif isinstance(exc, (KeyError, FileNotFoundError)):
                self._error(404, "That item is no longer available. Refresh the page.", "not_found")
            else:
                print(f"CV Studio request error: {type(exc).__name__}: {exc}", flush=True)
                self._error(500, "CV Studio could not read this item. Your files are unchanged. Try refreshing.", "internal_error")

    do_GET = _dispatch
    do_HEAD = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_PATCH = _dispatch
    do_DELETE = _dispatch

    def do_OPTIONS(self):
        self._error(405, "Cross-site requests are not supported.", "method_not_allowed")


def main():
    parser = argparse.ArgumentParser(description="Open your private CV workspace in a browser.")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    os.umask(0o077)
    server = StudioHTTPServer(("127.0.0.1", args.port), args.root)
    server.data.roles_cache_seconds = 8  # repeated Home refreshes reuse one role scan
    def shutdown(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"CV Studio is open at {server.origin}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        close = getattr(server.jobs, "close", None)
        if close:
            close()
        server.server_close()


if __name__ == "__main__":
    main()
