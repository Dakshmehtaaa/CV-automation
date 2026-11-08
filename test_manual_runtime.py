"""Independent manual Build safety tests. Every file/worker/gate is a local fixture."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from runtime import JobError, JobManager, SOL_BUILD, SOL_BUILD_REVIEW


class FixtureData:
    def __init__(self, root):
        self.role_root = root / 'Tailored CVs'
        self.role_root.mkdir()
        self.rows = []

    def active_pipeline(self):
        return {'active': False}

    def roles(self):
        return {'items': self.rows}


class ManualRuntimeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cv-manual-independent-')
        self.root = Path(self.temp.name).resolve()
        self.data = FixtureData(self.root)
        self.manager = JobManager(self.root, self.data, codex_command=['fixture-codex'],
                                  pipeline_owner_reader=lambda: {'active': False})
        self.role = self.data.role_root / 'Manual-Fixture-Role'
        self.role.mkdir()

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def item(self):
        return self.manager.manual_job_queue.enqueue({'url': 'https://jobs.example/role',
            'jd': 'Fixture public role description. ' * 30, 'title': 'Analyst', 'company': 'Fixture'})['item']

    def admission_locks(self):
        return (self.root.joinpath('process.lock').open('a+'),
                self.root.joinpath('pipeline.lock').open('a+'), self.root / 'owner.json')

    def provenance(self):
        source = self.role / 'manual-job-source.txt'
        if not source.exists():
            source.write_text('Fixture public source. ' * 30)
        packet = {'jd': source.read_text(), 'url': 'https://jobs.example/role',
                  'capture_method': 'user_pasted_jd', 'title': 'Analyst', 'company': 'Fixture',
                  'jd_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
        job = {'id': 'fixture-job', 'manual_item_id': 'a' * 16, 'started_at': '2026-10-05T08:00:00+00:00'}
        (self.role / 'manual-build-request.json').write_text(json.dumps({
            'kind': 'cv_studio_manual_build_request', 'item_id': job['manual_item_id'],
            'studio_job_id': job['id'], 'source_url': packet['url'],
            'source_capture': packet['capture_method'], 'jd_sha256': packet['jd_sha256'],
            'created_at': job['started_at'], 'role_path': str(self.role)}))
        return packet, job

    def test_persistence_failure_does_not_claim_queue_or_start_worker(self):
        item = self.item()
        with mock.patch.object(self.manager, '_guard'), \
             mock.patch.object(self.manager, '_manual_build_locks', return_value=self.admission_locks()), \
             mock.patch.object(self.manager, '_save', side_effect=OSError('fixture disk unavailable')), \
             mock.patch('runtime.threading.Thread') as worker:
            with self.assertRaises(JobError):
                self.manager.start_manual_build(item['id'])
            worker.assert_not_called()
        self.assertEqual(self.manager.manual_job_queue.get(item['id'])['status'], 'queued')
        self.assertFalse(self.manager.controls)
        self.assertTrue(all(job['status'] == 'failed' for job in self.manager.jobs.values()))

    def test_worker_start_failure_rolls_back_claim_and_releases_owner(self):
        item = self.item()
        fake_worker = mock.Mock(ident=None)
        fake_worker.start.side_effect = RuntimeError('fixture worker cannot start')
        with mock.patch.object(self.manager, '_guard'), \
             mock.patch.object(self.manager, '_manual_build_locks', return_value=self.admission_locks()), \
             mock.patch.object(self.manager, '_write_manual_owner') as owner, \
             mock.patch('runtime.threading.Thread', return_value=fake_worker):
            with self.assertRaises(JobError):
                self.manager.start_manual_build(item['id'])
        self.assertEqual(self.manager.manual_job_queue.get(item['id'])['status'], 'failed')
        self.assertEqual(owner.call_args.args[2], 'RELEASED')
        self.assertTrue(owner.call_args.kwargs['cleanup_verified'])
        self.assertFalse(self.manager.controls)

    def test_unverified_cleanup_keeps_shared_owner_held(self):
        item = self.item()
        self.manager.manual_job_queue.claim(item['id'], 'fixture-job')
        job = {'id': 'fixture-job', 'kind': 'manual_build', 'manual_item_id': item['id'],
               'started_at': '2026-10-05T08:00:00+00:00', 'finished_at': None,
               'status': 'running', 'logs': [], 'pending_requests': [], 'recovery_required': False}
        control = {'stop': threading.Event(), 'done': threading.Event(),
                   'turn_done': threading.Event(), 'turn_submitted': True,
                   'manual_owner_written': True, 'pipeline_owner_path': self.root / 'owner.json',
                   'pipeline_owner_token': 'fixture-owner', 'recovery_required': True}
        self.manager.jobs[job['id']] = job
        with mock.patch.object(self.manager, '_run_manual_build', side_effect=JobError('unverified child')), \
             mock.patch.object(self.manager, '_write_manual_owner') as owner:
            self.manager._run(job, control)
        self.assertEqual(owner.call_args.args[2], 'OWNERSHIP_UNCONFIRMED')
        self.assertFalse(owner.call_args.kwargs['cleanup_verified'])
        self.assertTrue(job['recovery_required'])
        self.assertEqual(job['status'], 'interrupted')

    def test_draft_is_saved_before_generation_and_cannot_be_ready(self):
        role = {'id': 'a' * 24, 'folder': 'Tailored CVs/' + self.role.name}
        self.data.rows = [role]
        service = mock.Mock()
        service.decorate.return_value = {'application': {'status': 'ready', 'revision': 'v1'}}
        service.set_status.return_value = {'application': {'status': 'saved'}, 'revision': 'v2'}
        self.manager.application_service = service
        receipt = self.manager._manual_mark_saved_pending_review(self.role)
        self.assertEqual(receipt['status'], 'saved')
        service.set_status.assert_called_once_with(role['id'], 'saved', 'v1')
        service.set_status.return_value = {'application': {'status': 'ready'}}
        with self.assertRaises(JobError):
            self.manager._manual_mark_saved_pending_review(self.role)

    def test_existing_settled_application_is_never_rewritten(self):
        self.data.rows = [{'id': 'a' * 24, 'folder': 'Tailored CVs/' + self.role.name}]
        service = mock.Mock()
        service.decorate.return_value = {'application': {'status': 'applied', 'revision': 'v1'}}
        self.manager.application_service = service
        with self.assertRaises(JobError):
            self.manager._manual_mark_saved_pending_review(self.role)
        service.set_status.assert_not_called()

    def test_write_scope_cannot_modify_other_roles_or_profile(self):
        roots = self.manager._manual_build_write_roots(self.role)
        self.assertEqual(set(roots), {self.role.resolve(),
            (self.root / 'Master/.cache/cv-v2' / self.role.name).resolve()})
        for protected in (self.root, self.root / 'PROFILE.md', self.data.role_root / 'Another-Role'):
            self.assertFalse(any(protected.is_relative_to(root) for root in roots))
        outside = self.root / 'Unrelated'
        outside.mkdir()
        with self.assertRaises(JobError):
            self.manager._manual_build_write_roots(outside)

    def test_receipt_rejects_changed_artifacts_or_changed_receipt(self):
        artifact = self.role / 'CV_Candidate.pdf'
        artifact.write_bytes(b'fixture original pdf')
        receipt = {'kind': 'cv_studio_manual_build_checks', 'status': 'passed',
                   'artifact_sha256': {artifact.relative_to(self.root).as_posix():
                       hashlib.sha256(artifact.read_bytes()).hexdigest()}}
        path = self.role / 'manual-build-checks.json'
        path.write_text(json.dumps(receipt))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        packet, job = self.provenance()
        self.assertFalse(self.manager._manual_build_receipt_matches(self.role, digest, packet, job),
                         'An incomplete hand-written pass receipt is not official gate evidence')
        artifact.write_bytes(b'fixture changed after audit')
        self.assertFalse(self.manager._manual_build_receipt_matches(self.role, digest, packet, job))
        path.write_text(json.dumps({**receipt, 'extra': 'changed'}))
        self.assertFalse(self.manager._manual_build_receipt_matches(self.role, digest, packet, job))

    def test_official_gate_exit_failure_cannot_be_replaced_by_pass_claim(self):
        # Even fabricated-looking success words do not replace process exit evidence.
        result = subprocess.CompletedProcess(['fixture'], 1, stdout='All checks passed', stderr='fixture actual failure')
        packet, job = self.provenance()
        with mock.patch('runtime.subprocess.run', return_value=result) as gates:
            receipt = self.manager._manual_build_gate_receipts(self.role, packet, job)
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['checks']['preflight']['returncode'], 1)
        self.assertFalse(self.manager._manual_build_receipt_matches(self.role, receipt['receipt_sha256'], packet, job))
        self.assertEqual(gates.call_count, 4)
        names = [Path(call.args[0][1]).name for call in gates.call_args_list[:3]]
        self.assertEqual(names, ['check_jds.py', 'check_cover_letters.py', 'preflight.py'])

    def successful_gate_fixture(self, *, mutate_source=False):
        names = ['JD.md', 'manual-job-source.txt', 'manual-build-request.json', 'build-spec.json',
                 'why-this-company.md', 'experience-stretch.md', 'language-review.md',
                 'fact-map.bank.json', 'CV_Candidate.html', 'CV_Candidate.pdf',
                 'cover-letter.json', 'Cover_Letter_Candidate.html', 'Cover_Letter_Candidate.pdf']
        for name in names:
            (self.role / name).write_text('fixture saved artifact ' + name)
        packet, job = self.provenance()
        if mutate_source:
            (self.role / 'manual-job-source.txt').write_text('Worker changed original source')
        cache = self.root / 'Master/.cache/cv-v2' / self.role.name
        cache.mkdir(parents=True)
        png = b'\x89PNG\r\n\x1a\nfixture PNG only; no actual visual audit claimed'
        (cache / 'preview.png').write_bytes(png)
        logs = self.root / 'Master/.cache/preflight/run-fixture'
        logs.mkdir(parents=True)
        gates = []
        for script in ['audit_cvs.py', 'verify_cv.py', 'visual_check.py', 'check_spacing.py']:
            log = logs / (script + '.log')
            log.write_text('fixture successful gate')
            gates.append({'script': script, 'status': 'passed', 'exit_code': 0, 'log': str(log)})
        (logs / 'result.json').write_text(json.dumps({'status': 'passed',
            'targets': [self.role.name], 'gates': gates}))

        def command(argv, **_kwargs):
            if argv[0] == 'qlmanage':
                output = Path(argv[argv.index('-o') + 1])
                (output / 'Cover_Letter_Candidate.pdf.png').write_bytes(png)
                stdout = 'Fixture preview created'
            elif Path(argv[1]).name == 'preflight.py':
                stdout = 'Full gate logs: ' + str(logs)
            else:
                stdout = 'Fixture role checks passed'
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr='')

        with mock.patch('runtime.subprocess.run', side_effect=command):
            receipt = self.manager._manual_build_gate_receipts(self.role, packet, job)
        return receipt, packet, job, cache, png

    def test_successful_official_gates_bind_artifacts_and_previews(self):
        receipt, packet, job, cache, png = self.successful_gate_fixture()
        self.assertEqual(receipt['status'], 'passed')
        self.assertEqual(len(receipt['preview_hashes']), 2)
        self.assertTrue(self.manager._manual_build_receipt_matches(self.role, receipt['receipt_sha256'], packet, job))
        (cache / 'preview.png').write_bytes(png + b'changed after the gates')
        self.assertFalse(self.manager._manual_build_receipt_matches(self.role, receipt['receipt_sha256'], packet, job))

    def test_changed_original_source_fails_even_when_all_official_commands_pass(self):
        receipt, _packet, _job, _cache, _png = self.successful_gate_fixture(mutate_source=True)
        self.assertEqual(receipt['status'], 'failed')
        self.assertFalse(receipt['checks']['source_integrity']['passed'])
        self.assertEqual(receipt['checks']['preflight']['returncode'], 0)

    def manual_flow(self, *, actual_views=True, gate_status='passed', audit_hash='fixture-receipt'):
        item = self.item()
        self.manager.manual_job_queue.claim(item['id'], 'fixture-job')
        packet, job = self.provenance()
        packet['jd'] = 'Analyst at Fixture. ' + packet['jd']
        job.update(kind='manual_build', manual_item_id=item['id'], logs=[], pending_requests=[],
                   status='running', manual_build_stages=[])
        self.manager.jobs[job['id']] = job
        control = {'stop': threading.Event()}
        bank = self.root / 'Master/cv/bank.json'
        bank.parent.mkdir(parents=True, exist_ok=True)
        bank.write_text(json.dumps({'roles': [{'id': 'approved-fact'}]}))
        gates = {'status': gate_status, 'receipt_sha256': 'fixture-receipt',
                 'preview_hashes': {'cv': 'fixture-cv-hash', 'cover_letter': 'fixture-letter-hash'}}
        stages = []

        def native(_job, _control, **kwargs):
            stage = kwargs['stage']; stages.append(stage)
            route = {'requested_model': kwargs['spec']['model'], 'requested_effort': kwargs['spec']['effort'],
                     'actual_model': kwargs['spec']['model'], 'actual_effort': kwargs['spec']['effort'],
                     'status': 'completed'}
            if stage == 'admission':
                result = {'decision': 'pass', 'source_complete': True, 'fit_supported': True,
                          'supported_fact_ids': ['approved-fact'], 'job_title': 'Analyst',
                          'company': 'Fixture', 'title_quote': 'Analyst', 'company_quote': 'Fixture'}
            elif stage == 'build':
                result = {'report': 'Fixture only; no model generation'}
            else:
                if actual_views:
                    for image_path in _control['manual_visual_allowed_paths']:
                        path = Path(image_path); path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b'fixture local image')
                        self.manager._manual_build_event(_job, _control,
                            {'method': 'item/completed', 'params': {'item': {'type': 'imageView', 'path': image_path}}})
                result = {'decision': 'pass', 'checks_receipt_sha256': audit_hash,
                    'official_checks': {'job_description': 'pass', 'cover_letter': 'pass', 'preflight': 'pass'},
                    'visual_review': 'pass', 'preview_hashes': gates['preview_hashes'],
                    'visual_observations': ['Fixture CV observation', 'Fixture letter observation'],
                    'factual_review': 'pass', 'factual_observations': ['Fixture approved fact'],
                    'tests_run': ['fixture role checks']}
            return json.dumps(result), route

        with mock.patch.object(self.manager, '_manual_build_source', return_value=packet), \
             mock.patch.object(self.manager, '_manual_public_profile', return_value='Fixture public profile'), \
             mock.patch.object(self.manager, 'capability_reader', return_value={'account_type': 'chatgpt'}), \
             mock.patch.object(self.manager, '_verify_luna_apply_route', return_value={'available': True}), \
             mock.patch.object(self.manager, '_create_manual_role_folder', return_value=self.role), \
             mock.patch.object(self.manager, '_manual_mark_saved_pending_review', return_value={'status': 'saved'}), \
             mock.patch.object(self.manager, '_manual_build_missing_files', return_value=[]), \
             mock.patch.object(self.manager, '_manual_build_gate_receipts', return_value=gates), \
             mock.patch.object(self.manager, '_manual_build_receipt_matches', return_value=True), \
             mock.patch.object(self.manager, '_manual_native_turn', side_effect=native):
            self.manager._run_manual_build(job, control)
        return job, self.manager.manual_job_queue.get(item['id']), stages

    def test_successful_independent_audit_remains_pending_approval_and_saved(self):
        job, queued, stages = self.manual_flow()
        self.assertEqual(stages, ['admission', 'build', 'audit'])
        self.assertEqual(queued['status'], 'built_pending_approval')
        self.assertEqual(job['final_pipeline_approval'], 'pending')
        self.assertEqual(job['application_hold']['status'], 'saved')
        routes = job['manual_build_stages']
        self.assertEqual(routes[1]['actual_model'], 'gpt-6.1-sol')
        self.assertEqual(routes[1]['actual_effort'], 'low')
        self.assertEqual(routes[2]['actual_model'], 'gpt-6.1-sol')
        self.assertEqual(routes[0]['requested_effort'], 'medium')
        self.assertEqual(routes[2]['requested_effort'], 'medium')

    def test_manual_native_turn_submits_sol_model_and_stage_effort(self):
        # Inspect the real request construction, using an entirely local server.
        for stage, spec in (("admission", SOL_BUILD_REVIEW), ("build", SOL_BUILD),
                            ("audit", SOL_BUILD_REVIEW)):
            with self.subTest(stage=stage):
                job = {"id": "fixture-native", "logs": [], "pending_requests": []}
                control = {"stop": threading.Event()}
                calls = []
                class Server:
                    def __init__(self, *args, **kwargs):
                        self.process = type("Process", (), {"pid": 1234})()
                    def initialize(self):
                        return {}
                    def request(self, method, params):
                        calls.append((method, params))
                        if method == "thread/start":
                            return {"thread": {"id": "fixture-thread"}, "model": spec["model"],
                                    "modelProvider": "openai", "reasoningEffort": spec["effort"]}
                        if method == "turn/start":
                            job["last_message"] = "Fixture result"
                            control["turn_status"] = "completed"
                            control["turn_done"].set()
                            return {"turn": {"id": "fixture-turn", "model": spec["model"],
                                             "reasoningEffort": spec["effort"]}}
                        raise AssertionError(method)
                    def close(self):
                        pass
                with mock.patch("runtime.AppServer", Server), \
                     mock.patch.object(self.manager, "_application_mcp_server_names", return_value=[]), \
                     mock.patch.object(self.manager, "_verify_application_tool_scope", return_value={}), \
                     mock.patch.object(self.manager, "_cleanup_background", return_value={"safe": True}):
                    self.manager._manual_native_turn(job, control, spec=spec, stage=stage,
                        prompt="Fixture only", writable=stage == "build", capability={"available": True},
                        write_roots=[self.role] if stage == "build" else [])
                thread, turn = [params for _method, params in calls]
                self.assertEqual(thread["model"], "gpt-6.1-sol")
                self.assertEqual(turn["model"], "gpt-6.1-sol")
                self.assertEqual(thread["config"]["model_reasoning_effort"],
                                 "low" if stage == "build" else "medium")
                self.assertEqual(turn["effort"], "low" if stage == "build" else "medium")
                self.assertFalse(turn["allowProviderModelFallback"])

    def test_claimed_visual_pass_without_actual_image_views_is_held(self):
        _job, queued, _stages = self.manual_flow(actual_views=False)
        self.assertEqual(queued['status'], 'held')

    def test_audit_for_wrong_receipt_is_held(self):
        _job, queued, _stages = self.manual_flow(audit_hash='another-package')
        self.assertEqual(queued['status'], 'held')

    def test_failed_official_checks_stop_before_audit(self):
        _job, queued, stages = self.manual_flow(gate_status='failed')
        self.assertEqual(queued['status'], 'held')
        self.assertEqual(stages, ['admission', 'build'])

    def test_image_view_outside_exact_previews_stops_audit(self):
        unrelated = self.root / 'private-fixture.png'
        unrelated.write_bytes(b'fixture unrelated image')
        job = {'id': 'fixture', 'logs': [], 'status': 'running'}
        control = {'stop': threading.Event(), 'manual_visual_allowed_paths': {str(self.role / 'preview.png')}}
        self.manager._manual_build_event(job, control, {'method': 'item/completed',
            'params': {'item': {'type': 'imageView', 'path': str(unrelated)}}})
        self.assertTrue(control['stop'].is_set())
        self.assertEqual(control['unexpected_tool'], 'imageView')
        self.assertFalse(control.get('manual_visual_views'))

    def test_started_image_view_is_not_completed_inspection_evidence(self):
        preview = self.role / 'preview.png'
        preview.write_bytes(b'fixture allowed image')
        job = {'id': 'fixture', 'logs': [], 'status': 'running'}
        control = {'stop': threading.Event(), 'manual_visual_allowed_paths': {str(preview)}}
        event = {'params': {'item': {'type': 'imageView', 'path': str(preview)}}}
        self.manager._manual_build_event(job, control, {**event, 'method': 'item/started'})
        self.assertFalse(control.get('manual_visual_views'), 'A started view is not proof the model saw the image')
        self.manager._manual_build_event(job, control, {**event, 'method': 'item/completed'})
        self.assertEqual(control['manual_visual_views'], [str(preview)])


if __name__ == '__main__':
    unittest.main()
