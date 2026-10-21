"""Pending -> server-owned import -> verified GitHub archive. No final uploads."""
import argparse
import json
import os
from pathlib import Path
import sys
import traceback
from model_sync_manifest import MAX_BATCH_BYTES, build_batches, canonical, digest, discover_pending, serialize_wire
from model_sync_ptero import PteroClient, SyncError
from model_sync_archive import publish_archive, validated_rows, verified_downloads


def sync_batch(root, batch, client):
    root = Path(root)
    if sum(item.dff_bytes + item.txd_bytes for item in batch.items) > MAX_BATCH_BYTES:
        raise SyncError('Batch exceeds memory-safe byte limit')
    old = client.existing_receipt(batch.batch_id)
    persisted = set()
    if old is not None and old.get('complete') is True:
        rows = validated_rows(batch, old)
        persisted = {key for key, row in rows.items() if row['status'] == 'persisted'}
        if len(persisted) == len(batch.items):
            return old, verified_downloads(batch, old, client)
        # Do not let polling consume the previous completed failure receipt.
        client.reset_failed_receipt(batch.batch_id)
    payloads = []
    for item in batch.items:
        if item.item_id in persisted:
            continue
        for source, expected in item.source_hashes.items():
            path = root / source
            if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != expected:
                raise SyncError('Pending item changed since discovery; retry with a fresh manifest')
        for source in (item.source_dff, item.source_txd):
            payloads.append((f'models/.sync_staging/{batch.batch_id}/{item.item_id}/{Path(source).name}', source, item.source_hashes[source]))
    for path, source, expected in payloads:
        blob = (root / source).read_bytes()
        if digest(blob) != expected:
            raise SyncError('Pending file changed before upload; retry with a fresh manifest')
        client.upload(path, blob)
        del blob
    client.upload(f'models/.sync_staging/{batch.batch_id}/manifest.tsv', serialize_wire(batch))
    client.command('modelsync import ' + batch.batch_id)
    receipt = client.poll_receipt(batch.batch_id)
    return receipt, verified_downloads(batch, receipt, client)


def summary(text):
    print(text)
    if target := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(target, 'a', encoding='utf-8') as stream:
            stream.write(text + '\n\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['sync'])
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--dry-run', action='store_true')
    options = parser.parse_args(argv)
    items, errors = discover_pending(options.root)
    for error in errors:
        summary('Invalid pending item: ' + error)
    batches = build_batches(items)
    if options.dry_run:
        for batch in batches:
            print(json.dumps(batch.to_dict(), indent=2))
        summary(f'Dry run: {len(items)} valid items, {len(errors)} validation failures; no transfers.')
        return 1 if errors else 0
    if not batches:
        summary('No valid pending items. Archive folders are never upload inputs.')
        return 1 if errors else 0
    try:
        client = PteroClient(os.environ.get('PTERO_PANEL_URL', ''), os.environ.get('PTERO_SERVER_ID', ''), os.environ.get('PTERO_CLIENT_API_KEY', ''),
                             allow_insecure_http=os.environ.get('PTERO_ALLOW_INSECURE_HTTP') == 'true')
        for batch in batches:
            summary(f'Processing batch {batch.batch_id}: {len(batch.items)} pending items.')
            receipt, downloaded = sync_batch(options.root, batch, client)
            commit, result = publish_archive(options.root, batch, receipt, downloaded)
            client.command(f'modelsync acknowledge {batch.batch_id} {commit}')
            summary(f'Batch {batch.batch_id}: archive commit {commit}; {len(result.failed)} failed items, {len(result.preserved)} newer pending items preserved.')
            errors.extend(result.failed)
        return 1 if errors else 0
    except (SyncError, OSError, ValueError, KeyError) as exc:
        # Transport errors intentionally contain no credentials or signed URLs.
        if isinstance(exc, SyncError):
            summary(str(exc))
        else:
            frames=[f for f in traceback.extract_tb(exc.__traceback__) if Path(f.filename).name.startswith('model_sync')]
            where=f' at {Path(frames[-1].filename).name}:{frames[-1].lineno}' if frames else ''
            summary(f'Model sync failed ({type(exc).__name__}{where}); pending files and server journal are retained for retry.')
        return 1


if __name__ == '__main__':
    sys.exit(main())
