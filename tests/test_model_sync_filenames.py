import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from model_sync_manifest import discover_pending, build_batches
from model_sync_archive import reconcile_archive

class FilenameTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def pair(self, folder, stem):
        path=self.root/folder/stem; path.parent.mkdir(parents=True,exist_ok=True)
        path.with_suffix('.dff').write_bytes(b'dff'); path.with_suffix('.txd').write_bytes(b'txd')
        return path
    def test_public_filename_supplies_all_metadata_without_json(self):
        self.pair('pending_public_skins/1','7_male_black')
        items,errors=discover_pending(self.root)
        self.assertEqual(errors,[])
        self.assertEqual((items[0].base_model,items[0].slot),(7,3))
        self.assertEqual(len(items[0].source_hashes),2)
    def test_private_filename_supplies_character_slot_and_base(self):
        self.pair('pending_skins/1','1_2_7_female_asian')
        items,errors=discover_pending(self.root)
        self.assertEqual(errors,[])
        self.assertEqual((items[0].owner_dbid,items[0].slot,items[0].base_model),(1,2,7))
    def test_private_character_must_match_folder(self):
        self.pair('pending_skins/1','2_1_7_male_white')
        self.assertFalse(discover_pending(self.root)[0])
    def test_numbered_public_variants_can_have_distinct_models(self):
        self.pair('pending_public_skins/1','7_male_black_v1')
        self.pair('pending_public_skins/1','7_male_black_v2')
        items,errors=discover_pending(self.root)
        self.assertEqual(errors,[])
        self.assertEqual(len(items),2)
        self.assertEqual(len({i.source_dff for i in items}),2)
    def test_filename_metadata_does_not_accept_json_overrides(self):
        path=self.pair('pending_public_skins/1','7_male_black')
        path.with_suffix('.model.json').write_text('{"base_model":8,"gender":"female","race":"asian"}')
        self.assertFalse(discover_pending(self.root)[0])
    def test_invalid_filename_metadata_is_rejected(self):
        for stem in ['74_male_white','312_male_white','7_other_black','7_male_other','07_male_white','7_male_white_v0']:
            with self.subTest(stem=stem):
                self.pair('pending_public_skins/1',stem)
        self.assertFalse(discover_pending(self.root)[0])
    def test_new_archive_has_no_skin_sidecar(self):
        self.pair('pending_public_skins/1','7_male_black')
        items,errors=discover_pending(self.root); self.assertEqual(errors,[])
        batch=build_batches(items)[0]; item=items[0]
        row=dict(item_id=item.item_id,status='persisted',error_code='',model_id=20001,dbid=20001,dff_path=item.final_dff,txd_path=item.final_txd)
        receipt=dict(version=1,batch_id=batch.batch_id,complete=True,items=[row])
        reconcile_archive(self.root,batch,receipt,{item.final_dff:b'dff',item.final_txd:b'txd'})
        self.assertFalse(list((self.root/'custom_skins').rglob('*.json')))
