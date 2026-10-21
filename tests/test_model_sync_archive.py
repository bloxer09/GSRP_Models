from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from model_sync_manifest import discover_pending, build_batches
from model_sync_archive import verified_downloads, reconcile_archive, publish_archive, git
from model_sync_ptero import SyncError


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'pending_skins/1/man_base=7_slot=1'
        self.source.parent.mkdir(parents=True)
        self.source.with_suffix('.dff').write_bytes(b'dff')
        self.source.with_suffix('.txd').write_bytes(b'txd')
        self.batch = build_batches(discover_pending(self.root)[0])[0]
        self.item = self.batch.items[0]
        self.receipt = dict(version=1, batch_id=self.batch.batch_id, complete=True, items=[dict(item_id=self.item.item_id, status='persisted', error_code='', model_id=20001, dbid=20001, dff_path=self.item.final_dff, txd_path=self.item.final_txd)])
        self.payloads = {self.item.final_dff: b'dff', self.item.final_txd: b'txd'}

    def test_verified_server_bytes_are_archived_and_pending_removed(self):
        result = reconcile_archive(self.root, self.batch, self.receipt, self.payloads)
        self.assertEqual((self.root / self.item.final_dff).read_bytes(), b'dff')
        self.assertFalse(self.source.with_suffix('.dff').exists())
        self.assertTrue(result.changed)

    def test_changed_pending_is_preserved_as_whole_pair(self):
        self.source.with_suffix('.txd').write_bytes(b'newer')
        result = reconcile_archive(self.root, self.batch, self.receipt, self.payloads)
        self.assertTrue(self.source.with_suffix('.dff').exists())
        self.assertEqual(self.source.with_suffix('.txd').read_bytes(), b'newer')
        self.assertIn(self.item.item_id, result.preserved)

    def test_final_conflict_never_overwritten(self):
        path = self.root / self.item.final_dff
        path.parent.mkdir(parents=True); path.write_bytes(b'other')
        with self.assertRaises(SyncError): reconcile_archive(self.root, self.batch, self.receipt, self.payloads)
        self.assertEqual(path.read_bytes(), b'other')
        self.assertTrue(self.source.with_suffix('.dff').exists())

    def test_digest_mismatch_never_archived(self):
        self.payloads[self.item.final_dff] = b'corrupt'
        with self.assertRaises(SyncError): reconcile_archive(self.root, self.batch, self.receipt, self.payloads)

    def test_receipt_cannot_choose_another_path(self):
        self.receipt['items'][0]['dff_path'] = 'custom_graffiti/99/wrong.dff'
        with self.assertRaises(SyncError): verified_downloads(self.batch, self.receipt, object())

    def test_failure_preserves_inputs(self):
        self.receipt['items'][0]['status'] = 'failed'
        result = reconcile_archive(self.root, self.batch, self.receipt, {})
        self.assertTrue(self.source.with_suffix('.dff').exists())
        self.assertEqual(result.failed, [self.item.item_id])

    def test_real_git_archive_retry_preserves_newer_push(self):
        with tempfile.TemporaryDirectory() as temporary:
            bare = Path(temporary) / 'remote.git'
            other = Path(temporary) / 'other'
            def run(*args):
                return subprocess.check_output(['git', *map(str,args)], stderr=subprocess.PIPE, text=True).strip()
            run('init','--bare','--initial-branch=main',bare)
            run('init','--initial-branch=main',self.root)
            run('-C',self.root,'remote','add','origin',bare)
            run('-C',self.root,'add','.')
            run('-C',self.root,'-c','user.name=Test','-c','user.email=test@example.com','commit','-m','Pending')
            run('-C',self.root,'push','-u','origin','main')
            run('clone',bare,other)
            pushed = False
            def race(root, *args):
                nonlocal pushed
                if args[:2] == ('push','origin') and not pushed:
                    pushed = True
                    (other / self.item.source_txd).write_bytes(b'newer')
                    run('-C',other,'add','.')
                    run('-C',other,'-c','user.name=Test','-c','user.email=test@example.com','commit','-m','Newer upload')
                    run('-C',other,'push')
                return git(root,*args)
            with patch('model_sync_archive.git',side_effect=race):
                commit,result=publish_archive(self.root,self.batch,self.receipt,self.payloads)
            self.assertTrue(pushed)
            self.assertIn(self.item.item_id,result.preserved)
            self.assertEqual(run('--git-dir',bare,'show','main:'+self.item.source_txd),'newer')
            self.assertEqual(run('--git-dir',bare,'show','main:'+self.item.final_txd),'txd')
            self.assertEqual(self.source.with_suffix('.txd').read_bytes(),b'txd')
