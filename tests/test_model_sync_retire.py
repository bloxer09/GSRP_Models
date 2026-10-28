import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from model_sync_ptero import PteroClient, SyncError
from model_sync_retire import archive_removals, parse_ids, request_id


class RetireTests(unittest.TestCase):
    def test_ids_are_parsed_deduplicated_and_bounded(self):
        self.assertEqual(parse_ids('21490, 21489 21490'), [21489, 21490])
        for bad in ['', 'abc', '311', '30001', ','.join(str(20001 + i) for i in range(33))]:
            with self.assertRaises(SyncError):
                parse_ids(bad)

    def test_request_ids_are_hex_and_unique_per_run(self):
        a, b = request_id([21489], 'run1'), request_id([21489], 'run2')
        self.assertRegex(a, r'^[0-9a-f]{64}$')
        self.assertNotEqual(a, b)

    def test_only_retired_private_archives_of_requested_ids_are_removed(self):
        receipt = {'results': [
            {'model_id': 21489, 'status': 'retired', 'dff': 'custom_skins/private_skins/15/15_1_121_male_asian.dff', 'txd': 'custom_skins/private_skins/15/15_1_121_male_asian.txd'},
            {'model_id': 21490, 'status': 'failed', 'dff': 'custom_skins/private_skins/15/a.dff', 'txd': 'custom_skins/private_skins/15/a.txd'},
            {'model_id': 21491, 'status': 'retired', 'dff': 'custom_skins/public_skins/1/7_male_black.dff', 'txd': '../../etc/passwd'},
            {'model_id': 25000, 'status': 'retired', 'dff': 'custom_skins/private_skins/9/x.dff', 'txd': ''},
        ]}
        self.assertEqual(archive_removals(receipt, [21489, 21490, 21491]),
                         ['custom_skins/private_skins/15/15_1_121_male_asian.dff', 'custom_skins/private_skins/15/15_1_121_male_asian.txd'])

    def test_console_accepts_only_well_formed_retire_commands(self):
        client = PteroClient('https://panel.test', 'abcd1234', 'secret', opener=lambda *a, **k: None)
        client.api = lambda *a, **k: {}
        client.command('modelsync retire ' + 'a' * 64 + ' 21489,21490')
        for bad in ['modelsync retire ' + 'a' * 64, 'modelsync retire ' + 'a' * 64 + ' 21489;quit', 'modelsync retire xyz 21489']:
            with self.assertRaises(SyncError):
                client.command(bad)


if __name__ == '__main__':
    unittest.main()
