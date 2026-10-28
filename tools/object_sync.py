"""custom_objects/<folder>/<file> -> server models/custom_objects/<folder>/<file>, uploaded directly.

Object models are registered by the gamemode itself (AddSimpleModel with fixed IDs), so unlike
skins and graffiti they have no server import step. A server file of a different size is never
overwritten; supply a new name instead."""
import argparse
import os
from pathlib import Path
import re
import sys
import urllib.parse
from model_sync_ptero import PteroClient, SyncError, safe_path

OBJECT_SOURCE = re.compile(r'custom_objects/([A-Za-z0-9_-]+)/([A-Za-z0-9_.-]+\.(?:dff|txd))')
# Same layout as the source, beside the server's custom_skins/ and custom_graffiti/
OBJECT_DESTINATION = re.compile(r'models/custom_objects/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+\.(?:dff|txd)')


def discover_objects(root):
    """Returns ([(source, destination)], [errors]) for every file under custom_objects/."""
    root = Path(root)
    found, errors = [], []
    base = root / 'custom_objects'
    if not base.is_dir():
        return found, errors
    for path in sorted(base.rglob('*')):
        if path.is_dir():
            continue
        source = path.relative_to(root).as_posix()
        match = OBJECT_SOURCE.fullmatch(source)
        if path.is_symlink() or not match:
            errors.append(source)
            continue
        found.append((source, 'models/' + source))
    return found, errors


class ObjectClient(PteroClient):
    def server_size(self, path):
        """Size of an existing server file, or None. Lists each parent: some panels 500 on missing paths."""
        root = '/'
        for part in safe_path(path).split('/'):
            listing = self.api('files/list?' + urllib.parse.urlencode({'directory': root}))
            row = next((r['attributes'] for r in listing['data'] if r['attributes']['name'] == part), None)
            if row is None:
                return None
            if row['is_file']:
                return row['size'] if root.rstrip('/') + '/' + part == '/' + path else None
            root = root.rstrip('/') + '/' + part
        return None

    def upload_object(self, path, data):
        safe_path(path)
        if not OBJECT_DESTINATION.fullmatch(path):
            raise SyncError('Object uploads are allowed only in models/custom_objects/<folder>/')
        self._put(path, data)


def sync_objects(root, files, client, report=print):
    """Uploads missing files, skips identical sizes, refuses to replace a different file. Returns failures."""
    failed = 0
    for source, destination in files:
        data = (Path(root) / source).read_bytes()
        size = client.server_size(destination)
        if size == len(data):
            report(f'{destination}: already on the server.')
        elif size is not None:
            report(f'{destination}: a different file ({size} bytes) is already on the server; not replaced.')
            failed += 1
        else:
            client.upload_object(destination, data)
            report(f'{destination}: uploaded ({len(data)} bytes).')
    return failed


def summary(text):
    print(text)
    # Annotations show on the run page without signing in; logs don't
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print('::notice title=Object upload::' + text.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A'))
    if target := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(target, 'a', encoding='utf-8') as stream:
            stream.write(text + '\n\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--dry-run', action='store_true')
    options = parser.parse_args(argv)
    files, errors = discover_objects(options.root)
    for error in errors:
        summary(f'Invalid object file (use custom_objects/<folder>/<name>.dff or .txd): {error}')
    if options.dry_run or not files:
        for source, destination in files:
            print(f'{source} -> {destination}')
        summary(f'{"Dry run: " if options.dry_run else ""}{len(files)} object files, {len(errors)} invalid; no transfers.')
        return 1 if errors else 0
    try:
        client = ObjectClient(os.environ.get('PTERO_PANEL_URL', ''), os.environ.get('PTERO_SERVER_ID', ''), os.environ.get('PTERO_CLIENT_API_KEY', ''),
                              allow_insecure_http=os.environ.get('PTERO_ALLOW_INSECURE_HTTP') == 'true')
        failed = sync_objects(options.root, files, client, summary)
    except (SyncError, OSError, KeyError) as exc:
        # Transport errors intentionally contain no credentials or signed URLs.
        summary(str(exc) if isinstance(exc, SyncError) else f'Object upload failed ({type(exc).__name__}).')
        return 1
    summary(f'{len(files)} object files checked, {failed} conflicts, {len(errors)} invalid. Restart the server to load new models.')
    return 1 if failed or errors else 0


if __name__ == '__main__':
    sys.exit(main())
