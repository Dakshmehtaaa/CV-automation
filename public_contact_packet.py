"""Small public job packet from a mechanically checked original advert capture."""
import hashlib
import html
import json
from pathlib import Path
import re
import sys


def load_capture(path, original_url):
    path = Path(path).resolve(strict=True)
    record = json.loads(path.read_text())
    if record.get('status') != 'captured_full_detail' or record.get('original_url') != original_url:
        raise ValueError('Contact source capture does not prove this original advert.')
    paths = record.get('paths') or {}
    files = {}
    for key in ('raw_html','body_text','ad_json'):
        name = paths.get(key)
        if not isinstance(name,str) or Path(name).name != name:
            raise ValueError('Public source capture has an unsafe sibling path.')
        source = (path.parent / name).resolve(strict=True)
        if source.parent != path.parent or source.stat().st_size > 16_000_000:
            raise ValueError('Public source capture is not a bounded sibling file.')
        files[key] = source.read_bytes()
    raw_sha = hashlib.sha256(files['raw_html']).hexdigest()
    body_sha = hashlib.sha256(files['body_text']).hexdigest()
    ad = json.loads(files['ad_json'])
    if (raw_sha != record.get('raw_sha256') or body_sha != record.get('body_sha256')
            or raw_sha != (ad.get('capture') or {}).get('raw_sha256')
            or body_sha != ad.get('description_sha256')):
        raise ValueError('Contact public source capture hashes do not match.')
    body_text = files['body_text'].decode('utf-8')
    # A collector scaffold has no fit judgments. APEC's two source sections
    # supply literal evidence only, without inventing requirements or fit.
    if ad.get('source_board') == 'apec' and 'Profil recherché' in body_text:
        duties, requirements = body_text.split('Profil recherché', 1)
        ad = {**ad, 'literal_duties_quotes':[duties.strip()],
              'literal_mandatory_requirement_quotes':[requirements.strip()]}
    local = Path(__file__).resolve().parents[1] / 'Master/auto/local' 
    original = list(sys.path)
    try:
        sys.path.insert(0,str(local))
        from full_ad_evidence import inspect_full_ad_evidence
        from hunt_candidates import _primary_advert_identity
        identity = _primary_advert_identity({"raw":files["raw_html"].decode("utf-8"), "canonical_url":record.get("canonical_url"), "site":ad.get("source_board"), "capture":record, "ad":ad})
        proof = inspect_full_ad_evidence(ad,files['raw_html'].decode('utf-8'),files['body_text'].decode('utf-8'))
    finally:
        sys.path[:] = original
    if proof.get('eligible_full_read') is not True:
        raise ValueError('Contact job source did not pass original full-ad verification.')
    company,title=ad.get('company'),ad.get('title')
    if identity.get('reason') or any(ad.get(key) != identity.get(key) for key in ('company','title')):
        raise ValueError('Contact employer and role did not match the bound primary advert identity.')
    return {'company':company,'title':title,'source_url':original_url,
            'canonical_url':record.get('canonical_url'),'captured_at':record.get('captured_at_utc'),
            'capture_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'raw_sha256':raw_sha,'body_sha256':body_sha,
            'public_duties':files['body_text'].decode('utf-8')[:4000],
            'verification':'original full advert mechanically verified; reused capture, not a new fetch'}
