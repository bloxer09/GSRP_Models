import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from model_sync_manifest import discover_pending, build_batches
from model_sync_ptero import SyncError
from model_sync_recover import restore_deleted


class Server:
    def __init__(self, batch):
        self.batch = batch
        item = batch.items[0]
        self.receipt = dict(version=1, batch_id=batch.batch_id, complete=True, items=[dict(
            item_id=item.item_id, status='failed', error_code='PATH_CONFLICT', model_id=20002,
            dbid=20002, dff_path=item.final_dff, txd_path=item.final_txd)])
        self.files = {f'models/.sync_staging/{batch.batch_id}/{item.item_id}/skin.dff': b'dff',
                      f'models/.sync_staging/{batch.batch_id}/{item.item_id}/skin.txd': b'txd'}
        self.moves = []
    def existing_receipt(self, batch_id): return copy.deepcopy(self.receipt)
    def ensure_directory(self, path): pass
    def api(self, route, method='GET', payload=None):
        parsed = urlsplit(route)
        query = parse_qs(parsed.query)
        if parsed.path == 'files/list':
            parent = query['directory'][0].strip('/')
            return {'data': [{'attributes': {'name': Path(p).name, 'is_file': True}} for p in self.files if str(Path(p).parent).replace('\\','/') == parent]}
        if parsed.path == 'files/download': return {'attributes': {'url': query['file'][0].strip('/')}}
        if route == 'files/rename':
            for row in payload['files']:
                assert row['to'] not in self.files
                self.files[row['to']] = self.files.pop(row['from'])
                self.moves.append(row)
            return {}
        raise AssertionError(route)
    def request(self, url, **kwargs): return self.files[url]
    def download(self, path): return self.files[path]
    def upload(self, *args): raise AssertionError('Recovery must never upload into final paths')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name) / 'pending_skins/1'
        folder.mkdir(parents=True)
        (folder/'skin.dff').write_bytes(b'dff')
        (folder/'skin.txd').write_bytes(b'txd')
        (folder/'skin.model.json').write_text(json.dumps({'base_model': 7, 'slot': 1}))
        items, errors = discover_pending(Path(self.temp.name))
        self.assertFalse(errors)
        self.batch = build_batches(items)[0]
        self.server = Server(self.batch)
    def test_restore_moves_verified_staged_pair_without_upload_or_id_change(self):
        restore_deleted(self.batch, self.server)
        self.assertEqual(len(self.server.moves), 2)
        self.assertEqual(self.server.files['models/' + self.batch.items[0].final_dff], b'dff')
        self.assertEqual(self.server.receipt['items'][0]['model_id'], 20002)
    def test_wrong_staged_bytes_prevent_both_moves(self):
        path = next(p for p in self.server.files if p.endswith('.txd'))
        self.server.files[path] = b'foreign'
        with self.assertRaises(SyncError): restore_deleted(self.batch, self.server)
        self.assertEqual(self.server.moves, [])
    def test_foreign_final_file_is_never_overwritten(self):
        self.server.files['models/' + self.batch.items[0].final_txd] = b'foreign'
        with self.assertRaises(SyncError): restore_deleted(self.batch, self.server)
        self.assertEqual(self.server.moves, [])
    def test_already_restored_matching_files_are_idempotent(self):
        restore_deleted(self.batch, self.server)
        self.server.moves.clear()
        restore_deleted(self.batch, self.server)
        self.assertEqual(self.server.moves, [])
    def test_unpersisted_or_other_failures_are_rejected(self):
        for field, value in [('dbid', 0), ('model_id', -1), ('error_code', 'RETIRED_ASSET')]:
            with self.subTest(field=field):
                row = self.server.receipt['items'][0]
                old = row[field]; row[field] = value
                with self.assertRaises(SyncError): restore_deleted(self.batch, self.server)
                row[field] = old
        self.assertEqual(self.server.moves, [])
