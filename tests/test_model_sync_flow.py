import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from model_sync_manifest import discover_pending, build_batches
from model_sync import sync_batch, main
from unittest.mock import patch


class FakeServer:
    def __init__(self, batch):
        self.batch = batch; self.uploads = []; self.commands = []; self.done = False
    def existing_receipt(self, batch): return self.receipt() if self.done else None
    def upload(self, path, blob): self.uploads.append(path)
    def command(self, text): self.commands.append(text); self.done = True
    def poll_receipt(self, batch): return self.receipt()
    def receipt(self):
        return dict(version=1,batch_id=self.batch.batch_id,complete=True,items=[dict(item_id=i.item_id,status='persisted',error_code='',model_id=20001,dbid=20001,dff_path=i.final_dff,txd_path=i.final_txd) for i in self.batch.items])
    def download(self, path): return b'dff' if path.endswith('.dff') else b'txd'


class FlowTests(unittest.TestCase):
    def test_archive_only_manual_run_never_constructs_client(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); archive=root/'custom_graffiti/1/tag.dff'
            archive.parent.mkdir(parents=True); archive.write_bytes(b'archive')
            with patch('model_sync.PteroClient') as constructor:
                self.assertEqual(main(['sync','--root',str(root)]),0)
                constructor.assert_not_called()

    def test_workflow_only_triggers_on_pending_pushes(self):
        workflow=(Path(__file__).parents[1]/'.github/workflows/sync-models.yml').read_text()
        self.assertIn("'pending_skins/**'",workflow)
        self.assertIn("'pending_graffiti/**'",workflow)
        self.assertNotIn("'custom_skins/**'",workflow)
        self.assertNotIn("'custom_graffiti/**'",workflow)
        self.assertNotIn('pull_request',workflow)
        self.assertIn('workflow_dispatch:',workflow)
        self.assertIn('cancel-in-progress: false',workflow)
    def test_receipt_retry_does_not_reupload(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); pair=root/'pending_skins/1/man_base=7_slot=1'
            pair.parent.mkdir(parents=True); pair.with_suffix('.dff').write_bytes(b'dff'); pair.with_suffix('.txd').write_bytes(b'txd')
            batch=build_batches(discover_pending(root)[0])[0]; server=FakeServer(batch)
            receipt, blobs = sync_batch(root,batch,server)
            self.assertEqual(len(server.uploads),3)
            self.assertTrue(all('/.sync_staging/' in path for path in server.uploads))
            server.uploads.clear()
            receipt, blobs = sync_batch(root,batch,server)
            self.assertEqual(server.uploads,[])
            self.assertEqual(blobs[batch.items[0].final_dff],b'dff')

    def test_source_changed_since_manifest_is_not_uploaded(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); pair=root/'pending_skins/1/man_base=7_slot=1'
            pair.parent.mkdir(parents=True); pair.with_suffix('.dff').write_bytes(b'dff'); pair.with_suffix('.txd').write_bytes(b'txd')
            batch=build_batches(discover_pending(root)[0])[0]; server=FakeServer(batch)
            pair.with_suffix('.txd').write_bytes(b'new')
            with self.assertRaises(Exception): sync_batch(root,batch,server)
            self.assertFalse(server.uploads)
