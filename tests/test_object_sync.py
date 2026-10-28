import sys
import tempfile
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from model_sync_ptero import SyncError
from object_sync import ObjectClient, discover_objects, sync_objects


class FakeServer:
    def __init__(self, sizes):
        self.sizes, self.uploaded = sizes, []

    def server_size(self, path):
        return self.sizes.get(path)

    def upload_object(self, path, data):
        self.uploaded.append((path, data))


class ObjectSyncTests(unittest.TestCase):
    def tree(self, files):
        root = Path(tempfile.mkdtemp())
        for name, data in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(data)
        return root

    def test_objects_map_to_models_folder(self):
        root = self.tree({'custom_objects/fuel/fuel_hose.dff': b'a', 'custom_objects/fuel/fuel.txd': b'b'})
        files, errors = discover_objects(root)
        self.assertEqual(errors, [])
        self.assertEqual(files, [('custom_objects/fuel/fuel.txd', 'models/fuel/fuel.txd'),
                                 ('custom_objects/fuel/fuel_hose.dff', 'models/fuel/fuel_hose.dff')])

    def test_bad_names_and_reserved_folders_are_rejected(self):
        root = self.tree({'custom_objects/fuel/readme.md': b'x', 'custom_objects/loose.dff': b'x',
                          'custom_objects/custom_skins/a.dff': b'x', 'custom_objects/a/b/c.dff': b'x'})
        files, errors = discover_objects(root)
        self.assertEqual(files, [])
        self.assertEqual(len(errors), 4)

    def test_uploads_missing_skips_same_size_and_never_replaces(self):
        root = self.tree({'custom_objects/fuel/new.dff': b'new', 'custom_objects/fuel/same.dff': b'same', 'custom_objects/fuel/diff.dff': b'diff'})
        files, _ = discover_objects(root)
        server = FakeServer({'models/fuel/same.dff': 4, 'models/fuel/diff.dff': 99})
        self.assertEqual(sync_objects(root, files, server, report=lambda _: None), 1)
        self.assertEqual(server.uploaded, [('models/fuel/new.dff', b'new')])

    def test_client_refuses_paths_outside_object_folders(self):
        client = ObjectClient('https://panel.test', 'abcd1234', 'secret', opener=lambda *a, **k: None)
        for path in ['models/custom_skins/x.dff', 'models/.sync_staging/x.dff', 'models/x.dff', 'scriptfiles/x.dff', 'models/fuel/x.amx']:
            with self.assertRaises(SyncError):
                client.upload_object(path, b'x')


if __name__ == '__main__':
    unittest.main()
