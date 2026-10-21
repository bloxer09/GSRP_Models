"""Explicit repair of deleted, previously persisted files; normal imports stay strict."""
import argparse
import os
from pathlib import Path
from urllib.parse import urlencode
from model_sync_archive import validated_rows, verified_downloads, publish_archive
from model_sync_manifest import build_batches, discover_pending, digest, HEX, serialize_wire
from model_sync_ptero import PteroClient, SyncError


def restore_deleted(batch, client, *, root=None):
    if not batch.items or len(batch.items)>32 or any(i.kind not in ('skin','public') for i in batch.items):
        raise SyncError('Repair requires a bounded skin batch')
    receipt=client.existing_receipt(batch.batch_id)
    if receipt is None: raise SyncError('Repair requires an existing completed receipt')
    rows=validated_rows(batch,receipt)
    models={}
    for item in batch.items:
        row=rows[item.item_id];model=row.get('model_id')
        eligible=(row.get('status')=='persisted' and not row.get('error_code')) or (
            row.get('status')=='failed' and row.get('error_code')=='PATH_CONFLICT')
        if not eligible or type(model) is not int or not 20001<=model<=30000 or row.get('dbid')!=model:
            raise SyncError('Repair requires previously persisted skins; retired assets are ineligible')
        models[item.item_id]=model
    if root is not None:
        root=Path(root).resolve()
        for item in batch.items:
            for source,expected in item.source_hashes.items():
                path=root/source
                if (not path.resolve().is_relative_to(root) or path.is_symlink() or not path.is_file()
                        or digest(path.read_bytes())!=expected):
                    raise SyncError('Pending manifest changed before repair; no files uploaded')
    plans={};names_by_parent={}
    # Verify every existing final file before uploading or moving replacements.
    for item in batch.items:
        for final,source,expected,size in [
                (item.final_dff,item.source_dff,item.dff_sha256,item.dff_bytes),
                (item.final_txd,item.source_txd,item.txd_sha256,item.txd_bytes)]:
            target='models/'+final
            if target in plans:
                if plans[target][2:]!=(expected,size): raise SyncError('Shared repair paths have different hashes')
                continue
            parent=str(Path(target).parent).replace('\\','/')
            if parent not in names_by_parent:
                client.ensure_directory(parent)
                listing=client.api('files/list?'+urlencode({'directory':'/'+parent}))['data']
                names_by_parent[parent]={r['attributes']['name'] for r in listing}
            if Path(target).name in names_by_parent[parent]:
                blob=client.download(target)
                if len(blob)!=size or digest(blob)!=expected:
                    raise SyncError('Existing final repair path differs; no files moved')
                plans[target]=(None,source,expected,size)
            else:
                staged=f'models/.sync_staging/{batch.batch_id}/{item.item_id}/{Path(source).name}'
                plans[target]=(staged,source,expected,size)
    moves=[]
    staged_names={}
    for number,(target,(staged,source,expected,size)) in enumerate(plans.items(),1):
        if staged is None: continue
        def staged_bytes():
            signed=client.api('files/download?'+urlencode({'file':'/'+staged}))['attributes']['url']
            return client.request(signed,limit=size,timeout=180)
        blob=None
        if root is not None:
            parent=staged.rsplit('/',1)[0]
            if parent not in staged_names:
                client.ensure_directory(parent)
                listing=client.api('files/list?'+urlencode({'directory':'/'+parent}))['data']
                staged_names[parent]={r['attributes']['name']:r['attributes'].get('size')
                                      for r in listing if r['attributes']['is_file']}
            if staged_names[parent].get(Path(staged).name)==size:
                blob=staged_bytes()
            if blob is None or len(blob)!=size or digest(blob)!=expected:
                local=(root/source).read_bytes()
                if len(local)!=size or digest(local)!=expected: raise SyncError('Pending file changed before staging upload')
                client.upload(staged,local)
                blob=staged_bytes()
        else:
            blob=staged_bytes()
        if len(blob)!=size or digest(blob)!=expected:
            raise SyncError('Repair bytes differ from pending manifest; no files moved')
        print(f'Recovery verified staged file {number}/{len(plans)}.',flush=True)
        moves.append({'from':staged,'to':target})
    if moves: client.api('files/rename',method='PUT',payload={'root':'/','files':moves})
    return models


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
        models = restore_deleted(batch, client, root=root)
        print(f'Server restored matching files for {len(models)} existing skin models.', flush=True)
        client.upload(f'models/.sync_staging/{batch.batch_id}/manifest.tsv', serialize_wire(batch))
        client.reset_failed_receipt(batch.batch_id)
        client.command('modelsync import ' + batch.batch_id)
        receipt = client.poll_receipt(batch.batch_id)
        rows = validated_rows(batch, receipt)
        if any(rows[key].get('status')!='persisted' or rows[key].get('model_id')!=model for key,model in models.items()):
            raise SyncError('Server has not confirmed all original model IDs; inputs retained')
        data = verified_downloads(batch, receipt, client)
        commit, result = publish_archive(root, batch, receipt, data)
        client.command(f'modelsync acknowledge {batch.batch_id} {commit}')
        print(f'Repair confirmed: {len(models)} skins; archive commit {commit}; {len(result.failed)} failed items.')
    except (SyncError, OSError, ValueError, KeyError) as exc:
        raise SystemExit(str(exc) if isinstance(exc, SyncError) else 'Repair failed; inputs retained.') from None


if __name__ == '__main__': main()
