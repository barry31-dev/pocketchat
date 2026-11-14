"""Actual loopback HTTP auth, framing and office flow without paid providers."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from pocketchat.config import load_config, profile_config
from pocketchat.office import OfficeWorkspace
from pocketchat.office_web import create_server
from tests.test_session import FakeContextAdapter, text_response


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = profile_config(load_config(Path('examples/config.json')))
        config['requires_api_key'] = False
        config['provider_adapter'] = FakeContextAdapter([text_response('Hi from the provider')])
        self.app = OfficeWorkspace(Path(self.tmp.name).resolve() / 'office', config)
        self.server = create_server(self.app, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:%s' % self.server.server_port
        self.cookie = ''
        self.csrf = ''

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.tmp.cleanup()

    def request(self, method, path, data=None, extra=None):
        headers = {'Origin': self.origin, 'Cookie': self.cookie, 'X-CSRF-Token': self.csrf}
        body = None if data is None else json.dumps(data)
        if body is not None:
            headers['Content-Type'] = 'application/json'
        headers.update(extra or {})
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        result = (response.status, dict(response.getheaders()), raw)
        conn.close()
        return result

    def login(self):
        status, headers, raw = self.request('POST', '/api/login', {'token':self.server.bootstrap_token})
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';')[0]
        self.csrf = json.loads(raw)['csrf']

    def test_auth_origin_host_and_csrf_fail_closed(self):
        self.assertEqual(self.request('GET', '/api/conversations')[0], 401)
        self.assertEqual(self.request('POST', '/api/login', {'token':self.server.bootstrap_token}, {'Origin':'https://evil.invalid'})[0], 403)
        self.login()
        self.assertEqual(self.request('POST', '/api/conversations', {'title':'Hello'}, {'X-CSRF-Token':'wrong'})[0], 403)
        self.assertEqual(self.request('GET', '/api/conversations', extra={'Host':'evil.invalid'})[0], 403)
        self.assertEqual(self.request('GET', '/api/conversations', extra={'Origin':'https://evil.invalid'})[0], 403)

    def test_real_http_conversation_turn_and_reopen(self):
        self.login()
        status, _, raw = self.request('POST', '/api/conversations', {'title':'First'})
        self.assertEqual(status, 201)
        cid = json.loads(raw)['id']
        status, _, raw = self.request('POST', '/api/conversations/'+cid+'/runs', dict(prompt='Hello', intent='chat', source_ids=[], revision_id=None, request_id='http-request-001', consent=True))
        self.assertEqual(status, 202)
        rid = json.loads(raw)['id']
        self.app.wait(rid, 2)
        status, _, raw = self.request('GET', '/api/conversations/'+cid)
        self.assertEqual(json.loads(raw)['messages'][-1]['content'], 'Hi from the provider')
        self.assertEqual(status, 200)

    def test_unknown_fields_paths_and_framing(self):
        self.login()
        self.assertEqual(self.request('POST', '/api/conversations', {'title':'Hello','path':'/etc/passwd'})[0], 400)
        self.assertEqual(self.request('GET', '/../../etc/passwd')[0], 404)
        self.assertEqual(self.request('POST', '/api/conversations', {'title':'Hello'}, {'Transfer-Encoding':'chunked'})[0], 400)

    def test_packaged_page_has_no_remote_resources_and_csp(self):
        status, headers, raw = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn("default-src 'self'", headers['Content-Security-Policy'])
        self.assertNotIn(b'<script src="http', raw)
        self.assertEqual(self.request('GET', '/api/conversations?token=bad')[0], 400)


class CLIAndPackageTests(unittest.TestCase):
    def test_web_help_is_additive_and_contains_no_credential(self):
        import subprocess
        import sys
        p = subprocess.run([sys.executable, '-m', 'pocketchat', 'web', '--help'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('--data-dir', p.stdout)
        self.assertIn('--port', p.stdout)

    def test_web_assets_are_declared_as_package_data(self):
        text = Path('pyproject.toml').read_text()
        self.assertIn('office_static/*.html', text)
        self.assertIn('python-docx', text)
