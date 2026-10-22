import sys
from pathlib import Path
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from model_sync_maintenance import retire_legacy, restart_server
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

    def test_full_reset_uses_separate_receipt_and_explicit_command(self):
        client=Mock()
        listing={'data':[{'attributes':{'name':'all_skin_cleanup.json'}}]}
        report={'complete':True,'success':True,'scope':'all','retired':32,'failed':0}
        client.api.side_effect=[listing,{},listing,report]
        self.assertEqual(retire_legacy(client,include_public=True),report)
        client.command.assert_called_once_with('modelsync retireallskins')
        self.assertEqual(client.api.call_args_list[1].kwargs['payload']['files'],['all_skin_cleanup.json'])
    def test_full_reset_requires_all_scope_in_receipt(self):
        client=Mock()
        client.api.side_effect=[{'data':[]},{'data':[{'attributes':{'name':'all_skin_cleanup.json'}}]},
            {'complete':True,'success':True,'scope':'legacy','retired':0,'failed':0}]
        with self.assertRaises(SyncError):retire_legacy(client,include_public=True)

    def test_restart_waits_for_new_uptime_and_stable_running_state(self):
        client=Mock(); now=[0]
        client.api.side_effect=[{'attributes':{'current_state':'running','resources':{'uptime':100000}}},{},
            *[{'attributes':{'current_state':'running','resources':{'uptime':10}}} for _ in range(6)]]
        restart_server(client,sleep=lambda n:now.__setitem__(0,now[0]+n),clock=lambda:now[0])
        self.assertEqual(client.api.call_args_list[1].args[0],'power')
        self.assertEqual(client.api.call_args_list[1].kwargs['payload'],{'signal':'restart'})
        self.assertGreaterEqual(now[0],20)
    def test_restart_rejects_unchanged_running_server(self):
        client=Mock(); now=[0]
        client.api.return_value={'attributes':{'current_state':'running','resources':{'uptime':100000}}}
        with self.assertRaisesRegex(SyncError,'restart'):
            restart_server(client,timeout_s=10,sleep=lambda n:now.__setitem__(0,now[0]+n),clock=lambda:now[0])
