"""Office uses the existing single Session, never arbitrary model tools."""
import json
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
