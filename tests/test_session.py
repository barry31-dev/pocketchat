"""Completed tool interactions remain part of the resumable context."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.branching import create_branch_from
from pocketchat.config import ConfigError
from pocketchat.context import context_chars
from pocketchat.provider import build_payload
from pocketchat.sessions import read_turns
from pocketchat.session import Session
from pocketchat.service import context_preview, export_session


class FakeContextAdapter:
    name = "context-persistence-test"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def build_payload(self, messages, config):
        return build_payload(messages, config)

    def complete(self, messages, config, api_key, on_delta=None,
                 prepared_payload=None):
        self.calls.append(list(messages))
        return self.responses.pop(0)


def tool_response():
    return {
        "content": "",
        "finish_reason": "tool_calls",
        "model": "m",
        "usage": {"input_tokens": 2, "output_tokens": 1,
                  "cache_read_tokens": 0, "cache_write_tokens": 0,
                  "reasoning_tokens": 0},
        "usage_reported": True,
        "tool_calls": [{
            "id": "call_ctx",
            "name": "read_file",
            "arguments": {"path": "facts.txt"},
        }],
    }


def text_response(content="final answer"):
    return {
        "content": content,
        "finish_reason": "stop",
        "model": "m",
        "usage": {"input_tokens": 3, "output_tokens": 2,
                  "cache_read_tokens": 0, "cache_write_tokens": 0,
                  "reasoning_tokens": 0},
        "usage_reported": True,
    }


class SessionToolContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "facts.txt").write_text("the answer\n", encoding="utf-8")
        self.session_path = self.root / "sessions" / "notes.jsonl"

    def config(self, adapter):
        return {
            "profile": "ctx", "model": "m",
            "base_url": "http://example.invalid/v1", "api_key_env": "K",
            "timeout_seconds": 5, "max_context_chars": 1000,
            "summary_prefix_chars": 40, "requires_api_key": True,
            "provider_adapter": adapter,
            "tool_read_enabled": True,
            "tool_read_allow_paths": ["facts.txt"],
            "tool_call_max_count": 2,
            "tool_result_max_bytes": 4096,
        }

    def run_tool_turn(self):
        adapter = FakeContextAdapter([tool_response(), text_response()])
        session = Session(
            self.config(adapter), self.root / "traces" / "run.jsonl",
            "run_context", session_path=self.session_path,
            api_key="secret-context")
        self.assertEqual("completed", session.run(["read the fact"]))
        return adapter

    def test_tool_context_is_recorded_and_resumed_by_a_new_process(self):
        self.run_tool_turn()
        records = [json.loads(line) for line in
                   self.session_path.read_text(encoding="utf-8").splitlines()]
        assistant = records[-1]
        self.assertEqual("assistant", assistant["role"])
        self.assertEqual(["assistant", "tool"],
                         [item["role"] for item in assistant["tool_context"]])
        self.assertEqual(assistant["tool_context"],
                         read_turns(self.session_path)[-1]["tool_context"])

        resumed_adapter = FakeContextAdapter([text_response("second answer")])
        resumed = Session(
            self.config(resumed_adapter), self.root / "traces" / "other.jsonl",
            "run_other", session_path=self.session_path,
            api_key="secret-context")
        self.assertEqual("completed", resumed.run(["what did you read?"]))
        restored = resumed_adapter.calls[0]
        self.assertTrue(any(message.get("tool_calls")
                            for message in restored))
        self.assertTrue(any(message.get("role") == "tool"
                            and "the answer" in message.get("content", "")
                            for message in restored))

    def test_next_prompt_in_the_same_session_keeps_the_tool_context(self):
        adapter = FakeContextAdapter([
            tool_response(), text_response("first answer"),
            text_response("second answer"),
        ])
        session = Session(
            self.config(adapter), self.root / "traces" / "same.jsonl",
            "run_same", session_path=self.session_path,
            api_key="secret-context")
        self.assertEqual("completed", session.run(["first", "second"]))
        self.assertTrue(any(message.get("tool_calls")
                            for message in adapter.calls[2]))

    def test_branch_copy_keeps_the_tool_context(self):
        self.run_tool_turn()
        create_branch_from(
            self.session_path, "turn_000002", name="branch",
            sessions_dir=str(self.root / "sessions"))
        branch_adapter = FakeContextAdapter([text_response("branch answer")])
        branch = Session(
            self.config(branch_adapter), self.root / "traces" / "branch.jsonl",
            "run_branch", session_path=self.root / "sessions" / "branch.jsonl",
            api_key="secret-context")
        self.assertEqual("completed", branch.run(["continue"]))
        restored = branch_adapter.calls[0]
        self.assertTrue(any(message.get("tool_calls")
                            for message in restored))

    def test_tool_call_arguments_count_toward_context_chars(self):
        message = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_ctx",
                "type": "function",
                "function": {"name": "read_file",
                             "arguments": "{\"path\":\"facts.txt\"}"},
            }],
        }
        self.assertGreater(context_chars([message]), 0)

    def test_context_preview_includes_persisted_tool_messages(self):
        self.run_tool_turn()
        result = context_preview(
            self.session_path, workspace=self.root, budget_chars=1000)
        self.assertEqual(["user", "assistant", "tool", "assistant"],
                         [message["role"] for message in result["messages"]])
        self.assertGreater(result["messages"][1]["chars"], 0)

    def test_session_export_contains_the_restored_tool_messages(self):
        self.run_tool_turn()
        out = self.root / "session.export.jsonl"
        result = export_session(
            self.session_path, "jsonl", out,
            sessions_dir=str(self.root / "sessions"), workspace=self.root)
        self.assertEqual(4, result["messages"])
        records = [json.loads(line) for line in
                   out.read_text(encoding="utf-8").splitlines()]
        roles = [record["role"] for record in records
                 if record.get("kind") == "message"]
        self.assertEqual(["user", "assistant", "tool", "assistant"], roles)

    def test_legacy_messages_without_tool_context_still_resume(self):
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_text("\n".join([
            json.dumps({"session": "run_old", "trace": "../traces/old.jsonl",
                        "created": "c", "pocketchat_version": "0.3.0"}),
            json.dumps({"role": "user", "content": "hello"}),
            json.dumps({"role": "assistant", "content": "hi"}),
        ]) + "\n", encoding="utf-8")
        adapter = FakeContextAdapter([text_response("next")])
        session = Session(
            self.config(adapter), self.root / "traces" / "legacy.jsonl",
            "run_next", session_path=self.session_path,
            api_key="secret-context")
        self.assertEqual("completed", session.run(["continue"]))
        self.assertNotIn("tool_calls", str(adapter.calls[0]))

    def test_unpaired_tool_context_is_refused(self):
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_text("\n".join([
            json.dumps({"session": "run_bad", "trace": "../traces/bad.jsonl",
                        "created": "c", "pocketchat_version": "0.3.0"}),
            json.dumps({"role": "user", "content": "hello"}),
            json.dumps({
                "role": "assistant",
                "content": "hi",
                "tool_context": [{
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_missing",
                        "type": "function",
                        "function": {"name": "read_file",
                                     "arguments": "{}"},
                    }],
                }],
            }),
        ]) + "\n", encoding="utf-8")
        with self.assertRaises(ConfigError):
            Session(
                self.config(FakeContextAdapter([])),
                self.root / "traces" / "bad.jsonl",
                "run_bad_next", session_path=self.session_path,
                api_key="secret-context")


if __name__ == "__main__":
    unittest.main()


class OfficePreparedTurnTests(unittest.TestCase):
    def test_host_prepared_turn_bypasses_legacy_projection_and_matches_stream_snapshot(self):
        from pocketchat.office_context import PreparedOfficeTurn
        from pocketchat.request import request_digest
        from unittest.mock import patch
        config = dict(model='m', base_url='http://example.invalid/v1', api_key_env='OFFICE_TEST_KEY', requires_api_key=False,
                      max_context_chars=2000, summary_prefix_chars=40, timeout_seconds=5, system_prompt='must not be appended',
                      tool_read_enabled=False, tool_write_enabled=False, memory_recall_enabled=False,
                      context_projection_enabled=False, retry_max_attempts=1, fallback_config=None)
        adapter = FakeContextAdapter([text_response('Done')])
        config['provider_adapter'] = adapter
        messages = [dict(role='system', content='Fixed office safety'), dict(role='user', content='Exact pending request')]
        payload = dict(adapter.build_payload(messages, config), stream=True)
        plan = PreparedOfficeTurn.create(messages, payload, {'project_id': 'project-' + 'a' * 32})
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(config, Path(tmp) / 'prepared.jsonl', 'office-prepared', on_delta=lambda *args: None)
            with patch.object(session, '_context_with_budget', side_effect=AssertionError('second projection')):
                self.assertEqual(session.run_prepared(plan), 'completed')
            self.assertEqual(adapter.calls, [messages])
            snapshot = json.loads(session.request_snapshots[0].read_text())
            self.assertEqual(request_digest(snapshot['request']), request_digest(payload))
            self.assertTrue(snapshot['request']['stream'])

    def test_tampered_plan_or_unsafe_config_refuses_before_provider(self):
        from pocketchat.office_context import PreparedOfficeTurn
        config = dict(model='m', base_url='http://example.invalid/v1', api_key_env='OFFICE_TEST_KEY', requires_api_key=False,
                      max_context_chars=2000, summary_prefix_chars=40, timeout_seconds=5, system_prompt='',
                      tool_read_enabled=False, tool_write_enabled=False, memory_recall_enabled=False,
                      context_projection_enabled=False, retry_max_attempts=1, fallback_config=None)
        adapter = FakeContextAdapter([text_response()])
        config['provider_adapter'] = adapter
        messages = [dict(role='user', content='Pending')]
        plan = PreparedOfficeTurn.create(messages, adapter.build_payload(messages, config), {'project_id': 'project-' + 'a' * 32})
        with tempfile.TemporaryDirectory() as tmp:
            for changes in [dict(tool_read_enabled=True), dict(retry_max_attempts=2), dict(fallback_config=config)]:
                session = Session(dict(config, **changes), Path(tmp) / (str(len(adapter.calls)) + '.jsonl'))
                with self.assertRaises((ValueError, RuntimeError)):
                    session.run_prepared(plan)
            session = Session(config, Path(tmp) / 'tampered.jsonl')
            with self.assertRaises((ValueError, RuntimeError)):
                session.run_prepared({'payload': plan.payload})
            self.assertEqual(adapter.calls, [])

    def test_prepared_snapshot_failure_or_payload_mismatch_never_sends(self):
        from pocketchat.office_context import PreparedOfficeTurn
        from unittest.mock import patch
        config = dict(model='m', base_url='http://example.invalid/v1', api_key_env='OFFICE_TEST_KEY',
                      requires_api_key=False, max_context_chars=2000, timeout_seconds=5,
                      summary_prefix_chars=40, system_prompt='', retry_max_attempts=1)
        adapter = FakeContextAdapter([text_response()])
        config['provider_adapter'] = adapter
        messages = [dict(role='user', content='Exact pending request')]
        payload = adapter.build_payload(messages, config)
        plan = PreparedOfficeTurn.create(messages, payload, {'project_id': 'project-' + 'a' * 32})
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(config, Path(tmp) / 'write-failure.jsonl')
            with patch.object(session, '_write_request_snapshot', side_effect=OSError('disk full')):
                self.assertEqual(session.run_prepared(plan), 'failed')
            self.assertEqual(adapter.calls, [])
            changed = dict(payload, temperature=0.123)
            bad = PreparedOfficeTurn.create(messages, changed, {'project_id': 'project-' + 'a' * 32})
            session = Session(config, Path(tmp) / 'payload-mismatch.jsonl')
            self.assertEqual(session.run_prepared(bad), 'failed')
            self.assertEqual(adapter.calls, [])

    def test_failed_or_cancelled_prepared_session_is_consumed_and_cannot_replay(self):
        from pocketchat.office_context import PreparedOfficeTurn
        from pocketchat.provider import ProviderError
        from unittest.mock import patch
        config = dict(model='m', base_url='http://example.invalid/v1', api_key_env='OFFICE_TEST_KEY',
                      requires_api_key=False, max_context_chars=2000, timeout_seconds=5,
                      summary_prefix_chars=40, system_prompt='', retry_max_attempts=1)
        with tempfile.TemporaryDirectory() as tmp:
            for index, effect in enumerate([ProviderError('timeout', 'ambiguous'), KeyboardInterrupt()]):
                adapter = FakeContextAdapter([text_response()])
                config['provider_adapter'] = adapter
                messages = [dict(role='user', content='Exact pending request')]
                plan = PreparedOfficeTurn.create(messages, adapter.build_payload(messages, config), {'project_id': 'project-' + 'a' * 32})
                session = Session(config, Path(tmp) / ('consumed-%d.jsonl' % index))
                with patch.object(adapter, 'complete', side_effect=effect) as complete:
                    self.assertIn(session.run_prepared(plan), ('failed', 'cancelled'))
                    self.assertEqual(complete.call_count, 1)
                    with self.assertRaises(ValueError):
                        session.run_prepared(plan)
                    self.assertEqual(complete.call_count, 1)
