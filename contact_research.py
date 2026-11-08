"""Optional, hash-bound pipeline contact side task. Never sends outreach."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import time
from applications import Applications
from data import StudioData
from runtime import JobManager, ACTIVE


def admitted_item(root: Path, run_dir: Path, run_id: str, snapshot: Path, digest: str, item_id: str) -> dict:
    """Check active ownership and frozen queued consent before any model starts."""
    root = (root / 'Resources') if (root / 'Resources').is_dir() else root
    run_dir = run_dir.resolve(strict=True)
    snapshot = snapshot.resolve(strict=True)
    if not snapshot.is_relative_to(run_dir):
        raise ValueError('Contact admission snapshot is outside the owned run.')
    raw = snapshot.read_bytes()
    if len(raw) > 32_000_000 or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Contact admission snapshot hash does not match.')
    saved = json.loads(raw)
    if saved.get('kind') != 'ordinary_pipeline_manual_job_snapshot' or saved.get('run_id') != run_id:
        raise ValueError('Contact admission snapshot belongs to a different run.')
    row = next((item for item in saved.get('items', []) if item.get('id') == item_id), None)
    if not row or row.get('status_at_admission') != 'queued' or row.get('find_contacts') is not True:
        raise ValueError('This job was not admitted with queued contact consent.')
    owner = json.loads((root / 'Master/auto/local/.pipeline-launch-owner.json').read_text())
    if (owner.get('status') not in {'STARTING', 'SUPERVISING', 'RUNNING', 'ACTIVE'} or not owner.get('owner_token')
            or Path(owner.get('run_dir', '')).resolve() != run_dir):
        raise ValueError('The pipeline no longer owns this contact side task.')
    return row


def main(argv=None):
    parser = argparse.ArgumentParser()
    for arg in ('root','run-dir','run-id','snapshot','snapshot-sha256','item-id'):
        parser.add_argument('--' + arg, required=True)
    parser.add_argument("--source-capture-json")
    parser.add_argument("--max-seconds", type=float, default=360)
    args = parser.parse_args(argv)
    root, run_dir, snapshot = Path(args.root).resolve(), Path(args.run_dir).resolve(), Path(args.snapshot)
    admitted = admitted_item(root, run_dir, args.run_id, snapshot, args.snapshot_sha256, args.item_id)
    data = StudioData(root)
    manager = JobManager(data.root, data, local_dir=data.local, load_jobs=False)
    manager.set_application_service(Applications(data))
    receipt_dir = run_dir / 'contacts'
    receipt_dir.mkdir(exist_ok=True, mode=0o700)
    receipt = receipt_dir / (args.item_id + '.json')
    try:
        current = manager.manual_job_queue.get(args.item_id)
        if current.get('input_sha256') != admitted.get('input_sha256'):
            raise ValueError('Saved job inputs changed after pipeline admission.')
        dispatch_clock = time.monotonic()
        job = manager.start_queued_contact_research(args.item_id, args.run_id, input_sha256=admitted.get('input_sha256'), source_capture_json=args.source_capture_json)
        if job:
            while manager.get(job['id']).get('status') in ACTIVE:
                current = manager.manual_job_queue.get(args.item_id)
                if current.get('find_contacts') is not True or time.monotonic() - dispatch_clock >= args.max_seconds:
                    manager.stop(job['id'])
                time.sleep(.5)
            control = manager.controls.get(job['id'])
            if control:
                control['done'].wait(30)
        record = manager.manual_jobs()
        research = next(row for row in record['items'] if row['id'] == args.item_id).get('contact_research')
        output = {'kind':'optional_pipeline_contact_research','run_id':args.run_id,'item_id':args.item_id,
                  'snapshot_sha256':args.snapshot_sha256,'research':research,'outreach_sent':False,
                  'route':({key:manager.get(job['id']).get(key) for key in ('requested_model','actual_model','requested_effort','actual_effort','provider_quota_before','provider_quota_after','turn_quiescent','background_cleanup','owned_process_cleanup','recovery_required','cleanup_status')} if job else None),
                  'budget_seconds':args.max_seconds, 'elapsed_seconds':time.monotonic()-dispatch_clock}
        temporary = receipt.with_suffix('.tmp')
        temporary.write_text(json.dumps(output, indent=2) + '\n')
        temporary.replace(receipt)
        print(json.dumps({'status':(research or {}).get('status'),'receipt':str(receipt)}))
    finally:
        manager.close()

if __name__ == '__main__':
    main()
