"""Approved requested document versions attached to existing canonical Studio jobs."""
from pathlib import Path
import hashlib
import json
from manual_jobs import canonical_url


def _digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def _requested_folders(data):
    folders = set()
    base = data.auto / 'evidence/requested-build-continuations'
    for manifest_path in base.glob('*/luna-restart/manifest.json'):
        try:
            manifest = json.loads(manifest_path.read_text())
            if manifest.get('kind') != 'explicit_luna_build_restart_v1': continue
            for role in manifest.get('roles', []):
                folder = Path(role['role_path']).resolve()
                if folder.is_relative_to(data.role_root): folders.add('Tailored CVs/' + folder.relative_to(data.role_root).as_posix())
        except (OSError, ValueError, KeyError, TypeError): continue
    return folders


def apply_aliases(data, items, *, _catalog=None):
    path = data.local / 'requested-document-aliases.json'
    registry = {'entries': []}
    try:
        if path.exists() and not path.is_symlink():
            data._safe(path, data.local)
            candidate = json.loads(path.read_text())
            if candidate.get('kind') == 'requested_document_alias_registry_v1': registry = candidate
    except (OSError, ValueError): pass
    # Catalog reuse is strictly local to this call; evidence is never cached.
    if _catalog is None and registry.get('entries'):
        from applications import Applications
        _catalog = Applications(data).decorate_list({'items': items})['items']
    by_id = {r['id']: dict(r) for r in items}
    hidden = set()
    for entry in registry.get('entries', []):
        source_id = entry.get('approved_document_studio_role_id')
        target_id = entry.get('canonical_studio_role_id')
        try:
            target = by_id[target_id]
            source = by_id[source_id]
            url = canonical_url(target.get('source_url'))
            if not url or url != canonical_url(source.get('source_url')) or url != entry['canonical_url']: raise ValueError('URL identity changed')
            for ref in [entry['approval'], entry['intent'], *entry['artifact_refs']]:
                p = Path(ref['path']).resolve(strict=True)
                if not (p.is_relative_to(data.role_root) or p.is_relative_to(data.auto / 'evidence/requested-build-continuations')) or _digest(p) != ref['sha256']: raise ValueError('changed alias evidence')
            from requested_studio_sync import project_requested
            proof = project_requested(data.root, entry['intent']['path'], validate_aliases=False,
                                      validate_current_code=False, _data=data, _catalog=_catalog)
            if proof['artifact_refs'] != entry['artifact_refs'] or proof['studio_role_id'] != target_id or proof['artifact_studio_role_id'] != source_id: raise ValueError('Alias differs from validated chain')
            names = {Path(r['path']).name for r in entry['artifact_refs']}
            assets = [r for r in source['artifacts'] if r['name'] in names]
            target.update(artifacts=assets, cv_available=source['cv_available'], canonical_cv_available=source.get('canonical_cv_available'), cover_letter_available=source['cover_letter_available'], cv_built_at=source.get('cv_built_at'), document_folder=source['folder'], approved_document_alias=entry)
            if entry.get('historical_applied') is True:
                target['applied'] = True
            for alias in entry['same_posting_studio_ids']:
                if alias != target['id'] and alias in by_id and canonical_url(by_id[alias].get('source_url')) == url: hidden.add(alias)
        except (KeyError, TypeError, OSError, ValueError):
            # A once-approved requested document version must never fall back to
            # automatic Ready when its approval chain changes. Keep old files
            # and application records; quarantine this version's document view.
            if source_id in by_id:
                if source_id != target_id:
                    hidden.add(source_id)
                else:
                    by_id[source_id].update(artifacts=[], cv_available=False, cover_letter_available=False, requested_document_gate='invalid')

    requested = _requested_folders(data)
    for row in by_id.values():
        if row['folder'] in requested and not row.get('approved_document_alias'):
            row.update(artifacts=[], cv_available=False, cover_letter_available=False, requested_document_gate='unapproved')
    return [by_id[r['id']] for r in items if r['id'] not in hidden]


def persist_alias(data, result, intent):
    path = data.local / 'requested-document-aliases.json'
    alias = result['canonical_alias']
    entry = {**alias, 'canonical_url': result['canonical_url'], 'artifact_refs': result['artifact_refs'], 'approval': intent['approval'], 'intent': result['intent'], 'role_id': result['role_id'], 'historical_applied': result.get('historical_applied', False)}
    with data._write_lock():
        data._safe(path, data.local)
        if path.is_symlink(): raise ValueError('Unsafe alias registry')
        registry = json.loads(path.read_text()) if path.exists() else {'kind': 'requested_document_alias_registry_v1', 'entries': []}
        if registry.get('kind') != 'requested_document_alias_registry_v1': raise ValueError('Unsupported alias registry')
        entries = registry['entries']
        previous = next((r for r in entries if r['canonical_url'] == entry['canonical_url']), None)
        if previous == entry: return
        if previous:
            registry.setdefault('versions', []).append(previous)
            entries.remove(previous)
        entries.append(entry)
        data._atomic(path, (json.dumps(registry, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode())


def source_asset_allowed(data, folder, name):
    """Prevent direct asset-key access from bypassing requested document gates."""
    path = data.local / 'requested-document-aliases.json'
    if not folder.resolve().is_relative_to(data.role_root): return True
    requested = 'Tailored CVs/' + folder.resolve().relative_to(data.role_root).as_posix() in _requested_folders(data)
    if not path.exists(): return not requested
    try:
        registry = json.loads(path.read_text())
        impacted = [e for e in registry.get('entries', []) if any(Path(r['path']).resolve().parent == folder.resolve() for r in e['artifact_refs'])]
        if not impacted: return not requested
        visible = apply_aliases(data, data.roles(apply_document_aliases=False)['items'])
        for entry in impacted:
            row = next((r for r in visible if r['id'] == entry['canonical_studio_role_id']), {})
            if row.get('approved_document_alias') != entry: return False
            target = folder / name
            if not any(Path(r['path']).resolve() == target.resolve() and _digest(target) == r['sha256'] for r in entry['artifact_refs']): return False
        return True
    except (OSError, ValueError, KeyError, TypeError): return False
