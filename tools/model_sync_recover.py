"""Explicit repair of deleted, previously persisted files; normal imports stay strict."""
import argparse
import os
from pathlib import Path
from urllib.parse import urlencode
from model_sync_archive import validated_rows, verified_downloads, publish_archive
from model_sync_manifest import build_batches, discover_pending, digest, HEX
from model_sync_ptero import PteroClient, SyncError


def restore_deleted(batch, client):
    if len(batch.items) != 1 or batch.items[0].kind != 'skin':
        raise SyncError('Repair requires exactly one pending skin')
    receipt = client.existing_receipt(batch.batch_id)
    if receipt is None:
        raise SyncError('Repair requires an existing completed receipt')
    item = batch.items[0]
    row = validated_rows(batch, receipt)[item.item_id]
    model = row.get('model_id')
    if (row.get('status') != 'failed' or row.get('error_code') != 'PATH_CONFLICT'
            or type(model) is not int or not 20001 <= model <= 30000 or row.get('dbid') != model):
        raise SyncError('Repair requires a previously persisted skin with missing final files')
    parent = 'models/' + str(Path(item.final_dff).parent).replace('\\', '/')
    client.ensure_directory(parent)
    listing = client.api('files/list?' + urlencode({'directory': '/' + parent}))['data']
    names = {r['attributes']['name'] for r in listing}
    moves = []
    # Verify the entire pair before moving either file. Never replace differing bytes.
    for final, source, expected, size in [
            (item.final_dff, item.source_dff, item.dff_sha256, item.dff_bytes),
            (item.final_txd, item.source_txd, item.txd_sha256, item.txd_bytes)]:
        target = 'models/' + final
        if Path(final).name in names:
            blob = client.download(target)
        else:
            staged = f'models/.sync_staging/{batch.batch_id}/{item.item_id}/{Path(source).name}'
            signed = client.api('files/download?' + urlencode({'file': '/' + staged}))['attributes']['url']
            blob = client.request(signed, limit=size, timeout=180)
            moves.append({'from': staged, 'to': target})
        if len(blob) != size or digest(blob) != expected:
            raise SyncError('Repair bytes differ from the pending manifest; no files moved')
    if moves:
        client.api('files/rename', method='PUT', payload={'root': '/', 'files': moves})
    return model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', required=True)
    parser.add_argument('--restore-deleted', action='store_true', required=True)
    args = parser.parse_args()
    if not HEX.fullmatch(args.batch): raise SystemExit('Invalid batch ID')
    root = Path('.')
    items, _ = discover_pending(root)
    batch = next((b for b in build_batches(items) if b.batch_id == args.batch), None)
    if batch is None: raise SystemExit('Pending manifest no longer matches requested batch')
    try:
        client = PteroClient(os.environ.get('PTERO_PANEL_URL', ''), os.environ.get('PTERO_SERVER_ID', ''),
                             os.environ.get('PTERO_CLIENT_API_KEY', ''),
                             allow_insecure_http=os.environ.get('PTERO_ALLOW_INSECURE_HTTP') == 'true')
        model = restore_deleted(batch, client)
        print('Server restored matching files for existing model ' + str(model), flush=True)
        client.reset_failed_receipt(batch.batch_id)
        client.command('modelsync import ' + batch.batch_id)
        receipt = client.poll_receipt(batch.batch_id)
        row = validated_rows(batch, receipt)[batch.items[0].item_id]
        if row.get('status') != 'persisted' or row.get('model_id') != model:
            raise SyncError('Server has not confirmed the original model; inputs retained')
        data = verified_downloads(batch, receipt, client)
        commit, result = publish_archive(root, batch, receipt, data)
        client.command(f'modelsync acknowledge {batch.batch_id} {commit}')
        print(f'Repair confirmed: model {model}; archive commit {commit}; {len(result.failed)} failed items.')
    except (SyncError, OSError, ValueError, KeyError) as exc:
        raise SystemExit(str(exc) if isinstance(exc, SyncError) else 'Repair failed; inputs retained.') from None


if __name__ == '__main__': main()
