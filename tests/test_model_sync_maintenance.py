import sys
from pathlib import Path
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from model_sync_maintenance import retire_legacy
from model_sync_ptero import SyncError


class MaintenanceTests(unittest.TestCase):
    def test_stale_report_is_cleared_before_command_and_success_requires_receipt(self):
        client=Mock()
        listing={'data':[{'attributes':{'name':'legacy_cleanup.json'}}]}
        report={'complete':True,'success':True,'retired':4,'failed':0,'backup_table':'legacy_skin_backup'}
        client.api.side_effect=[listing,{},listing,report]
        self.assertEqual(retire_legacy(client),report)
        self.assertEqual(client.api.call_args_list[1].args[0],'files/delete')
        client.command.assert_called_once_with('modelsync retirelegacy')
    def test_failed_retirement_is_not_reported_as_success(self):
        client=Mock()
        client.api.side_effect=[{'data':[]},{'data':[{'attributes':{'name':'legacy_cleanup.json'}}]},
                                {'complete':True,'success':False,'retired':0,'failed':1}]
        with self.assertRaises(SyncError): retire_legacy(client)
    def test_missing_matching_server_handler_times_out(self):
        client=Mock();client.api.return_value={'data':[]}
        now=[0]
        def sleep(n):now[0]+=n
        with self.assertRaisesRegex(SyncError,'Timed out'):
            retire_legacy(client,timeout_s=2,sleep=sleep,clock=lambda:now[0])
        self.assertEqual(now[0],2)
