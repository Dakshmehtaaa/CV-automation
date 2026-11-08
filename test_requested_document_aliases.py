import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from data import StudioData
from applications import Applications
from requested_document_aliases import persist_alias

class AliasIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.root = self.workspace / 'Resources'
        self.roles = self.workspace / 'Tailored CVs'; self.roles.mkdir()
        self.root.mkdir(); (self.root / 'Tailored CVs').symlink_to('../Tailored CVs')
        self.cont = self.root / 'Master/auto/evidence/requested-build-continuations/test'
        self.cont.mkdir(parents=True)
        self.data = StudioData(self.root); self.apps = Applications(self.data)
        for name in ['Old', 'New']:
            folder = self.roles / name; folder.mkdir()
            (folder / 'JD.md').write_text('# Analyst\nCompany: Test\nSource URL: https://example.com/jobs/1\n')
            (folder / 'CV_Candidate.pdf').write_text('pdf-'+name)
            (folder / 'Cover_Letter_Candidate.pdf').write_text('letter-'+name)
            (folder / 'why-this-company.md').write_text('motivation-'+name)
        raw = self.data.roles(apply_document_aliases=False)['items']
        self.old = next(r for r in raw if r['folder'].endswith('/Old'))
        self.new = next(r for r in raw if r['folder'].endswith('/New'))
        self.artifacts = [self.ref(self.roles / 'New' / name) for name in ['CV_Candidate.pdf','Cover_Letter_Candidate.pdf','why-this-company.md']]
        self.approval_path = self.cont / 'approval.json'; self.approval_path.write_text(json.dumps({'status':'APPROVED','artifact_refs':self.artifacts}))
        self.intent_path = self.cont / 'intent.json'; self.intent_path.write_text('{}')
        self.validation_patch = patch('requested_studio_sync.project_requested', side_effect=lambda *a, **k: {'artifact_refs': self.artifacts, 'studio_role_id': self.old['id'], 'artifact_studio_role_id': self.new['id']})
        self.validation_patch.start(); self.addCleanup(self.validation_patch.stop)
        self.result = {'canonical_alias': {'canonical_studio_role_id':self.old['id'],'approved_document_studio_role_id':self.new['id'],'same_posting_studio_ids':[self.old['id'],self.new['id']]},'canonical_url':'https://example.com/jobs/1','artifact_refs':self.artifacts,'intent':self.ref(self.intent_path),'role_id':'requested1'}
    def ref(self,p): return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    def persist(self): persist_alias(self.data,self.result,{'approval':self.ref(self.approval_path)})
    def test_one_canonical_applied_record_exposes_new_documents(self):
        self.apps.set_status(self.old['id'],'applied')
        before = (self.data.local / 'applications.json').read_bytes()
        self.persist()
        rows = self.apps.roles()['items']; self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['id'],self.old['id']); self.assertEqual(rows[0]['application']['status'],'applied')
        self.assertEqual(rows[0]['document_folder'],self.new['folder'])
        self.assertEqual(self.data.role(self.old['id'])['documents'][0]['content'],'motivation-New')
        self.assertEqual(before,(self.data.local / 'applications.json').read_bytes())
    def test_manual_inactive_preserved(self):
        self.apps.set_status(self.old['id'],'inactive'); self.persist()
        self.assertEqual(self.apps.role(self.old['id'])['application']['status'],'inactive')
    def test_changed_approved_artifact_invalidates_alias(self):
        self.persist(); (self.roles / 'New/CV_Candidate.pdf').write_text('changed')
        self.assertEqual(len(self.data.roles()['items']),1)
        self.assertEqual(self.data.roles()['items'][0]['id'],self.old['id'])
    def test_changed_approval_invalidates_alias(self):
        self.persist(); self.approval_path.write_text('{}')
        self.assertEqual(len(self.data.roles()['items']),1)
        self.assertEqual(self.data.roles()['items'][0]['id'],self.old['id'])

    def test_snapshot_does_not_cache_approval_proof(self):
        from requested_document_aliases import apply_aliases, source_asset_allowed
        self.persist()
        raw = self.data.roles(apply_document_aliases=False)['items']
        catalog = self.apps.decorate_list({'items': raw})['items']
        self.assertTrue(apply_aliases(self.data, raw, _catalog=catalog)[0].get('approved_document_alias'))
        self.approval_path.write_text('{}')
        visible = apply_aliases(self.data, raw, _catalog=catalog)
        self.assertFalse(any(r.get('approved_document_alias') for r in visible))
        self.assertFalse(source_asset_allowed(self.data, self.roles / 'New', 'CV_Candidate.pdf'))

    def test_51_aliases_share_actual_raw_application_snapshot(self):
        import time
        from requested_document_aliases import apply_aliases
        self.persist()
        path = self.data.local / 'requested-document-aliases.json'
        registry = json.loads(path.read_text())
        registry['entries'] *= 51
        path.write_text(json.dumps(registry))
        with patch.object(self.data, 'roles', wraps=self.data.roles) as scans:
            start = time.perf_counter()
            rows = self.data.roles()['items']
            elapsed = time.perf_counter() - start
        self.assertEqual(scans.call_count, 1)
        self.assertEqual(len(rows), 1)
        print('51-alias real temporary catalog fixture seconds:', elapsed)

if __name__ == '__main__': unittest.main()
