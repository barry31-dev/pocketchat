"""Private browser workspace never rewrites completed user work."""
import tempfile
import os
import unittest
from pathlib import Path
from pocketchat.office_store import OfficeStore, OfficeError


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / 'office'
        self.store = OfficeStore(self.root)
        self.cid = self.store.create('Weekly work')['id']

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_source_roundtrip_and_reopen(self):
        source = self.store.add_source(self.cid, 'notes.md', 'Sales: 12')
        self.store.close()
        self.store = OfficeStore(self.root)
        c = self.store.get(self.cid)
        self.assertEqual(c['sources'][0]['sha256'], source['sha256'])
        self.assertEqual(self.store.source(self.cid, source['id'])['text'], 'Sales: 12')

    def test_foreign_source_and_unsafe_file_name_refused(self):
        other = self.store.create('Other')['id']
        source = self.store.add_source(other, 'notes.txt', 'private')
        with self.assertRaises(OfficeError):
            self.store.source(self.cid, source['id'])
        for name in ('../notes.txt', 'a/b.txt', 'data.pdf', 'x\\y.md'):
            with self.subTest(name=name), self.assertRaises(OfficeError):
                self.store.add_source(self.cid, name, 'no')

    def test_idempotent_run_binding_and_restart_unknown(self):
        payload = dict(prompt='hello', intent='chat', source_ids=[], revision_id=None)
        run = self.store.begin(self.cid, 'request-001', payload)
        again = self.store.begin(self.cid, 'request-001', payload)
        self.assertEqual(run['id'], again['id'])
        with self.assertRaises(OfficeError):
            self.store.begin(self.cid, 'request-001', dict(payload, prompt='changed'))
        with self.assertRaises(OfficeError):
            self.store.begin(self.cid, 'request-002', payload)
        self.store.close()
        self.store = OfficeStore(self.root)
        self.assertEqual(self.store.run(run['id'])['status'], 'unknown')
        self.assertEqual(self.store.get(self.cid)['messages'], [])

    def test_reports_are_versioned_and_messages_persist(self):
        p = dict(prompt='report', intent='report', source_ids=[], revision_id=None)
        r = self.store.begin(self.cid, 'request-001', p)
        self.store.finish(r['id'], 'completed', '# Weekly\nHello', None)
        first = self.store.get(self.cid)
        rid = first['artifacts'][0]['id']
        r2 = self.store.begin(self.cid, 'request-002', dict(p, revision_id=rid))
        self.store.finish(r2['id'], 'completed', '# Weekly\nShort', None)
        c = self.store.get(self.cid)
        self.assertEqual(len(c['messages']), 4)
        self.assertEqual(len(c['artifacts']), 2)
        self.assertEqual(self.store.artifact(self.cid, rid)['content'], '# Weekly\nHello')

    def test_second_workspace_writer_refused(self):
        with self.assertRaises(OfficeError):
            OfficeStore(self.root)

    def test_reject_linked_request_directory_on_reopen(self):
        from pocketchat.office import OfficeWorkspace
        from pocketchat.config import load_config, profile_config
        self.store.close()
        (self.root/'traces').mkdir()
        outside=Path(self.tmp.name).resolve()/'outside'
        outside.mkdir()
        (self.root/'traces'/'requests').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(OfficeError):
            OfficeWorkspace(self.root,profile_config(load_config(Path('examples/config.json'))))

    def test_unreleased_old_schema_is_refused_without_rewriting(self):
        import sqlite3
        import hashlib
        self.store.close()
        path=self.root/'office.sqlite'
        db=sqlite3.connect(path)
        db.execute('PRAGMA user_version=1')
        db.commit();db.close()
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaises(OfficeError):OfficeStore(self.root)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)

    def test_literal_body_search_returns_stable_message_location_and_archive_filter(self):
        p = dict(prompt='Find budget?', intent='chat', source_ids=[], revision_id=None)
        r = self.store.begin(self.cid, 'search-request', p)
        self.store.finish(r['id'], 'completed', 'The BUDGET is 100% _complete_.', None)
        hits = self.store.search('budget')['items']
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[1]['message_id'], self.store.get(self.cid)['messages'][1]['id'])
        self.assertEqual(len(self.store.search('% _')['items']), 1)
        self.assertEqual(self.store.search("' OR 1=1 --")['items'], [])
        self.store.archive(self.cid, True)
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.search('budget')['items'], [])
        self.assertEqual(len(self.store.search('budget', archived=True)['items']), 2)
        self.assertTrue(self.store.get(self.cid)['archived'])
        self.store.archive(self.cid, False)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.search('budget')['items'], hits)

    def test_archive_preserves_receipts_but_refuses_active_or_new_writes(self):
        p = dict(prompt='Keep', intent='chat', source_ids=[], revision_id=None)
        r = self.store.begin(self.cid, 'archive-request', p)
        with self.assertRaises(OfficeError):
            self.store.archive(self.cid, True)
        self.store.finish(r['id'], 'completed', 'Original answer', None)
        self.store.archive(self.cid, True)
        self.assertEqual(self.store.begin(self.cid, 'archive-request', p)['id'], r['id'])
        for operation in (lambda: self.store.begin(self.cid, 'new-request', p),
                          lambda: self.store.add_source(self.cid, 'new.txt', 'new'),
                          lambda: self.store.rename(self.cid, 'new')):
            with self.assertRaises(OfficeError):
                operation()
        for value in ('false', 0, None):
            with self.assertRaises(OfficeError):
                self.store.archive(self.cid, value)
        self.store.close()
        self.store = OfficeStore(self.root)
        self.assertTrue(self.store.get(self.cid)['archived'])
        self.assertEqual(self.store.get(self.cid)['messages'][1]['content'], 'Original answer')

    def test_search_is_bounded_and_validates_query(self):
        for value in ('', ' ', 'x' * 241, ['hello'], '\x00'):
            with self.assertRaises(OfficeError):
                self.store.search(value)


    def test_unicode_casefold_search_excerpt_contains_actual_hit(self):
        p = dict(prompt='Find the marker', intent='chat', source_ids=[], revision_id=None)
        r = self.store.begin(self.cid, 'unicode-search', p)
        self.store.finish(r['id'], 'completed', 'ß' * 300 + 'BUDGET' + 'x' * 300, None)
        hit = self.store.search('budget')['items'][0]
        self.assertIn('BUDGET', hit['excerpt'])
        self.assertLessEqual(len(hit['excerpt']), 240)

    def test_message_location_ids_are_exact_json_strings(self):
        import json
        p = dict(prompt='Location', intent='chat', source_ids=[], revision_id=None)
        r = self.store.begin(self.cid, 'large-id-search', p)
        self.store.finish(r['id'], 'completed', 'Location answer', None)
        with self.store.db:
            self.store.db.execute('UPDATE messages SET id=id+?', (2**53,))
        messages = json.loads(json.dumps(self.store.get(self.cid)))['messages']
        hits = json.loads(json.dumps(self.store.search('location')))['items']
        self.assertEqual([m['id'] for m in messages], [str(2**53 + 1), str(2**53 + 2)])
        self.assertEqual([h['message_id'] for h in hits], [m['id'] for m in messages])


    def test_message_id_exhaustion_refuses_new_begin_but_preserves_receipts(self):
        from pocketchat.office_backup import snapshot
        p = dict(prompt='Keep receipt', intent='chat', source_ids=[], revision_id=None)
        p['wire'] = dict(p)
        r = self.store.begin(self.cid, 'bound-request', p)
        self.store.finish(r['id'], 'completed', 'Saved answer', None)
        with self.store.db:
            self.store.db.execute('UPDATE messages SET id=id+?', (2**63 - 4,))
        before = snapshot(self.store)
        database = (self.root / 'office.sqlite').read_bytes()
        self.assertEqual(self.store.begin(self.cid, 'bound-request', p)['id'], r['id'])
        self.assertEqual(self.store.receipt(self.cid, 'bound-request', p['wire'])['id'], r['id'])
        with self.assertRaises(OfficeError) as refused:
            self.store.begin(self.cid, 'new-request', p)
        self.assertEqual(refused.exception.status, 409)
        self.assertEqual(snapshot(self.store), before)
        self.assertEqual((self.root / 'office.sqlite').read_bytes(), database)
        self.assertFalse(self.store.db.in_transaction)

    def test_finish_message_id_exhaustion_is_atomic_and_can_be_marked_failed(self):
        from pocketchat.office_backup import snapshot
        p = dict(prompt='Report', intent='report', source_ids=[], revision_id=None)
        prior = self.store.begin(self.cid, 'prior-request', p)
        self.store.finish(prior['id'], 'completed', 'Original report', None)
        pending = self.store.begin(self.cid, 'pending-request', p)
        with self.store.db:
            self.store.db.execute('UPDATE messages SET id=id+?', (2**63 - 4,))
        before = list(self.store.db.iterdump())
        database = (self.root / 'office.sqlite').read_bytes()
        with self.assertRaises(OfficeError) as refused:
            self.store.finish(pending['id'], 'completed', 'New report', None)
        self.assertEqual(refused.exception.status, 409)
        self.assertEqual(list(self.store.db.iterdump()), before)
        self.assertEqual((self.root / 'office.sqlite').read_bytes(), database)
        self.assertFalse(self.store.db.in_transaction)
        self.assertEqual(self.store.run(pending['id'])['status'], 'running')
        self.store.finish(pending['id'], 'failed', '', 'Message IDs exhausted.')
        self.assertEqual(self.store.run(pending['id'])['status'], 'failed')
        snapshot(self.store)

    def test_last_message_id_pair_is_allocated_in_order_then_new_begin_refuses(self):
        p = dict(prompt='First', intent='chat', source_ids=[], revision_id=None)
        prior = self.store.begin(self.cid, 'prior-request', p)
        self.store.finish(prior['id'], 'completed', 'First answer', None)
        with self.store.db:
            self.store.db.execute('UPDATE messages SET id=id+?', (2**63 - 5,))
        pending = self.store.begin(self.cid, 'last-request', dict(p, prompt='Last'))
        self.store.finish(pending['id'], 'completed', 'Last answer', None)
        messages = self.store.get(self.cid)['messages']
        self.assertEqual([m['id'] for m in messages], [str(i) for i in range(2**63 - 4, 2**63)])
        self.assertEqual([m['role'] for m in messages], ['user', 'assistant', 'user', 'assistant'])
        self.assertEqual([m['content'] for m in messages], ['First', 'First answer', 'Last', 'Last answer'])
        with self.assertRaises(OfficeError):
            self.store.begin(self.cid, 'overflow-request', p)


    @unittest.skipUnless(hasattr(os, 'O_NOFOLLOW') and os.open in os.supports_dir_fd, 'requires private descriptor I/O')
    def test_cli_ui_state_is_bounded_private_cas_and_workspace_bound(self):
        from pocketchat.office_service import dispatch
        from pocketchat.office import OfficeWorkspace
        self.store.close()
        app = OfficeWorkspace(self.root)
        self.addCleanup(app.close)
        state = dispatch(app, '/api/cli-state', 'GET')[1]
        self.assertEqual(state, dict(revision=0, current=None, drafts={}, pending=None))
        draft = dict(prompt='Unsent private draft', intent='chat', source_ids=[], revision_id=None)
        saved = dispatch(app, '/api/cli-state', 'POST', dict(state, current=self.cid, drafts={self.cid:draft}))[1]
        self.assertEqual(saved['revision'], 1)
        with self.assertRaises(OfficeError):dispatch(app, '/api/cli-state', 'POST', state)
        self.assertEqual((self.root/'office-cli-state.json').stat().st_mode & 0o777, 0o600)
        app.close()
        app = OfficeWorkspace(self.root)
        self.addCleanup(app.close)
        self.assertEqual(dispatch(app, '/api/cli-state', 'GET')[1], saved)
        with self.assertRaises(OfficeError):
            dispatch(app, '/api/cli-state', 'POST', dict(saved, drafts={self.cid:dict(draft,prompt='x'*12001)}))
        self.assertIsNone(app.store.receipt(self.cid, 'never-sent', draft))


    @unittest.skipUnless(hasattr(os, 'O_NOFOLLOW') and os.open in os.supports_dir_fd, 'requires private descriptor I/O')
    def test_cli_state_refuses_symlink_foreign_pending_and_unusable_revision(self):
        from pocketchat.office_store import encoded
        state = self.store.cli_state()
        body = dict(prompt='Hi', intent='chat', source_ids=[], revision_id=None,
                    request_id='pending-missing', consent=True)
        with self.assertRaises(OfficeError):
            self.store.cli_state(dict(state,pending=dict(cid='new',body=body,confirmation='a'*64,run_id=None)))
        with self.assertRaises(OfficeError):
            self.store.cli_state(dict(state,revision=2**53-1))
        target=self.root/'office-cli-state.json'
        outside=Path(self.tmp.name)/'outside-state.json';outside.write_text(encoded(state))
        target.symlink_to(outside)
        with self.assertRaises(OfficeError):self.store.cli_state()
        self.assertEqual(outside.read_text(),encoded(state))


    @unittest.skipUnless(hasattr(os, 'O_NOFOLLOW') and os.open in os.supports_dir_fd, 'requires private descriptor I/O')
    def test_cli_state_malformed_unicode_is_content_free_refusal(self):
        state=self.store.cli_state()
        with self.assertRaises(OfficeError):
            self.store.cli_state(dict(state,drafts={'new':dict(prompt='\ud800',intent='chat',source_ids=[],revision_id=None)}))
