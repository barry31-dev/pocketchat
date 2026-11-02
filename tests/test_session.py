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
