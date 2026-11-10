"""Real Session pairing and provider offers for the opt-in file-work facade."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pocketchat.config import ConfigError
from pocketchat.file_change_store import require_secure_runtime, FileChangeStoreError
from pocketchat.file_work import FileWork
from pocketchat.provider import build_payload, tool_offer_summary
from pocketchat.session import Session
from pocketchat.task_policy import parse_policy, TaskPolicyError
from pocketchat.tool_contracts import contract_names, parse_arguments, ToolContractError
from test_tool_calls import FakeToolAdapter, multi_tool_response, text_response

try:
    require_secure_runtime()
    SECURE_RUNTIME = os.link in os.supports_dir_fd
except FileChangeStoreError:
    SECURE_RUNTIME = False


class FileWorkProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'facts.txt').write_text('before\n', encoding='utf-8')

    def config(self, adapter):
        return {'profile': 'local', 'model': 'm', 'base_url': 'http://example.invalid/v1',
                'api_key_env': 'K', 'timeout_seconds': 5, 'max_context_chars': 40000,
                'summary_prefix_chars': 40, 'requires_api_key': False,
                'provider_adapter': adapter, 'tool_call_max_count': 4,
                'tool_result_max_bytes': 4096, 'tool_read_enabled': True,
                'tool_read_allow_paths': ['facts.txt'], 'tool_write_enabled': True,
                'tool_write_allow_paths': ['new.txt']}

    def session(self, responses):
        adapter = FakeToolAdapter(responses)
        work = FileWork(self.root)
        session = Session(self.config(adapter), self.root / 'traces' / 'file.jsonl',
                          run_id='session-one', file_work=work)
        return session, work, adapter

    def grant(self, work):
        work.grant(read_paths=['facts.txt'], write_paths=['facts.txt', 'new.txt'],
                   disclosure_sha256=work.disclosure()['sha256'])

    def test_new_contract_does_not_expand_legacy_task_tool_inventory(self):
        self.assertEqual(('read_file', 'write_file', 'memory_search'), contract_names())
        self.assertEqual({'path': 'facts.txt', 'content': 'after'}, parse_arguments(
            'propose_file_change', {'path': 'facts.txt', 'content': 'after'}))
        for extra in ('approval_id', 'apply', 'grant'):
            with self.subTest(extra=extra), self.assertRaises(ToolContractError):
                parse_arguments('propose_file_change', {'path': 'facts.txt', 'content': 'after', extra: True})
        policy = {'schema_version': 'pocketchat-task-policy.v1', 'objective': 'change',
                  'max_steps': 1, 'max_tool_calls': 1, 'max_context_chars': 100,
                  'max_turn_tokens': 100,
                  'tools': {'propose_file_change': {'allow_paths': ['facts.txt']}}}
        with self.assertRaises(TaskPolicyError):
            parse_policy(policy)

    def test_opt_in_mode_hides_immediate_write_and_all_implicit_grants(self):
        session, work, _ = self.session([])
        payload = build_payload([], work.request_config(session.config))
        self.assertNotIn('tools', payload)
        self.grant(work)
        names = [x['function']['name'] for x in build_payload(
            [], work.request_config(session.config))['tools']]
        self.assertEqual(['read_file', 'propose_file_change'], names)
        self.assertEqual(names, [x['name'] for x in tool_offer_summary(work.request_config(session.config))])
        self.assertEqual(names, [x['name'] for x in session.config_summary()['tools']])

    @unittest.skipUnless(SECURE_RUNTIME, 'secure descriptor-relative file work required')
    def test_proposal_is_paired_pending_and_never_applied_by_model_completion(self):
        session, work, adapter = self.session([
            multi_tool_response(('proposal', 'propose_file_change', {'path': 'facts.txt', 'content': 'after\n'})),
            text_response()])
        self.grant(work)
        self.assertEqual('completed', session.run(['change the file']))
        self.assertEqual('final answer', session.last_reply())
        tools = [m for m in adapter.calls[-1] if m['role'] == 'tool']
        self.assertEqual(1, len(tools))
        self.assertEqual('proposal', tools[0]['tool_call_id'])
        result = json.loads(tools[0]['content'])
        self.assertEqual('pending', result['status'])
        self.assertEqual('pending', result['effect_disposition'])
        reference = json.loads(result['content'])
        self.assertEqual(work.state()['pending']['change']['change_id'], reference['change_id'])
        self.assertEqual('before\n', (self.root / 'facts.txt').read_text())
        self.assertEqual(['read_file'], work.request_config(session.config)['file_work_tools'])
        self.assertIsNone(work.state()['pending']['change']['approval'])
        events = [json.loads(line) for line in session.trace_path.read_text().splitlines()]
        completed = [e for e in events if e['type'] == 'tool.call.completed']
        self.assertEqual(1, len(completed))
        self.assertNotIn('effect_disposition', completed[0]['payload'])
        self.assertEqual('proposed', completed[0]['payload']['file_change_status'])
        self.assertEqual(reference['change_id'], completed[0]['payload']['change_id'])
        schema = json.loads((Path(__file__).resolve().parents[1] / 'pocketchat/schemas/trace.schema.json').read_text())
        for event in completed:
            disposition = event['payload'].get('effect_disposition')
            if disposition is not None:
                self.assertIn(disposition, schema['$defs']['effect_disposition']['enum'])

    @unittest.skipUnless(SECURE_RUNTIME, 'secure descriptor-relative file work required')
    def test_two_proposals_and_immediate_write_have_paired_zero_effect_refusals(self):
        session, work, adapter = self.session([
            multi_tool_response(
                ('one', 'propose_file_change', {'path': 'facts.txt', 'content': 'after'}),
                ('two', 'propose_file_change', {'path': 'new.txt', 'content': 'new'}),
                ('write', 'write_file', {'path': 'new.txt', 'content': 'written'})), text_response()])
        self.grant(work)
        session.run(['change two files'])
        results = [json.loads(m['content']) for m in adapter.calls[-1] if m['role'] == 'tool']
        self.assertEqual(['pending', 'failed', 'failed'], [r['status'] for r in results])
        self.assertEqual(['one', 'two', 'write'], [r['tool_call_id'] for r in results])
        self.assertEqual('file_pending_proposal', results[1]['error']['type'])
        self.assertEqual('unsupported_tool', results[2]['error']['type'])
        self.assertEqual('none', results[2]['effect_disposition'])
        self.assertFalse((self.root / 'new.txt').exists())

    def test_revocation_between_provider_request_and_tool_call_refuses(self):
        session, work, adapter = self.session([
            multi_tool_response(('read', 'read_file', {'path': 'facts.txt'})), text_response()])
        self.grant(work)
        original = adapter.complete
        def revoked(*args, **kwargs):
            work.revoke(['facts.txt'])
            return original(*args, **kwargs)
        with mock.patch.object(adapter, 'complete', side_effect=revoked):
            session.run(['read'])
        results = [json.loads(m['content']) for m in adapter.calls[-1] if m['role'] == 'tool']
        self.assertEqual('file_stale_grants', results[0]['error']['type'])
        self.assertNotIn('before', results[0]['content'] if 'content' in results[0] else '')

    def test_raw_profile_flag_cannot_activate_file_work_without_host_service(self):
        adapter = FakeToolAdapter([text_response()])
        config = self.config(adapter)
        config['file_work_tools'] = ['propose_file_change']
        session = Session(config, self.root / 'plain.jsonl', run_id='plain')
        session.run(['hello'])
        snapshot = json.loads(session.request_snapshots[0].read_text())
        # Snapshot envelopes own the exact payload, not a reconstructed offer.
        self.assertNotIn('propose_file_change', json.dumps(snapshot))

    def test_provider_change_after_request_stops_before_new_destination(self):
        session, work, adapter = self.session([
            multi_tool_response(('read', 'read_file', {'path': 'facts.txt'})), text_response()])
        self.grant(work)
        original = adapter.complete
        def changed(*args, **kwargs):
            session.config['base_url'] = 'http://another.invalid/v1'
            return original(*args, **kwargs)
        with mock.patch.object(adapter, 'complete', side_effect=changed):
            session.run(['read'])
        self.assertEqual(1, len(adapter.calls))
        self.assertIsNone(work.state()['pending'])
        self.assertEqual('file_stale_grants', session.last_error['type'])

    def test_same_run_id_does_not_let_another_session_import_the_grants(self):
        session, work, _ = self.session([])
        self.grant(work)
        with self.assertRaises(ConfigError):
            Session(session.config, self.root / 'another.jsonl',
                    run_id='session-one', file_work=work)

    @unittest.skipUnless(SECURE_RUNTIME, 'secure descriptor-relative file work required')
    def test_proposal_owner_records_actual_persisted_conversation_and_branch(self):
        adapter = FakeToolAdapter([
            multi_tool_response(('proposal', 'propose_file_change', {'path': 'facts.txt', 'content': 'after'})),
            text_response()])
        work = FileWork(self.root)
        session = Session(self.config(adapter), self.root / 'trace.jsonl',
                          session_path=self.root / 'session.jsonl', file_work=work)
        self.grant(work)
        session.run(['change'])
        owner = work.state()['pending']['change']['owner']
        self.assertEqual(session.session_file.conversation_id, owner['conversation_id'])
        self.assertEqual(session.session_file.branch_id, owner['branch'])
