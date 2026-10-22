import io
import json
import sys
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from model_sync_ptero import PteroClient, SafeRedirect, SyncError
import urllib.request


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class PteroTests(unittest.TestCase):
    def client(self, values):
        opened = []
        def opener(req, timeout):
            opened.append(req)
            value = values.pop(0)
            return Response(json.dumps(value).encode() if isinstance(value, dict) else value)
        return PteroClient('https://panel.test', 'abcd1234', 'secret', opener=opener, sleep=lambda _: None), opened

    def test_signed_download_never_gets_panel_token(self):
        client, calls = self.client([{'attributes': {'url': 'https://wings.test/file?token=signed'}}, b'binary'])
        self.assertEqual(client.download('models/custom_graffiti/1/tag.dff'), b'binary')
        self.assertEqual(calls[0].get_header('Authorization'), 'Bearer secret')
        self.assertIsNone(calls[1].get_header('Authorization'))

    def test_public_skin_final_download_keeps_signed_credentials_separate(self):
        for name in ['army.dff', 'army_v1.txd']:
            client, calls = self.client([{'attributes': {'url': 'https://wings.test/file?token=signed'}}, b'binary'])
            self.assertEqual(client.download('models/custom_skins/public_skins/1/' + name), b'binary')
            self.assertEqual(calls[0].get_header('Authorization'), 'Bearer secret')
            self.assertIsNone(calls[1].get_header('Authorization'))

    def test_pending_and_other_paths_are_not_downloadable_as_final_models(self):
        client, calls = self.client([])
        for path in ['models/pending_public_skins/1/army.dff', 'models/custom_skins/public_skins/1/army.model.json',
                     'models/custom_skins/public_skins/0/army.dff', 'models/custom_skins/public_skins/1/../army.dff']:
            with self.assertRaises(SyncError): client.download(path)
        self.assertEqual(calls, [])

    def test_upload_to_final_folder_is_rejected(self):
        client, calls = self.client([])
        with self.assertRaises(SyncError):
            client.upload('models/custom_skins/private_skins/1/tag.dff', b'data')
        self.assertEqual(calls, [])

    def test_mismatched_receipt_rejected(self):
        client, _ = self.client([{'data': [{'attributes': {'name': 'model_sync', 'is_file': False}}]}, {'data': [{'attributes': {'name': 'a' * 64 + '.json', 'is_file': True}}]}, {'version': 1, 'batch_id': 'b' * 64, 'complete': True, 'items': []}])
        with self.assertRaises(SyncError): client.poll_receipt('a' * 64)

    def test_insecure_signed_url_rejected(self):
        client, _ = self.client([{'attributes': {'url': 'http://wings.test/file'}}])
        with self.assertRaises(SyncError): client.download('models/custom_graffiti/1/tag.dff')

    def test_explicit_legacy_cleanup_command_is_allowed_without_extra_arguments(self):
        client, calls = self.client([{},{}])
        client.command('modelsync retirelegacy')
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0].data)['command'], 'modelsync retirelegacy')
        with self.assertRaises(SyncError): client.command('modelsync retirelegacy anything')
        client.command('modelsync retireallskins')
        with self.assertRaises(SyncError): client.command('modelsync retireallskins anything')

    def test_bad_console_batch_is_rejected(self):
        client, calls = self.client([])
        with self.assertRaises(SyncError): client.command('modelsync import x\nexit')
        self.assertFalse(calls)

    def test_auth_failure_is_not_retried(self):
        opened=Mock(side_effect=urllib.error.HTTPError('https://panel.test',403,'Forbidden',{},None))
        client=PteroClient('https://panel.test','abcd','secret',opener=opened,sleep=lambda _:None)
        with self.assertRaisesRegex(SyncError,'authorization'): client.api('resources')
        self.assertEqual(opened.call_count,1)

    def test_transient_error_retry_is_bounded(self):
        opened=Mock(side_effect=urllib.error.HTTPError('https://panel.test',503,'Unavailable',{},None))
        client=PteroClient('https://panel.test','abcd','secret',opener=opened,sleep=lambda _:None)
        with self.assertRaises(SyncError): client.api('resources')
        self.assertEqual(opened.call_count,4)

    def test_receipt_timeout_is_bounded(self):
        now=[0]
        def clock(): return now[0]
        def sleep(seconds): now[0]+=seconds
        opened=Mock(side_effect=urllib.error.HTTPError('https://panel.test',404,'Missing',{},None))
        client=PteroClient('https://panel.test','abcd','secret',opener=opened,sleep=sleep,clock=clock)
        with self.assertRaisesRegex(SyncError,'Timed out'): client.poll_receipt('a'*64,timeout_s=10)
        self.assertEqual(now[0],10)


class HttpCompatibilityTests(unittest.TestCase):
    def test_http_still_requires_explicit_opt_in(self):
        with self.assertRaises(SyncError):
            PteroClient('http://panel.test', 'abcd', 'secret')

    def test_opt_in_allows_panel_and_signed_http_without_token_leak(self):
        calls = []
        values = [b'{"attributes":{"url":"http://wings.test/file?token=signed"}}', b'model']
        def opened(req, timeout):
            calls.append(req)
            return Response(values.pop(0))
        client = PteroClient('http://panel.test', 'abcd', 'secret',
                             allow_insecure_http=True, opener=opened)
        self.assertEqual(client.download('models/custom_graffiti/1/tag.dff'), b'model')
        self.assertEqual(calls[0].get_header('Authorization'), 'Bearer secret')
        self.assertIsNone(calls[1].get_header('Authorization'))

    def test_opt_in_preserves_redirect_boundaries(self):
        handler = SafeRedirect(allow_insecure_http=True)
        req = urllib.request.Request('http://panel.test/api/client/servers/abcd/resources',
                                     headers={'Authorization': 'Bearer secret'})
        redirected = handler.redirect_request(req, None, 302, '', {},
                                              'http://panel.test/api/client/servers/abcd/resources/')
        self.assertEqual(redirected.get_header('Authorization'), 'Bearer secret')
        with self.assertRaises(SyncError):
            handler.redirect_request(req, None, 302, '', {}, 'http://other.test/')
        req = urllib.request.Request('https://wings.test/file')
        with self.assertRaises(SyncError):
            handler.redirect_request(req, None, 302, '', {}, 'http://wings.test/file')


class DiagnosticTests(unittest.TestCase):
    def test_trace_reports_request_boundary_without_signed_urls_or_tokens(self):
        from contextlib import redirect_stdout
        from unittest.mock import patch
        output = io.StringIO()
        with patch.dict('os.environ', {'MODEL_SYNC_TRACE': 'true'}), redirect_stdout(output):
            client, _ = PteroTests.client(self, [
                {'attributes': {'url': 'https://wings.test/file?token=signed-secret'}}, b'binary'])
            client.download('models/custom_graffiti/1/tag.dff')
        self.assertIn('Client API files/download', output.getvalue())
        self.assertIn('File transfer GET', output.getvalue())
        self.assertIn('completed', output.getvalue())
        for sensitive in ['signed-secret', 'wings.test', 'Bearer', 'tag.dff']:
            self.assertNotIn(sensitive, output.getvalue())

    def test_api_error_identifies_route_without_query_or_credentials(self):
        opened = Mock(side_effect=urllib.error.HTTPError('http://panel.test', 500, 'Error', {}, None))
        client = PteroClient('http://panel.test', 'abcd', 'secret',
                             allow_insecure_http=True, opener=opened, sleep=lambda _: None)
        with self.assertRaisesRegex(SyncError, 'Client API files/contents: Pterodactyl request failed \(500\)') as raised:
            client.api('files/contents?file=private-path')
        self.assertNotIn('private-path', str(raised.exception))
        self.assertNotIn('secret', str(raised.exception))


class MissingReceiptTests(unittest.TestCase):
    client = PteroTests.client

    def test_absent_receipt_is_detected_without_failing_contents_request(self):
        client, calls = self.client([
            {'data': [{'attributes': {'name': 'model_sync', 'is_file': False}}]},
            {'data': []},
        ])
        self.assertIsNone(client.existing_receipt('a' * 64))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all('/files/list?' in req.full_url for req in calls))

    def test_absent_receipt_directory_is_not_read(self):
        client, calls = self.client([{'data': []}])
        self.assertIsNone(client.existing_receipt('a' * 64))
        self.assertEqual(len(calls), 1)

    def test_existing_receipt_is_read_after_listing(self):
        receipt = {'version': 1, 'batch_id': 'a' * 64, 'complete': True, 'items': []}
        client, calls = self.client([
            {'data': [{'attributes': {'name': 'model_sync', 'is_file': False}}]},
            {'data': [{'attributes': {'name': 'a' * 64 + '.json', 'is_file': True}}]}, receipt,
        ])
        self.assertEqual(client.existing_receipt('a' * 64), receipt)
        self.assertIn('/files/contents?', calls[-1].full_url)


class UploadTimeoutTests(unittest.TestCase):
    def test_binary_upload_has_longer_bounded_timeout(self):
        opened = Mock(side_effect=lambda req, timeout: Response(b''))
        client = PteroClient('http://panel.test', 'abcd', 'secret', allow_insecure_http=True, opener=opened)
        client.ensure_directory = Mock()
        client.api = Mock(side_effect=[{'attributes': {'url': 'http://wings.test/upload?token=signed'}},
                                       {'data': [{'attributes': {'name': 'skin.txd', 'is_file': True, 'size': 4}}]}])
        client.upload('models/.sync_staging/' + 'a' * 64 + '/' + 'b' * 64 + '/skin.txd', b'data')
        self.assertEqual(opened.call_args.kwargs['timeout'], 180)
        self.assertIsNone(opened.call_args.args[0].get_header('Authorization'))
