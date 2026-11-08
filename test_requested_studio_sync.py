import copy
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import requested_studio_sync as sync


class RequestedSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.auto = self.root / 'Master/auto'
        self.cont = self.auto / 'evidence/requested-build-continuations/test/luna-restart'
        self.cont.mkdir(parents=True)
        self.roles = self.root / 'Tailored CVs'
        self.folder = self.roles / 'Example'
        self.folder.mkdir(parents=True)
        self.url = 'https://example.com/jobs/1'
        self.row = {'id': 'studio1', 'folder': 'Tailored CVs/Example', 'source_url': self.url, 'cv_available': True, 'cover_letter_available': True, 'application': {'status': 'ready', 'source': 'folder', 'canonical_applied': False}}
        self.catalog = [self.row]
        self.refs = {}
        for name in ['JD.md', 'CV_Candidate.html', 'CV_Candidate.pdf', 'Cover_Letter_Candidate.pdf', 'why-this-company.md', 'provenance.json']:
            (self.folder / name).write_text(name)
        self.assignment = {'id': 'role1', 'role_path': str(self.folder), 'jd_sha256': self.ref(self.folder / 'JD.md')['sha256'], 'source_content_sha256': 'a'*64, 'provenance_path': str(self.folder / 'provenance.json'), 'provenance_sha256': self.ref(self.folder / 'provenance.json')['sha256'], 'history_match': {'candidate_url': self.url, 'matches': []}}
        manifest = self.save('manifest', {'kind': 'explicit_luna_build_restart_v1', 'roles': [self.assignment]})
        user = self.save('user', {'message': 'Build these requested CVs through Sync'})
        auth = self.save('authorization', {'kind': 'explicit_requested_continuation_authorization_v1', 'manifest': manifest, 'role_ids': ['role1'], 'user_request_evidence': user})
        raw_source = self.save('raw_source', {'model': 'gpt-6.1-sol', 'reasoning_effort': 'medium', 'audit_executor_id': 'source_auditor', 'requested_manual_job_items': [self.assignment]})
        source = self.save('source', {'manifest': manifest, 'source_audit': raw_source, 'actual_audit_executor_id': 'source_auditor', 'roles': [{'id': 'role1', 'status': 'PASS', 'jd_sha256': self.assignment['jd_sha256'], 'source_content_sha256': 'a'*64}]})
        native = self.save('native', [self.dispatch('build1', 'gpt-6-luna', 'max')])
        stopped = self.save('stopped', {'kind': 'requested_native_workers_terminal_v1', 'manifest': manifest, 'native_dispatches': native, 'observed_at': '2020-10-08T00:00:00+00:00', 'workers': [{'worker_id': 'build1', 'status': 'completed', 'pending_tool_calls': False}]})
        completion = self.save('completion', {'role_id': 'role1', 'model': 'gpt-6-luna', 'reasoning_effort': 'max', 'jd_sha256': self.assignment['jd_sha256'], 'source_content_sha256': 'a'*64, 'provenance_sha256': self.assignment['provenance_sha256'], 'outputs': []})
        artifacts = [self.ref(self.folder / n) for n in ['JD.md', 'CV_Candidate.html', 'CV_Candidate.pdf', 'Cover_Letter_Candidate.pdf', 'why-this-company.md']]
        completion_projection = self.save('completion_projection', {'kind': 'requested_owner_completion_projection_v1', 'raw_completion': completion, 'assignment': self.assignment, 'manifest': manifest, 'role_id': 'role1', 'native_dispatches': native, 'build_worker_id': 'build1', 'jd_sha256': self.assignment['jd_sha256'], 'source_content_sha256': self.assignment['source_content_sha256'], 'provenance_sha256': self.assignment['provenance_sha256'], 'artifact_refs': artifacts})
        packet = self.save('packet', {'source_manifest': manifest['path'], 'roles': [{'assignment': self.assignment, 'completion': completion, 'role_files': artifacts}]})
        audit = self.save('audit', {'executor': {'model': 'gpt-6.1-sol', 'reasoning_effort': 'medium', 'independent_of_authors': True, 'name': 'auditor1'}, 'audited_at_utc': '2020-10-08T00:00:00+00:00', 'input_packet': packet, 'roles': [{'id': 'role1', 'status': 'PASS'}]})
        audit_native = self.save('auditnative', self.dispatch('auditor1', 'gpt-6.1-sol', 'medium'))
        baseline = self.save('baseline', {'used_percent': 10, 'at': '2020-10-08T00:00:10Z', 'window': 'weekly-2020'})
        current = self.save('current', {'used_percent': 10.5, 'at': '2020-10-08T00:00:50Z', 'window': 'weekly-2020'})
        usage = self.save('usage', {'exact_request_usage': None, 'account_before': None, 'account_after': None, 'baseline_receipt': baseline, 'current_receipt': current, 'stage_started_at': '2020-10-08T00:00:05Z'})
        code_files = [self.ref(Path(__file__).parent / n) for n in ['requested_studio_sync.py', 'requested_document_aliases.py', 'studio_sync.py', 'data.py']]
        code_input = self.save('code_input', {'synthetic_fixture': True, 'files': code_files})
        code_audit = self.save('code_audit', {'status': 'PASS', 'executor': {'model': 'gpt-6.1-sol', 'reasoning_effort': 'medium', 'independent_of_authors': True}, 'audited_at_utc': '2020-10-08T00:00:00Z', 'input': code_input, 'code': code_files})
        high_dispatch = self.dispatch('approver1', 'gpt-6.1-sol', 'high'); high_dispatch.update(submitted_at='2020-10-08T00:00:20Z', ack_at='2020-10-08T00:00:21Z')
        high_native = self.save('highnative', high_dispatch)
        self.intent = {'kind': sync.KIND, 'sealed': True, 'continuation_dir': str(self.cont), 'role_id': 'role1', 'manifest': manifest, 'authorization': auth, 'source_audit': source, 'native_dispatches': native, 'workers_terminal': stopped, 'completion': completion, 'completion_projection': completion_projection, 'build_worker_id': 'build1', 'build_audit': audit, 'build_audit_native': audit_native, 'code_audit': code_audit}
        approval = {k: self.intent[k] for k in ['manifest', 'authorization', 'source_audit', 'native_dispatches', 'workers_terminal', 'completion', 'completion_projection', 'build_audit', 'code_audit']}
        approval.update(kind='requested_continuation_sol_high_approval_v1', status='APPROVED', model='gpt-6.1-sol', reasoning_effort='high', role_id='role1', artifact_refs=artifacts, canonical_role_path=str(self.folder), approved_at='2020-10-08T00:01:00+00:00', native_submission=high_native, worker_id='approver1', usage=usage)
        self.intent['approval'] = self.save('approval', approval)
        self.intent_path = self.cont / 'intent.json'
        self.write_intent()
        outer = self
        class Data:
            root = outer.root
            auto = outer.auto
            role_root = outer.roles
            local = outer.root / 'Studio/.local'
            local.mkdir(parents=True)
            def roles(self, **kwargs): return {'items': outer.catalog}
            def _write_lock(self): return nullcontext()
            def _safe(self, p, root): return p
            def _atomic(self, p, raw): p.write_bytes(raw)
            def __init__(self, _): pass
            def _relative(self, p): return 'Tailored CVs/' + p.relative_to(self.role_root).as_posix()
        class Apps:
            def __init__(self, _): pass
            def decorate_list(self, rows): return rows
            def roles(self): return {'items': outer.catalog}
            def role(self, ident): return next(r for r in outer.catalog if r['id'] == ident)
        self.addCleanup(patch.stopall)
        patch.object(sync, 'StudioData', Data).start()
        patch.object(sync, 'Applications', Apps).start()

    def ref(self, path): return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    def save(self, name, value):
        path = self.cont / (name + '.json')
        path.write_text(json.dumps(value))
        return self.ref(path)
    def write_intent(self): self.intent_path.write_text(json.dumps(self.intent))
    def dispatch(self, task, model, effort): return {'raw_args': {'model': model, 'reasoning_effort': effort}, 'raw_result': {'task_name': task}, 'submitted_at': '2020-10-07T23:00:00Z', 'ack_at': '2020-10-07T23:00:01Z'}
    def project(self): return sync.project_requested(self.root, self.intent_path)

    def test_ready_real_readback_no_production_writes(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.project()['application']['status'], 'ready')
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
    def test_changed_proof_revalidated_with_existing_catalog_snapshot(self):
        sync.project_requested(self.root, self.intent_path, validate_aliases=False, _catalog=self.catalog)
        (self.folder / 'CV_Candidate.pdf').write_text('changed after snapshot')
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            sync.project_requested(self.root, self.intent_path, validate_aliases=False, _catalog=self.catalog)

    def test_missing_approval_fails(self):
        del self.intent['approval']; self.write_intent()
        with self.assertRaises(KeyError): self.project()
    def test_changed_artifact_fails(self):
        (self.folder / 'CV_Candidate.pdf').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'hash changed'): self.project()
    def test_applied_and_manual_choice_preserved(self):
        for status in ['applied', 'inactive', 'saved']:
            self.row['application']['status'] = status
            self.row['application']['source'] = 'local'
            self.assertEqual(self.project()['application']['status'], status)
    def test_duplicate_applied_url_links_one_canonical_job(self):
        other = copy.deepcopy(self.row); other.update(id='old', folder='Tailored CVs/old')
        other['application']['status'] = 'applied'; other['application']['canonical_applied'] = True
        self.catalog.append(other)
        result = self.project()
        self.assertEqual(result['studio_role_id'], 'old')
        self.assertEqual(result['application_status'], 'applied')
        self.assertTrue(result['requested_CV_ready'])
    def test_running_worker_fails(self):
        stop = json.loads(Path(self.intent['workers_terminal']['path']).read_text()); stop['workers'][0]['pending_tool_calls'] = True
        self.intent['workers_terminal'] = self.save('stopped', stop); self.write_intent()
        with self.assertRaisesRegex(ValueError, 'not all stopped|hash changed'): self.project()
    def test_wrong_role_fails(self):
        self.intent['role_id'] = 'other'; self.write_intent()
        with self.assertRaisesRegex(ValueError, 'exactly one'): self.project()
    def test_idempotent_immutable_readback(self):
        output = self.cont / 'cv-studio-sync/role1/readback.v1.json'
        first = sync.execute_requested(self.root, self.intent_path, output)
        raw = output.read_bytes()
        self.assertEqual(sync.execute_requested(self.root, self.intent_path, output), first)
        self.assertEqual(output.read_bytes(), raw)
        self.row['application']['status'] = 'applied'
        with self.assertRaisesRegex(ValueError, 'reconcile'):
            sync.execute_requested(self.root, self.intent_path, output)
    def test_wrong_native_model_fails(self):
        self.intent['native_dispatches'] = self.save('native', [self.dispatch('build1', 'gpt-6.1-sol', 'max')])
        self.write_intent()
        with self.assertRaisesRegex(ValueError, 'Luna MAX|hash changed'): self.project()
    def test_future_approval_rejected(self):
        approval = json.loads(Path(self.intent['approval']['path']).read_text())
        approval['approved_at'] = '2099-01-01T00:00:00Z'
        self.intent['approval'] = self.save('approval', approval); self.write_intent()
        with self.assertRaisesRegex(ValueError, 'future-dated'): self.project()
    def test_high_submission_before_cleanup_rejected(self):
        native = self.save('highnative', self.dispatch('approver1', 'gpt-6.1-sol', 'high'))
        approval = json.loads(Path(self.intent['approval']['path']).read_text()); approval['native_submission'] = native
        self.intent['approval'] = self.save('approval', approval); self.write_intent()
        with self.assertRaisesRegex(ValueError, 'follow completed audit'): self.project()

    def real_surface(self):
        """Rebind synthetic source fields before using unmocked Studio classes."""
        patch.stopall()
        (self.folder/'JD.md').write_text('# Analyst\nCompany: Test\nSource URL: '+self.url+'\n')
        self.assignment['jd_sha256'] = self.ref(self.folder/'JD.md')['sha256']
        manifest = self.save('manifest', {'kind':'explicit_luna_build_restart_v1','roles':[self.assignment]}); self.intent['manifest'] = manifest
        auth = json.loads(Path(self.intent['authorization']['path']).read_text()); auth['manifest']=manifest; self.intent['authorization']=self.save('authorization',auth)
        raw = json.loads((self.cont/'raw_source.json').read_text()); raw['requested_manual_job_items']=[self.assignment]; rawref=self.save('raw_source',raw)
        source=json.loads(Path(self.intent['source_audit']['path']).read_text()); source.update(manifest=manifest,source_audit=rawref); source['roles'][0]['jd_sha256']=self.assignment['jd_sha256']; self.intent['source_audit']=self.save('source',source)
        stop=json.loads(Path(self.intent['workers_terminal']['path']).read_text()); stop['manifest']=manifest; self.intent['workers_terminal']=self.save('stopped',stop)
        packet=json.loads((self.cont/'packet.json').read_text()); packet['roles'][0]['assignment']=self.assignment
        artifacts=[self.ref(Path(r['path'])) for r in packet['roles'][0]['role_files']]; packet['roles'][0]['role_files']=artifacts
        audit=json.loads(Path(self.intent['build_audit']['path']).read_text()); audit['input_packet']=self.save('packet',packet); self.intent['build_audit']=self.save('audit',audit)
        projection=json.loads(Path(self.intent['completion_projection']['path']).read_text()); projection.update(manifest=manifest,assignment=self.assignment,artifact_refs=artifacts,jd_sha256=self.assignment['jd_sha256']); self.intent['completion_projection']=self.save('completion_projection',projection)
        approval=json.loads(Path(self.intent['approval']['path']).read_text()); approval.update({k:self.intent[k] for k in ('manifest','authorization','source_audit','native_dispatches','workers_terminal','completion','completion_projection','build_audit','code_audit')}); approval['artifact_refs']=artifacts; self.intent['approval']=self.save('approval',approval); self.write_intent()
        output=self.cont/'cv-studio-sync/role1/readback.v1.json'
        result=sync.execute_requested(self.root,self.intent_path,output)
        from data import StudioData
        from applications import Applications
        data=StudioData(self.root)
        return data, Applications(data), result

    def test_real_surface_transitive_authorization_change_quarantines_requested_cv(self):
        data, apps, result=self.real_surface()
        self.assertTrue(data.roles()['items'][0].get('approved_document_alias'))
        Path(self.intent['authorization']['path']).write_text('{}')
        row=apps.roles()['items'][0]
        self.assertFalse(row.get('approved_document_alias'))
        self.assertFalse(row['cv_available'])
        self.assertNotEqual(row['application']['status'],'ready')

    def test_real_surface_unapproved_text_and_pdf_not_exposed(self):
        data, apps, result=self.real_surface()
        (self.folder/'application-answers.md').write_text('UNAPPROVED NEW TEXT')
        (self.folder/'CV_unapproved.pdf').write_text('UNAPPROVED PDF')
        detail=data.role(result['studio_role_id'])
        self.assertFalse(any(r['content']=='UNAPPROVED NEW TEXT' for r in detail['documents']))
        self.assertFalse(any(r['name']=='CV_unapproved.pdf' for r in detail['artifacts']))
        from requested_document_aliases import source_asset_allowed
        self.assertFalse(source_asset_allowed(data,self.folder,'CV_unapproved.pdf'))
        self.assertTrue(source_asset_allowed(data,self.folder,'CV_Candidate.pdf'))

    def test_real_surface_changed_transitive_native_proof_invalidates_alias(self):
        data, apps, result=self.real_surface()
        Path(self.intent['native_dispatches']['path']).write_text('[]')
        self.assertNotEqual(apps.roles()['items'][0]['application']['status'],'ready')

    def test_existing_approved_documents_survive_app_code_update(self):
        data, apps, result = self.real_surface()
        original_sha = sync._sha
        def updated_code(path):
            return '0' * 64 if path == Path(sync.__file__).parent / 'data.py' else original_sha(path)
        with patch.object(sync, '_sha', side_effect=updated_code):
            row = apps.roles()['items'][0]
            self.assertEqual(row['application']['status'], 'ready')
            self.assertTrue(row['approved_document_alias'])
            from requested_document_aliases import source_asset_allowed
            self.assertTrue(source_asset_allowed(data, self.folder, 'CV_Candidate.pdf'))
            with self.assertRaisesRegex(ValueError, 'Reference hash changed'):
                sync.execute_requested(self.root, self.intent_path, self.cont / 'cv-studio-sync/new.json')
            (self.folder / 'CV_Candidate.pdf').write_text('changed PDF')
            self.assertFalse(apps.roles()['items'][0]['cv_available'])
            self.assertFalse(source_asset_allowed(data, self.folder, 'CV_Candidate.pdf'))

    def test_historical_code_audit_input_mismatch_or_duplicates_fail(self):
        for duplicate in (False, True):
            audit = json.loads(Path(self.intent['code_audit']['path']).read_text())
            files = copy.deepcopy(audit['code'])
            if duplicate:
                files.append(files[0])
            else:
                files[0]['sha256'] = '0' * 64
            audit['input'] = self.save('changed-code-input', {'files': files})
            self.intent['code_audit'] = self.save('changed-code-audit', audit)
            self.write_intent()
            with self.assertRaisesRegex(ValueError, 'Historical code audit differs|duplicate historical code binding'):
                sync.project_requested(self.root, self.intent_path, validate_current_code=False)

    def update_usage(self, used_percent=10.5, window='weekly-2020', **fields):
        usage=json.loads((self.cont/'usage.json').read_text())
        usage['current_receipt']=self.save('current', {'used_percent':used_percent,'at':'2020-10-08T00:00:50Z','window':window})
        usage.update(fields)
        approval=json.loads(Path(self.intent['approval']['path']).read_text())
        approval['usage']=self.save('usage',usage)
        self.intent['approval']=self.save('approval',approval); self.write_intent()

    def test_usage_one_point_stops_new_reviews_but_final_pass_allowed(self):
        self.update_usage(11)
        result=self.project()
        self.assertEqual(result['usage_gate']['status'],'STOP_NEW_REVIEW')
        self.assertFalse(result['usage_gate']['allow_new_review'])

    def test_usage_two_points_holds_approval(self):
        self.update_usage(12)
        with self.assertRaisesRegex(ValueError,'two points'): self.project()

    def test_usage_missing_reset_or_changed_window_holds(self):
        for value,window,fields in [(None,'weekly-2020',{}),(10.5,'other-window',{}),(10.5,'weekly-2020',{'reset':True})]:
            self.update_usage(value,window,**fields)
            with self.assertRaises(ValueError): self.project()

    def test_requested_folder_without_alias_is_not_automatically_ready(self):
        data,apps,result=self.real_surface()
        (data.local/'requested-document-aliases.json').unlink()
        row=apps.roles()['items'][0]
        self.assertNotEqual(row['application']['status'],'ready')
        self.assertFalse(row['cv_available'])

if __name__ == '__main__': unittest.main()
