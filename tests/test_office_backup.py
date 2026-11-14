"""Portable office snapshots preserve references without copying live SQLite files."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from pocketchat.office_store import OfficeError, OfficeStore, encoded


SECURE_IO = hasattr(os, 'O_NOFOLLOW') and os.open in os.supports_dir_fd


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.store = OfficeStore(self.base / 'original')
        self.addCleanup(self.store.close)
        self.cid = self.store.create('Weekly')['id']
        self.source = self.store.add_source(self.cid, 'notes.txt', 'Sales: 12')
        self.body = dict(prompt='Report', intent='report', source_ids=[self.source['id']],
                         revision_id=None, wire=dict(prompt='Report', intent='report',
                         source_ids=[self.source['id']], revision_id=None))
        r = self.store.begin(self.cid, 'request-first', self.body)
        self.store.finish(r['id'], 'completed', '# Weekly\nSales: 12 [S1]', None)
        self.old = self.store.get(self.cid)['artifacts'][0]['id']
        p = dict(self.body, prompt='Shorten', revision_id=self.old,
                 wire=dict(self.body['wire'], prompt='Shorten', revision_id=self.old))
        r = self.store.begin(self.cid, 'request-next', p)
        self.store.finish(r['id'], 'completed', '12 [S1]', None)

    def backup(self):
        from pocketchat import office_backup
        return office_backup.snapshot(self.store)

    def restore(self, data, name='restored'):
        from pocketchat import office_backup
        return office_backup.restore(data, self.base / name)

    @unittest.skipUnless(SECURE_IO, "requires no-follow descriptor I/O")
    def test_roundtrip_preserves_ids_versions_receipts_and_source_order(self):
        raw = self.backup()
        self.restore(raw)
        restored = OfficeStore(self.base / 'restored')
        self.addCleanup(restored.close)
        self.assertEqual(restored.get(self.cid), self.store.get(self.cid))
        self.assertEqual(restored.artifact(self.cid, self.old), self.store.artifact(self.cid, self.old))
        self.assertEqual(restored.citation(self.cid, self.source['id']), 'S1')
        self.assertEqual(restored.history(self.cid), self.store.history(self.cid))
        self.assertEqual(restored.receipt(self.cid, 'request-first', self.body['wire'])['status'], 'completed')
        self.assertEqual(restored.source(self.cid, self.source['id'])['text'], 'Sales: 12')
        self.assertEqual((self.base / 'restored').stat().st_mode & 0o777, 0o700)

    def test_excludes_credentials_discovery_and_raw_traces(self):
        (self.store.root / 'config.json').write_text('{"key":"DO-NOT-EXPORT"}')
        (self.store.root / 'traces').mkdir()
        (self.store.root / 'traces' / 'private.jsonl').write_text('DO-NOT-EXPORT')
        self.assertNotIn(b'DO-NOT-EXPORT', self.backup())

    def test_live_request_refused(self):
        self.store.begin(self.cid, 'request-live', self.body)
        with self.assertRaisesRegex(OfficeError, 'running|active'):
            self.backup()

    def test_corruption_unknown_version_and_unknown_table_preserve_original(self):
        original = (self.store.root / 'office.sqlite').read_bytes()
        good = json.loads(self.backup())
        variants = [b'not json', json.dumps(dict(good, schema_version='future')).encode(),
                    json.dumps(dict(good, sha256='0' * 64)).encode()]
        for key, value in [('database_version', 900), ('tables', {'evil': []})]:
            bad = json.loads(json.dumps(good))
            bad['data'][key] = value
            bad['sha256'] = hashlib.sha256(encoded(bad['data']).encode()).hexdigest()
            variants.append(json.dumps(bad).encode())
        for i, raw in enumerate(variants):
            with self.subTest(i=i), self.assertRaises(OfficeError):
                self.restore(raw, 'bad-%d' % i)
            self.assertFalse((self.base / ('bad-%d' % i)).exists())
        self.assertEqual((self.store.root / 'office.sqlite').read_bytes(), original)

    def test_rehashed_broken_reference_and_source_hash_refused(self):
        for table, field, value in [('sources', 'sha256', '0' * 64),
                                    ('artifacts', 'predecessor', 'missing'),
                                    ('messages', 'run_id', 'missing')]:
            doc = json.loads(self.backup())
            doc['data']['tables'][table][0][field] = value
            doc['sha256'] = hashlib.sha256(encoded(doc['data']).encode()).hexdigest()
            with self.subTest(table=table), self.assertRaises(OfficeError):
                self.restore(json.dumps(doc).encode())
            self.assertFalse((self.base / 'restored').exists())

    @unittest.skipUnless(SECURE_IO, "requires no-follow descriptor I/O")
    def test_existing_destination_and_symlink_parent_refused(self):
        raw = self.backup()
        with self.assertRaises(OfficeError):
            self.restore(raw, 'original')
        (self.base / 'alias').symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(OfficeError):
            self.restore(raw, 'alias/new')
        self.assertFalse((self.base / 'new').exists())

    def test_duplicate_json_and_oversize_refused(self):
        from pocketchat import office_backup
        for raw in (b'{"data":{},"data":{}}', b' ' * (office_backup.MAX_BACKUP_BYTES + 1)):
            with self.assertRaises(OfficeError):
                self.restore(raw)

    @unittest.skipUnless(SECURE_IO, "requires no-follow descriptor I/O")
    def test_backup_file_exclusive_private_and_bad_paths_leave_existing_bytes(self):
        from pocketchat import office_backup
        out = self.base / 'backup.json'
        raw = self.backup()
        office_backup.write_new(out, raw)
        self.assertEqual(out.read_bytes(), raw)
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        for target in (out, self.base / '.hidden.json'):
            with self.assertRaises(OfficeError):
                office_backup.write_new(target, b'changed')
        self.assertEqual(out.read_bytes(), raw)

    def test_version_cycles_and_per_conversation_quotas_refused(self):
        good = json.loads(self.backup())
        cycle = json.loads(json.dumps(good))
        first, second = cycle['data']['tables']['artifacts']
        first['predecessor'] = second['id']
        first['created'] = second['created']
        run = next(r for r in cycle['data']['tables']['runs'] if r['id'] == first['run_id'])
        payload = json.loads(run['payload'])
        payload['revision_id'] = second['id']
        payload['wire']['revision_id'] = second['id']
        run['payload'] = encoded(payload)
        variants = [cycle]
        for table, limit in [('sources', 64), ('runs', 250)]:
            doc = json.loads(json.dumps(good))
            row = dict(doc['data']['tables'][table][0])
            if table == 'runs':
                row.update(status='unknown', reply='', error=None)
            for i in range(limit):
                extra = dict(row, id='extra-%d' % i)
                if table == 'runs':
                    extra['request_id'] = 'extra-request-%d' % i
                doc['data']['tables'][table].append(extra)
            variants.append(doc)
        for i, doc in enumerate(variants):
            doc['sha256'] = hashlib.sha256(encoded(doc['data']).encode()).hexdigest()
            with self.subTest(case=i), self.assertRaises(OfficeError):
                self.restore(json.dumps(doc).encode(), 'invalid-%d' % i)
            self.assertFalse((self.base / ('invalid-%d' % i)).exists())

    @unittest.skipUnless(SECURE_IO, "requires no-follow descriptor I/O")
    def test_restore_holds_destination_writer_lock_during_database_publication(self):
        from unittest.mock import patch
        import os
        from pocketchat import office_backup
        raw = self.backup()
        fdopen = os.fdopen
        checked = []
        test = self
        class ObservedStream:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def write(self, data):
                if data.startswith(b'SQLite format 3'):
                    rival = None
                    try:
                        with test.assertRaisesRegex(OfficeError, 'running|owner|writer'):
                            rival = OfficeStore(test.base / 'restored')
                        checked.append(True)
                    finally:
                        if rival:
                            rival.close()
                return self.stream.write(data)
        with patch.object(office_backup.os, 'fdopen', side_effect=lambda *a, **kw: ObservedStream(fdopen(*a, **kw))):
            self.restore(raw)
        self.assertEqual(checked, [True])
