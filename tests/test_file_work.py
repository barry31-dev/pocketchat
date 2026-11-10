"""Explicit host grants and proposal-only file work on real temporary files."""
import copy
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pocketchat.file_work import FileWork, FileWorkError
from pocketchat.file_change_store import require_secure_runtime, FileChangeStoreError

try:
    require_secure_runtime()
    SECURE_RUNTIME = os.link in os.supports_dir_fd
except FileChangeStoreError:
    SECURE_RUNTIME = False


def config():
    return {'profile': 'local', 'model': 'm', 'base_url': 'http://example.invalid/v1'}


class FileWorkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'facts.txt').write_text('before\n', encoding='utf-8')
        self.work = FileWork(self.root)
        self.work.bind(config(), 'session-one')

    def grant(self, read=(), write=()):
        self.work.grant(read_paths=read, write_paths=write,
                        disclosure_sha256=self.work.disclosure()['sha256'])

    def execute(self, name, arguments, request=None, active=None):
        request = request or self.work.request_config(config())
        return self.work.execute(name, arguments, config=active or config(),
                                 policy_sha256=request['file_work_policy_sha256'],
                                 run_id='session-one', tool_call_id='call-one',
                                 max_bytes=4096)

    def test_no_implicit_profile_grants_or_store_creation(self):
        self.assertEqual([], self.work.request_config(config())['file_work_tools'])
        with self.assertRaises(FileWorkError):
            self.execute('read_file', {'path': 'facts.txt'})
        self.assertFalse((self.root / '.pocketchat').exists())

    def test_disclosure_must_match_the_bound_provider(self):
        notice = self.work.disclosure()
        self.assertIn('http://example.invalid/v1', notice['text'])
        self.assertIn('sent', notice['text'])
        with self.assertRaises(FileWorkError):
            self.work.grant(read_paths=['facts.txt'], disclosure_sha256='0' * 64)
        self.grant(read=['facts.txt'])
        self.assertEqual(['read_file'], self.work.request_config(config())['file_work_tools'])
        if SECURE_RUNTIME:
            self.assertEqual('before\n', self.execute('read_file', {'path': 'facts.txt'})['content'])
        else:
            with self.assertRaises(ValueError) as caught:
                self.execute('read_file', {'path': 'facts.txt'})
            self.assertEqual('file_unsupported_runtime', caught.exception.error_type)

    def test_grants_reject_sensitive_directory_glob_and_escape_paths(self):
        (self.root / 'folder').mkdir()
        for path in ('.pocketchat/changes/x', '.env', '.git/config', '../facts.txt',
                     '/facts.txt', 'folder', '*.txt', 'facts.txt/**', ''):
            with self.subTest(path=path), self.assertRaises(FileWorkError):
                self.grant(read=[path])
        self.assertEqual([], self.work.state()['read_paths'])

    @unittest.skipUnless(SECURE_RUNTIME, 'secure descriptor-relative file work required')
    def test_proposal_is_durable_complete_and_never_applies(self):
        self.grant(read=['facts.txt'], write=['facts.txt', 'new.txt'])
        result = self.execute('propose_file_change', {'path': 'facts.txt', 'content': 'after\n'})
        pending = self.work.state()['pending']
        self.assertEqual('pending', result['status'])
        self.assertEqual('before\n', (self.root / 'facts.txt').read_text())
        self.assertIn('-before', pending['diff'])
        self.assertIn('+after', pending['diff'])
        change = self.work.store.load_record(pending['change']['change_id'])
        self.assertIsNone(change['approval'])
        self.assertEqual('proposed', change['events'][-1]['type'])
        self.assertEqual(hashlib.sha256(b'before\n').hexdigest(), change['before']['sha256'])
        with self.assertRaises(FileWorkError):
            self.execute('propose_file_change', {'path': 'new.txt', 'content': 'new'})
        self.assertFalse((self.root / 'new.txt').exists())
        altered = self.work.state(); altered['pending']['diff'] = 'forged'
        self.assertEqual(pending['diff'], self.work.state()['pending']['diff'])

    @unittest.skipUnless(SECURE_RUNTIME, 'secure descriptor-relative file work required')
    def test_replace_requires_read_and_create_does_not_grant_read(self):
        self.grant(write=['facts.txt', 'new.txt'])
        with self.assertRaises(ValueError):
            self.execute('propose_file_change', {'path': 'facts.txt', 'content': 'after'})
        self.execute('propose_file_change', {'path': 'new.txt', 'content': 'new'})
        with self.assertRaises(FileWorkError):
            self.execute('read_file', {'path': 'facts.txt'})
        self.assertFalse((self.root / 'new.txt').exists())

    @unittest.skipUnless(SECURE_RUNTIME, 'secure descriptor-relative file work required')
    def test_revocation_invalidates_request_policy_and_pending(self):
        self.grant(read=['facts.txt'], write=['facts.txt'])
        request = self.work.request_config(config())
        self.execute('propose_file_change', {'path': 'facts.txt', 'content': 'after'})
        saved = copy.deepcopy(self.work.state()['pending'])
        self.work.revoke(['facts.txt'])
        self.assertIsNone(self.work.state()['pending'])
        self.assertEqual('proposed', self.work.store.load_record(saved['change']['change_id'])['events'][-1]['type'])
        with self.assertRaises(FileWorkError):
            self.execute('read_file', {'path': 'facts.txt'}, request=request)

    def test_profile_change_cannot_send_old_grants_to_fallback(self):
        self.grant(read=['facts.txt'])
        changed = {**config(), 'base_url': 'http://other.invalid/v1'}
        with self.assertRaises(FileWorkError):
            self.work.request_config(changed)
        self.assertEqual([], self.work.state()['read_paths'])

    def test_session_rebinding_and_workspace_replacement_refuse(self):
        with self.assertRaises(FileWorkError):
            self.work.bind(config(), 'another-session')
        self.grant(read=['facts.txt'])
        old = self.root.with_name(self.root.name + '-old')
        self.root.rename(old)
        self.root.mkdir()
        self.addCleanup(lambda: old.rename(self.root) if not self.root.exists() else None)
        try:
            with self.assertRaises(FileWorkError):
                self.work.request_config(config())
        finally:
            self.root.rmdir(); old.rename(self.root)

    def test_model_cannot_self_approve_apply_undo_or_grant(self):
        self.grant(read=['facts.txt'], write=['facts.txt'])
        for name in ('write_file', 'apply', 'undo', 'grant', 'approve'):
            with self.subTest(name=name), self.assertRaises(FileWorkError):
                self.execute(name, {'path': 'facts.txt', 'content': 'after'})
        self.assertEqual('before\n', (self.root / 'facts.txt').read_text())

    def test_revoke_does_not_depend_on_target_still_being_a_file(self):
        self.grant(read=['facts.txt'], write=['facts.txt'])
        (self.root / 'facts.txt').unlink()
        (self.root / 'facts.txt').mkdir()
        self.work.revoke(['facts.txt'])
        self.assertEqual([], self.work.state()['read_paths'])
        self.assertEqual([], self.work.state()['write_paths'])

    @unittest.skipUnless(os.name != 'nt', 'local symlink fixture')
    def test_workspace_replaced_by_symlink_to_same_inode_is_stale(self):
        self.grant(read=['facts.txt'])
        old = self.root.with_name(self.root.name + '-old')
        self.root.rename(old)
        self.root.symlink_to(old, target_is_directory=True)
        try:
            with self.assertRaises(FileWorkError):
                self.work.request_config(config())
        finally:
            self.root.unlink(); old.rename(self.root)

    @unittest.skipUnless(SECURE_RUNTIME, 'secure descriptor-relative file work required')
    def test_symlink_cannot_read_internal_store_or_outside_the_workspace(self):
        secret_dir = self.root / '.pocketchat'
        secret_dir.mkdir()
        (secret_dir / 'secret.txt').write_text('secret', encoding='utf-8')
        (self.root / 'alias.txt').symlink_to(secret_dir / 'secret.txt')
        self.grant(read=['alias.txt'])
        with self.assertRaises(ValueError):
            self.execute('read_file', {'path': 'alias.txt'})

    @unittest.skipUnless(SECURE_RUNTIME, 'secure descriptor-relative file work required')
    def test_opened_root_descriptor_must_match_the_granted_identity(self):
        self.grant(read=['facts.txt'])
        other = self.root / 'replacement'
        other.mkdir()
        (other / 'facts.txt').write_text('not authorized', encoding='utf-8')
        real_open = os.open
        def replaced(path, flags, mode=0o777, *, dir_fd=None):
            if dir_fd is None and str(path) == str(self.work.workspace):
                return real_open(str(other), flags, mode)
            if dir_fd is None:
                return real_open(path, flags, mode)
            return real_open(path, flags, mode, dir_fd=dir_fd)
        supports = os.supports_dir_fd | {replaced}
        with mock.patch.object(os, 'open', replaced), mock.patch.object(os, 'supports_dir_fd', supports):
            with self.assertRaises(FileWorkError):
                self.execute('read_file', {'path': 'facts.txt'})
