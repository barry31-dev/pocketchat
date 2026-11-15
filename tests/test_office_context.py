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


class OfficeMemoryLifecycleTests(unittest.TestCase):
    def test_propose_accept_replace_forget_and_recall_are_project_bound(self):
        from pocketchat.office_context import office_memory_action, office_memory_view, update_project_metadata
        from pocketchat.office_store import OfficeStore
        with tempfile.TemporaryDirectory() as tmp:
            store = OfficeStore(Path(tmp).resolve() / 'project')
            try:
                project = read_project_metadata(store)
                view = office_memory_view(store, 'concise')
                self.assertEqual(view['selected'], [])
                proposed = office_memory_action(store, 'propose', project['project_id'], view['store_sha256'],
                                               content='Prefer concise replies', category='user_preference', personal_guidance=True)
                record = proposed['record']
                self.assertEqual(record['admission_status'], 'candidate')
                self.assertEqual(office_memory_view(store, 'concise')['selected'], [])
                accepted = office_memory_action(store, 'accept', project['project_id'], proposed['store_sha256'],
                                               memory_id=record['memory_id'], revision=record['revision'], personal_guidance=True)
                self.assertEqual(accepted['record']['admission_status'], 'accepted')
                update_project_metadata(store, project['project_id'], memory_revision=1, memory_enabled=True)
                self.assertEqual(office_memory_view(store, 'concise')['selected'][0]['memory_id'], record['memory_id'])
                revised = office_memory_action(store, 'revise', project['project_id'], accepted['store_sha256'],
                                              memory_id=record['memory_id'], revision=accepted['record']['revision'],
                                              content='Prefer concise responses in Chinese', personal_guidance=True)
                self.assertEqual(revised['record']['admission_status'], 'candidate')
                self.assertEqual(office_memory_view(store, 'concise')['selected'][0]['memory_id'], record['memory_id'])
                replaced = office_memory_action(store, 'accept', project['project_id'], revised['store_sha256'],
                                               memory_id=revised['record']['memory_id'], revision=1, personal_guidance=True)
                old = next(r for r in replaced['records'] if r['memory_id'] == record['memory_id'])
                self.assertEqual(old['admission_status'], 'retired')
                forgotten = office_memory_action(store, 'forget', project['project_id'], replaced['store_sha256'],
                                                 memory_id=record['memory_id'], revision=old['revision'], acknowledge_history=True)
                successor = revised['record']['memory_id']
                self.assertEqual(office_memory_view(store, 'concise')['selected'][0]['memory_id'], successor)
                forgotten = office_memory_action(store, 'forget', project['project_id'], forgotten['store_sha256'],
                                                 memory_id=successor, revision=replaced['record']['revision'], acknowledge_history=True)
                self.assertEqual(forgotten['records'], [])
                self.assertEqual(office_memory_view(store, 'concise')['selected'], [])
            finally:
                store.close()

    def test_stale_foreign_fact_and_implicit_acceptance_refuse_without_store_changes(self):
        from pocketchat.office_context import office_memory_action, office_memory_view
        from pocketchat.office_store import OfficeStore
        with tempfile.TemporaryDirectory() as tmp:
            store = OfficeStore(Path(tmp).resolve() / 'project')
            try:
                project = read_project_metadata(store)
                view = office_memory_view(store, 'reply')
                for project_id, digest, changes in [
                        ('foreign', view['store_sha256'], dict(content='text', category='user_preference', personal_guidance=True)),
                        (project['project_id'], '0' * 64, dict(content='text', category='user_preference', personal_guidance=True)),
                        (project['project_id'], view['store_sha256'], dict(content='fact', category='source_note', personal_guidance=True)),
                        (project['project_id'], view['store_sha256'], dict(content='fact', category='user_preference', personal_guidance=False))]:
                    with self.subTest(changes=changes), self.assertRaises(OfficeError):
                        office_memory_action(store, 'propose', project_id, digest, **changes)
                    self.assertFalse((store.root / 'office-memory.v2.jsonl').exists())
                proposed = office_memory_action(store, 'propose', project['project_id'], view['store_sha256'],
                                               content='Prefer concise replies', category='user_preference', personal_guidance=True)
                before = (store.root / 'office-memory.v2.jsonl').read_bytes()
                for revision, declared in [(0, True), (True, True), (1, False)]:
                    with self.subTest(revision=revision, declared=declared), self.assertRaises(OfficeError):
                        office_memory_action(store, 'accept', project['project_id'], proposed['store_sha256'],
                                             memory_id=proposed['record']['memory_id'], revision=revision, personal_guidance=declared)
                    self.assertEqual((store.root / 'office-memory.v2.jsonl').read_bytes(), before)
            finally:
                store.close()

    def test_legacy_accepted_or_candidate_without_office_declaration_cannot_be_recalled_or_accepted(self):
        import hashlib
        from pocketchat.office_context import office_memory_action, office_memory_view, update_project_metadata
        from pocketchat import memory_migration as memory
        from pocketchat.office_store import OfficeStore
        with tempfile.TemporaryDirectory() as tmp:
            store = OfficeStore(Path(tmp).resolve() / 'project')
            try:
                project = read_project_metadata(store)
                path = store.root / 'office-memory.v2.jsonl'
                proposed = memory.propose_v2_record(store.root, path, 'Prefer concise replies', workspace_id=project['project_id'], proposed_by='legacy-user')
                digest = hashlib.sha256(memory._store_payload(memory.read_v2_store(store.root, path))).hexdigest()
                with self.assertRaises(OfficeError):
                    office_memory_action(store, 'accept', project['project_id'], digest, memory_id=proposed['memory_id'], revision=1, personal_guidance=True)
                memory.accept_v2_record(store.root, path, proposed['memory_id'], 'legacy-user', verification_status='not_required')
                update_project_metadata(store, project['project_id'], memory_revision=1, memory_enabled=True)
                self.assertEqual(office_memory_view(store, 'concise')['selected'], [])
            finally:
                store.close()

    def test_multiple_pending_revisions_do_not_block_one_accepted_replacement(self):
        from pocketchat.office_context import office_memory_action, office_memory_view
        from pocketchat.office_store import OfficeStore
        with tempfile.TemporaryDirectory() as tmp:
            store = OfficeStore(Path(tmp).resolve() / 'project')
            try:
                view = office_memory_view(store)
                pid = view['project_id']
                proposed = office_memory_action(store, 'propose', pid, view['store_sha256'], content='Concise replies', category='user_preference', personal_guidance=True)
                accepted = office_memory_action(store, 'accept', pid, proposed['store_sha256'], memory_id=proposed['record']['memory_id'], revision=1, personal_guidance=True)
                old = accepted['record']
                first = office_memory_action(store, 'revise', pid, accepted['store_sha256'], memory_id=old['memory_id'], revision=old['revision'], content='Concise Chinese replies', personal_guidance=True)
                second = office_memory_action(store, 'revise', pid, first['store_sha256'], memory_id=old['memory_id'], revision=old['revision'], content='Concise English replies', personal_guidance=True)
                saved = office_memory_action(store, 'accept', pid, second['store_sha256'], memory_id=first['record']['memory_id'], revision=1, personal_guidance=True)
                self.assertEqual(saved['record']['admission_status'], 'accepted')
                competing = next(r for r in saved['records'] if r['memory_id'] == second['record']['memory_id'])
                self.assertEqual(competing['admission_status'], 'candidate')
                with self.assertRaises(OfficeError):
                    office_memory_action(store, 'accept', pid, saved['store_sha256'], memory_id=competing['memory_id'], revision=1, personal_guidance=True)
            finally:
                store.close()


class SummaryOperatorTests(unittest.TestCase):
    def test_explicit_summary_original_readback_revision_and_branch_refusals(self):
        import hashlib
        from pocketchat.office_context import office_summary_view, save_office_summary
        from pocketchat.office_store import OfficeStore
        with tempfile.TemporaryDirectory() as tmp:
            store = OfficeStore(Path(tmp).resolve() / 'project')
            try:
                pid = read_project_metadata(store)['project_id']
                cid = store.create('Long conversation')['id']
                run = store.begin(cid, 'summary-operator-request', dict(prompt='Decision: small first release', intent='chat', source_ids=[], revision_id=None))
                store.finish(run['id'], 'completed', 'Keep it small.', None)
                view = office_summary_view(store, cid)
                self.assertIsNone(view['summary'])
                original = view['original_messages'][0]
                refs = [dict(message_id=original['id'], run_id=run['id'], start=0, end=len(original['content']),
                             sha256=hashlib.sha256(original['content'].encode()).hexdigest())]
                saved = save_office_summary(store, cid, pid, revision=0, content='Decision: release the small result.', refs=refs, accept=True)
                self.assertEqual(saved['summary']['revision'], 1)
                self.assertEqual(office_summary_view(store, cid)['summary'], saved['summary'])
                for changes in [dict(revision=0), dict(accept=False), dict(refs=[dict(refs[0], sha256='0' * 64)]),
                                dict(refs=[dict(refs[0], start=-1)]), dict(content='x' * 12289)]:
                    values = dict(revision=1, content='Decision: small result.', refs=refs, accept=True)
                    values.update(changes)
                    with self.subTest(changes=changes), self.assertRaises(OfficeError):
                        save_office_summary(store, cid, pid, **values)
                self.assertEqual(office_summary_view(store, cid)['summary']['revision'], 1)
                foreign = store.create('Other conversation')['id']
                with self.assertRaises(OfficeError):
                    save_office_summary(store, foreign, pid, revision=0, content='Foreign summary', refs=refs, accept=True)
            finally:
                store.close()
