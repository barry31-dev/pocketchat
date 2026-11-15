"""Project metadata is admitted before office context can acquire authority."""
import copy
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from pocketchat.office_context import read_project_metadata, validate_project_metadata
from pocketchat.office_store import OfficeError


class ProjectAdmissionTests(unittest.TestCase):
    def metadata(self, **changes):
        value = dict(project_id='project-' + 'a' * 32, instructions='',
                     instruction_revision=1, memory_enabled=False, memory_revision=1)
        value.update(changes)
        return value

    def test_metadata_is_closed_bounded_and_not_caller_owned(self):
        original = self.metadata(instructions='Use concise Chinese responses.')
        admitted = validate_project_metadata(original)
        self.assertEqual(admitted, original)
        admitted['instructions'] = 'Changed'
        self.assertEqual(original['instructions'], 'Use concise Chinese responses.')
        for changes in [dict(project_id='../other'), dict(project_id=''),
                        dict(instructions='界' * 4097), dict(instructions='unsafe\x00text'),
                        dict(instructions='\ud800'), dict(instruction_revision=True),
                        dict(instruction_revision=0), dict(instruction_revision=2**53),
                        dict(memory_enabled=1), dict(memory_revision=False),
                        dict(memory_revision=-1), dict(store_path='/outside'),
                        dict(workspace_id='path-based-native-identity')]:
            with self.subTest(changes=changes), self.assertRaises(OfficeError):
                validate_project_metadata(self.metadata(**changes))
        for value in [None, [], 'metadata', {k: v for k, v in original.items()
                                           if k != 'memory_revision'}]:
            with self.subTest(value=value), self.assertRaises(OfficeError):
                validate_project_metadata(value)
        self.assertEqual(validate_project_metadata(self.metadata(instructions='界' * 4096))['instructions'],
                         '界' * 4096)

    def test_project_identity_is_not_interchangeable(self):
        first = self.metadata()
        second = self.metadata(project_id='project-' + 'b' * 32)
        self.assertEqual(validate_project_metadata(first, expected_project_id=first['project_id']), first)
        with self.assertRaises(OfficeError):
            validate_project_metadata(second, expected_project_id=first['project_id'])
        self.assertEqual(second, self.metadata(project_id='project-' + 'b' * 32))

    def database(self, root, version, rows):
        db = sqlite3.connect(root / 'office.sqlite')
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA user_version=%d' % version)
        db.execute('CREATE TABLE office_project(project_id TEXT, instructions TEXT, '
                   'instruction_revision INTEGER, memory_enabled INTEGER, memory_revision INTEGER)')
        for row in rows:
            db.execute('INSERT INTO office_project VALUES(?,?,?,?,?)',
                       tuple(row[k] for k in self.metadata()))
        db.commit()
        return SimpleNamespace(db=db, lock=threading.RLock())

    def test_legacy_workspace_refuses_without_implicit_upgrade_or_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.database(root, 4, [self.metadata()])
            before = (root / 'office.sqlite').read_bytes()
            try:
                with self.assertRaisesRegex(OfficeError, 'upgrade'):
                    read_project_metadata(store)
                self.assertEqual(store.db.execute('PRAGMA user_version').fetchone()[0], 4)
                self.assertEqual((root / 'office.sqlite').read_bytes(), before)
            finally:
                store.db.close()

    def test_schema_five_requires_one_valid_project_without_mutating_rows(self):
        good = self.metadata(instructions='Project instructions')
        for rows in [[], [good, copy.deepcopy(good)],
                     [dict(good, memory_enabled=2)], [dict(good, instruction_revision=0)], [good]]:
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                store = self.database(root, 5, rows)
                before = (root / 'office.sqlite').read_bytes()
                try:
                    if rows == [good]:
                        self.assertEqual(read_project_metadata(store), good)
                    else:
                        with self.assertRaises(OfficeError):
                            read_project_metadata(store)
                    self.assertEqual((root / 'office.sqlite').read_bytes(), before)
                finally:
                    store.db.close()

    def test_missing_metadata_or_future_schema_is_not_empty_project(self):
        for version in (5, 6):
            with self.subTest(version=version), sqlite3.connect(':memory:') as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA user_version=%d' % version)
                store = SimpleNamespace(db=db, lock=threading.RLock())
                with self.assertRaises(OfficeError):
                    read_project_metadata(store)
