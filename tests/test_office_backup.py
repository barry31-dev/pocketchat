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

    @unittest.skipUnless(SECURE_IO, "requires no-follow descriptor I/O")
    def test_archive_roundtrip_and_invalid_flag_refusal(self):
        self.store.archive(self.cid, True)
        raw = self.backup()
        self.restore(raw)
        restored = OfficeStore(self.base / 'restored')
        self.addCleanup(restored.close)
        self.assertTrue(restored.get(self.cid)['archived'])
        doc = json.loads(raw)
        doc['data']['tables']['conversations'][0]['archived'] = 2
        doc['sha256'] = hashlib.sha256(encoded(doc['data']).encode()).hexdigest()
        with self.assertRaises(OfficeError):
            self.restore(encoded(doc).encode(), 'bad-archive')

    @unittest.skipUnless(SECURE_IO, "requires no-follow descriptor I/O")
    def test_explicit_upgrade_keeps_validated_old_backup_and_restores_it(self):
        from pocketchat import office_backup
        # Rebuild precisely the legacy conversation table, independent of fresh defaults.
        self.store.db.executescript('''
            ALTER TABLE conversations RENAME TO old_conversations;
            CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT,created REAL);
            INSERT INTO conversations SELECT id,title,created FROM old_conversations;
            DROP TABLE old_conversations;
            PRAGMA user_version=2;
        ''')
        self.store.close()
        self.store = OfficeStore(self.base / 'original')
        self.addCleanup(self.store.close)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 2)
        with self.assertRaises(OfficeError):
            self.store.archive(self.cid, True)
        before = office_backup.snapshot(self.store)
        result = office_backup.upgrade(self.store)
        self.assertEqual(result['database_version'], 4)
        self.assertEqual((self.store.root / result['backup']).read_bytes(), before)
        self.assertEqual(office_backup.validate(before)['database_version'], 2)
        self.assertEqual((self.store.root / result['backup']).stat().st_mode & 0o777, 0o600)
        self.restore(before)
        restored = OfficeStore(self.base / 'restored')
        self.addCleanup(restored.close)
        self.assertEqual(restored.get(self.cid), self.store.get(self.cid))
        self.assertEqual(restored.db.execute('PRAGMA user_version').fetchone()[0], 2)
        self.assertNotIn('archived', [r[1] for r in restored.db.execute('PRAGMA table_info(conversations)')])
        self.assertEqual(office_backup.snapshot(restored), before)
        self.assertEqual(office_backup.upgrade(self.store), {'database_version': 4, 'upgraded': False})

    @unittest.skipUnless(SECURE_IO, "requires no-follow descriptor I/O")
    def test_failed_upgrade_backup_preserves_legacy_schema_and_contents(self):
        from unittest.mock import patch
        from pocketchat import office_backup
        self.store.db.executescript('''
            ALTER TABLE conversations RENAME TO old_conversations;
            CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT,created REAL);
            INSERT INTO conversations SELECT id,title,created FROM old_conversations;
            DROP TABLE old_conversations;
            PRAGMA user_version=2;
        ''')
        before = self.backup()
        with patch.object(office_backup, 'write_new', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                office_backup.upgrade(self.store)
        self.assertEqual(self.backup(), before)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 2)


    def _check_upgrade_directory_sync_failure_and_retry(self, reuse):
        import stat
        from unittest.mock import patch
        from pocketchat import office_backup
        self.store.db.executescript('''
            ALTER TABLE conversations RENAME TO old_conversations;
            CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT,created REAL);
            INSERT INTO conversations SELECT id,title,created FROM old_conversations;
            DROP TABLE old_conversations;
            PRAGMA user_version=2;
        ''')
        before = self.backup()
        target = self.store.root / ('office-v2-before-upgrade-' + hashlib.sha256(before).hexdigest() + '.json')
        if reuse:
            office_backup.write_new(target, before)
        database = (self.store.root / 'office.sqlite').read_bytes()
        fsync = os.fsync
        failed = []

        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                failed.append(fd)
                self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 2)
                self.assertNotIn('archived', [r[1] for r in self.store.db.execute('PRAGMA table_info(conversations)')])
                raise OSError('directory sync failed')
            return fsync(fd)

        with patch.object(office_backup.os, 'fsync', side_effect=fail_directory):
            with self.assertRaises(OfficeError):
                office_backup.upgrade(self.store)
        self.assertEqual(len(failed), 1)
        self.assertEqual(self.backup(), before)
        self.assertEqual((self.store.root / 'office.sqlite').read_bytes(), database)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 2)
        self.assertFalse(self.store.db.in_transaction)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        synced = []

        def sync_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 2)
                self.assertNotIn('archived', [r[1] for r in self.store.db.execute('PRAGMA table_info(conversations)')])
                synced.append(fd)
            return fsync(fd)

        with patch.object(office_backup.os, 'fsync', side_effect=sync_directory):
            result = office_backup.upgrade(self.store)
        self.assertEqual(len(synced), 1)
        self.assertTrue(result['upgraded'])
        self.assertEqual(result['database_version'], 4)
        self.assertEqual(target.read_bytes(), before)

    @unittest.skipUnless(SECURE_IO, 'requires no-follow descriptor I/O')
    def test_new_upgrade_backup_directory_sync_failure_preserves_schema_and_retries(self):
        self._check_upgrade_directory_sync_failure_and_retry(reuse=False)

    @unittest.skipUnless(SECURE_IO, 'requires no-follow descriptor I/O')
    def test_reused_upgrade_backup_directory_sync_failure_preserves_schema_and_retries(self):
        self._check_upgrade_directory_sync_failure_and_retry(reuse=True)

    @unittest.skipUnless(SECURE_IO, 'requires no-follow descriptor I/O')
    def test_max_message_ids_roundtrip_as_integers_and_refuse_only_new_requests(self):
        from pocketchat import office_backup
        with self.store.db:
            self.store.db.execute('UPDATE messages SET id=id+?', (2**63 - 5,))
        raw = self.backup()
        ids = [m['id'] for m in office_backup.validate(raw)['tables']['messages']]
        self.assertEqual(ids, list(range(2**63 - 4, 2**63)))
        self.assertTrue(all(type(mid) is int for mid in ids))
        self.restore(raw)
        restored = OfficeStore(self.base / 'restored')
        self.addCleanup(restored.close)
        self.assertEqual(restored.get(self.cid), self.store.get(self.cid))
        prior = restored.begin(self.cid, 'request-first', self.body)
        self.assertEqual(prior['status'], 'completed')
        with self.assertRaises(OfficeError):
            restored.begin(self.cid, 'overflow-request', self.body)
        self.assertEqual(office_backup.snapshot(restored), raw)


    @unittest.skipUnless(SECURE_IO, 'requires private descriptor I/O')
    def test_restore_v3_preserves_version_until_explicit_upgrade(self):
        self.store.db.execute('PRAGMA user_version=3')
        self.store.db.commit()
        self.restore(self.backup(), 'v3-restored')
        restored = OfficeStore(self.base / 'v3-restored')
        self.addCleanup(restored.close)
        self.assertEqual(restored.db.execute('PRAGMA user_version').fetchone()[0], 3)

    def test_reordered_completed_runs_cannot_invert_saved_message_chronology(self):
        document = json.loads(self.backup())
        document['data']['tables']['runs'].reverse()
        document['sha256'] = hashlib.sha256(encoded(document['data']).encode()).hexdigest()
        from pocketchat.office_backup import validate
        with self.assertRaises(OfficeError):
            validate(encoded(document).encode())

    def test_interleaved_turn_messages_cannot_make_an_answer_a_future_prefix(self):
        document = json.loads(self.backup())
        rows = document['data']['tables']['messages']
        rows[1]['id'], rows[2]['id'] = rows[2]['id'], rows[1]['id']
        rows.sort(key=lambda row: row['id'])
        document['sha256'] = hashlib.sha256(encoded(document['data']).encode()).hexdigest()
        from pocketchat.office_backup import validate
        with self.assertRaises(OfficeError):
            validate(encoded(document).encode())
