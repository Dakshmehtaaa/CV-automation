"""Explicit requested continuation readback. Never changes batch history or application choices.

This producer requires independently submitted, hash-bound evidence. Its intent is
not an approval. No source admission or successful old pipeline stage is inferred.
"""
from __future__ import annotations
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from data import StudioData
from applications import Applications
from manual_jobs import canonical_url

KIND = 'requested_cv_studio_sync_intent_v1'


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _ref(ref, allowed):
    _require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'}, 'Exact path/hash reference required')
    path = Path(ref['path'])
    _require(path.is_absolute() and not path.is_symlink(), 'Reference must be absolute, regular and local')
    path = path.resolve(strict=True)
    _require(path.is_file() and any(path.is_relative_to(base) for base in allowed), 'Reference escaped authorized scope')
    _require(_sha(path) == ref['sha256'], 'Reference hash changed: ' + str(path))
    return path


def _no_symlink(path):
    return not any(p.is_symlink() for p in (path, *path.parents))


def _json_ref(ref, allowed):
    path = _ref(ref, allowed)
    value = json.loads(path.read_text())
    _require(isinstance(value, dict), 'Object receipt required')
    return value


def _row(rows, role_id):
    matches = [r for r in rows if r.get('id', r.get('role_id')) == role_id]
    _require(len(matches) == 1, 'Evidence must bind exactly one requested role')
    return matches[0]


def _native_gate(ref, allowed, effort, authors):
    receipt = _json_ref(ref, allowed)
    args = receipt.get('raw_args', {})
    worker = receipt.get('raw_result', {}).get('task_name')
    _require(args.get('model') == 'gpt-6.1-sol' and args.get('reasoning_effort') == effort and worker and worker not in authors and receipt.get('submitted_at') and receipt.get('ack_at'), 'Actual independent native Sol ' + effort + ' submission required')
    return worker


def _usage_guard(usage, evidence, *, audited_at, stopped_at, submitted_at, approved_at):
    baseline = _json_ref(usage['baseline_receipt'], evidence)
    current = _json_ref(usage['current_receipt'], evidence)
    start = datetime.fromisoformat(usage['stage_started_at'].replace('Z', '+00:00'))
    before = datetime.fromisoformat(baseline['at'].replace('Z', '+00:00'))
    after = datetime.fromisoformat(current['at'].replace('Z', '+00:00'))
    _require(all(t.tzinfo is not None for t in (start, before, after)), 'Aware usage sample times required')
    _require(max(audited_at, stopped_at) <= start <= before <= submitted_at <= after <= approved_at, 'Usage baseline must be fresh after audit/cleanup and before native HIGH; final sample must precede approval')
    _require(isinstance(baseline.get('window'), str) and bool(baseline['window']) and baseline['window'] == current.get('window'), 'Actual same-window usage observations required')
    local = Path(__file__).resolve().parents[1] / 'Master/auto/local'
    if str(local) not in sys.path: sys.path.insert(0, str(local))
    from weekday_engine import sol_review_usage_gate
    gate = sol_review_usage_gate(baseline.get('used_percent'), current.get('used_percent'), baseline_at=baseline['at'], current_at=current['at'], stage_started_at=usage['stage_started_at'], baseline_window=baseline['window'], current_window=current['window'], reset=usage.get('reset', False), ambiguous=usage.get('ambiguous', False), available=usage.get('available', True))
    _require(gate['can_pass'], 'Fresh Sol HIGH usage guard held: ' + gate['reason'])
    return gate


# Current edges are validated explicitly below. Raw worker completions and the
# original source audit are immutable opaque receipt references: their nested
# artifact snapshots and append-only session origins are historical provenance.
# Current artifact bytes are exclusively the independently audited packet +
# owner projection + fresh approval, never a superseded raw snapshot.


def project_requested(root, intent_path, *, validate_aliases=True, validate_current_code=True,
                      _data=None, _catalog=None):
    """Validate without writes and return real Studio/application readback.

    Persisted document links validate the approval's historical code audit.
    A fresh Sync must additionally match the code currently executing it.
    """
    data = _data if _data is not None else StudioData(Path(root))
    _require(_no_symlink(Path(intent_path)), 'Intent may not traverse symlinks')
    intent_path = Path(intent_path).resolve(strict=True)
    base = (data.auto / 'evidence' / 'requested-build-continuations').resolve()
    _require(intent_path.is_relative_to(base) and not Path(intent_path).is_symlink(), 'Intent outside requested continuation')
    intent = json.loads(intent_path.read_text())
    _require(intent.get('kind') == KIND and intent.get('sealed') is True, 'Sealed explicit requested intent required')
    continuation = Path(intent['continuation_dir']).resolve(strict=True)
    _require(continuation.is_relative_to(base) and intent_path.is_relative_to(continuation), 'Wrong continuation scope')
    allowed = (continuation, data.role_root)
    evidence = (continuation, continuation.parent)
    manifest = _json_ref(intent['manifest'], evidence)
    _require(manifest.get('kind') == 'explicit_luna_build_restart_v1', 'Wrong requested manifest')
    role_id = intent['role_id']
    assignment = _row(manifest['roles'], role_id)
    folder = Path(assignment['role_path']).resolve(strict=True)
    _require(folder.is_relative_to(data.role_root), 'Requested role outside canonical role inventory')
    _require(_sha(folder / 'JD.md') == assignment['jd_sha256'], 'Requested JD changed')
    _ref({'path': assignment['provenance_path'], 'sha256': assignment['provenance_sha256']}, (data.role_root,))
    auth = _json_ref(intent['authorization'], evidence)
    _require(auth.get('kind') == 'explicit_requested_continuation_authorization_v1' and auth.get('manifest') == intent['manifest'] and role_id in auth.get('role_ids', []), 'Actual explicit requested authorization missing')
    _ref(auth['user_request_evidence'], evidence)
    source = _json_ref(intent['source_audit'], evidence)
    _require(source.get('manifest') == intent['manifest'], 'Source audit must bind exact restart manifest')
    _require(_row(source.get('roles', []), role_id).get('status') == 'PASS', 'Source/JD audit did not pass')
    _require(_row(source['roles'], role_id).get('jd_sha256') == assignment['jd_sha256'] and _row(source['roles'], role_id).get('source_content_sha256') == assignment['source_content_sha256'], 'Source audit JD/source binding mismatch')
    original_source_audit = _json_ref(source['source_audit'], (data.auto / 'evidence',))
    _require(original_source_audit.get('model') == 'gpt-6.1-sol' and original_source_audit.get('reasoning_effort') == 'medium' and original_source_audit.get('audit_executor_id') == source.get('actual_audit_executor_id'), 'Source projection must preserve actual independent Sol MEDIUM audit')
    original_source_role = _row(original_source_audit.get('requested_manual_job_items', []), role_id)
    for key in ('jd_sha256', 'source_content_sha256', 'input_sha256'):
        _require(original_source_role.get(key) == assignment.get(key), 'Original source audit mismatch: ' + key)
    dispatch_path = _ref(intent['native_dispatches'], evidence)
    dispatches = json.loads(dispatch_path.read_text())
    build_task = intent['build_worker_id']
    native = [r for r in dispatches if r.get('raw_result', {}).get('task_name') == build_task]
    _require(len(native) == 1 and native[0]['raw_args'].get('model') == 'gpt-6-luna' and native[0]['raw_args'].get('reasoning_effort') == 'max' and native[0].get('ack_at'), 'Actual Luna MAX native dispatch missing')
    terminal = _json_ref(intent['workers_terminal'], evidence)
    _require(terminal.get('kind') == 'requested_native_workers_terminal_v1' and terminal.get('manifest') == intent['manifest'] and terminal.get('native_dispatches') == intent['native_dispatches'], 'All-workers cleanup provenance missing')
    actual_tasks = {r['raw_result']['task_name'] for r in dispatches}
    terminal_rows = terminal.get('workers', [])
    _require({r.get('worker_id') for r in terminal_rows} == actual_tasks and len(terminal_rows) == len(actual_tasks), 'Cleanup does not account for every actual Build worker')
    _require(all(r.get('status') in ('completed', 'stopped', 'failed') and r.get('pending_tool_calls') is False for r in terminal_rows), 'Build workers are not all stopped')
    completion = _json_ref(intent['completion'], evidence)
    projection = _json_ref(intent['completion_projection'], evidence)
    _require(projection.get('kind') == 'requested_owner_completion_projection_v1' and projection.get('raw_completion') == intent['completion'] and projection.get('assignment') == assignment and projection.get('manifest') == intent['manifest'], 'Completion projection must preserve raw receipt and exact assignment')
    _require(projection.get('role_id') == role_id and projection.get('native_dispatches') == intent['native_dispatches'] and projection.get('build_worker_id') == build_task, 'Completion projection native identity mismatch')
    for key in ('jd_sha256', 'source_content_sha256', 'provenance_sha256'):
        _require(projection.get(key) == assignment[key], 'Completion projection source hash mismatch: ' + key)
    audit = _json_ref(intent['build_audit'], evidence)
    code_audit = _json_ref(intent['code_audit'], evidence)
    _require(code_audit.get('status') == 'PASS' and code_audit.get('executor', {}).get('model') == 'gpt-6.1-sol' and code_audit['executor'].get('reasoning_effort') == 'medium' and code_audit['executor'].get('independent_of_authors') is True, 'Independent passing Sol MEDIUM code audit required')
    code_input = _json_ref(code_audit['input'], evidence)
    required_code = {'requested_studio_sync.py', 'requested_document_aliases.py', 'studio_sync.py', 'data.py'}
    _require(required_code <= {Path(r['path']).name for r in code_audit['code']}, 'Code audit must cover actual producer and catalog integration')
    if validate_current_code:
        for row in code_audit['code']:
            _ref({k: row[k] for k in ('path', 'sha256')}, (Path(__file__).resolve().parent,))
    else:
        # Later UI/producer updates do not revoke an approved document package.
        # Keep the immutable audit bound to the exact code its auditor received;
        # every document, approval and source proof is still rehashed below.
        audited = code_audit['code']
        supplied = code_input.get('files')
        _require(isinstance(supplied, list) and bool(supplied), 'Historical code audit input lacks file bindings')
        def bindings(rows):
            result = {}
            for row in rows:
                path, digest = row['path'], row['sha256']
                _require(isinstance(path, str) and Path(path).is_absolute()
                         and Path(path).parent == Path(__file__).resolve().parent
                         and path not in result and isinstance(digest, str)
                         and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest),
                         'Invalid or duplicate historical code binding')
                _require(row.get('observed_sha256', digest) == digest
                         and row.get('matches_input', True) is True,
                         'Historical code audit did not match its input')
                result[path] = digest
            return result
        _require(bindings(audited) == bindings(supplied), 'Historical code audit differs from its input')
    executor = audit.get('executor', {})
    _require(executor.get('model') == 'gpt-6.1-sol' and executor.get('reasoning_effort') == 'medium' and executor.get('independent_of_authors') is True, 'Independent Sol MEDIUM audit required')
    auditor = _native_gate(intent['build_audit_native'], evidence, 'medium', actual_tasks)
    _require(auditor.rsplit('/', 1)[-1] == executor.get('name'), 'Audit receipt is not from submitted auditor')
    _require(_row(audit.get('roles', []), role_id).get('status') == 'PASS', 'Artifact audit did not pass')
    packet = _json_ref(audit['input_packet'], evidence)
    _require(Path(packet['source_manifest']).resolve() == _ref(intent['manifest'], evidence), 'Audit packet manifest mismatch')
    packet_role = _row([dict(r, id=r['assignment']['id']) for r in packet['roles']], role_id)
    _require(packet_role['assignment'] == assignment and packet_role['completion'] == intent['completion'], 'Artifact audit assignment/completion mismatch')
    artifacts = packet_role['role_files']
    _require(projection.get('artifact_refs') == artifacts, 'Completion projection differs from already audited final artifacts')
    for artifact in artifacts:
        path = _ref(artifact, allowed)
        _require(path.parent == folder, 'Audit artifacts must belong to exact requested folder')
    required = {'CV_Candidate.html', 'CV_Candidate.pdf', 'JD.md', 'Cover_Letter_Candidate.pdf', 'why-this-company.md'}
    _require(required <= {Path(r['path']).name for r in artifacts}, 'Audit packet lacks required final artifacts')
    approval = _json_ref(intent['approval'], evidence)
    _require(approval.get('kind') == 'requested_continuation_sol_high_approval_v1' and approval.get('status') == 'APPROVED' and approval.get('model') == 'gpt-6.1-sol' and approval.get('reasoning_effort') == 'high', 'Fresh Sol HIGH approval missing')
    approver = _native_gate(approval['native_submission'], evidence, 'high', actual_tasks | {auditor})
    _require(approval.get('worker_id') == approver, 'Approval receipt is not from submitted HIGH worker')
    for key in ('manifest', 'authorization', 'source_audit', 'native_dispatches', 'workers_terminal', 'completion', 'completion_projection', 'build_audit', 'code_audit'):
        _require(approval.get(key) == intent[key], 'Approval does not bind exact ' + key)
    _require(approval.get('role_id') == role_id and approval.get('artifact_refs') == artifacts and Path(approval['canonical_role_path']).resolve() == folder, 'Approval role/artifact/canonical identity mismatch')
    usage = _json_ref(approval['usage'], evidence)
    _require('exact_request_usage' in usage and 'account_before' in usage and 'account_after' in usage, 'Approval usage must explicitly report known or unavailable values')
    approved_at = datetime.fromisoformat(approval['approved_at'].replace('Z', '+00:00'))
    audited_at = datetime.fromisoformat(audit['audited_at_utc'].replace('Z', '+00:00'))
    code_audited_at = datetime.fromisoformat(code_audit['audited_at_utc'].replace('Z', '+00:00'))
    stopped_at = datetime.fromisoformat(terminal['observed_at'].replace('Z', '+00:00'))
    submitted = _json_ref(approval['native_submission'], evidence)
    submitted_at = datetime.fromisoformat(submitted['submitted_at'].replace('Z', '+00:00'))
    ack_at = datetime.fromisoformat(submitted['ack_at'].replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    _require(all(t.tzinfo is not None for t in (approved_at, audited_at, code_audited_at, stopped_at, submitted_at, ack_at)), 'Timezone-aware actual gate times required')
    audited_at = max(audited_at, code_audited_at)
    _require(max(audited_at, stopped_at) <= submitted_at <= ack_at <= approved_at <= now, 'HIGH native submission/approval must follow completed audit and cleanup, and cannot be future-dated')
    usage_gate = _usage_guard(usage, evidence, audited_at=audited_at, stopped_at=stopped_at, submitted_at=submitted_at, approved_at=approved_at)
    apps = Applications(data)
    raw_items = None
    if _catalog is None:
        raw_items = data.roles(apply_document_aliases=False)['items']
        catalog = apps.decorate_list({'items': raw_items})['items']
    else:
        catalog = _catalog
    relative = data._relative(folder)
    candidates = [r for r in catalog if r.get('folder') == relative]
    _require(len(candidates) == 1, 'Exact requested Studio folder missing or ambiguous')
    role = candidates[0]
    url = canonical_url(role.get('source_url'))
    _require(bool(url) and url == canonical_url(assignment['history_match']['candidate_url']), 'Actual Studio source URL mismatch')
    duplicates = [r for r in catalog if r['id'] != role['id'] and canonical_url(r.get('source_url')) == url and (r.get('cv_available') or r['application']['source'] == 'local' or r['application']['canonical_applied'])]
    # A requested CV is a new document version of the same posting. Bind it to
    # one existing canonical job; keep each actual application choice intact.
    existing = sorted(duplicates, key=lambda r: (not (r['application']['status'] == 'applied' or r['application']['canonical_applied']), r['application']['source'] != 'local', r['folder'], r['id']))
    canonical_job = existing[0] if existing else role
    canonical_application = canonical_job['application']
    if validate_aliases and (data.local / 'requested-document-aliases.json').exists():
        from requested_document_aliases import apply_aliases
        # Reuse this operation's actual raw scan, but revalidate every proof.
        visible = apply_aliases(data, raw_items, _catalog=catalog)
        decorated = apps.decorate_list({'items': visible})['items']
        canonical_application = next(r for r in decorated if r['id'] == canonical_job['id'])['application']
    prior_applied = any(r.get('applied') is True or r.get('status') == 'applied' for r in assignment.get('history_match', {}).get('matches', []))
    # Historical Applied remains explicit even when the legacy job is absent
    # from the current catalog. It never authorizes a fresh Ready application.
    effective_status = ('applied' if prior_applied and canonical_application['source'] != 'local' else canonical_application['status'])
    _require(role.get('cv_available') and role.get('cover_letter_available'), 'Studio does not expose both approved PDFs')
    return {'kind': 'requested_cv_studio_sync_readback_v1', 'status': 'DONE', 'usage_gate': usage_gate, 'role_id': role_id, 'continuation_dir': str(continuation), 'observed_at': datetime.now(timezone.utc).isoformat(), 'intent': {'path': str(intent_path), 'sha256': _sha(intent_path)}, 'artifact_refs': artifacts, 'artifact_studio_role_id': role['id'], 'artifact_role_path': role['folder'], 'studio_role_id': canonical_job['id'], 'canonical_role_path': canonical_job['folder'], 'canonical_url': url, 'application': canonical_application, 'application_status': effective_status, 'historical_applied': prior_applied, 'requested_CV_ready': True, 'canonical_alias': {'canonical_studio_role_id': canonical_job['id'], 'approved_document_studio_role_id': role['id'], 'same_posting_studio_ids': sorted({r['id'] for r in duplicates} | {role['id']}), 'document_link_scope': 'persistent_approved_document_alias_registry'}, 'history_treatment': 'separate_explicit_requested_continuation_original_batch_unchanged', 'queue_mutated': False}


def execute_requested(root, intent_path, output):
    result = project_requested(root, intent_path, validate_current_code=True)
    continuation = Path(result['continuation_dir'])
    output = Path(output)
    _require(_no_symlink(output), 'Readback may not traverse symlinks')
    _require(output.is_absolute() and output.parent.resolve().is_relative_to(continuation / 'cv-studio-sync'), 'Readback must remain in continuation/cv-studio-sync')
    data = StudioData(Path(root))
    intent = json.loads(Path(intent_path).read_text())
    from requested_document_aliases import persist_alias
    if output.exists():
        previous = json.loads(output.read_text())
        _require({k: v for k, v in previous.items() if k != 'observed_at'} == {k: v for k, v in result.items() if k != 'observed_at'}, 'Existing immutable readback no longer matches actual Studio/evidence; reconcile before new version')
        persist_alias(data, previous, intent)
        return previous
    from studio_sync import _write_immutable_json
    output.parent.mkdir(parents=True, exist_ok=True)
    persist_alias(data, result, intent)
    # Read actual catalog/application projection after the durable document link.
    result = project_requested(root, intent_path)
    _write_immutable_json(output, result, label='Requested continuation Sync readback')
    return result
