"""Process-boundary contracts for the additive office CLI."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pocketchat.office_store import OfficeStore
from tests._helpers import subprocess_environment

ROOT = Path(__file__).resolve().parents[1]
SECURE_IO = hasattr(os, 'O_NOFOLLOW') and os.open in os.supports_dir_fd


class OfficeCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.workspace = self.base / 'work'
        with self.open_store() as store:
            self.cid = store.create('From browser')['id']
            store.add_source(self.cid, 'notes.md', 'Three decisions')

    def open_store(self):
        from contextlib import contextmanager
        @contextmanager
        def opened():
            store = OfficeStore(self.workspace)
            try:
                yield store
            finally:
                store.close()
        return opened()

    def cli(self, *args, data_dir=None):
        env = subprocess_environment({'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(ROOT)})
        p = subprocess.run([sys.executable, '-m', 'pocketchat', 'office', '--data-dir',
                            str(data_dir or self.workspace), '--json', *args],
                           cwd=self.base, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(p.stderr, '', p.stderr)
        try:
            payload = json.loads(p.stdout)
        except ValueError:
            self.fail('Expected exactly one JSON result: %r' % p.stdout)
        self.assertEqual(payload['schema_version'], 'pocketchat-office-cli.v1')
        return p.returncode, payload

    @unittest.skipUnless(SECURE_IO, 'requires no-follow descriptor I/O')
    def test_backup_restore_roundtrip_without_provider_config(self):
        out = self.base / 'backup.json'
        code, data = self.cli('backup', '--out', str(out))
        self.assertEqual(code, 0, data)
        self.assertTrue(data['ok'])
        restored = self.base / 'restored'
        code, data = self.cli('restore', '--from', str(out), data_dir=restored)
        self.assertEqual(code, 0, data)
        self.assertEqual(data['result']['conversations'], 1)
        store = OfficeStore(restored)
        self.addCleanup(store.close)
        self.assertEqual(store.get(self.cid)['title'], 'From browser')

    def test_unknown_restore_is_content_free_error_and_creates_no_workspace(self):
        bad = self.base / 'bad.json'
        bad.write_text('{"secret":"PRIVATE-CONTENT"}')
        dest = self.base / 'absent'
        code, data = self.cli('restore', '--from', str(bad), data_dir=dest)
        self.assertEqual(code, 2)
        self.assertFalse(data['ok'])
        self.assertNotIn('PRIVATE-CONTENT', json.dumps(data))
        self.assertNotIn(str(self.base), json.dumps(data))
        self.assertFalse(dest.exists())

    def test_offline_backup_does_not_steal_an_active_writer(self):
        with self.open_store():
            code, data = self.cli('backup', '--out', str(self.base / 'out.json'))
        self.assertEqual(code, 2)
        self.assertFalse(data['ok'])
        self.assertFalse((self.base / 'out.json').exists())

    @unittest.skipUnless(SECURE_IO, 'requires no-follow descriptor I/O')
    def test_read_import_and_export_commands_keep_stable_ids(self):
        code, result = self.cli('conversations', 'list')
        self.assertEqual(code, 0, result)
        self.assertEqual(result['result']['items'][0]['id'], self.cid)
        source = self.base / 'second.md'
        source.write_text('Four decisions')
        code, result = self.cli('materials', 'add', '--conversation', self.cid, '--file', str(source))
        self.assertEqual(code, 0, result)
        code, result = self.cli('conversations', 'show', '--conversation', self.cid)
        self.assertEqual(len(result['result']['sources']), 2)
        with self.open_store() as store:
            r = store.begin(self.cid, 'request-export', dict(prompt='Report', intent='report', source_ids=[], revision_id=None))
            store.finish(r['id'], 'completed', '# Keep this version', None)
            version = store.get(self.cid)['artifacts'][0]['id']
        out = self.base / 'report.md'
        code, result = self.cli('export', '--conversation', self.cid, '--version', version, '--format', 'md', '--out', str(out))
        self.assertEqual(code, 0, result)
        self.assertEqual(out.read_text(), '# Keep this version')
        code, result = self.cli('export', '--conversation', self.cid, '--version', version, '--format', 'md', '--out', str(out))
        self.assertEqual(code, 2)
        self.assertEqual(out.read_text(), '# Keep this version')

    def test_offline_process_confirmation_and_recovery_disable_legacy_tools(self):
        from http.server import BaseHTTPRequestHandler
        from tests._helpers import StubHTTPServer
        calls = []
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                raw = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'Offline reply'},
                                              'finish_reason': 'stop'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = StubHTTPServer(Provider)
        self.addCleanup(server.close)
        config = json.loads((ROOT / 'examples/config.json').read_text())
        profile = config['profiles']['local']
        profile.update(base_url='http://127.0.0.1:%d/v1' % server.port, requires_api_key=False,
                       tool_read_enabled=True, tool_read_allow_paths=['notes.md'],
                       tool_write_enabled=True, tool_write_allow_paths=['output.md'],
                       tool_memory_search_enabled=True)
        cfg = self.base / 'config.json'
        cfg.write_text(json.dumps(config))
        args = ('--config', str(cfg), 'chat', '--conversation', self.cid, '--prompt', 'Hello', '--request-id', 'offline-confirmed')
        code, preview = self.cli(*args)
        self.assertEqual(code, 0, preview)
        self.assertEqual(calls, [])
        proof = preview['result']['confirmation']
        code, sent = self.cli(*args, '--confirm', proof)
        self.assertEqual(code, 0, sent)
        self.assertEqual(sent['result']['reply'], 'Offline reply')
        code, recovered = self.cli(*args, '--confirm', proof)
        self.assertEqual(code, 0, recovered)
        self.assertEqual(recovered['result']['id'], sent['result']['id'])
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0].get('tools'))
        self.assertFalse((self.workspace / 'output.md').exists())

    def test_non_ascii_confirmation_returns_one_json_refusal(self):
        cfg = self.base / 'config.json'
        cfg.write_bytes((ROOT / 'examples/config.json').read_bytes())
        code, data = self.cli('--config', str(cfg), 'chat', '--conversation', self.cid,
                              '--prompt', 'Hello', '--request-id', 'unicode-proof', '--confirm', 'é')
        self.assertEqual(code, 2)
        self.assertFalse(data['ok'])

    def test_office_preview_refuses_credential_bearing_provider_urls(self):
        cfg = self.base / 'config.json'
        base = json.loads((ROOT / 'examples/config.json').read_text())
        for endpoint in ('https://user:synthetic-password@example.invalid/v1',
                         'https://example.invalid/v1?api_key=synthetic-token',
                         'https://example.invalid/v1#synthetic-token'):
            base['profiles']['local']['base_url'] = endpoint
            cfg.write_text(json.dumps(base))
            with self.subTest(endpoint=endpoint):
                code, data = self.cli('--config', str(cfg), 'chat', '--conversation', self.cid,
                                      '--prompt', 'Hello', '--request-id', 'safe-preview')
                self.assertEqual(code, 2, data)
                self.assertNotIn('synthetic-', json.dumps(data))

    @unittest.skipUnless(SECURE_IO, 'requires no-follow descriptor I/O')
    def test_daily_search_archive_restore_without_a_model_call(self):
        with self.open_store() as store:
            p = dict(prompt='Budget question', intent='chat', source_ids=[], revision_id=None)
            run = store.begin(self.cid, 'cli-search-request', p)
            store.finish(run['id'], 'completed', 'Budget answer', None)
        code, data = self.cli('search', '--query', 'budget')
        self.assertEqual(code, 0, data)
        self.assertEqual(len(data['result']['items']), 2)
        code, data = self.cli('conversations', 'archive', '--conversation', self.cid)
        self.assertEqual(code, 0, data)
        self.assertTrue(data['result']['archived'])
        self.assertEqual(self.cli('conversations', 'list')[1]['result']['items'], [])
        self.assertEqual(len(self.cli('search', '--query', 'budget', '--archived')[1]['result']['items']), 2)
        self.assertEqual(self.cli('conversations', 'list', '--archived')[1]['result']['items'][0]['id'], self.cid)
        code, data = self.cli('conversations', 'restore', '--conversation', self.cid)
        self.assertEqual(code, 0, data)
        self.assertFalse(data['result']['archived'])
        self.assertEqual(self.cli('upgrade')[1]['result'], {'database_version': 3, 'upgraded': False})
