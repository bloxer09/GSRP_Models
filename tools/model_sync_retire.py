"""Retire custom skins on the server by model ID, then drop their private archive copies here.

The server retires each skin like /delskin: the registration, wardrobe ownership and equipped
outfits go, while the files stay on the server and stay reserved against re-import. Archived
public skins are kept, because public variants can share one DFF."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import sys
import time
from model_sync_ptero import PteroClient, SyncError

MIN_SKIN, MAX_SKIN, MAX_IDS = 20001, 30000, 32
PRIVATE_ARCHIVE = re.compile(r'custom_skins/private_skins/[1-9][0-9]*/[A-Za-z0-9_.=-]+\.(?:dff|txd)')


def parse_ids(text):
    try:
        ids = sorted({int(part) for part in re.split(r'[\s,]+', text.strip()) if part})
    except ValueError:
        raise SyncError('Skin IDs must be whole numbers separated by commas or spaces') from None
    if not ids or len(ids) > MAX_IDS or any(not MIN_SKIN <= i <= MAX_SKIN for i in ids):
        raise SyncError(f'Give 1-{MAX_IDS} custom skin IDs between {MIN_SKIN} and {MAX_SKIN}')
    return ids


def request_id(ids, nonce):
    return hashlib.sha256(f'retire:{",".join(map(str, ids))}:{nonce}'.encode()).hexdigest()


def archive_removals(receipt, ids):
    """Archived private files of the skins the receipt says were retired. Anything else is ignored."""
    paths = []
    for row in receipt.get('results', []):
        if row.get('status') != 'retired' or row.get('model_id') not in ids:
            continue
        paths += [row[key] for key in ('dff', 'txd') if PRIVATE_ARCHIVE.fullmatch(str(row.get(key, '')))]
    return paths


def summary(text):
    print(text)
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print('::notice title=Retire skins::' + text.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A'))
    if target := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(target, 'a', encoding='utf-8') as stream:
            stream.write(text + '\n\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ids', help='custom skin IDs, e.g. "21489,21490"')
    parser.add_argument('--root', type=Path, default=Path('.'))
    options = parser.parse_args(argv)
    try:
        ids = parse_ids(options.ids)
        nonce = f'{os.environ.get("GITHUB_RUN_ID", "")}.{os.environ.get("GITHUB_RUN_ATTEMPT", "")}.{time.time_ns()}'
        request = request_id(ids, nonce)
        client = PteroClient(os.environ.get('PTERO_PANEL_URL', ''), os.environ.get('PTERO_SERVER_ID', ''), os.environ.get('PTERO_CLIENT_API_KEY', ''),
                             allow_insecure_http=os.environ.get('PTERO_ALLOW_INSECURE_HTTP') == 'true')
        client.command(f'modelsync retire {request} {",".join(map(str, ids))}')
        receipt = client.poll_receipt(request)
    except (SyncError, OSError, KeyError, ValueError) as exc:
        # Transport errors intentionally contain no credentials or signed URLs.
        summary(str(exc) if isinstance(exc, SyncError) else f'Retire failed ({type(exc).__name__}).')
        return 1

    results = {row.get('model_id'): row.get('status') for row in receipt.get('results', [])}
    for skin in ids:
        summary(f'Skin {skin}: {results.get(skin, "no result")}.')
    for path in archive_removals(receipt, ids):
        (options.root / path).unlink(missing_ok=True)
        summary(f'Removed archived {path}.')
    return 0 if all(results.get(skin) == 'retired' for skin in ids) else 1


if __name__ == '__main__':
    sys.exit(main())
