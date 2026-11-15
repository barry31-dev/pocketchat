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


class ProjectSchemaTests(unittest.TestCase):
    def test_new_workspace_creates_schema_five_identity_once(self):
        from pocketchat.office_store import OfficeStore
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'project'
            with_store = OfficeStore(root)
            try:
                self.assertEqual(with_store.db.execute('PRAGMA user_version').fetchone()[0], 5)
                project = read_project_metadata(with_store)
                self.assertFalse(project['memory_enabled'])
                self.assertEqual(project['instructions'], '')
            finally:
                with_store.close()
            reopened = OfficeStore(root)
            try:
                self.assertEqual(read_project_metadata(reopened), project)
            finally:
                reopened.close()

    def test_existing_version_zero_database_does_not_acquire_identity(self):
        from pocketchat.office_store import OfficeStore
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'project'
            root.mkdir(mode=0o700)
            with sqlite3.connect(root / 'office.sqlite') as db:
                db.execute('CREATE TABLE unrelated(secret TEXT)')
            before = (root / 'office.sqlite').read_bytes()
            with self.assertRaises(OfficeError):
                OfficeStore(root)
            self.assertEqual((root / 'office.sqlite').read_bytes(), before)

    def test_schema_five_bad_project_refuses_open_without_database_writes(self):
        from pocketchat.office_store import OfficeStore
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'project'
            store = OfficeStore(root)
            try:
                store.db.execute("UPDATE office_project SET project_id='foreign'")
                store.db.commit()
            finally:
                store.close()
            before = (root / 'office.sqlite').read_bytes()
            with self.assertRaises(OfficeError):
                OfficeStore(root)
            self.assertEqual((root / 'office.sqlite').read_bytes(), before)


class ProjectMutationTests(unittest.TestCase):
    def test_revisioned_instruction_and_memory_preferences_preserve_identity(self):
        from pocketchat.office_store import OfficeStore
        from pocketchat.office_context import update_project_metadata
        with tempfile.TemporaryDirectory() as tmp:
            store = OfficeStore(Path(tmp).resolve() / 'project')
            try:
                original = read_project_metadata(store)
                changed = update_project_metadata(store, original['project_id'],
                                                  instruction_revision=1, instructions='Use concise text')
                self.assertEqual(changed['project_id'], original['project_id'])
                self.assertEqual(changed['instruction_revision'], 2)
                self.assertFalse(changed['memory_enabled'])
                enabled = update_project_metadata(store, original['project_id'], memory_revision=1, memory_enabled=True)
                self.assertEqual(enabled['memory_revision'], 2)
                self.assertEqual(enabled['instructions'], changed['instructions'])
                before = (store.root / 'office.sqlite').read_bytes()
                for changes in [dict(instruction_revision=1, instructions='stale'),
                                dict(memory_revision=1, memory_enabled=False),
                                dict(memory_revision=True, memory_enabled=True),
                                dict(instruction_revision=2, instructions='x' * 12289),
                                dict(memory_revision=2, memory_enabled='yes'),
                                dict(instructions='missing revision'), dict(store_path='/outside')]:
                    with self.subTest(changes=changes), self.assertRaises(OfficeError):
                        update_project_metadata(store, original['project_id'], **changes)
                    self.assertEqual((store.root / 'office.sqlite').read_bytes(), before)
                with self.assertRaises(OfficeError):
                    update_project_metadata(store, 'project-' + 'b' * 32, instruction_revision=2, instructions='foreign')
            finally:
                store.close()
