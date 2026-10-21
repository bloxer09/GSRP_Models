import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from model_sync_manifest import discover_pending, build_batches, serialize_wire


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def pair(self, path='pending_skins/1/man_base=7_slot=1'):
        base = self.root / path
        base.parent.mkdir(parents=True, exist_ok=True)
        base.with_suffix('.dff').write_bytes(b'dff-data')
        base.with_suffix('.txd').write_bytes(b'txd-data')
        return base

    def test_only_pending_roots_are_inputs(self):
        self.pair('custom_skins/private_skins/1/archived')
        self.pair('custom_graffiti/2/archived')
        self.assertEqual(discover_pending(self.root), ([], []))

    def test_valid_pair_is_stable_and_bounded(self):
        self.pair()
        items, errors = discover_pending(self.root)
        self.assertEqual(errors, [])
        self.assertEqual((items[0].base_model, items[0].slot), (7, 1))
        batch = build_batches(items)[0]
        self.assertEqual(batch.batch_id, build_batches(items)[0].batch_id)
        self.assertLess(max(map(len, serialize_wire(batch).splitlines())), 512)
        self.assertEqual(items[0].final_dff, 'custom_skins/private_skins/1/man_base=7_slot=1.dff')

    def test_sidecar_supplies_metadata(self):
        base = self.pair('pending_skins/1/man')
        base.with_suffix('.model.json').write_text(json.dumps({'base_model': 7, 'slot': 2}))
        items, errors = discover_pending(self.root)
        self.assertFalse(errors)
        self.assertEqual(items[0].slot, 2)

    def test_missing_skin_metadata_is_rejected(self):
        self.pair('pending_skins/1/man')
        items, errors = discover_pending(self.root)
        self.assertFalse(items)
        self.assertTrue(errors)

    def test_conflicting_sidecar_rejected(self):
        base = self.pair()
        base.with_suffix('.model.json').write_text(json.dumps({'base_model': 8, 'slot': 1}))
        self.assertFalse(discover_pending(self.root)[0])

    def test_orphans_and_incomplete_pairs_remain(self):
        base = self.pair('pending_skins/orphan')
        self.assertTrue(discover_pending(self.root)[1])
        self.assertTrue(base.with_suffix('.dff').exists())

    def test_graffiti_default(self):
        self.pair('pending_graffiti/50/tag')
        item = discover_pending(self.root)[0][0]
        self.assertEqual((item.base_model, item.slot, item.final_dff), (19477, 0, 'custom_graffiti/50/tag.dff'))

    def test_lfs_and_long_paths_rejected(self):
        base = self.pair()
        base.with_suffix('.dff').write_bytes(b'version https://git-lfs.github.com/spec/v1\n')
        self.assertFalse(discover_pending(self.root)[0])
        self.pair('pending_skins/2/' + 'x' * 50 + '_base=7_slot=1')
        self.assertFalse(discover_pending(self.root)[0])

    def test_case_collisions_rejected(self):
        self.pair('pending_graffiti/1/tag')
        # Windows cannot create distinct files differing only in case. A wrong
        # extension case is itself invalid on every host.
        (self.root / 'pending_graffiti/1/tag.txd').rename(self.root / 'pending_graffiti/1/tag.TXD')
        self.assertFalse(discover_pending(self.root)[0])

    def test_changed_bytes_change_identity(self):
        base = self.pair()
        first = discover_pending(self.root)[0][0].item_id
        base.with_suffix('.txd').write_bytes(b'new')
        self.assertNotEqual(first, discover_pending(self.root)[0][0].item_id)

    def test_batches_bound_total_bytes(self):
        self.pair()
        item=discover_pending(self.root)[0][0]
        large=[replace(item,item_id=f'{i:064x}',dff_bytes=100*1024*1024,txd_bytes=100*1024*1024) for i in range(3)]
        self.assertEqual(len(build_batches(large)),3)

    def test_shared_pawn_wire_fixture(self):
        base=self.pair('pending_skins/1/man')
        base.with_suffix('.dff').write_bytes(b'dff'); base.with_suffix('.txd').write_bytes(b'txd')
        base.with_suffix('.model.json').write_bytes(b'{"base_model":7,"slot":1}\n')
        wire=serialize_wire(build_batches(discover_pending(self.root)[0])[0])
        self.assertEqual(wire,(Path(__file__).parent/'fixtures/model-sync.tsv').read_bytes())


if __name__ == '__main__':
    unittest.main()
