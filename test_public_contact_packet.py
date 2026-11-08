"""Focused, saved-public-source replay; zero network/provider calls."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from public_contact_packet import load_capture

SOURCE = Path(__file__).resolve().parents[1] / 'Master/auto/evidence/manual/2026-10-06-hunt-repair-145408Z/browser/apec-public-1548z/candidate-reprocessed-apec-exact-v3/captures/apec/apec-20261006T162050855145Z-ca0de52de2/capture.json'
URL = 'https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/179546757W'

class PublicContactPacketTests(unittest.TestCase):
    def test_reused_original_capture_keeps_clock_and_verified_identity(self):
        packet = load_capture(SOURCE,URL)
        self.assertEqual(packet['company'],'CMA IDF')
        self.assertEqual(packet['captured_at'],'2026-10-06T16:02:44.225Z')
        self.assertIn('reused capture',packet['verification'])
        self.assertNotIn('PROFILE',json.dumps(packet))

    def test_footer_employer_cannot_replace_primary_employer(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            for name in ('capture.json','ad.json','raw.html','body.txt'):
                shutil.copyfile(SOURCE.parent/name,folder/name)
            raw=(folder/'raw.html').read_text()+'<footer>Footer Corp</footer>'
            (folder/'raw.html').write_text(raw)
            digest=hashlib.sha256(raw.encode()).hexdigest()
            capture=json.loads((folder/'capture.json').read_text());capture['raw_sha256']=digest
            ad=json.loads((folder/'ad.json').read_text());ad['company']='Footer Corp';ad['capture']['raw_sha256']=digest
            ad['current_apply_evidence']['raw_sha256']=digest
            (folder/'capture.json').write_text(json.dumps(capture));(folder/'ad.json').write_text(json.dumps(ad))
            with self.assertRaisesRegex(ValueError,'primary advert identity'):
                load_capture(folder/'capture.json',URL)

    def test_body_hash_change_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            for name in ('capture.json','ad.json','raw.html','body.txt'):
                shutil.copyfile(SOURCE.parent/name,folder/name)
            (folder/'body.txt').write_text('Synthetic replacement')
            with self.assertRaisesRegex(ValueError,'hashes'):
                load_capture(folder/'capture.json',URL)
