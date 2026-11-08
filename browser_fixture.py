"""Isolated browser-test workspace. Never connects to providers or live records."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import tempfile
import threading
import time

from data import StudioData
from server import StudioHTTPServer
from company_logos import CompanyLogoLibrary


def _pdf(path):
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    stream = b"BT /F1 22 Tf 64 745 Td (Browser test CV) Tj ET"
    objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    body, offsets = b"%PDF-1.4\n", [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body += str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(body)
    body += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    body += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"
    path.write_bytes(body)


def make_fixture(root):
    resources = root / "Resources"
    resources.mkdir()
    (root / "Master").symlink_to(resources / "Master", target_is_directory=True)
    (root / "Tailored CVs").mkdir()
    (resources / "Tailored CVs").symlink_to(root / "Tailored CVs", target_is_directory=True)
    (resources / "PROFILE.md").write_text("# Your profile\n\n## 1. Experience\n\nSustainability project experience.\n\n## 15. Approved wording\n\nProtected bank mirror.\n")
    (resources / "SEARCH.md").write_text("# Search brief\n\n## 1. Priorities\n\nSustainability roles in France.\n\n## 2. Geography\n\nFrance only.\n")
    (resources / "LAYOUT.md").write_text("# Design guide\n\nOne page. Clear typography.\n")
    auto = resources / "Master" / "auto"
    (auto / "state").mkdir(parents=True)
    (auto / "local").mkdir()
    (auto / "local" / "ROUTING.md").write_text("# Model routing\n\nFixture routing, never executed.\n")
    (resources / "Master" / "cv").mkdir()
    (resources / "Master" / "cv" / "bank.json").write_text('{"language":"en","phrases":["Fixture wording"]}')
    for index, (company, title) in enumerate([("Example Green", "Sustainability Analyst"), ("Sample Partners", "CSR Project Manager"), ("Demo Energy", "ESG Associate")]):
        folder = root / "Tailored CVs" / company
        folder.mkdir()
        (folder / "JD.md").write_text(f"# {title}\n\nCompany: {company}\nLocation: Paris\nContract: CDI\nFit: 4/5\nSource URL: https://example.com/jobs/{index}\n\n## Role\n\nThis is isolated browser test data.\n")
        if index != 2:
            _pdf(folder / "CV_Candidate.pdf")
    logo_root = root / "fixture-company-logos"
    logo_root.mkdir()
    logo = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><path d="M8 8h24v24H8z" fill="#fff"/></svg>'
    (logo_root / "example-green.svg").write_bytes(logo)
    (logo_root / "manifest.json").write_text(json.dumps([{
        "company": "Example Green", "categories": ["shortlist"], "path": "example-green.svg",
        "format": "svg", "bytes": len(logo), "sha256": hashlib.sha256(logo).hexdigest(),
        "verification_status": "verified_safe_transparent_svg",
        "render_hint": "light_ink/use_dark_background",
    }]), encoding="utf-8")
    for language in ("EN", "FR"):
        name = f"Candidate - CV ({language}).pdf"
        _pdf(root / name)
        (resources / name).symlink_to(root / name)
    batch = auto / "evidence" / "runs" / "2026-09-01-fixture"
    batch.mkdir(parents=True)
    (batch / "batch-manifest.json").write_text(json.dumps({"batch_id": batch.name, "policy_version": 3, "batch_started_at": "2026-09-01T12:30:00+00:00", "batch_deadline_at": "2026-09-01T14:29:00+00:00"}))
    (batch / "terminal.json").write_text(json.dumps({"status": "done", "finished_at": "2026-09-01T13:10:00+00:00", "summary": "Isolated browser test receipt.", "stages": {"hunt": "done", "hunt_audit": "done", "build": "done", "build_audit": "done", "sol_review": "done", "sync": "done"}}))
    (batch / "site-lane-outcomes.json").write_text(json.dumps({"lanes": [{"worker_id": "hunt:" + site, "status": "done", "full_live_ads_reviewed": 20, "target": 20} for site in ["hellowork", "france_travail", "linkedin", "apec", "wttj"]]}))
    (batch / "REPORT.md").write_text("# Browser fixture report\n\nThe test receipt is isolated from the real CV workspace.\n")
    return resources


class BrowserJobs:
    """In-memory UI contract fixture. It cannot dispatch a model or command."""
    def __init__(self):
        self.jobs = {}
        self.saved_manual_jobs = {}
        self.lock = threading.RLock()

    def capabilities(self):
        return {"available": True, "authenticated": True, "checked_at": "2026-10-01T12:00:00+00:00", "checking": False,
                "can_run": True, "can_check": True, "blocked_reason": "", "fixture": True,
                "models": [{"id": "gpt-6.1-sol", "listed": True, "efforts": ["high"]}],
                "connectors": {k: {"enabled": True, "callable": True, "tools_available": True} for k in ["gmail", "google_drive"]}}

    def refresh_capabilities(self):
        return self.capabilities()

    def list(self):
        return {"items": list(self.jobs.values()), "total": len(self.jobs)}

    def get(self, job_id):
        return self.jobs[job_id]

    def manual_jobs(self):
        """Saved local queue fixture. Reads cannot start a model or process."""
        items = list(self.saved_manual_jobs.values())
        counts = {status: sum(item["status"] == status for item in items)
                  for status in ("queued", "building", "completed", "held", "failed")}
        return {"items": items, "total": len(items), "counts": counts}

    def enqueue_manual_job(self, payload):
        if set(payload) != {"url", "jd", "title", "company", "find_contacts"}:
            raise ValueError("Incorrect manual queue fixture contract")
        if not payload.get("url") and not payload.get("jd"):
            raise ValueError("Paste a job URL or the full advert.")
        normalized = str(payload.get("url") or " ".join(str(payload["jd"]).split()))
        ident = hashlib.sha256(normalized.encode()).hexdigest()[:16]
        if ident in self.saved_manual_jobs:
            return {"item": self.saved_manual_jobs[ident], "duplicate": True}
        item = {"id": ident, "status": "queued", "url": payload["url"],
                "title": payload["title"], "company": payload["company"],
                "source_kind": "url_and_jd" if payload["url"] and payload["jd"] else "url" if payload["url"] else "jd",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "summary": "Saved for the next run. No model started.", "build_job_id": None,
                "find_contacts": payload.get("find_contacts") is True,
                "contact_research": {"status": "queued" if payload.get("find_contacts") is True else "off", "contacts": []}}
        self.saved_manual_jobs[ident] = item
        return {"item": item, "duplicate": False}

    def set_contact_choice(self, ident, find_contacts):
        item = self.saved_manual_jobs[ident]
        item["find_contacts"] = find_contacts
        item["contact_research"]["status"] = "queued" if find_contacts else "off"
        return {"item": item}

    def start_manual_build(self, ident):
        """In-memory UI job only; never starts the production builder."""
        item = self.saved_manual_jobs[ident]
        if item.get("build_job_id"):
            return self.jobs[item["build_job_id"]]
        stamp = datetime.now(timezone.utc).isoformat()
        job = {"id": "fixture-manual-" + ident, "kind": "manual_build",
               "label": "Build saved job", "status": "held", "started_at": stamp,
               "finished_at": stamp, "model": "gpt-6-luna", "effort": "max",
               "summary": "Fixture only. The production builder was not started.",
               "logs": [], "pending_requests": [], "recovery_required": False}
        self.jobs[job["id"]] = job
        item.update(status="held", build_job_id=job["id"], summary=job["summary"])
        return job

    def start(self, kind):
        if kind not in ("check", "pipeline"):
            raise ValueError("Unknown fixture action")
        ident = "fixture-" + str(len(self.jobs) + 1)
        stamp = datetime.now(timezone.utc).isoformat()
        job = {"id": ident, "kind": kind, "label": "Isolated " + kind, "status": "running", "started_at": stamp,
               "summary": "Browser fixture only. No real process started.", "logs": [{"at": stamp, "level": "info", "message": "Fixture process output."}],
               "pending_requests": [], "pipeline_complete": False, "recovery_required": False}
        self.jobs[ident] = job
        if kind == "pipeline":
            job["status"] = "waiting_input"
            job["pending_requests"] = [{"id": "fixture-request", "method": "item/tool/requestUserInput", "answer_kind": "questions", "title": "Fixture input needed", "description": "Test the in-browser answer form.", "questions": [{"id": "choice", "header": "Test choice", "question": "Choose the fixture response.", "options": [{"label": "Continue", "description": "Complete the fixture."}, {"label": "Stop", "description": "Stop the fixture."}]}]}]
        else:
            def finish():
                time.sleep(1)
                if job["status"] == "running":
                    job.update(status="completed", summary="Fixture checks passed.", finished_at=datetime.now(timezone.utc).isoformat())
            threading.Thread(target=finish, daemon=True).start()
        return job

    def stop(self, ident):
        self.jobs[ident].update(status="stopped", summary="Fixture stopped.", pending_requests=[])
        return self.jobs[ident]

    def respond(self, ident, request_id, answer):
        if request_id != "fixture-request" or not isinstance(answer, dict):
            raise ValueError("Incorrect input API contract")
        self.jobs[ident].update(status="agent_finished", summary="Fixture answer received.", pending_requests=[], response=answer)
        return self.jobs[ident]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--with-prospector", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cv-studio-browser-") as directory:
        root = Path(directory)
        make_fixture(root)
        if args.with_prospector:
            from prospector_fixture import make_prospector_fixture
            make_prospector_fixture(root)
            os.environ["PROSPECTOR_OFFLINE"] = "1"
        data = StudioData(root)
        server = StudioHTTPServer(("127.0.0.1", args.port), root, data=data, jobs=BrowserJobs(),
                                  company_logos=CompanyLogoLibrary(root / "fixture-company-logos"))
        signal.signal(signal.SIGTERM, lambda _sig, _frame: threading.Thread(target=server.shutdown, daemon=True).start())
        args.ready.write_text(json.dumps({"url": server.origin, "root": str(root)}))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
