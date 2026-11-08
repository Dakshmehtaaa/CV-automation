"""An unfinished requested package is not made ready by its PDF files."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from applications import Applications
from browser_fixture import make_fixture
from data import StudioData
from requested_review import annotate_requested_reviews


class RequestedReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        make_fixture(self.root)
        self.data = StudioData(self.root)
        self.apps = Applications(self.data)
        self.folder = self.root / 'Tailored CVs/Example Green'
        self.provenance = {'kind': 'requested_manual_job_role_provenance_v1',
            'queue_id': 'a' * 16, 'input_sha256': 'b' * 64, 'run_id': 'fixture-held',
            'role_path': str(self.folder),
            'role_jd_sha256': hashlib.sha256((self.folder / 'JD.md').read_bytes()).hexdigest()}
        self.item = {'id': 'a' * 16, 'input_sha256': 'b' * 64, 'status': 'held',
                     'role_path': str(self.folder), 'audit': {'final_approval': None, 'studio_sync': None}}
        self.data.local.mkdir(parents=True)
        self.save()

    def save(self):
        (self.folder / 'requested-job-provenance.json').write_text(json.dumps(self.provenance))
        (self.data.local / 'manual-jobs.json').write_text(json.dumps({'schema_version': 1, 'items': [self.item]}))

    def role(self):
        return next(r for r in self.apps.roles()['items'] if r['company'] == 'Example Green')

    def test_held_pdfs_remain_inspectable_but_do_not_count_as_ready(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        role = self.role()
        self.assertTrue(role['cv_available'])
        self.assertTrue(role['artifacts'])
        self.assertEqual(role['application']['status'], 'pending')
        self.assertTrue(role['application']['needs_review'])
        self.assertEqual(role['build_review']['state'], 'held')
        self.assertEqual(self.apps.roles()['application_summary']['ready'], 1)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_completed_label_without_approval_and_sync_stays_pending(self):
        self.item['status'] = 'completed'; self.save()
        self.assertEqual(self.role()['application']['status'], 'pending')
        self.item['audit'] = ['invalid']; self.save()
        self.assertEqual(self.role()['application']['status'], 'pending')

    def test_queue_identity_and_document_version_must_match(self):
        for field, value in [('id', 'c' * 16), ('input_sha256', 'c' * 64),
                             ('role_path', str(self.root / 'Tailored CVs/Sample Partners'))]:
            with self.subTest(field=field):
                old = self.item[field]; self.item[field] = value; self.save()
                role = self.role()
                self.assertEqual(role['application']['status'], 'pending')
                self.assertEqual(role['build_review']['state'], 'unverified')
                self.item[field] = old

    def test_missing_or_corrupt_queue_never_falls_back_to_ready(self):
        path = self.data.local / 'manual-jobs.json'
        path.unlink()
        self.assertEqual(self.role()['application']['status'], 'pending')
        path.write_text('{broken')
        self.assertEqual(self.role()['application']['status'], 'pending')
        path.write_text('[]')
        self.assertEqual(self.role()['application']['status'], 'pending')

    def test_changed_jd_or_ambiguous_provenance_stays_pending(self):
        (self.folder / 'JD.md').write_text('# Changed advert\nCompany: Example Green\n')
        self.assertEqual(self.role()['build_review']['state'], 'unverified')
        (self.folder / 'requested_manual_job_provenance.json').write_text(json.dumps(self.provenance))
        self.assertEqual(self.role()['application']['status'], 'pending')

    def test_explicit_application_choices_are_preserved_with_review_notice(self):
        for status in ('applied', 'saved', 'ready'):
            role = self.role()
            self.apps.set_status(role['id'], status, role['application']['revision'])
            updated = self.role()
            self.assertEqual(updated['application']['status'], status)
            self.assertEqual(updated['application']['source'], 'local')
            self.assertTrue(updated['application']['needs_review'])

    def test_applied_archive_keeps_applied(self):
        role = self.role()
        role['collection'] = 'applied'
        result = self.apps.decorate(role)
        self.assertEqual(result['application']['status'], 'applied')
        self.assertTrue(result['application']['needs_review'])

    def test_revalidated_alias_remains_ready_and_unrelated_folders_unchanged(self):
        role = self.data.roles()['items'][0]
        role = {**role, 'approved_document_alias': {'validated': True}}
        role.pop('build_review', None)
        self.assertNotIn('build_review', annotate_requested_reviews(self.data, [role])[0])
        unrelated = next(r for r in self.apps.roles()['items'] if r['company'] == 'Sample Partners')
        self.assertEqual(unrelated['application']['status'], 'ready')

    def test_completed_sync_is_revalidated_and_changed_artifacts_lose_ready(self):
        import test_studio_sync as fixtures
        helper = fixtures.StudioSyncFixture()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        row = helper.roles[0]
        saved = helper._produce(row)
        data = StudioData(helper.resources)
        data.local.mkdir(parents=True, exist_ok=True)
        provenance = {**self.provenance, 'run_id': helper.run_id,
            'role_path': str(row['role_dir']),
            'role_jd_sha256': fixtures.digest(row['role_dir'] / 'JD.md')}
        fixtures.save_json(row['role_dir'] / 'requested-job-provenance.json', provenance)
        item = {**self.item, 'status': 'completed', 'role_path': str(row['role_dir']), 'audit': {
            'final_approval': {'path': str(row['gate_paths']['sol_review']), 'sha256': fixtures.digest(row['gate_paths']['sol_review'])},
            'studio_sync': {'path': str(saved), 'sha256': fixtures.digest(saved)}}}
        fixtures.save_json(data.local / 'manual-jobs.json', {'schema_version': 1, 'items': [item]})
        apps = Applications(data)
        self.assertEqual(apps.role(row['studio_id'])['application']['status'], 'ready')
        # The native pipeline stores stage terminal refs in the queue, not a
        # direct readback. Test that producer shape and aggregate readbacks too.
        other = helper._produce(helper.roles[1])
        first = json.loads(saved.read_text()); second = json.loads(other.read_text())
        aggregate = helper.run_dir / 'aggregate-readback.json'
        fixtures.save_json(aggregate, {'kind': 'cv_studio_sync_readback_v1', 'run_id': helper.run_id,
            'status': 'DONE', 'roles': first['roles'] + second['roles'],
            'role_readbacks': {row['public_id']: first, helper.roles[1]['public_id']: second}})
        synced = helper.run_dir / 'sync-summary.json'
        fixtures.save_json(synced, {'run_id': helper.run_id, 'status': 'DONE'})
        approval_terminal = helper.run_dir / 'stages/sol_review/weekday_engine_terminal.json'
        fixtures.save_json(approval_terminal, {'kind': 'weekday_worker_terminal', 'stage': 'sol_review', 'status': 'DONE'})
        sync_terminal = helper.run_dir / 'stages/sync/weekday_engine_terminal.json'
        terminal = {'kind': 'weekday_worker_terminal', 'stage': 'sync', 'status': 'DONE', 'receipts': {
            'readback': {'path': str(aggregate), 'sha256': fixtures.digest(aggregate)},
            'studio_sync': {'path': str(synced), 'sha256': fixtures.digest(synced)}}}
        fixtures.save_json(sync_terminal, terminal)
        item['audit'] = {'run_id': helper.run_id, 'queue_id': item['id'],
            'final_approval': {'path': str(approval_terminal), 'sha256': fixtures.digest(approval_terminal)},
            'studio_sync': {'path': str(sync_terminal), 'sha256': fixtures.digest(sync_terminal)}}
        fixtures.save_json(data.local / 'manual-jobs.json', {'schema_version': 1, 'items': [item]})
        self.assertEqual(apps.role(row['studio_id'])['application']['status'], 'ready')
        terminal['receipts']['readback']['sha256'] = '0' * 64
        fixtures.save_json(sync_terminal, terminal)
        item['audit']['studio_sync']['sha256'] = fixtures.digest(sync_terminal)
        fixtures.save_json(data.local / 'manual-jobs.json', {'schema_version': 1, 'items': [item]})
        self.assertEqual(apps.role(row['studio_id'])['application']['status'], 'pending')
        terminal['receipts']['readback']['sha256'] = fixtures.digest(aggregate)
        fixtures.save_json(sync_terminal, terminal)
        item['audit']['studio_sync']['sha256'] = fixtures.digest(sync_terminal)
        fixtures.save_json(data.local / 'manual-jobs.json', {'schema_version': 1, 'items': [item]})
        self.assertEqual(apps.role(row['studio_id'])['application']['status'], 'ready')
        (row['role_dir'] / 'CV_Candidate.pdf').write_bytes(b'changed draft')
        self.assertEqual(apps.role(row['studio_id'])['application']['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
