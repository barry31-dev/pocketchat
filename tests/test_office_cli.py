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


    @unittest.skipUnless(SECURE_IO, 'requires private descriptor I/O')
    def test_interactive_draft_reopens_without_sending_and_isolates_conversations(self):
        def run(text):
            return subprocess.run([sys.executable, '-m', 'pocketchat', 'office', '--data-dir', str(self.workspace), 'interactive'],
                                  input=text, text=True, capture_output=True, timeout=10,
                                  env=subprocess_environment({'PATH':os.environ.get('PATH',''),'PYTHONPATH':str(ROOT)}))
        first = run('/open '+self.cid+'\nPrivate draft A\n/quit\n')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotIn('Traceback', first.stderr)
        second = run('/draft\n/new Other\n/draft\n/open '+self.cid+'\n/draft\n/quit\n')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('Private draft A', second.stdout)
        with self.open_store() as store:
            self.assertEqual(store.get(self.cid)['messages'], [])
            self.assertEqual(len(store.list()), 2)
        self.assertNotIn('consent', (self.workspace/'office-cli-state.json').read_text())

    def interactive(self, text, *args, data_dir=None):
        return subprocess.run([sys.executable, '-m', 'pocketchat', 'office', '--data-dir',
                               str(data_dir or self.workspace), *args, 'interactive'],
                              input=text, text=True, capture_output=True, timeout=10,
                              cwd=self.base, env=subprocess_environment({
                                  'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(ROOT)}))

    def remote_fixture(self):
        import threading
        from pocketchat.config import load_config, profile_config
        from pocketchat.office import OfficeWorkspace
        from pocketchat.office_web import create_server
        from tests.test_session import FakeContextAdapter, text_response
        config = profile_config(load_config(ROOT / 'examples/config.json'))
        config['requires_api_key'] = False
        adapter = FakeContextAdapter([text_response('Fixture receipt reply')])
        config['provider_adapter'] = adapter
        app = OfficeWorkspace(self.workspace, config)
        self.addCleanup(app.close)
        server = create_server(app, port=0)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        return app, adapter

    def live_interactive(self):
        import queue
        import threading
        process = subprocess.Popen([sys.executable, '-u', '-m', 'pocketchat', 'office',
                                    '--data-dir', str(self.workspace), 'interactive'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, cwd=self.base,
                                   env=subprocess_environment({'PATH': os.environ.get('PATH', ''),
                                                               'PYTHONPATH': str(ROOT)}))
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        self.addCleanup(cleanup)
        lines = queue.Queue()
        def read():
            for line in process.stdout:
                lines.put(line)
        threading.Thread(target=read, daemon=True).start()
        def until(prefix):
            output = []
            while True:
                line = lines.get(timeout=8)
                output.append(line)
                if line.startswith(prefix):
                    return ''.join(output), line.strip()
        return process, until

    @unittest.skipUnless(SECURE_IO, 'requires private descriptor I/O')
    def test_models_command_lists_and_selects_configured_profiles_without_calls(self):
        config = json.loads((ROOT / 'examples/config.json').read_text())
        config['profiles']['alternate'] = dict(config['profiles']['local'], model='fixture-alternate')
        cfg = self.base / 'models.json'
        cfg.write_text(json.dumps(config))
        code, listed = self.cli('--config', str(cfg), 'models')
        self.assertEqual(code, 0, listed)
        self.assertEqual({item['profile'] for item in listed['result']['items']}, set(config['profiles']))
        code, selected = self.cli('--config', str(cfg), 'models', '--select', 'alternate')
        self.assertEqual(code, 0, selected)
        self.assertEqual(selected['result']['selected'], 'alternate')
        self.assertTrue(all(item['capabilities']['tools'] is False for item in selected['result']['items']))

    @unittest.skipUnless(SECURE_IO, 'requires private descriptor I/O')
    def test_interactive_eof_saves_new_workspace_draft_and_json_is_refused(self):
        dest = self.base / 'fresh'
        result = self.interactive('Unsent EOF draft\n', data_dir=dest)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads((dest / 'office-cli-state.json').read_text())['state']
        self.assertEqual(state['drafts']['new']['prompt'], 'Unsent EOF draft')
        self.assertIsNone(state['pending'])
        self.assertNotIn('consent', json.dumps(state['drafts']))
        code, response = self.cli('interactive', data_dir=self.base / 'no-json')
        self.assertEqual(code, 2, response)
        self.assertFalse((self.base / 'no-json').exists())

    @unittest.skipUnless(SECURE_IO, 'requires private descriptor I/O')
    def test_interactive_exact_confirmation_saves_pending_before_send_and_recovers_receipt(self):
        app, adapter = self.remote_fixture()
        admitted = []
        original = app.submit_confirmed
        def confirmed(cid, body, proof):
            pending = app.store.cli_state()['pending']
            self.assertEqual(pending, dict(cid=cid, body=body, confirmation=proof, run_id=None))
            self.assertNotIn('consent', json.dumps(app.store.cli_state()['drafts']))
            admitted.append(body['request_id'])
            return original(cid, body, proof)
        app.submit_confirmed = confirmed
        process, until = self.live_interactive()
        until('Office interactive')
        process.stdin.write('/open ' + self.cid + '\nHello fixture\n/send\n')
        process.stdin.flush()
        output, confirmation = until('Confirmation: ')
        proof = confirmation.split(': ', 1)[1]
        self.assertIn('Provider:', output)
        self.assertIn('History:', output)
        self.assertIn('Material/message:', output)
        self.assertEqual(adapter.calls, [])
        self.assertIsNone(app.store.cli_state()['pending'])
        process.stdin.write(proof + '\n')
        process.stdin.flush()
        output, _ = until('Receipt: ')
        pending = app.store.cli_state()['pending']
        if pending is not None:
            app.wait(pending['run_id'], 3)
            process.stdin.write('/recover\n')
            process.stdin.flush()
            recovered, _ = until('Receipt: ')
            output += recovered
        process.stdin.write('/quit\n')
        process.stdin.flush()
        process.wait(timeout=8)
        self.assertEqual(process.returncode, 0, process.stderr.read())
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(len(admitted), 1)
        self.assertIsNone(app.store.cli_state()['pending'])
        self.assertEqual(app.store.get(self.cid)['messages'][-1]['content'], 'Fixture receipt reply')

    @unittest.skipUnless(SECURE_IO, 'requires private descriptor I/O')
    def test_interactive_wrong_confirmation_and_confirmation_eof_never_send(self):
        app, adapter = self.remote_fixture()
        result = self.interactive('/open ' + self.cid + '\nKeep this\n/send\nyes\n/send\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        state = app.store.cli_state()
        self.assertEqual(state['drafts'][self.cid]['prompt'], 'Keep this')
        self.assertIsNone(state['pending'])
        self.assertEqual(adapter.calls, [])
        self.assertIn('not sent', result.stdout.lower())

    @unittest.skipUnless(SECURE_IO, 'requires private descriptor I/O')
    def test_interactive_cas_conflict_stops_without_overwriting_or_sending(self):
        app, adapter = self.remote_fixture()
        process, until = self.live_interactive()
        until('Office interactive')
        state = app.store.cli_state()
        draft = dict(prompt='Concurrent saved draft', intent='chat', source_ids=[], revision_id=None)
        app.store.cli_state(dict(state, drafts={'new': draft}))
        process.stdin.write('PRIVATE-UNSAVED-INPUT\n/send\n')
        process.stdin.flush()
        process.wait(timeout=8)
        self.assertEqual(process.returncode, 2)
        self.assertNotIn('PRIVATE-UNSAVED-INPUT', process.stderr.read())
        output, _ = until('PRIVATE-UNSAVED-INPUT')
        self.assertIn('Uncommitted draft', output)
        self.assertEqual(app.store.cli_state()['drafts']['new']['prompt'], 'Concurrent saved draft')
        self.assertEqual(adapter.calls, [])

    @unittest.skipUnless(SECURE_IO, 'requires private descriptor I/O')
    def test_interactive_pending_recovery_404_is_unknown_not_permission_to_replay(self):
        app, adapter = self.remote_fixture()
        body = dict(prompt='Pending private request', intent='chat', source_ids=[], revision_id=None,
                    request_id='unknown-request', consent=True)
        state = app.store.cli_state()
        pending = dict(cid=self.cid, body=body, confirmation='a' * 64, run_id=None)
        app.store.cli_state(dict(state, current=self.cid, pending=pending))
        result = self.interactive('/recover\n/send\n/quit\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('unknown', result.stdout.lower())
        self.assertEqual(app.store.cli_state()['pending'], pending)
        self.assertEqual(adapter.calls, [])

    @unittest.skipUnless(SECURE_IO, 'requires private descriptor I/O')
    def test_interactive_restart_recovers_matching_request_without_provider_replay(self):
        app, adapter = self.remote_fixture()
        body = dict(prompt='Original receipt', intent='chat', source_ids=[], revision_id=None,
                    request_id='restart-receipt', consent=True)
        proof = app.preview(self.cid, body)['confirmation']
        run = app.submit_confirmed(self.cid, body, proof)
        app.wait(run['id'], 3)
        state = app.store.cli_state()
        draft = {k: v for k, v in body.items() if k not in ('request_id', 'consent')}
        pending = dict(cid=self.cid, body=body, confirmation=proof, run_id=None)
        app.store.cli_state(dict(state, current=self.cid, drafts={self.cid: draft}, pending=pending))
        result = self.interactive('/send\n/recover\n/quit\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Receipt: ' + run['id'] + ' completed', result.stdout)
        self.assertEqual(len(adapter.calls), 1)
        self.assertIsNone(app.store.cli_state()['pending'])
        self.assertEqual(app.store.cli_state()['drafts'][self.cid]['prompt'], '')

    @unittest.skipUnless(SECURE_IO, 'requires private descriptor I/O')
    def test_interactive_unknown_receipt_stays_pending_and_blocks_send(self):
        app, adapter = self.remote_fixture()
        body = dict(prompt='Unknown outcome', intent='chat', source_ids=[], revision_id=None,
                    request_id='unknown-receipt', consent=True)
        wire = {k: v for k, v in body.items() if k not in ('request_id', 'consent')}
        run = app.store.begin(self.cid, body['request_id'], dict(wire, wire=wire))
        with app.guard, app.store.lock, app.store.db:
            app.store.db.execute("UPDATE runs SET status='unknown' WHERE id=?", (run['id'],))
        state = app.store.cli_state()
        pending = dict(cid=self.cid, body=body, confirmation='a' * 64, run_id=None)
        app.store.cli_state(dict(state, current=self.cid, pending=pending))
        result = self.interactive('/recover\n/send\n/quit\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Receipt: ' + run['id'] + ' unknown', result.stdout)
        self.assertEqual(app.store.cli_state()['pending'], dict(pending, run_id=run['id']))
        self.assertEqual(adapter.calls, [])

    @unittest.skipUnless(SECURE_IO, 'requires private descriptor I/O')
    def test_interactive_materials_and_import_copy_plain_text_without_sending(self):
        app, adapter = self.remote_fixture()
        source = self.base / 'selected notes.md'
        source.write_text('Selected local text')
        result = self.interactive('/open ' + self.cid + '\n/materials\n/import "' + str(source) +
                                  '"\n/materials\n/quit\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('notes.md', result.stdout)
        self.assertIn('Imported material:', result.stdout)
        materials = app.store.get(self.cid)['sources']
        self.assertEqual(len(materials), 2)
        self.assertIn(materials[-1]['id'], result.stdout)
        self.assertEqual(app.store.source(self.cid, materials[-1]['id'])['text'], 'Selected local text')
        self.assertEqual(adapter.calls, [])


    @unittest.skipUnless(SECURE_IO, 'requires private descriptor I/O')
    def test_interactive_report_revision_and_exact_markdown_export(self):
        from tests.test_session import text_response
        app,adapter=self.remote_fixture()
        adapter.responses.extend([text_response('# Revised report')])
        process,until=self.live_interactive()
        until('Office interactive')
        process.stdin.write('/open '+self.cid+'\n/report\nWrite a report\n/send\n');process.stdin.flush()
        _,line=until('Confirmation: ')
        process.stdin.write(line.split(': ',1)[1]+'\n');process.stdin.flush()
        _,line=until('Receipt: ');rid=line.split()[1];app.wait(rid,3)
        first=app.store.get(self.cid)['artifacts'][0]['id']
        process.stdin.write('/recover\n/revise '+first+'\nShorten the report\n/send\n');process.stdin.flush()
        _,line=until('Confirmation: ')
        process.stdin.write(line.split(': ',1)[1]+'\n');process.stdin.flush()
        _,line=until('Receipt: ');rid=line.split()[1];app.wait(rid,3)
        newest=app.store.get(self.cid)['artifacts'][0]['id']
        old_out=self.base/'old.md';new_out=self.base/'new.md'
        process.stdin.write('/recover\n/export '+first+' md '+str(old_out)+'\n/export '+newest+' md '+str(new_out)+'\n/quit\n');process.stdin.flush()
        process.wait(timeout=8)
        self.assertEqual(process.returncode,0,process.stderr.read())
        self.assertEqual(old_out.read_text(),'Fixture receipt reply')
        self.assertEqual(new_out.read_text(),'# Revised report')
        self.assertEqual(len(app.store.get(self.cid)['artifacts']),2)
        self.assertEqual(len(adapter.calls),2)

    @unittest.skipUnless(SECURE_IO, 'requires private descriptor I/O')
    def test_interactive_stop_uses_same_worker_and_receipt(self):
        import threading
        app,adapter=self.remote_fixture();started=threading.Event();release=threading.Event()
        original=adapter.complete
        def slow(*args,**kwargs):
            started.set();release.wait(4);return original(*args,**kwargs)
        adapter.complete=slow
        self.addCleanup(release.set)
        process,until=self.live_interactive();until('Office interactive')
        process.stdin.write('/open '+self.cid+'\nStop this request\n/send\n');process.stdin.flush()
        _,line=until('Confirmation: ');process.stdin.write(line.split(': ',1)[1]+'\n');process.stdin.flush()
        _,line=until('Receipt: ');rid=line.split()[1];self.assertTrue(started.wait(2))
        process.stdin.write('/stop\n');process.stdin.flush();until('Receipt: ')
        self.assertEqual(app.status(rid)['status'],'stopping')
        release.set();app.wait(rid,3)
        process.stdin.write('/recover\n/quit\n');process.stdin.flush();process.wait(timeout=8)
        self.assertEqual(process.returncode,0,process.stderr.read())
        self.assertEqual(app.status(rid)['status'],'cancelled')
        self.assertEqual(app.store.get(self.cid)['messages'],[])


    @unittest.skipUnless(SECURE_IO, 'requires private descriptor I/O')
    def test_interactive_preserves_blank_lines_and_clears_unaccepted_confirmation(self):
        result=self.interactive('Paragraph one\n\nParagraph two\n/quit\n')
        self.assertEqual(result.returncode,0,result.stderr)
        with self.open_store() as store:
            self.assertEqual(store.cli_state()['drafts']['new']['prompt'],'Paragraph one\n\nParagraph two')
        app,adapter=self.remote_fixture()
        process,until=self.live_interactive();until('Office interactive')
        process.stdin.write('/open '+self.cid+'\nConfirm me\n/send\n');process.stdin.flush()
        _,line=until('Confirmation: ')
        app.select_model(app.models()['selected'])
        process.stdin.write(line.split(': ',1)[1]+'\n');process.stdin.flush()
        until('Request not accepted')
        process.stdin.write('/quit\n');process.stdin.flush();process.wait(timeout=8)
        self.assertEqual(process.returncode,0,process.stderr.read())
        self.assertIsNone(app.store.cli_state()['pending'])
        self.assertEqual(adapter.calls,[])
