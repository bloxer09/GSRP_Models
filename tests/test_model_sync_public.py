import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from model_sync_manifest import discover_pending, build_batches, serialize_wire
from model_sync_archive import reconcile_archive, validated_rows, verified_downloads


class PublicSkinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'pending_public_skins/1'
        self.folder.mkdir(parents=True)
        (self.folder / 'male01.dff').write_bytes(b'shared-dff')

    def variant(self, name, meta=None):
        (self.folder / f'{name}.txd').write_bytes(name.encode())
        if meta is not False:
            (self.folder / f'{name}.model.json').write_text(json.dumps(meta or {'base_model': 7, 'gender': 'male', 'race': 'black'}))

    def receipt(self, batch, model=20001):
        rows = [dict(item_id=item.item_id, status='persisted', error_code='', model_id=model + i, dbid=model + i,
                     dff_path=item.final_dff, txd_path=item.final_txd) for i, item in enumerate(batch.items)]
        return dict(version=1, batch_id=batch.batch_id, complete=True, items=rows)

    def payloads(self, batch):
        data = {}
        for item in batch.items:
            data[item.final_dff] = b'shared-dff'
            data[item.final_txd] = (self.root / item.source_txd).read_bytes()
        return data

    def test_variants_share_one_model(self):
        self.variant('male01_v1'); self.variant('male01_v2', {'base_model': 7, 'gender': 'female', 'race': 'hispanic'})
        items, errors = discover_pending(self.root)
        self.assertEqual(errors, [])
        self.assertEqual({i.final_dff for i in items}, {'custom_skins/public_skins/1/male01.dff'})
        self.assertEqual(sorted(i.slot for i in items), [3, 6])
        self.assertEqual({i.kind for i in items}, {'public'})
        wire = serialize_wire(build_batches(items)[0]).decode()
        self.assertIn('\tpublic\t1\t7\t', wire)
        self.assertIn('\tmale01.dff\tmale01_v1.txd\t', wire)

    def test_plain_texture_name_uses_same_stem(self):
        self.variant('male01')
        item = discover_pending(self.root)[0][0]
        self.assertEqual(item.final_txd, 'custom_skins/public_skins/1/male01.txd')

    def test_missing_or_invalid_metadata_rejected(self):
        self.variant('male01_v1', False)
        self.variant('male01_v2', {'base_model': 7, 'gender': 'male'})
        self.variant('male01_v3', {'base_model': 74, 'gender': 'male', 'race': 'white'})
        self.variant('male01_v4', {'base_model': 7, 'gender': 'male', 'race': 'white', 'slot': 1})
        items, errors = discover_pending(self.root)
        self.assertFalse(items)
        for n in range(1, 5):
            self.assertTrue(any(e.startswith(f'pending_public_skins/1/male01_v{n}.txd:') for e in errors))

    def test_missing_shared_model_and_orphan_model_rejected(self):
        self.variant('female02_v1')
        items, errors = discover_pending(self.root)
        self.assertFalse(items)
        self.assertTrue(any('female02_v1.txd' in e for e in errors))
        self.assertTrue(any('male01.dff' in e for e in errors))

    def test_shared_model_downloaded_once(self):
        self.variant('male01_v1'); self.variant('male01_v2')
        batch = build_batches(discover_pending(self.root)[0])[0]
        payloads = self.payloads(batch)
        calls = []

        class Client:
            def download(self, path):
                calls.append(path)
                return payloads[path[len('models/'):]]
        verified_downloads(batch, self.receipt(batch), Client())
        self.assertEqual(calls.count('models/custom_skins/public_skins/1/male01.dff'), 1)

    def test_shared_model_stays_pending_until_last_variant(self):
        self.variant('male01_v1'); self.variant('male01_v2')
        items = discover_pending(self.root)[0]
        first = build_batches([i for i in items if i.source_txd.endswith('v1.txd')])[0]
        reconcile_archive(self.root, first, self.receipt(first), self.payloads(first))
        self.assertTrue((self.folder / 'male01.dff').exists())
        self.assertFalse((self.folder / 'male01_v1.txd').exists())
        self.assertTrue((self.root / 'custom_skins/public_skins/1/male01_v1.model.json').exists())
        second = build_batches(discover_pending(self.root)[0])[0]
        self.assertEqual(len(second.items), 1)
        reconcile_archive(self.root, second, self.receipt(second, 20002), self.payloads(second))
        self.assertFalse((self.folder / 'male01.dff').exists())
        self.assertEqual((self.root / 'custom_skins/public_skins/1/male01.dff').read_bytes(), b'shared-dff')

    def test_public_receipt_requires_skin_identity(self):
        self.variant('male01_v1')
        batch = build_batches(discover_pending(self.root)[0])[0]
        receipt = self.receipt(batch)
        validated_rows(batch, receipt)
        receipt['items'][0]['dbid'] = 5
        with self.assertRaises(Exception):
            validated_rows(batch, receipt)


if __name__ == '__main__':
    unittest.main()
