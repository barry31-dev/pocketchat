"""Same-instance local clients must not weaken browser authentication or ownership."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from pocketchat.config import load_config, profile_config
from pocketchat.office import OfficeWorkspace
from pocketchat.office_store import OfficeError
from pocketchat.office_web import create_server
from tests.test_session import FakeContextAdapter, text_response

SECURE_IO = hasattr(os, 'O_NOFOLLOW') and os.open in os.supports_dir_fd


@unittest.skipUnless(SECURE_IO, 'requires no-follow descriptor I/O')
class SharedOfficeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'office'
        self.config = profile_config(load_config(Path('examples/config.json')))
        self.config['requires_api_key'] = False
        self.adapter = FakeContextAdapter([text_response('# First [S1]'), text_response('# Revised [S1]')])
        self.config['provider_adapter'] = self.adapter
        self.app = OfficeWorkspace(self.root, self.config)
        self.server = create_server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.thread.join(2)

    def connect(self, root=None):
        from pocketchat.office_service import OfficeConnection
        client = OfficeConnection(root or self.root)
        self.addCleanup(client.close)
        return client

    def test_shared_conversation_report_old_version_and_backup(self):
        client = self.connect()
        self.assertTrue(client.remote)
        c = client.request('/api/conversations', {'title': 'CLI'})
        sid = client.request('/api/conversations/%s/sources' % c['id'], {'name': 'notes.md', 'text': '12'})['id']
        body = dict(prompt='Draft', intent='report', source_ids=[sid], revision_id=None,
                    request_id='request-shared', consent=True)
        r = client.request('/api/conversations/%s/runs' % c['id'], body)
        self.app.wait(r['id'], 3)
        old = self.app.store.get(c['id'])['artifacts'][0]['id']
        r2 = self.app.submit(c['id'], dict(body, prompt='Revise', revision_id=old, request_id='request-revise'))
        self.app.wait(r2['id'], 3)
        latest = client.request('/api/conversations/' + c['id'])['artifacts'][0]['id']
        self.assertNotEqual(old, latest)
        self.assertIn(b'First', client.request('/api/conversations/%s/artifacts/%s/md' % (c['id'], old)))
        self.assertIn(b'Revised', client.request('/api/conversations/%s/artifacts/%s/md' % (c['id'], latest)))
        backup = client.request('/api/backup', {})
        self.assertIn(c['id'].encode(), backup)
        self.assertEqual(len(self.adapter.calls), 2)

    def test_receipt_recovery_and_conflicting_request_content(self):
        c = self.app.store.create('Same request')
        client = self.connect()
        body = dict(prompt='Draft', intent='chat', source_ids=[], revision_id=None,
                    request_id='request-shared', consent=True)
        first = client.request('/api/conversations/%s/runs' % c['id'], body)
        self.app.wait(first['id'], 3)
        second = client.request('/api/conversations/%s/runs' % c['id'], body)
        self.assertEqual(first['id'], second['id'])
        with self.assertRaises(OfficeError):
            client.request('/api/conversations/%s/runs' % c['id'], dict(body, prompt='changed'))
        self.assertEqual(len(self.adapter.calls), 1)

    def test_discovery_wrong_workspace_or_external_host_refused(self):
        path = self.root / 'office-service.json'
        original = json.loads(path.read_text())
        for update in ({'workspace_id': 'wrong'}, {'origin': 'http://example.com'}, {'token': 'wrong'}):
            path.write_text(json.dumps(dict(original, **update)))
            with self.assertRaises(OfficeError):
                self.connect()
        path.write_text(json.dumps(original))
        self.assertEqual(self.app.store.list(), [])

    def test_native_token_with_browser_origin_or_wrong_binding_is_refused(self):
        discovery = json.loads((self.root / 'office-service.json').read_text())
        for extra in ({'Origin': self.server.origin}, {'X-Office-Workspace': 'wrong'}):
            conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
            headers = {'Authorization': 'Bearer ' + discovery['token'],
                       'X-Office-Workspace': discovery['workspace_id'], **extra}
            conn.request('GET', '/api/conversations', headers=headers)
            self.assertEqual(conn.getresponse().status, 403)
            conn.close()

    def test_offline_reopen_ignores_no_longer_running_service_but_keeps_same_data(self):
        c = self.app.store.create('Persistent')
        # An independent workspace can be used without a provider configuration.
        from pocketchat.office_service import OfficeConnection
        independent = Path(self.tmp.name).resolve() / 'independent'
        with OfficeConnection(independent) as local:
            self.assertFalse(local.remote)
            made = local.request('/api/conversations', {'title': 'Offline'})
        with OfficeConnection(independent) as reopened:
            self.assertEqual(reopened.request('/api/conversations/' + made['id'])['title'], 'Offline')
        self.assertEqual(self.app.store.get(c['id'])['title'], 'Persistent')

    def test_confirmation_binds_model_history_and_material_not_only_yes(self):
        client = self.connect()
        cid = self.app.store.create('Confirmed')['id']
        body = dict(prompt='Draft', intent='report', source_ids=[], revision_id=None,
                    request_id='request-confirmed', consent=True)
        prefix = '/api/conversations/' + cid
        preview = client.request(prefix + '/preview', body)
        self.assertEqual(len(self.adapter.calls), 0)
        self.app.config['model'] = 'changed-model'
        with self.assertRaises(OfficeError):
            client.request(prefix + '/confirmed', dict(message=body, confirmation=preview['confirmation']))
        self.assertEqual(len(self.adapter.calls), 0)
        fresh = client.request(prefix + '/preview', body)
        run = client.request(prefix + '/confirmed', dict(message=body, confirmation=fresh['confirmation']))
        self.app.wait(run['id'], 3)
        recovered = client.request(prefix + '/confirmed', dict(message=body, confirmation=fresh['confirmation']))
        self.assertEqual(recovered['id'], run['id'])
        self.assertEqual(len(self.adapter.calls), 1)
        with self.assertRaises(OfficeError):
            client.request(prefix + '/confirmed', dict(message=dict(body, prompt='different'), confirmation=fresh['confirmation']))

    def test_native_cli_state_can_save_multiple_bounded_drafts_over_96k(self):
        client=self.connect();state=client.request('/api/cli-state');drafts={}
        for i in range(9):
            cid=self.app.store.create('Draft '+str(i))['id']
            drafts[cid]=dict(prompt='x'*12000,intent='chat',source_ids=[],revision_id=None)
        saved=client.request('/api/cli-state',dict(state,drafts=drafts))
        self.assertEqual(len(saved['drafts']),9)
        self.assertEqual(client.request('/api/cli-state'),saved)
        self.assertEqual(self.adapter.calls,[])

    def test_read_only_receipt_lookup_is_exact_and_models_are_shared(self):
        client = self.connect()
        cid = self.app.store.create('Receipt lookup')['id']
        body = dict(prompt='Hi', intent='chat', source_ids=[], revision_id=None,
                    request_id='lookup-exact-request', consent=True)
        run = self.app.submit(cid, body)
        self.app.wait(run['id'], 3)
        result = client.request('/api/conversations/'+cid+'/requests/'+body['request_id'])
        self.assertEqual(result['id'], run['id'])
        self.assertEqual(result['request_id'], body['request_id'])
        with self.assertRaises(OfficeError):
            client.request('/api/conversations/'+cid+'/requests/missing-request')
        models = client.request('/api/models')
        self.assertEqual(len(models['items']), 1)
        selected = client.request('/api/models', {'profile': models['items'][0]['profile']})
        self.assertEqual(selected['selected'], models['items'][0]['profile'])
        self.assertEqual(len(self.adapter.calls), 1)

    def test_wrong_listener_cannot_echo_workspace_identity_to_steal_client_data(self):
        from http.server import BaseHTTPRequestHandler
        from tests._helpers import StubHTTPServer
        path = self.root / 'office-service.json'
        discovery = json.loads(path.read_text())
        observed = []
        class EchoServer(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                observed.append(dict(self.headers))
                raw = b'{"model":"fake"}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('X-Office-Workspace', self.headers.get('X-Office-Workspace', ''))
                self.end_headers()
                self.wfile.write(raw)
        rogue = StubHTTPServer(EchoServer)
        self.addCleanup(rogue.close)
        path.write_text(json.dumps(dict(discovery, origin='http://127.0.0.1:%d' % rogue.port)))
        with self.assertRaises(OfficeError):
            self.connect()
        self.assertTrue(observed)
        self.assertTrue(all('Authorization' not in headers for headers in observed))

    def test_non_string_discovery_fields_are_typed_refusals(self):
        path = self.root / 'office-service.json'
        original = json.loads(path.read_text())
        for key in original:
            path.write_text(json.dumps(dict(original, **{key: 7})))
            with self.subTest(key=key), self.assertRaises(OfficeError):
                self.connect()
        path.write_text(json.dumps(original))

    def test_native_project_preferences_are_explicit_revisioned_and_offline(self):
        client = self.connect()
        project = client.request('/api/project')
        self.assertFalse(project['memory_enabled'])
        saved = client.request('/api/project/instructions', dict(
            project_id=project['project_id'], instruction_revision=1, instructions='Be concise.'))
        self.assertEqual(saved['instruction_revision'], 2)
        with self.assertRaises(OfficeError):
            client.request('/api/project/instructions', dict(
                project_id=project['project_id'], instruction_revision=1, instructions='Stale.'))
        enabled = client.request('/api/project/memory', dict(
            project_id=project['project_id'], memory_revision=1, memory_enabled=True))
        self.assertTrue(enabled['memory_enabled'])
        self.assertEqual(client.request('/api/project'), enabled)
        self.assertEqual(self.adapter.calls, [])

    def test_memory_proposal_acceptance_and_forget_are_shared_without_provider_calls(self):
        client = self.connect()
        view = client.request('/api/memory')
        proposal = client.request('/api/memory/propose', dict(project_id=view['project_id'], store_sha256=view['store_sha256'],
            content='Prefer concise replies', category='user_preference', personal_guidance=True))
        record = proposal['record']
        accepted = client.request('/api/memory/accept', dict(project_id=view['project_id'], store_sha256=proposal['store_sha256'],
            memory_id=record['memory_id'], revision=record['revision'], personal_guidance=True))
        self.assertEqual(accepted['record']['admission_status'], 'accepted')
        forgotten = client.request('/api/memory/forget', dict(project_id=view['project_id'], store_sha256=accepted['store_sha256'],
            memory_id=record['memory_id'], revision=accepted['record']['revision'], acknowledge_history=True))
        self.assertEqual(forgotten['records'], [])
        self.assertEqual(self.adapter.calls, [])
