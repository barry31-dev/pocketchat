"""Private browser workspace never rewrites completed user work."""
import tempfile
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
