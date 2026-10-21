"""Return confirmed server bytes to archive folders, never to deployment inputs."""
from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
import tempfile
from model_sync_manifest import canonical, digest
from model_sync_ptero import SyncError


@dataclass
class ArchiveResult:
    changed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    preserved: list[str] = field(default_factory=list)


def validated_rows(batch, receipt):
    if receipt.get('version') != 1 or receipt.get('batch_id') != batch.batch_id or receipt.get('complete') is not True:
        raise SyncError('Receipt identity/version/completion mismatch')
    rows = receipt.get('items')
    if not isinstance(rows, list) or len(rows) != len(batch.items):
        raise SyncError('Receipt item count mismatch')
    by_id = {}
    expected = {item.item_id: item for item in batch.items}
    for row in rows:
        if not isinstance(row, dict) or row.get('item_id') not in expected or row['item_id'] in by_id:
            raise SyncError('Unknown or duplicate receipt item')
        item = expected[row['item_id']]
        if (row.get('dff_path'), row.get('txd_path')) != (item.final_dff, item.final_txd):
            raise SyncError('Receipt final path mismatch')
        if row.get('status') == 'persisted':
            model = row.get('model_id')
            if type(model) is not int or type(row.get('dbid')) is not int or row['dbid'] <= 0 or row.get('error_code'):
                raise SyncError('Invalid persisted receipt')
            if not (20001 <= model <= 30000 if item.kind == 'skin' else -1999 <= model <= -1000):
                raise SyncError('Invalid registered model ID')
            if item.kind == 'skin' and row['dbid'] != model:
                raise SyncError('Skin persistence identity mismatch')
        elif row.get('status') != 'failed':
            raise SyncError('Receipt contains nonterminal items')
        by_id[row['item_id']] = row
    return by_id


def verified_downloads(batch, receipt, client):
    rows = validated_rows(batch, receipt)
    data = {}
    for item in batch.items:
        if rows[item.item_id]['status'] != 'persisted':
            continue
        for path, expected, length in [(item.final_dff, item.dff_sha256, item.dff_bytes), (item.final_txd, item.txd_sha256, item.txd_bytes)]:
            blob = client.download('models/' + path)
            if len(blob) != length or digest(blob) != expected:
                raise SyncError('Confirmed server file digest mismatch; inputs retained')
            data[path] = blob
    return data


def _path(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root) or any(parent.is_symlink() for parent in [path, *path.parents] if parent != root and root in parent.parents):
        raise SyncError('Refused symlink/out-of-repository archive path')
    return path


def reconcile_archive(root, batch, receipt, downloaded):
    rows = validated_rows(batch, receipt)
    root = Path(root)
    writes, removals = {}, []
    result = ArchiveResult()
    for item in batch.items:
        if rows[item.item_id]['status'] != 'persisted':
            result.failed.append(item.item_id)
            continue
        for final, expected in [(item.final_dff, item.dff_sha256), (item.final_txd, item.txd_sha256)]:
            blob = downloaded.get(final)
            if blob is None or digest(blob) != expected:
                raise SyncError('Missing or unverified server bytes')
            path = _path(root, final)
            if path.exists() and path.read_bytes() != blob:
                raise SyncError('Existing archive differs; pending inputs retained')
            writes[final] = blob
        # Preserve the whole pending item if any part changed, including metadata.
        current = {source: (_path(root, source).read_bytes() if _path(root, source).is_file() else None)
                   for source in item.source_hashes}
        unchanged = all(blob is not None and digest(blob) == item.source_hashes[source] for source, blob in current.items())
        # A newly added sidecar also changes the pending item.
        sidecar = str(Path(item.source_dff).with_suffix('.model.json')).replace('\\', '/')
        if sidecar not in item.source_hashes and _path(root, sidecar).exists():
            unchanged = False
        if unchanged:
            removals.extend(item.source_hashes)
            if sidecar in current:
                writes[str(Path(item.final_dff).with_suffix('.model.json')).replace('\\', '/')] = current[sidecar]
        elif any(blob is not None for blob in current.values()):
            result.preserved.append(item.item_id)
    # No partial filesystem edits before all receipt/digest/conflict checks pass.
    report = f'model_imports/{batch.batch_id}.json'
    writes[report] = canonical({'manifest': batch.to_dict(), 'receipt': receipt}) + b'\n'
    for relative, blob in writes.items():
        path = _path(root, relative)
        if not path.exists() or path.read_bytes() != blob:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
            result.changed.append(relative)
    for relative in removals:
        _path(root, relative).unlink()
        result.changed.append(relative)
    return result


def git(root, *args):
    try:
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE, text=True).strip()
    except subprocess.CalledProcessError:
        raise SyncError('Git operation failed; pending inputs and server receipts remain recoverable') from None


def publish_archive(root, batch, receipt, downloaded):
    """Reconcile in a disposable worktree, preserving the invoking checkout."""
    for attempt in range(3):
        git(root, 'fetch', 'origin', 'main')
        with tempfile.TemporaryDirectory(prefix='model-sync-archive-') as directory:
            work = Path(directory) / 'checkout'
            git(root, 'worktree', 'add', '--detach', str(work), 'origin/main')
            try:
                result = reconcile_archive(work, batch, receipt, downloaded)
                if result.changed:
                    git(work, 'add', '--', *result.changed)
                    git(work, '-c', 'user.name=github-actions[bot]', '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com', 'commit', '-m', f'Archive imported models {batch.batch_id[:12]}')
                    try:
                        git(work, 'push', 'origin', 'HEAD:refs/heads/main')
                    except SyncError:
                        if attempt == 2:
                            raise
                        continue
                return git(work, 'rev-parse', 'HEAD'), result
            finally:
                # This is only the verified temporary checkout created above.
                git(root, 'worktree', 'remove', '--force', str(work))
    raise SyncError('Archive reconciliation exhausted retries')
