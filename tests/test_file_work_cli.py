"""Trusted terminal controls over the real, shared file-work service."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pocketchat.file_work import FileWork, FileWorkError
from pocketchat.file_changes import FileChangeError
from pocketchat.file_change_store import require_secure_runtime, FileChangeStoreError
from pocketchat.agent import AgentState
from pocketchat.tui import AgentTUI, compose_screen
from pocketchat.sessions import SessionFile
from pocketchat.session_writer import acquire_session_writer

try:
    require_secure_runtime()
    SECURE_RUNTIME = os.link in os.supports_dir_fd
except FileChangeStoreError:
    SECURE_RUNTIME = False


def config():
    return {'profile': 'local', 'model': 'm',
            'base_url': 'http://example.invalid/v1'}


@unittest.skipUnless(SECURE_RUNTIME, 'secure file runtime unavailable')
class FileTerminalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'facts.txt').write_text('before\n', encoding='utf-8')
        self.work = FileWork(self.root)
        self.work.bind(config(), 'run-one', conversation_id='conversation-one')

    def command(self, text, *, trusted=True, active=None):
        return self.work.terminal_command(text, config=active or config(), trusted=trusted)

    def confirm(self, preview):
        challenge = preview.rsplit('/confirm ', 1)[1].strip()
        return self.command('/confirm ' + challenge)

    def grants(self):
        self.confirm(self.command('/files read facts.txt'))
        self.confirm(self.command('/files write facts.txt'))

    def propose(self, content='after\n'):
        self.work.execute('propose_file_change', {'path': 'facts.txt', 'content': content},
                          config=config(), policy_sha256=self.work.request_config(config())[
                              'file_work_policy_sha256'], run_id='run-one',
                          tool_call_id='call-one', max_bytes=1)
        return self.work.state()['pending']['change']['change_id']

    def test_worker_exit_cancellation_revokes_review_outside_session_run(self):
        import threading
        self.grants()
        identifier = self.propose()
        preview = self.command('/apply ' + identifier)
        started, finish = threading.Event(), threading.Event()
        controller = AgentTUI(AgentState('local', 'm', 'main'), {}, {})
        controller._session = mock.Mock(file_work=self.work)
        def before_run(text):
            started.set()
            finish.wait(3)
            raise KeyboardInterrupt
        controller._send_once = before_run
        def finish_worker():
            finish.set()
            controller._worker.join(3)
        controller._interrupt_worker = finish_worker
        controller.start_send('cancel outside Session.run')
        self.assertTrue(started.wait(1))
        try:
            controller.cancel_send()
            self.assertIsNone(self.work.state()['pending'])
            self.assertEqual([], self.work.state()['write_paths'])
            with self.assertRaises(FileWorkError):
                self.confirm(preview)
            controller._session._release_session_writer.assert_called_once()
        finally:
            finish.set()
            controller.close()
        self.assertEqual('before\n', (self.root / 'facts.txt').read_text())

    def test_cancel_pending_review_invalidates_digest_without_applying(self):
        self.grants()
        identifier = self.propose()
        preview = self.command('/apply ' + identifier)
        self.assertIn('cancelled', self.command('/files cancel'))
        with self.assertRaises(FileWorkError):
            self.confirm(preview)
        self.assertIsNone(self.work.state()['pending'])
        self.assertEqual([], self.work.state()['write_paths'])
        self.assertEqual('before\n', (self.root / 'facts.txt').read_text())
        self.assertEqual('proposed', self.work.store.load_record(identifier)['events'][-1]['type'])

    def test_untrusted_status_or_change_listing_cannot_reveal_or_create_state(self):
        with self.assertRaises(FileWorkError):
            self.command('/files', trusted=False)
        with self.assertRaises(FileWorkError):
            self.command('/changes', trusted=False)
        self.assertFalse((self.root / '.pocketchat').exists())

    def test_disclosure_precedes_grants_and_pipe_cannot_authorize(self):
        preview = self.command('/files read facts.txt')
        self.assertIn('sent to provider', preview)
        self.assertEqual([], self.work.state()['read_paths'])
        challenge = preview.rsplit('/confirm ', 1)[1].strip()
        with self.assertRaises(FileWorkError):
            self.command('/confirm ' + challenge, trusted=False)
        with self.assertRaises(FileWorkError):
            self.command('/files write facts.txt', trusted=False)
        self.confirm(preview)
        self.assertEqual(['facts.txt'], self.work.state()['read_paths'])

    def test_same_session_undo_requires_a_write_grant_after_apply(self):
        self.grants()
        identifier = self.propose()
        self.confirm(self.command('/apply ' + identifier))
        with self.assertRaises(FileWorkError):
            self.command('/undo ' + identifier)
        self.confirm(self.command('/files write facts.txt'))
        undo_preview = self.command('/undo ' + identifier)
        self.assertIn('restore original bytes', undo_preview)

    def test_complete_diff_apply_readback_and_persisted_undo(self):
        self.grants()
        identifier = self.propose('after\n' + 'tail\n' * 900)
        preview = self.command('/apply ' + identifier)
        self.assertIn('-before', preview)
        self.assertEqual(900, preview.count('+tail'))
        self.assertEqual('before\n', (self.root / 'facts.txt').read_text())
        receipt = self.confirm(preview)
        self.assertIn('applied', receipt)
        self.assertEqual('after\n' + 'tail\n' * 900, (self.root / 'facts.txt').read_text())
        self.assertIsNone(self.work.state()['pending'])
        # A new run of the same persisted conversation still requires fresh grants.
        restarted = FileWork(self.root)
        restarted.bind(config(), 'run-two', conversation_id='conversation-one')
        with self.assertRaises(FileWorkError):
            restarted.terminal_command('/undo ' + identifier, config=config(), trusted=True)
        restarted.grant(read_paths=['facts.txt'], write_paths=['facts.txt'],
                        disclosure_sha256=restarted.disclosure()['sha256'])
        undo_preview = restarted.terminal_command('/undo ' + identifier, config=config(), trusted=True)
        challenge = undo_preview.rsplit('/confirm ', 1)[1].strip()
        undone = restarted.terminal_command('/confirm ' + challenge, config=config(), trusted=True)
        self.assertIn('undone', undone)
        self.assertEqual('before\n', (self.root / 'facts.txt').read_text())

    def test_failed_apply_consumes_the_confirmed_pending_proposal(self):
        self.grants()
        identifier = self.propose()
        preview = self.command('/apply ' + identifier)
        (self.root / 'facts.txt').write_text('changed elsewhere\n', encoding='utf-8')
        with self.assertRaises(FileChangeError):
            self.confirm(preview)
        self.assertIsNone(self.work.state()['pending'])
        self.assertIn('propose_file_change',
                      self.work.request_config(config())['file_work_tools'])

    def test_changes_revoke_profile_and_wrong_challenge_do_not_apply(self):
        self.grants()
        identifier = self.propose()
        preview = self.command('/apply ' + identifier)
        with self.assertRaises(FileWorkError):
            self.command('/confirm ' + '0' * 64)
        self.command('/files revoke facts.txt')
        with self.assertRaises(FileWorkError):
            self.confirm(preview)
        self.assertEqual('before\n', (self.root / 'facts.txt').read_text())
        self.grants()
        preview = self.command('/files read facts.txt')
        with self.assertRaises(FileWorkError):
            self.command('/confirm ' + preview.rsplit('/confirm ', 1)[1].strip(),
                         active={**config(), 'model': 'different'})
        self.assertEqual([], self.work.state()['read_paths'])

    def test_undo_other_conversation_is_refused(self):
        self.grants()
        identifier = self.propose()
        self.confirm(self.command('/apply ' + identifier))
        stranger = FileWork(self.root)
        stranger.bind(config(), 'run-two', conversation_id='other-conversation')
        stranger.grant(write_paths=['facts.txt'],
                       disclosure_sha256=stranger.disclosure()['sha256'])
        with self.assertRaises(FileWorkError):
            stranger.terminal_command('/undo ' + identifier, config=config(), trusted=True)
        self.assertEqual('after\n', (self.root / 'facts.txt').read_text())

    def test_diff_controls_are_escaped_without_truncation(self):
        self.grants()
        identifier = self.propose('after\x1b[2J\u202e spoof\u2066\n')
        preview = self.command('/apply ' + identifier)
        self.assertNotIn('\x1b', preview)
        self.assertNotIn('\u202e', preview)
        self.assertNotIn('\u2066', preview)
        self.assertIn('\\x1b', preview)
        self.assertIn('\\u202e', preview)
        self.assertIn('\\u2066', preview)


class FileTerminalControllerTests(unittest.TestCase):
    def test_full_screen_file_review_preserves_diff_line_whitespace(self):
        state = AgentState('local', 'm', 'main')
        controller = AgentTUI(state, {'local': {'model': 'm'}}, {'local': 'm'})
        with mock.patch.object(
                controller, 'run_command',
                return_value='Complete diff:\n context  \n+  indented  '):
            controller.start_send('/diff')
        self.assertEqual('file', controller.transcript[-1][0])
        rows = compose_screen(controller, input_text='', width=80, height=20)
        self.assertTrue(any('    context  ' in row for row in rows), rows)
        self.assertTrue(any('   +  indented  ' in row for row in rows), rows)

    def test_full_screen_palette_exposes_file_review_commands(self):
        state = AgentState('local', 'm', 'main')
        controller = AgentTUI(state, {'local': {'model': 'm'}}, {'local': 'm'})
        self.assertIn('/files', controller.command_names())
        self.assertEqual(['/apply'], controller.command_completions('/ap'))
        self.assertIn('/undo', controller.command_completions('/'))

    def test_file_confirmation_refuses_a_session_changed_between_commands(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        selected = config()
        selected.pop('profile')
        selected['api_key_env'] = 'POCKETCHAT_TEST_KEY'
        active = {'default_profile': 'local', 'profiles': {'local': selected}}
        controller = AgentTUI(
            AgentState('local', 'm', 'main'), {'local': {'model': 'm'}},
            {'local': 'm'}, config=active, workspace=root, trusted_input=True)
        self.addCleanup(controller.close)
        self.assertIn('/confirm ', controller.run_command('/files read facts.txt'))
        session_path = root / 'sessions' / 'main.jsonl'
        writer = acquire_session_writer(session_path, owner='other-terminal')
        external = SessionFile.open(
            session_path, 'ignored', root / 'traces' / 'ignored.jsonl', '1.2.0')
        external.append('user', 'other terminal', writer=writer)
        writer.release()
        self.assertIn('changed', controller.run_command('/files write facts.txt'))

    def test_file_control_refuses_after_session_persistence_is_lost(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        selected = config()
        selected.pop('profile')
        selected['api_key_env'] = 'POCKETCHAT_TEST_KEY'
        controller = AgentTUI(
            AgentState('local', 'm', 'main'), {'local': {'model': 'm'}},
            {'local': 'm'},
            config={'default_profile': 'local', 'profiles': {'local': selected}},
            workspace=root, trusted_input=True)
        self.addCleanup(controller.close)
        self.assertIn('/confirm ', controller.run_command('/files read facts.txt'))
        controller._session.session_file = None
        self.assertIn('persistence', controller.run_command('/files'))

    def test_each_file_command_reacquires_the_session_writer(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        selected = config()
        selected.pop('profile')
        selected['api_key_env'] = 'POCKETCHAT_TEST_KEY'
        active = {
            'default_profile': 'local',
            'profiles': {'local': selected},
        }
        first = AgentTUI(
            AgentState('local', 'm', 'main'), {'local': {'model': 'm'}},
            {'local': 'm'}, config=active, workspace=root, trusted_input=True)
        second = AgentTUI(
            AgentState('local', 'm', 'main'), {'local': {'model': 'm'}},
            {'local': 'm'}, config=active, workspace=root, trusted_input=True)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        self.assertIn('/confirm ', first.run_command('/files read facts.txt'))
        self.assertEqual('', second._prepare_session())
        self.assertIn('writer', first.run_command('/files write facts.txt'))

    def test_untrusted_or_busy_controller_does_not_create_file_service(self):
        state = AgentState('local', 'm', 'main')
        controller = AgentTUI(state, {'local': {'model': 'm'}}, {'local': 'm'})
        self.assertIn('trusted', controller.run_command('/files write facts.txt'))
        controller.trusted_input = True
        controller.busy = True
        with mock.patch('pocketchat.tui.Session') as session:
            self.assertIn('running', controller.run_command('/files write facts.txt'))
        session.assert_not_called()

    def test_untrusted_file_status_does_not_create_session_or_store(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        selected = config()
        selected.pop('profile')
        selected['api_key_env'] = 'POCKETCHAT_TEST_KEY'
        controller = AgentTUI(
            AgentState('local', 'm', 'main'), {'local': {'model': 'm'}},
            {'local': 'm'},
            config={'default_profile': 'local', 'profiles': {'local': selected}},
            workspace=root, trusted_input=False)
        self.assertIn('trusted', controller.run_command('/files'))
        self.assertIsNone(controller._session)
        self.assertEqual([], list(root.iterdir()))


if __name__ == '__main__':
    unittest.main()
