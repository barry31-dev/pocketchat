"""The bounded task run: one policy, one evidence chain, one terminal answer."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.agent import run_task
from pocketchat.config import ConfigError
from pocketchat.provider import ProviderError, build_payload
from pocketchat.sessions import read_context_messages
from pocketchat.task_policy import parse_policy
from pocketchat.tasking import list_records, load_record


class FakeTaskAdapter:
    name = "task-run-test"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def build_payload(self, messages, config):
        return build_payload(messages, config)

    def complete(self, messages, config, api_key, on_delta=None,
                 prepared_payload=None):
        self.calls.append(list(messages))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def model_tool(call_id, name, arguments):
    return {
        "content": "",
        "finish_reason": "tool_calls",
        "model": "task-model",
        "usage": {"input_tokens": 2, "output_tokens": 1,
                  "cache_read_tokens": 0, "cache_write_tokens": 0,
                  "reasoning_tokens": 0},
        "usage_reported": True,
        "tool_calls": [{"id": call_id, "name": name,
                        "arguments": arguments}],
    }


def final_response():
    return {
        "content": "comparison complete",
        "finish_reason": "stop",
        "model": "task-model",
        "usage": {"input_tokens": 3, "output_tokens": 2,
                  "cache_read_tokens": 0, "cache_write_tokens": 0,
                  "reasoning_tokens": 0},
        "usage_reported": True,
    }


class TaskRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "facts.txt").write_text("the answer\n", encoding="utf-8")

    def policy(self, **overrides):
        payload = {
            "schema_version": "pocketchat-task-policy.v1",
            "objective": "compare allowlisted facts",
            "workspace_root": ".",
            "max_steps": 8,
            "max_tool_calls": 4,
            "max_context_chars": 8000,
            "max_turn_tokens": 8000,
            "tools": {
                "read_file": {"allow_paths": ["facts.txt"]},
                "write_file": {"allow_paths": ["report.md"], "create_only": True},
            },
        }
        payload.update(overrides)
        return parse_policy(payload)

    def config(self, adapter):
        return {
            "profile": "task",
            "model": "task-model",
            "base_url": "http://example.invalid/v1",
            "api_key_env": "TASK_KEY",
            "api_key": "secret-task",
            "requires_api_key": True,
            "timeout_seconds": 5,
            "max_context_chars": 8000,
            "summary_prefix_chars": 80,
            "provider_adapter": adapter,
        }

    def execute(self, adapter, policy=None, **policy_overrides):
        return run_task(
            policy=policy or self.policy(**policy_overrides),
            workspace=self.root,
            config=self.config(adapter),
            trace_path=self.root / "traces" / "task.jsonl",
            session_path=self.root / "sessions" / "task.jsonl",
            prompts=["compare the fact"],
        )

    def events(self):
        path = self.root / "traces" / "task.jsonl"
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines()]

    def test_task_run_writes_report_and_complete_trace(self):
        adapter = FakeTaskAdapter([
            model_tool("call_read", "read_file", {"path": "facts.txt"}),
            final_response(),
        ])
        result = self.execute(adapter)
        self.assertEqual("completed", result.status)
        self.assertEqual("completed", result.record.state)
        self.assertEqual(3, result.report["steps"])
        self.assertEqual(1, result.report["tool_calls"])
        self.assertEqual(result.record.task_id, result.task_id)
        terminal = self.events()[-1]
        self.assertEqual("run.finished", terminal["type"])
        self.assertEqual(result.task_id, terminal["payload"]["task_id"])
        self.assertEqual("completed", terminal["payload"]["task_state"])

    def test_task_run_refuses_tool_outside_allowlist(self):
        adapter = FakeTaskAdapter([
            model_tool("call_bad", "read_file", {"path": "secret.txt"}),
            final_response(),
        ])
        result = self.execute(adapter)
        self.assertEqual("completed", result.status,
                         result.record.terminal_reason)
        tool_steps = [step for step in result.record.steps
                      if step["kind"] == "tool"]
        self.assertEqual(["refused"], [step["state"] for step in tool_steps])
        failures = [event["payload"] for event in self.events()
                    if event["type"] == "tool.call.failed"]
        self.assertEqual("not_allowlisted", failures[0]["error"]["type"])
        self.assertFalse((self.root / "secret.txt").exists())

    def test_task_run_stops_at_tool_call_budget(self):
        adapter = FakeTaskAdapter([
            model_tool("call_1", "read_file", {"path": "facts.txt"}),
            model_tool("call_2", "read_file", {"path": "facts.txt"}),
            final_response(),
        ])
        result = self.execute(adapter, max_tool_calls=1)
        self.assertEqual("failed", result.status)
        self.assertEqual("failed", result.record.state)
        self.assertEqual(1, result.report["tool_calls"])
        self.assertIn("tool_call_budget_exceeded", result.record.terminal_reason)
        self.assertEqual("run.failed", self.events()[-1]["type"])

    def test_task_run_stops_at_step_budget(self):
        adapter = FakeTaskAdapter([
            model_tool("call_1", "read_file", {"path": "facts.txt"}),
            final_response(),
        ])
        result = self.execute(adapter, max_steps=2)
        self.assertEqual("failed", result.status)
        self.assertEqual(2, result.report["steps"])
        self.assertIn("step_budget_exceeded", result.record.terminal_reason)
        self.assertEqual("run.failed", self.events()[-1]["type"])

    def test_task_run_narrows_fallback_tools_to_the_signed_policy(self):
        adapter = FakeTaskAdapter([
            ProviderError("transient", "retry this request", retryable=True),
            model_tool("call_write", "write_file",
                       {"path": "outside.txt", "content": "escaped"}),
            final_response(),
        ])
        config = self.config(adapter)
        config.update({
            "retry_max_attempts": 1,
            "fallback_config": {
                "profile": "fallback",
                "model": "fallback-model",
                "base_url": "http://example.invalid/v1",
                "api_key_env": "FALLBACK_KEY",
                "requires_api_key": False,
                "timeout_seconds": 5,
                "max_context_chars": 8000,
                "summary_prefix_chars": 80,
                "tool_call_max_count": 10,
                "tool_write_enabled": True,
                "tool_write_allow_paths": ["outside.txt"],
            },
        })
        result = run_task(
            policy=self.policy(),
            workspace=self.root,
            config=config,
            trace_path=self.root / "traces" / "task.jsonl",
            session_path=self.root / "sessions" / "task.jsonl",
            prompts=["compare the fact"],
        )
        self.assertEqual("completed", result.status, result.record.terminal_reason)
        tool_steps = [step for step in result.record.steps
                      if step["kind"] == "tool"]
        self.assertEqual(["refused"], [step["state"] for step in tool_steps])
        self.assertFalse((self.root / "outside.txt").exists())

    def test_task_run_records_failure_when_session_cannot_open(self):
        """A task record never stops at running because setup failed."""
        blocked_session = self.root / "sessions" / "task.jsonl"
        blocked_session.mkdir(parents=True)
        adapter = FakeTaskAdapter([])
        with self.assertRaises(ConfigError):
            run_task(
                policy=self.policy(),
                workspace=self.root,
                config=self.config(adapter),
                trace_path=self.root / "traces" / "task.jsonl",
                session_path=blocked_session,
                prompts=["compare the fact"],
            )
        records = list_records(self.root)
        self.assertEqual(1, len(records))
        record = load_record(self.root, records[0])
        self.assertEqual("failed", record.state)
        self.assertIn("session_setup_failed", record.terminal_reason)

    def test_task_run_keeps_cancel_terminal_evidence(self):
        adapter = FakeTaskAdapter([KeyboardInterrupt()])
        result = self.execute(adapter)
        self.assertEqual("cancelled", result.status)
        self.assertEqual("cancelled", result.record.state)
        self.assertTrue(result.record.terminal_reason)
        terminal = self.events()[-1]
        self.assertEqual("run.cancelled", terminal["type"])
        self.assertEqual("cancelled", terminal["payload"]["task_state"])

    def test_task_run_persists_tool_context_for_resume(self):
        adapter = FakeTaskAdapter([
            model_tool("call_read", "read_file", {"path": "facts.txt"}),
            final_response(),
        ])
        result = self.execute(adapter)
        self.assertEqual("completed", result.status)
        restored = read_context_messages(self.root / "sessions" / "task.jsonl")
        self.assertEqual(
            ["user", "assistant", "tool", "assistant"],
            [message["role"] for message in restored[-4:]],
        )
        self.assertEqual("call_read", restored[-3]["tool_calls"][0]["id"])
        self.assertEqual("call_read", restored[-2]["tool_call_id"])
        self.assertIn("the answer", restored[-2]["content"])
        self.assertEqual("comparison complete", restored[-1]["content"])


if __name__ == "__main__":
    unittest.main()
