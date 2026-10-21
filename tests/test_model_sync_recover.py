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
        self.receipt = dict(version=1, batch_id=batch.batch_id, complete=True, items=[dict(
            item_id=item.item_id, status='failed', error_code='PATH_CONFLICT', model_id=20002+i,
            dbid=20002+i, dff_path=item.final_dff, txd_path=item.final_txd) for i,item in enumerate(batch.items)])
        self.files = {}
        for item in batch.items:
            for path,blob in [(item.source_dff,b'dff'),(item.source_txd,b'txd')]:
                self.files[f'models/.sync_staging/{batch.batch_id}/{item.item_id}/{Path(path).name}']=blob
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


class PublicRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);folder=self.root/'pending_public_skins/1';folder.mkdir(parents=True)
        (folder/'skin.dff').write_bytes(b'dff')
        for name in ['skin_v1','skin_v2']:
            (folder/(name+'.txd')).write_bytes(b'txd')
            (folder/(name+'.model.json')).write_text(json.dumps({'base_model':7,'gender':'male','race':'black'}))
        self.batch=build_batches(discover_pending(self.root)[0])[0]
        class UploadServer(Server):
            def upload(self,path,blob):
                assert path.startswith('models/.sync_staging/')
                self.files[path]=blob
                self.uploads.append(path)
        self.server=UploadServer(self.batch);self.server.files.clear();self.server.uploads=[]
        for row in self.server.receipt['items']:
            row.update(status='persisted',error_code='')
    def test_restores_missing_public_batch_from_pending_via_staging(self):
        models=restore_deleted(self.batch,self.server,root=self.root)
        self.assertEqual(len(models),2)
        self.assertEqual(len(self.server.moves),3)
        self.assertEqual(len(self.server.uploads),3)
        self.assertEqual(self.server.files['models/custom_skins/public_skins/1/skin.dff'],b'dff')
    def test_changed_pending_bytes_are_rejected_before_upload(self):
        (self.root/self.batch.items[0].source_txd).write_bytes(b'changed')
        with self.assertRaises(SyncError):restore_deleted(self.batch,self.server,root=self.root)
        self.assertEqual(self.server.moves,[])
        self.assertEqual(self.server.uploads,[])
    def test_foreign_public_final_prevents_any_move(self):
        self.server.files['models/'+self.batch.items[1].final_txd]=b'foreign'
        with self.assertRaises(SyncError):restore_deleted(self.batch,self.server,root=self.root)
        self.assertEqual(self.server.moves,[])
