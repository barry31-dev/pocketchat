"""Office uses the existing single Session, never arbitrary model tools."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from pocketchat.config import load_config, profile_config
from pocketchat.office import OfficeWorkspace
from pocketchat.office_store import OfficeError
from tests.test_session import FakeContextAdapter, text_response


class OfficeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / 'office'
        self.config = profile_config(load_config(Path('examples/config.json')))
        self.config['api_key_env'] = 'OFFICE_TEST_UNUSED_KEY'
        self.config['requires_api_key'] = False
        self.adapter = FakeContextAdapter([text_response('Hello'), text_response('# Weekly\nRevenue: 12 [S1]'), text_response('# Weekly\n12 [S1]')])
        self.config['provider_adapter'] = self.adapter
        self.app = OfficeWorkspace(self.root, self.config)
        self.cid = self.app.store.create('Weekly')['id']

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def send(self, prompt, intent='chat', sources=None, previous=None, key='request-001'):
        r = self.app.submit(self.cid, dict(prompt=prompt, intent=intent, source_ids=sources or [], revision_id=previous, request_id=key, consent=True))
        self.app.wait(r['id'], 3)
        return self.app.status(r['id'])

    def test_chat_report_revision_and_fixed_source_context(self):
        self.assertEqual(self.send('Hello')['status'], 'completed')
        s = self.app.store.add_source(self.cid, 'weekly.txt', 'Revenue: 12')
        self.assertEqual(self.send('Make a report', 'report', [s['id']], key='request-002')['status'], 'completed')
        report = self.app.store.get(self.cid)['artifacts'][0]['id']
        self.assertEqual(self.send('Shorten it', 'report', [], report, 'request-003')['status'], 'completed')
        final = self.app.store.get(self.cid)
        self.assertEqual(len(final['artifacts']), 2)
        self.assertEqual(len(self.adapter.calls), 3)
        self.assertIn('Revenue: 12', self.adapter.calls[1][-1]['content'])
        self.assertIn(s['sha256'], self.adapter.calls[2][-1]['content'])
        self.assertIn('Hello', str(self.adapter.calls[2]))
        self.assertFalse(self.app.config['tool_write_enabled'])

    def test_disclosure_and_foreign_source_refuse_before_provider(self):
        with self.assertRaises(OfficeError):
            self.app.submit(self.cid, dict(prompt='Hi', intent='chat', source_ids=[], revision_id=None, request_id='request-001', consent=False))
        other = self.app.store.create('Other')['id']
        s = self.app.store.add_source(other, 'private.txt', 'secret')
        with self.assertRaises(OfficeError):
            self.send('Read', sources=[s['id']])
        self.assertEqual(self.adapter.calls, [])

    def test_transport_retry_does_not_call_provider_twice(self):
        first = self.send('Hi')
        second = self.send('Hi')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(self.adapter.calls), 1)

    def test_cancel_waits_for_worker_and_never_commits_report(self):
        started, release = threading.Event(), threading.Event()
        adapter = self.adapter
        original = adapter.complete
        def slow(*args, **kwargs):
            started.set()
            release.wait(3)
            return original(*args, **kwargs)
        adapter.complete = slow
        r = self.app.submit(self.cid, dict(prompt='Hi', intent='report', source_ids=[], revision_id=None, request_id='request-001', consent=True))
        self.assertTrue(started.wait(2))
        self.app.cancel(r['id'])
        self.assertEqual(self.app.status(r['id'])['status'], 'stopping')
        with self.assertRaises(OfficeError):
            self.send('Another', key='request-002')
        release.set()
        self.app.wait(r['id'], 3)
        self.assertEqual(self.app.status(r['id'])['status'], 'cancelled')
        self.assertEqual(self.app.store.get(self.cid)['artifacts'], [])

    def test_traces_remain_real_session_evidence(self):
        r = self.send('Hi')
        trace = self.root / 'traces' / (r['id'] + '.jsonl')
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertTrue(any(e.get('type')=='run.finished' and e['payload']['status']=='completed' for e in events))

    def test_multiple_concurrent_report_requests_never_write_twice(self):
        # The immutable receipt is also returned after completion, with no re-execution.
        first = self.send('Make report', 'report')
        second = self.send('Make report', 'report')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(self.app.store.get(self.cid)['artifacts']), 1)

    def test_revision_cannot_silently_drop_original_source_manifest(self):
        s = self.app.store.add_source(self.cid, 'notes.txt', '12')
        self.send('Make report', 'report', [s['id']])
        old = self.app.store.get(self.cid)['artifacts'][0]['id']
        self.send('Shorten', 'report', [], old, 'request-002')
        new = self.app.store.get(self.cid)['artifacts'][0]['id']
        self.assertEqual(self.app.store.artifact(self.cid,new)['sources'], self.app.store.artifact(self.cid,old)['sources'])

    def test_truncated_reply_is_not_a_complete_report(self):
        self.adapter.responses = [dict(text_response('Unfinished'), finish_reason='length')]
        r = self.send('report','report')
        self.assertEqual(r['status'],'failed')
        self.assertEqual(self.app.store.get(self.cid)['artifacts'],[])

    def test_source_ids_stay_stable_through_changed_revision_selection(self):
        a=self.app.store.add_source(self.cid,'a.txt','A facts')
        b=self.app.store.add_source(self.cid,'b.txt','B facts')
        self.send('First','report',[a['id']])
        aid=self.app.store.get(self.cid)['artifacts'][0]['id']
        self.send('Revise','report',[b['id']],aid,'request-002')
        envelope=json.loads(self.adapter.calls[-1][-1]['content'].split('\n',1)[1])
        self.assertIn('sources',envelope['previous_report'])
        mapping={s['name']:s['citation'] for s in envelope['sources']}
        self.assertEqual(mapping['a.txt'],'S1')
        self.assertEqual(mapping['b.txt'],'S2')

    def test_report_inherits_previously_disclosed_sources(self):
        s=self.app.store.add_source(self.cid,'notes.txt','12')
        self.send('Discuss',sources=[s['id']])
        self.send('Report from those notes','report',key='request-002')
        aid=self.app.store.get(self.cid)['artifacts'][0]['id']
        self.assertEqual(self.app.store.artifact(self.cid,aid)['sources'][0]['id'],s['id'])


    def test_terminal_receipt_does_not_release_slot_before_worker_exit(self):
        completed, release = threading.Event(), threading.Event()
        original = self.app._execute
        def finishing(*args):
            original(*args)
            completed.set()
            release.wait(3)
        self.app._execute = finishing
        try:
            r = self.app.submit(self.cid, dict(prompt='One', intent='chat', source_ids=[],
                                revision_id=None, request_id='request-worker-one', consent=True))
            self.assertTrue(completed.wait(2))
            self.assertEqual(self.app.status(r['id'])['status'], 'completed')
            with self.assertRaisesRegex(OfficeError, 'worker|exiting'):
                self.app.submit(self.cid, dict(prompt='Two', intent='chat', source_ids=[],
                                revision_id=None, request_id='request-worker-two', consent=True))
        finally:
            release.set()
            self.app.wait(r['id'], 3)


class OfficeTransportTests(unittest.TestCase):
    def test_cancel_before_send_and_heartbeat_deadline(self):
        import time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from pocketchat.office import OfficeTransportGuard
        from pocketchat.provider import DEFAULT_PROVIDER_ADAPTER, ProviderError
        cancel = threading.Event()
        cancel.set()
        guard=OfficeTransportGuard(cancel,1)
        config=profile_config(load_config(Path('examples/config.json')))
        with self.assertRaises(KeyboardInterrupt):
            guard.wrap(DEFAULT_PROVIDER_ADAPTER).complete([dict(role='user',content='Hi')],config,'')
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.end_headers()
                try:
                    for _ in range(40):
                        self.wfile.write(b': heartbeat\n\n');self.wfile.flush();time.sleep(.05)
                except (BrokenPipeError,ConnectionResetError): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            config['base_url']='http://127.0.0.1:%d/v1'%server.server_port
            cancel.clear();guard=OfficeTransportGuard(cancel,1)
            start=time.monotonic()
            with self.assertRaises(ProviderError):
                guard.wrap(DEFAULT_PROVIDER_ADAPTER).complete([dict(role='user',content='Hi')],config,'',on_delta=lambda *x:None)
            self.assertLess(time.monotonic()-start,2)
        finally:
            server.shutdown();server.server_close()


class OfficeReviewRecoveryTests(OfficeTests):
    def test_same_wire_receipt_survives_later_disclosed_sources(self):
        a=self.app.store.add_source(self.cid,'a.txt','A')
        self.send('First','report',[a['id']])
        b=self.app.store.add_source(self.cid,'b.txt','B')
        self.send('Discuss',sources=[b['id']],key='request-002')
        replay=self.send('First','report',[a['id']])
        self.assertEqual(replay['status'],'completed')
        self.assertEqual(len(self.adapter.calls),2)


class OfficeTransportAdversarialTests(unittest.TestCase):
    def test_no_newline_sse_is_deadline_bounded(self):
        self.probe('trickle')

    def test_error_body_is_not_read_unbounded(self):
        self.probe('error')

    def test_short_content_length_never_completes(self):
        self.probe('short')

    def probe(self, mode):
        import time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from pocketchat.office import OfficeTransportGuard
        from pocketchat.provider import DEFAULT_PROVIDER_ADAPTER, ProviderError
        stop=threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(500 if mode=='error' else 200)
                self.send_header('Content-Type','text/event-stream' if mode=='trickle' else 'application/json')
                body=json.dumps({'choices':[{'message':{'content':'partial'},'finish_reason':'stop'}]}).encode()
                if mode=='short':self.send_header('Content-Length',str(len(body)+100))
                self.end_headers()
                if mode=='short':self.wfile.write(body);return
                try:
                    for _ in range(70):
                        if stop.is_set():break
                        self.wfile.write(b'x');self.wfile.flush();time.sleep(.05)
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        config=profile_config(load_config(Path('examples/config.json')))
        config['base_url']='http://127.0.0.1:%d/v1'%server.server_port
        guard=OfficeTransportGuard(threading.Event(),1)
        start=time.monotonic()
        try:
            with self.assertRaises(ProviderError):
                guard.wrap(DEFAULT_PROVIDER_ADAPTER).complete([dict(role='user',content='Hi')],config,'',on_delta=lambda *a:None)
            self.assertLess(time.monotonic()-start,2)
        finally:
            stop.set();server.shutdown();server.server_close()


class OfficeProfileTests(unittest.TestCase):
    setUp = OfficeTests.setUp
    tearDown = OfficeTests.tearDown

    def test_configured_profile_switch_expires_preview_but_recovers_old_receipt(self):
        other = dict(self.config, model='second-model', tool_write_enabled=True,
                     fallback_profile='unsafe', retry_max_attempts=9)
        self.app.close()
        self.app = OfficeWorkspace(self.root, self.config, profiles={'first': self.config, 'second': other})
        body = dict(prompt='Hello', intent='chat', source_ids=[], revision_id=None,
                    request_id='switch-request', consent=True)
        preview = self.app.preview(self.cid, body)
        before = self.app.models()
        self.assertEqual(len(before['items']), 2)
        self.assertNotIn('api_key_env', str(before))
        self.assertEqual(self.adapter.calls, [])
        self.app.select_model('second')
        self.assertFalse(self.app.config['tool_write_enabled'])
        self.assertIsNone(self.app.config['fallback_profile'])
        with self.assertRaises(OfficeError):
            self.app.submit_confirmed(self.cid, body, preview['confirmation'])
        fresh = self.app.preview(self.cid, body)
        run = self.app.submit_confirmed(self.cid, body, fresh['confirmation'])
        self.app.wait(run['id'], 3)
        self.app.select_model('first')
        self.assertEqual(self.app.submit_confirmed(self.cid, body, fresh['confirmation'])['id'], run['id'])
        self.assertEqual(self.app.status(run['id'])['request']['model_snapshot']['model'], 'second-model')
        self.assertEqual(len(self.adapter.calls), 1)

    def test_model_selection_rejects_unknown_secret_url_and_running_worker(self):
        self.app.close()
        self.app = OfficeWorkspace(self.root, self.config, profiles={'first': self.config})
        for profile in ('missing', ['first'], None):
            with self.assertRaises(OfficeError): self.app.select_model(profile)
        started, release = threading.Event(), threading.Event()
        original = self.adapter.complete
        def slow(*args, **kwargs):
            started.set();release.wait(3);return original(*args, **kwargs)
        self.adapter.complete = slow
        run = self.app.submit(self.cid, dict(prompt='Hi', intent='chat', source_ids=[], revision_id=None,
                                           request_id='switch-running', consent=True))
        self.assertTrue(started.wait(2))
        try:
            with self.assertRaises(OfficeError): self.app.select_model('first')
        finally: release.set();self.app.wait(run['id'], 3)
        self.app.close()
        with self.assertRaises(OfficeError):
            OfficeWorkspace(self.root, self.config, profiles={'secret': dict(self.config, base_url='https://host.invalid/v1?key=private')})


    def test_profile_choice_identity_survives_equal_model_and_endpoint(self):
        self.app.close()
        second = dict(self.config, profile='second', system_prompt='Second instruction')
        self.app = OfficeWorkspace(self.root, second, profiles={'first':self.config,'second':second})
        self.assertEqual(self.app.models()['selected'], 'second')


    def test_switching_equal_config_profiles_always_expires_preview(self):
        self.app.close()
        self.app=OfficeWorkspace(self.root,self.config,profiles={'one':self.config,'two':self.config})
        body=dict(prompt='Hi',intent='chat',source_ids=[],revision_id=None,request_id='alias-preview',consent=True)
        preview=self.app.preview(self.cid,body)
        self.app.select_model('two')
        with self.assertRaises(OfficeError):self.app.submit_confirmed(self.cid,body,preview['confirmation'])
        self.assertEqual(self.adapter.calls,[])


    def test_switch_away_and_back_does_not_revive_old_confirmation(self):
        self.app.close();self.app=OfficeWorkspace(self.root,self.config,profiles={'one':self.config,'two':self.config})
        body=dict(prompt='Hi',intent='chat',source_ids=[],revision_id=None,request_id='switch-cycle',consent=True)
        preview=self.app.preview(self.cid,body)
        self.app.select_model('two');self.app.select_model('one')
        with self.assertRaises(OfficeError):self.app.submit_confirmed(self.cid,body,preview['confirmation'])
        self.assertEqual(self.adapter.calls,[])


class OfficeBranchTests(unittest.TestCase):
    setUp = OfficeTests.setUp
    tearDown = OfficeTests.tearDown
    send = OfficeTests.send
    def seed(self):
        first = self.send('Original question', key='branch-first')
        later = self.send('Later secret', key='branch-later')
        self.assertEqual(later['status'], 'completed')
        return first, later

    def branch_body(self, run, prompt='Edited question', key='branch-edit'):
        return dict(prompt=prompt, intent='chat', source_ids=[], revision_id=None,
                    request_id=key, consent=True, branch_from=run['id'])

    def test_edit_uses_original_prefix_and_keeps_all_saved_messages(self):
        first, later = self.seed()
        original = self.app.store.get(self.cid)['messages']
        body = self.branch_body(first)
        preview = self.app.preview(self.cid, body)
        self.assertEqual(preview['history'], [])
        edited = self.app.submit_confirmed(self.cid, body, preview['confirmation'])
        self.app.wait(edited['id'], 3)
        self.assertEqual(self.app.status(edited['id'])['status'], 'completed')
        c = self.app.store.get(self.cid)
        self.assertEqual(c['messages'][:4], original)
        self.assertEqual(c['active_run_ids'], [edited['id']])
        self.assertNotIn('Later secret', str(self.adapter.calls[-1]))
        self.assertNotIn('Original question', str(self.adapter.calls[-1]))
        self.assertEqual(self.app.submit_confirmed(self.cid, body, preview['confirmation'])['id'], edited['id'])
        self.assertEqual(len(self.adapter.calls), 3)
        with self.assertRaises(OfficeError):
            self.app.submit(self.cid, dict(body, branch_from=later['id']))

    def test_regenerate_saved_later_turn_uses_its_original_parent(self):
        first, later = self.seed()
        body = self.branch_body(later, 'Later secret')
        preview = self.app.preview(self.cid, body)
        self.assertEqual([m['content'] for m in preview['history']], ['Original question', 'Hello'])
        r = self.app.submit(self.cid, body)
        self.app.wait(r['id'], 3)
        self.assertEqual(self.app.store.get(self.cid)['active_run_ids'], [first['id'], r['id']])
        self.assertEqual(len(self.app.store.history(self.cid)), 4)

    def test_failed_branch_does_not_change_active_context(self):
        first, later = self.seed()
        before = self.app.store.history(self.cid)
        # Force failure without a provider call or a successful terminal reply.
        from unittest.mock import patch
        with patch('pocketchat.office.Session.run', side_effect=RuntimeError('fixture')):
            r = self.app.submit(self.cid, self.branch_body(first))
            self.app.wait(r['id'], 3)
        self.assertEqual(self.app.status(r['id'])['status'], 'failed')
        self.assertEqual(self.app.store.history(self.cid), before)
        self.assertEqual(self.app.store.get(self.cid)['active_run_ids'], [first['id'], later['id']])

    def test_foreign_report_and_noncompleted_targets_refused(self):
        first, _ = self.seed()
        other = self.app.store.create('Other')['id']
        with self.assertRaises(OfficeError):
            self.app.preview(other, self.branch_body(first))
        with self.assertRaises(OfficeError):
            self.app.preview(self.cid, dict(self.branch_body(first), intent='report'))
        r = self.app.store.begin(self.cid, 'failed-target', dict(prompt='No', intent='chat', source_ids=[], revision_id=None))
        self.app.store.finish(r['id'], 'failed', '', 'fixture')
        with self.assertRaises(OfficeError):
            self.app.preview(self.cid, self.branch_body(r))

    def test_branch_backup_roundtrip_and_invalid_ancestry_refused(self):
        from pocketchat.office_backup import snapshot, validate
        from pocketchat.office_store import encoded
        import hashlib
        first, _ = self.seed()
        r = self.app.submit(self.cid, self.branch_body(first))
        self.app.wait(r['id'], 3)
        document = json.loads(snapshot(self.app.store))
        self.assertEqual(validate(encoded(document).encode())['database_version'], 5)
        row = document['data']['tables']['runs'][-1]
        payload = json.loads(row['payload'])
        payload['history_run_ids'] = [r['id']]
        row['payload'] = encoded(payload)
        document['sha256'] = hashlib.sha256(encoded(document['data']).encode()).hexdigest()
        with self.assertRaises(OfficeError):
            validate(encoded(document).encode())

    @unittest.skipUnless(hasattr(os, 'O_NOFOLLOW') and os.open in os.supports_dir_fd, 'requires private descriptor I/O')
    def test_legacy_workspace_requires_explicit_backup_upgrade_for_branches(self):
        from pocketchat.office_backup import upgrade, validate
        first, _ = self.seed()
        # Fixture represents a v3 database; remove newly introduced payload keys.
        for row in self.app.store.db.execute('SELECT id,payload FROM runs').fetchall():
            p = json.loads(row['payload']); p.pop('history_run_ids', None); p.pop('prepared_context', None)
            self.app.store.db.execute('UPDATE runs SET payload=? WHERE id=?', (json.dumps(p), row['id']))
        self.app.store.db.execute('DROP TABLE office_project')
        self.app.store.db.execute('DROP TABLE office_summaries')
        self.app.store.db.execute('PRAGMA user_version=3'); self.app.store.db.commit()
        with self.assertRaisesRegex(OfficeError, 'upgrade|Upgrade'):
            self.app.preview(self.cid, self.branch_body(first))
        result = upgrade(self.app.store)
        self.assertEqual(result['database_version'], 5)
        self.assertEqual(validate((self.root / result['backup']).read_bytes())['database_version'], 3)
        self.assertEqual(self.app.preview(self.cid, self.branch_body(first))['history'], [])

    def test_abandoned_source_context_stays_out_of_new_report_and_saved_branch_prefix(self):
        self.adapter.responses.extend([text_response('New report')])
        first = self.send('Original', key='source-first')
        source = self.app.store.add_source(self.cid, 'old.txt', 'Abandoned private material')
        later = self.send('Later', sources=[source['id']], key='source-later')
        changed = self.app.submit(self.cid, self.branch_body(first))
        self.app.wait(changed['id'], 3)
        # The older branch remains addressable with its own original prefix.
        old_preview = self.app.preview(self.cid, self.branch_body(later, key='source-old-preview'))
        self.assertEqual(old_preview['history'][0]['content'], 'Original')
        self.assertNotIn('Edited question', str(old_preview['history']))
        report = self.send('Report on this branch', intent='report', key='source-report')
        self.assertEqual(report['status'], 'completed')
        self.assertNotIn('Abandoned private material', str(self.adapter.calls[-1]))
        self.assertNotIn('Original', str(self.adapter.calls[-1]))
        self.assertEqual(report['request']['source_ids'], [])
        self.assertEqual(report['request']['history_run_ids'], [changed['id']])
        self.assertEqual(len(self.app.store.get(self.cid)['messages']), 8)


class OfficeFoundationSendTests(OfficeTests):
    def test_project_memory_plan_matches_sent_snapshot_and_stale_confirmation_refuses(self):
        from pocketchat.office_context import office_memory_action, office_memory_view, read_project_metadata, update_project_metadata
        project = read_project_metadata(self.app.store)
        update_project_metadata(self.app.store, project['project_id'], instruction_revision=1, instructions='Use concise Chinese explanations.')
        view = office_memory_view(self.app.store)
        proposal = office_memory_action(self.app.store, 'propose', project['project_id'], view['store_sha256'],
            content='Prefer concise replies about releases', category='user_preference', personal_guidance=True)
        acceptance = office_memory_action(self.app.store, 'accept', project['project_id'], proposal['store_sha256'],
            memory_id=proposal['record']['memory_id'], revision=1, personal_guidance=True)
        update_project_metadata(self.app.store, project['project_id'], memory_revision=1, memory_enabled=True)
        body = dict(prompt='Explain releases concisely', intent='chat', source_ids=[], revision_id=None, request_id='foundation-send', consent=True)
        preview = self.app.preview(self.cid, body)
        self.assertEqual(preview['project_id'], project['project_id'])
        self.assertIn('Use concise Chinese explanations.', str(preview['prepared_payload']))
        self.assertIn('Prefer concise replies about releases', str(preview['prepared_payload']))
        old_confirmation = preview['confirmation']
        retired = office_memory_action(self.app.store, 'retire', project['project_id'], acceptance['store_sha256'],
            memory_id=acceptance['record']['memory_id'], revision=acceptance['record']['revision'])
        with self.assertRaises(OfficeError):
            self.app.submit_confirmed(self.cid, body, old_confirmation)
        self.assertEqual(self.adapter.calls, [])
        fresh = self.app.preview(self.cid, body)
        self.assertNotIn('Prefer concise replies about releases', str(fresh['prepared_payload']))
        run = self.app.submit_confirmed(self.cid, body, fresh['confirmation'])
        self.app.wait(run['id'], 3)
        self.assertEqual(self.app.status(run['id'])['status'], 'completed')
        self.assertEqual(self.adapter.calls[0], fresh['prepared_payload']['messages'])
        snapshot = next((self.root / 'traces' / 'requests').glob('*.json'))
        self.assertEqual(json.loads(snapshot.read_text())['request'], fresh['prepared_payload'])
        # Already admitted original receipt is recoverable after preferences change.
        update_project_metadata(self.app.store, project['project_id'], instruction_revision=2, instructions='Changed future guidance')
        recovered = self.app.submit_confirmed(self.cid, body, fresh['confirmation'])
        self.assertEqual(recovered['id'], run['id'])
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(retired['record']['admission_status'], 'retired')

    def test_preview_full_history_budget_and_disabled_recall_do_not_call_provider(self):
        from pocketchat.office_context import read_project_metadata, update_project_metadata
        project = read_project_metadata(self.app.store)
        update_project_metadata(self.app.store, project['project_id'], instruction_revision=1, instructions='x' * 12000)
        self.app.config['max_context_chars'] = 500
        body = dict(prompt='Hi', intent='chat', source_ids=[], revision_id=None, request_id='oversize-project', consent=True)
        with self.assertRaises(OfficeError):
            self.app.preview(self.cid, body)
        self.assertEqual(self.adapter.calls, [])

    def test_confirmed_admission_does_not_project_a_third_time(self):
        from unittest.mock import patch
        from pocketchat import office_context
        body = dict(prompt='Hi', intent='chat', source_ids=[], revision_id=None, request_id='one-admission-plan', consent=True)
        original = office_context.prepare_office_turn
        plans = []
        def prepare(*args, **kwargs):
            plan = original(*args, **kwargs)
            plans.append(plan)
            if len(plans) > 2:
                raise AssertionError('a second admission projection was attempted')
            return plan
        with patch.object(office_context, 'prepare_office_turn', side_effect=prepare):
            preview = self.app.preview(self.cid, body)
            run = self.app.submit_confirmed(self.cid, body, preview['confirmation'])
            self.app.wait(run['id'], 3)
        self.assertEqual(self.app.status(run['id'])['status'], 'completed')
        self.assertEqual(len(plans), 2)
        self.assertEqual(self.adapter.calls[0], plans[1].messages)

    def test_recording_loopback_stream_wire_equals_preview_snapshot_and_saved_plan(self):
        from http.server import BaseHTTPRequestHandler
        from tests._helpers import StubHTTPServer
        calls = []
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                event = dict(choices=[dict(delta=dict(content='Recorded reply'), finish_reason='stop')])
                raw = ('data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = StubHTTPServer(Provider)
        self.addCleanup(server.close)
        self.app.config.pop('provider_adapter', None)
        self.app.config['base_url'] = 'http://127.0.0.1:%d/v1' % server.port
        body = dict(prompt='Exact streaming request', intent='chat', source_ids=[], revision_id=None,
                    request_id='recorded-wire', consent=True)
        preview = self.app.preview(self.cid, body)
        self.assertEqual(calls, [])
        run = self.app.submit_confirmed(self.cid, body, preview['confirmation'])
        self.app.wait(run['id'], 3)
        status = self.app.status(run['id'])
        self.assertEqual(status['status'], 'completed', status)
        self.assertEqual(calls, [preview['prepared_payload']])
        snapshot = next((self.root / 'traces' / 'requests').glob('*.json'))
        self.assertEqual(json.loads(snapshot.read_text())['request'], calls[0])
        self.assertEqual(status['request']['prepared_context']['payload'], calls[0])

    def test_tight_budget_keeps_recent_complete_history_and_discloses_omitted_runs(self):
        first = self.send('Old decision ' + 'a' * 350, key='old-history')
        second = self.send('Recent correction ' + 'b' * 350, key='recent-history')
        self.app.config['max_context_chars'] = 1600
        body = dict(prompt='Continue', intent='chat', source_ids=[], revision_id=None, request_id='window-history', consent=True)
        preview = self.app.preview(self.cid, body)
        sent = str(preview['prepared_payload']['messages'])
        self.assertIn('Recent correction', sent)
        self.assertNotIn('Old decision', sent)
        manifest = preview['context_manifest']
        self.assertEqual(manifest['selected_history_run_ids'], [second['id']])
        self.assertEqual(manifest['omitted_history_run_ids'], [first['id']])

    def test_rehashed_backup_cannot_change_prepared_request_model_or_history(self):
        import hashlib
        from pocketchat.office_backup import snapshot, validate
        run = self.send('Bound original prompt', key='backup-prepared')
        raw = snapshot(self.app.store)
        for kind in ('model', 'history', 'prompt'):
            document = json.loads(raw)
            row = next(r for r in document['data']['tables']['runs'] if r['id'] == run['id'])
            payload = json.loads(row['payload'])
            plan = payload['prepared_context']
            if kind == 'model':
                plan['payload']['model'] = 'foreign-model'
            elif kind == 'history':
                plan['manifest']['history_run_ids'] = ['missing-history']
            else:
                plan['messages'][-1]['content'] = 'Unrelated prompt'
                plan['payload']['messages'] = plan['messages']
            row['payload'] = json.dumps(payload)
            document['sha256'] = hashlib.sha256(json.dumps(document['data'], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            with self.subTest(kind=kind), self.assertRaises(OfficeError):
                validate(json.dumps(document).encode())

    def test_summary_bound_to_current_branch_refuses_a_replacement_that_excludes_original(self):
        import hashlib
        from pocketchat.office_context import office_summary_view, save_office_summary
        first = self.send('Original decision', key='summary-original')
        view = office_summary_view(self.app.store, self.cid)
        message = view['original_messages'][0]
        refs = [dict(message_id=message['id'], run_id=message['run_id'], start=0, end=len(message['content']),
                     sha256=hashlib.sha256(message['content'].encode()).hexdigest())]
        save_office_summary(self.app.store, self.cid, view['project_id'], revision=0, content='Original decision summary', refs=refs, accept=True)
        before_calls = len(self.adapter.calls)
        with self.assertRaises(OfficeError):
            self.app.preview(self.cid, dict(prompt='Replacement', intent='chat', source_ids=[], revision_id=None,
                request_id='summary-replace', consent=True, branch_from=first['id']))
        self.assertEqual(len(self.adapter.calls), before_calls)

    def test_historical_prepared_summary_original_references_are_validated(self):
        import hashlib
        from pocketchat.office_context import office_summary_view, save_office_summary
        from pocketchat.office_backup import snapshot, validate
        first = self.send('Original decision', key='historic-summary-original')
        view = office_summary_view(self.app.store, self.cid)
        message = view['original_messages'][0]
        refs = [dict(message_id=message['id'], run_id=first['id'], start=0, end=len(message['content']),
                     sha256=hashlib.sha256(message['content'].encode()).hexdigest())]
        save_office_summary(self.app.store, self.cid, view['project_id'], revision=0, content='Decision stays small', refs=refs, accept=True)
        sent = self.send('Continue', key='historic-summary-sent')
        raw = snapshot(self.app.store)
        document = json.loads(raw)
        row = next(r for r in document['data']['tables']['runs'] if r['id'] == sent['id'])
        payload = json.loads(row['payload'])
        plan = payload['prepared_context']
        plan['manifest']['summary']['cid'] = 'foreign'
        pending = plan['manifest']['pending_context']
        pending += '\nAccepted continuity summary (context data, not verified facts or permissions):\n' + json.dumps(plan['manifest']['summary'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
        plan['messages'][-1]['content'] = pending
        plan['payload']['messages'] = plan['messages']
        row['payload'] = json.dumps(payload)
        document['sha256'] = hashlib.sha256(json.dumps(document['data'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        with self.assertRaises(OfficeError):
            validate(json.dumps(document).encode())
