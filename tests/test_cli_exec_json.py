"""JSON contracts for the commands that create or execute work.

`show` and `export` have their own machine-readable contract. This module
closes the remaining CLI gap: `init`, `run`, `chat` and `branch` must also be
safe to consume from scripts without mixing human text into stdout or leaking
workspace-absolute artifact paths.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path


from pocketchat.recovery import create_task_checkpoint
from pocketchat.task_policy import parse_policy
from pocketchat.tasking import (create_record, record_step, save_record,
                                transition)
from tests import _helpers as hp
from tests._helpers import run_cli


class StubProviderHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = json.dumps({
            "id": "cmpl-cli-json",
            "model": "stub-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "stub reply"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class CliExecJsonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self._stub = hp.StubHTTPServer(StubProviderHandler)
        self.addCleanup(self._stub.close)

    def _config(self):
        path = self.base / "pocketchat.json"
        path.write_text(json.dumps({
            "schema_version": "2",
            "name": "exec-json",
            "default_profile": "local",
            "profiles": {"local": {
                "base_url": "http://127.0.0.1:%d/v1"
                            % self._stub.port,
                "model": "stub-model",
                "api_key_env": "POCKETCHAT_API_KEY",
            }},
        }), encoding="utf-8")
        return path

    def test_init_json_reports_the_written_path_and_default_human_output(self):
        result = run_cli(("init", "--json"), self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {
            "ok": True,
            "schema_version": "1",
            "path": "pocketchat.json",
            "created": True,
        })
        human = run_cli(("init", "--force"), self.base)
        self.assertEqual(human.returncode, 0, human.stdout + human.stderr)
        self.assertIn("wrote", human.stdout)

    def test_init_json_refusal_is_stable_and_stderr_is_empty(self):
        self._config()
        result = run_cli(("init", "--json"), self.base)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {
            "ok": False,
            "schema_version": "1",
            "error": "config",
            "message": "pocketchat.json already exists (use --force to overwrite)",
        })

    def test_init_json_force_reports_created_false(self):
        target = self._config()
        before = target.read_bytes()
        result = run_cli(("init", "--force", "--json"), self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {
            "ok": True,
            "schema_version": "1",
            "path": "pocketchat.json",
            "created": False,
        })
        self.assertNotEqual(target.read_bytes(), before)

    def test_run_json_is_one_document_with_reply_and_relative_trace(self):
        config = self._config()
        result = run_cli(
            ("run", "hello", "--config", str(config), "--json"), self.base,
            env={"POCKETCHAT_API_KEY": "test-key-not-real"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["schema_version"], "1")
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["reply"], "stub reply")
        self.assertEqual(payload["turns"], 1)
        self.assertTrue(payload["trace"].startswith("traces/"))
        self.assertNotIn(str(self.base), result.stdout)

    def test_run_json_preparation_failure_is_machine_readable(self):
        result = run_cli(
            ("run", "hello", "--config", "missing.json", "--json"), self.base,
            env={"POCKETCHAT_API_KEY": "test-key-not-real"})
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["schema_version"], "1")
        self.assertEqual(payload["error"], "config")
        self.assertIn("message", payload)

    def test_compare_json_accepts_profiles_that_require_no_api_key(self):
        config = self.base / "pocketchat-nokey.json"
        config.write_text(json.dumps({
            "schema_version": "2",
            "name": "exec-json-nokey",
            "default_profile": "local",
            "profiles": {
                "local": {
                    "base_url": "http://127.0.0.1:%d/v1" % self._stub.port,
                    "model": "stub-model",
                    "api_key_env": "",
                    "requires_api_key": False,
                },
                "second": {
                    "base_url": "http://127.0.0.1:%d/v1" % self._stub.port,
                    "model": "stub-model",
                    "api_key_env": "",
                    "requires_api_key": False,
                },
            },
        }), encoding="utf-8")
        result = run_cli(
            ("compare", "hello", "--config", str(config), "--profiles",
             "local,second", "--json"),
            self.base,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("comparison_id", payload)
        self.assertIn("manifest", payload)
        self.assertEqual([run["profile"] for run in payload["runs"]],
                         ["local", "second"])
        self.assertEqual([run["status"] for run in payload["runs"]],
                         ["completed", "completed"])

    def test_run_json_provider_failure_is_one_exit_one_document(self):
        config = self._config()
        self._stub.close()
        result = run_cli(
            ("run", "hello", "--config", str(config), "--json"), self.base,
            env={"POCKETCHAT_API_KEY": "test-key-not-real"})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["schema_version"], "1")
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "run_failed")
        self.assertIsNone(payload["reply"])
        self.assertTrue(payload["trace"].startswith("traces/"))
        self.assertNotIn(str(self.base), result.stdout)

    def test_chat_json_suppresses_replies_and_reports_the_run(self):
        config = self._config()
        result = run_cli(
            ("chat", "--session", "notes", "--config", str(config), "--json"),
            self.base, stdin="hello\n/exit\n",
            env={"POCKETCHAT_API_KEY": "test-key-not-real"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["reply"], "stub reply")
        self.assertTrue(payload["trace"].startswith("traces/"))
        self.assertNotIn(str(self.base), result.stdout)

    def test_branch_json_has_safe_paths_and_no_message_payload(self):
        config = self._config()
        run = run_cli(
            ("run", "hello", "--session", "notes", "--config", str(config),
             "--json"), self.base,
            env={"POCKETCHAT_API_KEY": "test-key-not-real"})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = run_cli(
            ("branch", "notes", "--from", "turn_000002", "--name", "fork",
             "--json"), self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema_version"], "1")
        self.assertEqual(payload["session"], "sessions/fork.jsonl")
        self.assertEqual(payload["source"], "sessions/notes.jsonl")
        self.assertTrue(payload["trace"].startswith("traces/"))
        self.assertNotIn("messages", payload)
        self.assertNotIn(str(self.base), result.stdout)

    def test_branch_json_refusal_is_stable(self):
        result = run_cli(
            ("branch", "missing", "--from", "turn_000002", "--json"), self.base)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "branch")
        self.assertIn("message", payload)


class TaskCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self._stub = hp.StubHTTPServer(StubProviderHandler)
        self.addCleanup(self._stub.close)

    def policy(self):
        path = self.base / "task-policy.json"
        payload = {
            "schema_version": "pocketchat-task-policy.v1",
            "objective": "run one CLI task",
            "workspace_root": ".",
            "max_steps": 8,
            "max_tool_calls": 4,
            "max_context_chars": 8000,
            "max_turn_tokens": 8000,
            "max_session_tokens": 16000,
            "tools": {"read_file": {"allow_paths": ["facts.txt"]}},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path, parse_policy(payload)

    def config(self):
        path = self.base / "pocketchat.json"
        path.write_text(json.dumps({
            "schema_version": "2",
            "name": "task-cli",
            "default_profile": "local",
            "profiles": {"local": {
                "base_url": "http://127.0.0.1:%d/v1" % self._stub.port,
                "model": "stub-model",
                "api_key_env": "POCKETCHAT_API_KEY",
            }},
        }), encoding="utf-8")
        return path

    def run_task(self):
        policy_path, _policy = self.policy()
        config = self.config()
        return run_cli(
            ("task", "run", "hello", "--policy", str(policy_path),
             "--session", "task", "--trace", "traces/task.jsonl",
             "--config", str(config), "--json"),
            self.base,
            env={"POCKETCHAT_API_KEY": "test-key-not-real"})

    def test_task_run_json_creates_record_and_relative_evidence(self):
        result = self.run_task()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema_version"], "1")
        self.assertEqual(payload["status"], "completed")
        self.assertTrue(payload["task_id"].startswith("task_"))
        self.assertEqual(payload["session"], "sessions/task.jsonl")
        self.assertEqual(payload["trace"], "traces/task.jsonl")
        self.assertTrue(payload["record"].startswith(".pocketchat/tasks/"))
        self.assertIn("steps", payload["report"])
        self.assertNotIn(str(self.base), result.stdout)

    def test_task_run_json_refuses_a_missing_policy_without_partial_record(self):
        config = self.config()
        result = run_cli(
            ("task", "run", "hello", "--policy", "missing.json",
             "--session", "task", "--config", str(config), "--json"),
            self.base, env={"POCKETCHAT_API_KEY": "test-key-not-real"})
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "task")
        self.assertFalse((self.base / ".pocketchat" / "tasks").exists())

    def test_task_status_distinguishes_interrupted_from_failed(self):
        _path, policy = self.policy()
        states = {}
        for name, terminal in (("interrupted", "interrupt"),
                               ("failed", "fail")):
            record = create_record(policy)
            transition(record, "start", "started")
            if terminal == "fail":
                transition(record, "fail", "provider failed")
            else:
                transition(record, "interrupt", "stopped by operator")
            save_record(self.base, record)
            result = run_cli(("task", "status", record.task_id, "--json"),
                             self.base)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            states[name] = payload["state"]
        self.assertEqual({"interrupted", "failed"}, set(states.values()))

    def test_task_resume_json_reports_the_recovery_step(self):
        policy_path, policy = self.policy()
        record = create_record(policy)
        transition(record, "start", "started")
        record_step(record, "model", state="completed", run_id="run_task")
        record_step(record, "tool", state="completed", tool="read_file",
                    run_id="run_task", detail="facts.txt")
        save_record(self.base, record)
        session = self.base / "sessions" / "source.jsonl"
        trace = self.base / "traces" / "run_task.jsonl"
        session.parent.mkdir(parents=True)
        trace.parent.mkdir(parents=True)
        session.write_text("\n".join([
            json.dumps({"session": "run_task", "schema_version": "1",
                        "created": "2026-10-30T00:00:00.000Z",
                        "pocketchat_version": "test",
                        "trace": "../traces/run_task.jsonl",
                        "conversation_id": "conv", "branch_id": "branch"}),
            json.dumps({"role": "user", "content": "hello",
                        "turn_id": "turn_000001"}),
            json.dumps({"role": "assistant", "content": "done",
                        "turn_id": "turn_000002"}),
        ]) + "\n", encoding="utf-8")
        trace.write_text("\n".join([
            json.dumps({"schema_version": "1", "event_id": "evt_000001",
                        "run_id": "run_task", "type": "run.started"}),
            json.dumps({"schema_version": "1", "event_id": "evt_000002",
                        "run_id": "run_task", "type": "run.finished"}),
        ]) + "\n", encoding="utf-8")
        checkpoint = create_task_checkpoint(
            self.base, session, task_id=record.task_id, step_id=2,
            policy_sha256=record.policy_sha256, turn_id="turn_000002",
            checkpoint_id="cp_task")
        result = run_cli(
            ("task", "resume", record.task_id,
             "--checkpoint", checkpoint["checkpoint_id"],
             "--policy", str(policy_path), "--name", "resumed", "--json"),
            self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["step_id"], 2)
        self.assertEqual(payload["report"]["state"], "running")
        self.assertEqual(payload["report"]["steps"], 4)
        self.assertEqual(payload["report"]["checkpoints"], 1)

    def test_task_report_json_lists_existing_evidence_only(self):
        result = self.run_task()
        payload = json.loads(result.stdout)
        report = run_cli(("task", "report", payload["task_id"], "--json"),
                         self.base)
        self.assertEqual(report.returncode, 0, report.stdout + report.stderr)
        self.assertEqual(report.stderr, "")
        body = json.loads(report.stdout)
        self.assertTrue(body["ok"])
        self.assertEqual(body["task_id"], payload["task_id"])
        self.assertEqual(len(body["steps"]), body["report"]["steps"])
        self.assertEqual(body["step_count"], body["report"]["steps"])
        self.assertEqual(body["tools"], [])
        self.assertEqual(body["budget"]["max_steps"], 8)
        self.assertEqual(body["budget"]["max_tool_calls"], 4)
        self.assertEqual(body["budget"]["max_context_chars"], "unknown")
        self.assertEqual(body["checkpoints"], [])
        self.assertEqual(body["runs"], [payload["run_id"]])
        self.assertEqual(body["trace"], ["traces/task.jsonl"])
        self.assertEqual(body["artifacts"], [])

    def test_task_report_rejects_a_different_policy(self):
        result = self.run_task()
        payload = json.loads(result.stdout)
        other = self.base / "other-policy.json"
        other.write_text(json.dumps({
            "schema_version": "pocketchat-task-policy.v1",
            "objective": "different objective",
            "workspace_root": ".",
            "max_steps": 8,
            "max_tool_calls": 4,
            "max_context_chars": 8000,
            "max_turn_tokens": 8000,
            "tools": {},
        }), encoding="utf-8")
        report = run_cli(
            ("task", "report", payload["task_id"], "--policy", str(other),
             "--json"), self.base)
        self.assertEqual(report.returncode, 2)
        self.assertEqual(report.stderr, "")
        body = json.loads(report.stdout)
        self.assertFalse(body["ok"])
        self.assertIn("policy", body["message"])

    def test_task_json_error_is_one_document_and_stderr_empty(self):
        result = run_cli(("task", "status", "task_missing", "--json"), self.base)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "task")
        self.assertIn("message", payload)


if __name__ == "__main__":
    unittest.main()
