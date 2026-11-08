"""Fixture-only pipeline contact admission tests; no provider or web calls."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from contact_research import admitted_item


class PipelineContactAdmissionTests(unittest.TestCase):
    def test_admitted_consent_hash_and_owner_all_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / 'Master/auto/evidence/runs/fixture-owned'
            run.mkdir(parents=True)
            local = root / 'Master/auto/local'
            local.mkdir(parents=True)
            owner_path = local / '.pipeline-launch-owner.json'
            owner_path.write_text(json.dumps({'status':'SUPERVISING','owner_token':'fixture-owner','run_dir':str(run)}))
            path = run / 'snapshot.json'
            def save(consent=True):
                raw=json.dumps({'kind':'ordinary_pipeline_manual_job_snapshot','run_id':'fixture-owned',
                    'items':[{'id':'a'*16,'status_at_admission':'queued','find_contacts':consent}]}).encode()
                path.write_bytes(raw)
                return hashlib.sha256(raw).hexdigest()
            digest = save()
            row = admitted_item(root,run,'fixture-owned',path,digest,'a'*16)
            self.assertTrue(row['find_contacts'])
            with self.assertRaisesRegex(ValueError,'hash'):
                admitted_item(root,run,'fixture-owned',path,'0'*64,'a'*16)
            with self.assertRaisesRegex(ValueError,'queued contact consent'):
                admitted_item(root,run,'fixture-owned',path,save(False),'a'*16)
            digest=save()
            owner_path.write_text(json.dumps({'status':'RELEASED','owner_token':'fixture-owner','run_dir':str(run)}))
            with self.assertRaisesRegex(ValueError,'no longer owns'):
                admitted_item(root,run,'fixture-owned',path,digest,'a'*16)
