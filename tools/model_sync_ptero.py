"""Minimal Client API transport. Signed Wings URLs never get panel credentials."""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import PurePosixPath
from model_sync_manifest import HEX


class SyncError(RuntimeError):
    pass


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, *, allow_insecure_http=False):
        super().__init__()
        self.allow_insecure_http = allow_insecure_http

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if req.has_header('Authorization') and (old.scheme, old.netloc) != (new.scheme, new.netloc):
            raise SyncError('Refused authenticated cross-origin redirect')
        if new.scheme != 'https' and not (self.allow_insecure_http and old.scheme == new.scheme == 'http' and old.netloc == new.netloc):
            raise SyncError('Refused insecure redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_path(path):
    if not isinstance(path, str) or not path or '\\' in path or path.startswith('/'):
        raise SyncError('Invalid file path')
    parts = path.split('/')
    if any(not part or part in ('.', '..') or not re.fullmatch(r'[A-Za-z0-9_.=-]+', part) for part in parts):
        raise SyncError('Invalid file path')
    return path


class PteroClient:
    def __init__(self, panel, server, token, *, allow_insecure_http=False, opener=None, sleep=time.sleep, clock=time.monotonic):
        url = urllib.parse.urlsplit(panel)
        schemes = ('https', 'http') if allow_insecure_http else ('https',)
        if url.scheme not in schemes or not url.netloc or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
            raise SyncError('PTERO_PANEL_URL must be an HTTPS origin (HTTP requires explicit opt-in)')
        if not re.fullmatch(r'[A-Za-z0-9-]+', server) or not token:
            raise SyncError('Missing or invalid Pterodactyl configuration')
        self.base = panel.rstrip('/') + '/api/client/servers/' + server
        self.token = token
        self.schemes = schemes
        self.open = opener or urllib.request.build_opener(SafeRedirect(allow_insecure_http=allow_insecure_http)).open
        self.sleep, self.clock = sleep, clock
        self.directories = {'/'}

    def request(self, url, *, method='GET', data=None, authenticated=False, content_type=None, limit=105 * 1024 * 1024):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in self.schemes or not parsed.netloc or parsed.username or parsed.password:
            raise SyncError('Refused insecure transfer URL')
        headers = {'Accept': 'application/json', 'User-Agent': 'Greenside-Model-Sync/1'}
        if authenticated:
            if not url.startswith(self.base + '/'):
                raise SyncError('Refused panel credentials outside Client API')
            headers['Authorization'] = 'Bearer ' + self.token
        if content_type:
            headers['Content-Type'] = content_type
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                with self.open(req, timeout=30) as response:
                    body = response.read(limit + 1)
                    if len(body) > limit:
                        raise SyncError('Response exceeds configured size limit')
                    return body
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise FileNotFoundError('Pterodactyl file not found') from None
                if exc.code in (401, 403):
                    raise SyncError(f'Pterodactyl authorization failed ({exc.code})') from None
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise SyncError(f'Pterodactyl request failed ({exc.code})') from None
                delay = exc.headers.get('Retry-After', '')
                self.sleep(min(30, int(delay)) if delay.isdecimal() else 2 ** attempt)
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 3:
                    raise SyncError('Pterodactyl transfer unavailable after retries') from None
                self.sleep(2 ** attempt)

    def api(self, route, *, method='GET', payload=None):
        body = self.request(self.base + '/' + route, method=method, authenticated=True,
                            data=None if payload is None else json.dumps(payload).encode(),
                            content_type=None if payload is None else 'application/json', limit=1024 * 1024)
        try:
            return json.loads(body) if body else {}
        except (ValueError, UnicodeError):
            raise SyncError('Invalid Client API response') from None

    def ensure_directory(self, directory):
        root = '/'
        for part in safe_path(directory).split('/'):
            current = root.rstrip('/') + '/' + part
            if current not in self.directories:
                listing = self.api('files/list?' + urllib.parse.urlencode({'directory': root}))
                if not any(row['attributes']['name'] == part and not row['attributes']['is_file'] for row in listing['data']):
                    self.api('files/create-folder', method='POST', payload={'root': root, 'name': part})
                self.directories.add(current)
            root = current

    def upload(self, path, data):
        safe_path(path)
        if not re.fullmatch(r'models/\.sync_staging/[0-9a-f]{64}/(?:[0-9a-f]{64}/[A-Za-z0-9_.=-]+|manifest.tsv)', path):
            raise SyncError('Uploads are allowed only in batch staging')
        parent, name = path.rsplit('/', 1)
        self.ensure_directory(parent)
        signed = self.api('files/upload')['attributes']['url']
        parsed = urllib.parse.urlsplit(signed)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query = [(key, value) for key, value in query if key != 'directory'] + [('directory', '/' + parent)]
        signed = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
        boundary = 'model-sync-' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
        self.request(signed, method='POST', data=body, content_type=f'multipart/form-data; boundary={boundary}')
        listing = self.api('files/list?' + urllib.parse.urlencode({'directory': '/' + parent}))
        if not any(row['attributes']['name'] == name and row['attributes']['is_file'] and row['attributes']['size'] == len(data) for row in listing['data']):
            raise SyncError('Uploaded file size could not be confirmed')

    def download(self, path):
        safe_path(path)
        if not re.fullmatch(r'models/custom_(?:skins/private_skins|graffiti)/[1-9][0-9]*/[A-Za-z0-9_.=-]+\.(?:dff|txd)', path):
            raise SyncError('Only confirmed final model paths may be downloaded')
        signed = self.api('files/download?' + urllib.parse.urlencode({'file': '/' + path}))['attributes']['url']
        return self.request(signed)

    def command(self, text):
        if not re.fullmatch(r'modelsync (?:import [0-9a-f]{64}|acknowledge [0-9a-f]{64} [0-9a-f]{40,64})', text):
            raise SyncError('Invalid model sync console command')
        self.api('command', method='POST', payload={'command': text})

    def poll_receipt(self, batch_id, timeout_s=300):
        if not HEX.fullmatch(batch_id):
            raise SyncError('Invalid batch ID')
        deadline = self.clock() + timeout_s
        while self.clock() < deadline:
            try:
                receipt = self.api('files/contents?' + urllib.parse.urlencode({'file': f'/scriptfiles/model_sync/{batch_id}.json'}))
                if receipt.get('version') != 1 or receipt.get('batch_id') != batch_id:
                    raise SyncError('Receipt does not match requested batch/version')
                if receipt.get('complete') is True:
                    return receipt
            except FileNotFoundError:
                pass
            self.sleep(5)
        raise SyncError('Timed out waiting for server import receipt; pending inputs retained')

    def existing_receipt(self, batch_id):
        if not HEX.fullmatch(batch_id):
            raise SyncError('Invalid batch ID')
        try:
            return self.api('files/contents?' + urllib.parse.urlencode({'file': f'/scriptfiles/model_sync/{batch_id}.json'}))
        except FileNotFoundError:
            return None

    def reset_failed_receipt(self, batch_id):
        if not HEX.fullmatch(batch_id):
            raise SyncError('Invalid batch ID')
        self.api('files/delete', method='POST', payload={'root': '/scriptfiles/model_sync', 'files': [batch_id + '.json']})
