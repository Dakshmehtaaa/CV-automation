"""Fixture-only local Studio Sync producer tests; no network or model use."""
from __future__ import annotations
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
PIPELINE = HERE.parent / "Master" / "auto"
LOCAL = PIPELINE / "local"
sys.path.insert(0, str(PIPELINE))
sys.path.insert(0, str(LOCAL))
sys.path.insert(0, str(HERE))

import run_metrics
import build_evidence
import next_step
from data import StudioData
from studio_sync import (StudioSyncError, _artifact_path, _receipt_matches_role,
                         main as sync_main, project_run)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


class StudioSyncFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="studio-sync-fixture-")
        self.workspace = Path(self.temp.name)
        self.resources = self.workspace / "Resources"
        self.run_id = "2030-01-03-policy4-studio-fixture"
        self.run_dir = self.resources / "Master/auto/evidence/runs" / self.run_id
        self.run_dir.mkdir(parents=True)
        (self.resources / "Master/auto/evidence/runs").mkdir(parents=True, exist_ok=True)
        (self.workspace / "Tailored CVs").mkdir()
        (self.resources / "Tailored CVs").symlink_to("../Tailored CVs")
        self._run_metrics("init", "--started-at", "2030-01-03T09:00:00Z",
                          "--workflow-mode", "manual", "--policy-version", "4")
        save_json(self.run_dir / "batch-manifest.json", {
            "batch_id": self.run_id, "policy_version": 4,
            "batch_started_at": "2030-01-03T09:00:00Z"})
        self.roles = [self._make_role(1), self._make_role(2)]

    def tearDown(self):
        self.temp.cleanup()

    def _run_metrics(self, *args):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            run_metrics.main([args[0], "--run-dir", str(self.run_dir), *args[1:]])

    def _owner(self, stage: str, worker: str, start: str, end: str, *, role_id: str | None = None,
               role: str | None = None):
        args = ["start", "--stage", stage, "--worker-id", worker, "--started-at", start]
        if role_id:
            args += ["--role-id", role_id]
        if role:
            args += ["--role", role]
        self._run_metrics(*args)
        self._run_metrics("finish", "--stage", stage, "--worker-id", worker,
                          "--finished-at", end, "--status", "done")

    def _make_role(self, index: int) -> dict:
        public_id = f"public:JOB-{120 + index}"
        folder_name = f"Fixture Company {index} - ESG Analyst"
        canonical = f"Tailored CVs/{folder_name}"
        role_dir = self.workspace / canonical
        role_dir.mkdir(parents=True)
        company = f"Fixture Company {index}"
        title = "ESG Analyst"
        url = f"https://example.test/jobs/{index}"
        (role_dir / "JD.md").write_text(f"# {title}\n\nCompany: {company}\nSource URL: {url}\n", encoding="utf-8")
        for name, content in {
            "CV_Candidate.html": f"<html><body>CV {index}</body></html>",
            "CV_Candidate.pdf": f"pdf-cv-{index}",
            "Cover_Letter_Candidate.pdf": f"pdf-letter-{index}",
            "motivation.md": f"Motivation {index}.",
        }.items():
            (role_dir / name).write_text(content, encoding="utf-8")

        studio = StudioData(self.resources)
        inventory_matches = [row for row in studio.roles()["items"] if row.get("folder") == canonical]
        self.assertEqual(len(inventory_matches), 1)
        studio_role_id = inventory_matches[0]["id"]
        self.assertNotEqual(studio_role_id, public_id)

        base_time = index * 10
        hunt_at = f"2030-01-03T09:{1 + base_time:02d}:00+00:00"
        artifact_at = f"2030-01-03T09:{4 + base_time:02d}:00+00:00"
        audit_at = f"2030-01-03T09:{7 + base_time:02d}:00+00:00"
        approval_at = f"2030-01-03T09:{10 + base_time:02d}:00+00:00"
        role_label = f"{company} - {title}"

        def evidence_file(name: str, value: dict) -> tuple[Path, str]:
            path = self.run_dir / "receipts" / f"{index}-{name}.json"
            save_json(path, value)
            return path, digest(path)

        hunt_path, hunt_hash = evidence_file("hunt-audit", {
            "kind": "hunt_audit_role_receipt_v1", "status": "DONE", "role_id": public_id,
            "canonical_role_path": canonical, "accepted_role_paths": [canonical], "at": hunt_at})
        self._owner("hunt_audit", f"hunt_audit:fixture-{index}",
                    f"2030-01-03T09:{base_time:02d}:00Z", f"2030-01-03T09:{1 + base_time:02d}:00Z")
        self._run_metrics("milestone", "--stage", "hunt_audit", "--worker-id", f"hunt_audit:fixture-{index}",
                          "--role-id", public_id, "--role", role_label, "--at", hunt_at,
                          "--status", "accepted", "--evidence-sha256", hunt_hash)

        self._owner("build", f"build:fixture-{index}", f"2030-01-03T09:{1 + base_time:02d}:00Z",
                    f"2030-01-03T09:{4 + base_time:02d}:00Z", role_id=public_id, role=role_label)
        artifact_path, artifact_hash = evidence_file("artifact", {
            "kind": "build_artifact_receipt_v1", "status": "DONE", "role_id": public_id,
            "canonical_role_path": canonical, "at": artifact_at})
        self._run_metrics("artifact", "--worker-id", f"build:fixture-{index}", "--completed-at", artifact_at,
                          "--role", role_label, "--role-id", public_id, "--company", company,
                          "--title", title, "--artifact-sha256", artifact_hash)

        build_audit_path, build_audit_hash = evidence_file("build-audit", {
            "kind": "build_audit_receipt_v1", "status": "PASS", "role_id": public_id,
            "canonical_role_path": canonical, "at": audit_at})
        self._owner("build_audit", f"build_audit:fixture-{index}",
                    f"2030-01-03T09:{4 + base_time:02d}:00Z", f"2030-01-03T09:{7 + base_time:02d}:00Z")
        self._run_metrics("milestone", "--stage", "build_audit", "--worker-id", f"build_audit:fixture-{index}",
                          "--role-id", public_id, "--role", role_label, "--at", audit_at,
                          "--status", "passed", "--evidence-sha256", build_audit_hash,
                          "--predecessor-sha256", artifact_hash)

        sol_path, sol_hash = evidence_file("sol-review", {
            "kind": "sol_review_receipt_v1", "status": "APPROVED", "role_id": public_id,
            "canonical_role_path": canonical, "at": approval_at})
        self._owner("sol_review", f"sol_review:fixture-{index}",
                    f"2030-01-03T09:{7 + base_time:02d}:00Z", f"2030-01-03T09:{10 + base_time:02d}:00Z")
        self._run_metrics("milestone", "--stage", "sol_review", "--worker-id", f"sol_review:fixture-{index}",
                          "--role-id", public_id, "--role", role_label, "--at", approval_at,
                          "--status", "approved", "--evidence-sha256", sol_hash,
                          "--predecessor-sha256", build_audit_hash)

        # Exercise the exact Resources/Tailored CVs reverse alias used by Studio.
        refs = []
        for name, filename in (
            ("html", "CV_Candidate.html"), ("pdf", "CV_Candidate.pdf"),
            ("jd", "JD.md"), ("letter_pdf", "Cover_Letter_Candidate.pdf"),
            ("motivation", "motivation.md"),
        ):
            source = self.resources / "Tailored CVs" / folder_name / filename
            refs.append({"name": name, "path": source.relative_to(self.workspace).as_posix(),
                         "sha256": digest(source)})
        manifest_path = self.run_dir / "source-manifests" / f"{index}-artifact-manifest.json"
        save_json(manifest_path, {
            "kind": "cv_studio_artifact_manifest_v1", "sealed": True,
            "run_id": self.run_id, "role_id": public_id,
            "canonical_role_path": canonical, "studio_role_id": studio_role_id,
            "receipt_path": artifact_path.relative_to(self.workspace).as_posix(),
            "receipt_sha256": artifact_hash, "artifact_refs": refs,
        })
        gate_manifest_path = self.run_dir / "source-manifests" / f"{index}-gate-refs.json"
        save_json(gate_manifest_path, {
            "kind": "cv_studio_gate_refs_v1", "sealed": True,
            "run_id": self.run_id, "role_id": public_id,
            "gates": {
                "hunt_audit": {"receipt_path": hunt_path.relative_to(self.workspace).as_posix(),
                               "sha256": hunt_hash, "status": "DONE", "at": hunt_at},
                "build_audit": {"receipt_path": build_audit_path.relative_to(self.workspace).as_posix(),
                                "sha256": build_audit_hash, "status": "PASS", "at": audit_at},
                "sol_review": {"receipt_path": sol_path.relative_to(self.workspace).as_posix(),
                               "sha256": sol_hash, "status": "APPROVED", "at": approval_at},
            },
        })
        return {"public_id": public_id, "studio_id": studio_role_id, "role_dir": role_dir,
                "canonical_path": canonical, "artifact_manifest": manifest_path,
                "gate_manifest": gate_manifest_path, "artifact_receipt": artifact_path,
                "artifact_hash": artifact_hash, "gate_paths": {
                    "hunt_audit": hunt_path, "build_audit": build_audit_path, "sol_review": sol_path}}

    def _produce(self, role: dict) -> Path:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            status = sync_main([
                "--root", str(self.resources), "--run-id", self.run_id, "--role", role["public_id"],
                "--artifact-manifest", str(role["artifact_manifest"]),
                "--gate-refs", str(role["gate_manifest"]), "--output", "readback.v1.json",
            ])
        self.assertEqual(status, 0)
        return self.run_dir / "cv-studio-sync" / role["public_id"] / "readback.v1.json"

    def test_two_public_roles_produce_separate_hash_bound_studio_readbacks(self):
        outputs = [self._produce(role) for role in self.roles]
        self.assertEqual(len(set(outputs)), 2)
        for role, output in zip(self.roles, outputs):
            readback = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual((readback["kind"], readback["status"], readback["run_id"]),
                             ("cv_studio_sync_readback_v1", "DONE", self.run_id))
            row, = readback["roles"]
            self.assertEqual((row["role_id"], row["studio_role_id"]), (role["public_id"], role["studio_id"]))
            self.assertNotEqual(row["role_id"], row["studio_role_id"])
            self.assertEqual(row["canonical_role_path"], role["canonical_path"])
            self.assertEqual(set(ref["name"] for ref in row["artifact_refs"]),
                             {"html", "pdf", "jd", "letter_pdf", "motivation"})
            self.assertEqual(set(row["gate_refs"]), {"hunt_audit", "build_audit", "sol_review"})
            self.assertTrue(readback["history_projection_sha256"])
            self.assertFalse(any(m["stage"] == "sync" for m in readback["history_projection"]["role_milestones"]))
            self.assertEqual(row["studio_role_readback"], __import__("hashlib").sha256(
                json.dumps({key: row[key] for key in ("canonical_role_path", "company", "title", "url", "artifact_refs")},
                           sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest())
            # Sync stays local: the recorder has no success milestone yet.
            saved = json.loads((self.run_dir / "stage-metrics.json").read_text(encoding="utf-8"))
            self.assertFalse(any(m.get("stage") == "sync" for m in saved.get("role_milestones", [])))

    def test_missing_gate_ref_and_changed_receipts_fail_closed(self):
        role = self.roles[0]
        save_json(role["gate_manifest"], {
            "kind": "cv_studio_gate_refs_v1", "sealed": True, "run_id": self.run_id,
            "role_id": role["public_id"], "gates": {"hunt_audit": {}}})
        error_output = StringIO()
        with redirect_stderr(error_output), self.assertRaises(SystemExit) as raised:
            sync_main(["--root", str(self.resources), "--run-id", self.run_id, "--role", role["public_id"],
                       "--artifact-manifest", str(role["artifact_manifest"]),
                       "--gate-refs", str(role["gate_manifest"]), "--output", "readback.v1.json"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("hunt_audit gate path is missing", error_output.getvalue())

        # A valid staged chain still fails if the actual receipt bytes change later.
        role = self.roles[1]
        self._produce(role)
        gate = role["gate_paths"]["build_audit"]
        save_json(gate, {"kind": "build_audit_receipt_v1", "status": "PASS",
                         "role_id": role["public_id"], "canonical_role_path": role["canonical_path"],
                         "at": "2030-01-03T09:00:00+00:00"})
        with self.assertRaisesRegex(StudioSyncError, "does not match its current sealed role milestone"):
            project_run(self.resources, self.run_id, role["public_id"])

    def test_mutated_jd_or_replaced_pdf_cannot_pass_saved_artifact_hashes(self):
        for role, filename, contents in (
            (self.roles[0], "JD.md", "\nMutated factual source.\n"),
            (self.roles[1], "CV_Candidate.pdf", "replacement PDF bytes"),
        ):
            with self.subTest(filename=filename):
                self._produce(role)
                artifact = role["role_dir"] / filename
                artifact.write_bytes((artifact.read_bytes() + contents.encode("utf-8")))
                with self.assertRaisesRegex(StudioSyncError, "hash changed"):
                    project_run(self.resources, self.run_id, role["public_id"])

    def test_tidy_roles_manifest_is_required_for_legacy_artifact_symlink(self):
        role = self.roles[0]
        support_dir_name = f"{role['role_dir'].name} support"
        support_dir = role["role_dir"] / support_dir_name
        support_dir.mkdir()
        source_jd = role["role_dir"] / "JD.md"
        original_jd = source_jd.read_bytes()
        (support_dir / "JD.md").write_bytes(original_jd)
        source_jd.unlink()
        alias_target = f"{support_dir_name}/JD.md"
        source_jd.symlink_to(alias_target)
        tidy_manifest = self.resources / "Master/auto/local/pipeline-repair/tidy_roles_manifest.json"
        save_json(tidy_manifest, {
            "schema_version": 1,
            "manifest_id": "fixture-tidy-roles-v1",
            "root_relative": "Tailored CVs",
            "roles": [{
                "role_path": role["canonical_path"].removeprefix("Tailored CVs/"),
                "support_dir": support_dir_name,
                "files": [{
                    "kind": "job_description",
                    "legacy_link_path": f"{role['canonical_path'].removeprefix('Tailored CVs/')}/JD.md",
                    "original_path": f"{role['canonical_path'].removeprefix('Tailored CVs/')}/JD.md",
                    "legacy_link_target": alias_target,
                    "support_path": f"{support_dir_name}/JD.md",
                    "sha256": digest(support_dir / "JD.md"),
                    "size_bytes": len(original_jd),
                }],
            }],
        })

        output = self._produce(role)
        readback = json.loads(output.read_text(encoding="utf-8"))
        refs = {row["name"]: row for row in readback["roles"][0]["artifact_refs"]}
        self.assertEqual(refs["jd"]["path"], f"{role['canonical_path']}/JD.md")

        # The same symlink is rejected after removing its exact tidy_roles binding.
        tidy_manifest.unlink()
        with self.assertRaisesRegex(StudioSyncError, "compatibility manifest"):
            _artifact_path(f"{role['canonical_path']}/JD.md", digest(support_dir / "JD.md"),
                           StudioData(self.resources), role["canonical_path"], role["role_dir"], "Artifact JD")
        with self.assertRaises(StudioSyncError):
            project_run(self.resources, self.run_id, role["public_id"])

    def test_manifest_hash_and_role_id_must_match_exact_chain(self):
        role = self.roles[0]
        manifest = json.loads(role["artifact_manifest"].read_text(encoding="utf-8"))
        manifest["artifact_refs"][0]["sha256"] = "0" * 64
        save_json(role["artifact_manifest"], manifest)
        error_output = StringIO()
        with redirect_stderr(error_output), self.assertRaises(SystemExit) as raised:
            sync_main(["--root", str(self.resources), "--run-id", self.run_id, "--role", role["public_id"],
                       "--artifact-manifest", str(role["artifact_manifest"]),
                       "--gate-refs", str(role["gate_manifest"]), "--output", "readback.v1.json"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("Artifact html hash changed", error_output.getvalue())
        with self.assertRaisesRegex(StudioSyncError, "lacks a current|current accepted"):
            project_run(self.resources, self.run_id, "public:UNKNOWN")

    def test_multirole_receipt_cannot_cross_bind_id_and_canonical_path(self):
        first, second = self.roles
        receipt = self.run_dir / "receipts/multi-role-hunt-audit.json"
        save_json(receipt, {"roles": [
            {"role_id": first["public_id"], "canonical_role_path": first["canonical_path"]},
            {"role_id": second["public_id"], "canonical_role_path": second["canonical_path"]},
        ]})
        with self.assertRaisesRegex(StudioSyncError, "does not bind this public role ID"):
            _receipt_matches_role(receipt, first["public_id"], second["canonical_path"],
                                  second["role_dir"], require_path=True)
        _receipt_matches_role(receipt, first["public_id"], first["canonical_path"],
                              first["role_dir"], require_path=True)

    def test_two_role_producer_passes_actual_ledger_and_completion_validators(self):
        # The real validators discover and execute the saved projector from the
        # fixture Resources tree, matching their production path.
        projector_target = self.resources / "Studio/studio_sync.py"
        projector_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERE / "studio_sync.py", projector_target)
        queue_items = []
        for index, role in enumerate(self.roles, start=1):
            output = self._produce(role)
            readback = json.loads(output.read_text(encoding="utf-8"))
            row, = readback["roles"]
            refs = {ref["name"]: ref for ref in row["artifact_refs"]}
            intake = {"role": role["role_dir"].name, "folder": role["canonical_path"],
                      "company": row["company"], "job_title": row["title"], "url": row["url"]}
            ledger_artifact = {
                "paths": {name: refs[name]["path"] for name in ("html", "pdf", "jd")},
                "sha256": {name: refs[name]["sha256"] for name in ("html", "pdf", "jd")},
            }
            ledger = build_evidence._cv_studio_surface_extra(
                self.resources, str(output), intake, ledger_artifact)
            self.assertEqual(ledger["cv_studio_run_id"], self.run_id)
            self.assertEqual(ledger["cv_studio_gate_refs"]["sol_review"]["status"], "APPROVED")

            artifacts = {
                key: {"path": str(role["role_dir"] / filename),
                      "sha256": digest(role["role_dir"] / filename)}
                for key, filename in (("cv_pdf", "CV_Candidate.pdf"),
                                      ("letter_pdf", "Cover_Letter_Candidate.pdf"),
                                      ("motivation", "motivation.md"))
            }
            artifact_hashes = {key: value["sha256"] for key, value in artifacts.items()}
            stage_specs = (
                ("build_audit", "PASS", "2030-01-03T10:00:00+00:00", "medium"),
                ("sol_review", "APPROVED", "2030-01-03T10:01:00+00:00", "high"),
                ("sync", "VERIFIED", "2030-01-03T10:02:00+00:00", None),
            )
            stages = {}
            for name, status, at, effort in stage_specs:
                wrapper = {"status": status, "at": at,
                           "role_path": str(role["role_dir"].resolve()),
                           "accepted_role_paths": [str(role["role_dir"].resolve())],
                           "artifact_hashes": artifact_hashes}
                if name == "build_audit":
                    wrapper.update(actual_model="gpt-6.1-sol", actual_effort=effort,
                                   source_receipt={"path": str(role["gate_paths"][name]),
                                                   "sha256": digest(role["gate_paths"][name])})
                elif name == "sol_review":
                    wrapper.update(actual_model="gpt-6.1-sol", actual_effort=effort,
                                   source_receipt={"path": str(role["gate_paths"][name]),
                                                   "sha256": digest(role["gate_paths"][name])})
                else:
                    wrapper.update(surface="cv_studio", verified_readback=True,
                                   studio_readback={"path": str(output), "sha256": digest(output)})
                wrapper_path = self.run_dir / "completion-fixture" / f"{index}-{name}.json"
                save_json(wrapper_path, wrapper)
                stages[name] = {"receipt_path": str(wrapper_path),
                                "receipt_sha256": digest(wrapper_path)}

            completion = {"kind": "cv_completion_receipt_v2", "policy_version": 4,
                          "status": "DONE", "role_path": str(role["role_dir"].resolve()),
                          "artifacts": artifacts, "stages": stages}
            completion_path = self.run_dir / "completion-fixture" / f"{index}-completion.json"
            save_json(completion_path, completion)
            accepted_path = self.run_dir / "completion-fixture" / f"{index}-accepted.json"
            save_json(accepted_path, {"accepted_role_paths": [str(role["role_dir"].resolve())]})
            queue_items.append({"policy_version": 4, "status": "complete",
                                "accepted_role_path": str(role["role_dir"].resolve()),
                                "accepted_receipt": str(accepted_path), "parent_run": self.run_id,
                                "completion_receipt": str(completion_path),
                                "completion_receipt_sha256": digest(completion_path)})

        queue_path = self.resources / "Master/auto/local/completion-queue.fixture.json"
        save_json(queue_path, {"version": 1, "items": queue_items})
        self.assertIsNone(next_step.completion_admission(queue_path))


if __name__ == "__main__":
    unittest.main()
