"""Task checkpoint and resume: every recovery claim is checked before a write."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pocketchat.recovery import (RecoveryError, create_task_checkpoint,
                                 resume_task)
from pocketchat.task_policy import parse_policy
from pocketchat.tasking import (TaskError, create_record, load_record,
                                record_step, save_record, transition,
                                update_record)


POLICY = {
    "schema_version": "pocketchat-task-policy.v1",
    "objective": "compare two documents",
    "workspace_root": ".",
    "max_steps": 8,
    "max_tool_calls": 4,
    "max_context_chars": 8000,
    "max_turn_tokens": 8000,
    "tools": {"read_file": {"allow_paths": ["facts.txt"]}},
}


def raw_session(root: Path) -> Path:
    session = root / "sessions" / "task.jsonl"
    trace = root / "traces" / "run_task.jsonl"
    session.parent.mkdir(parents=True)
    trace.parent.mkdir(parents=True)
    session.write_text("\n".join([
        json.dumps({
            "session": "run_task", "schema_version": "1",
            "created": "2026-10-30T00:00:00.000Z",
            "pocketchat_version": "test", "trace": "../traces/run_task.jsonl",
            "conversation_id": "conv_task", "branch_id": "branch_task",
        }, sort_keys=True),
        json.dumps({"role": "user", "content": "read",
                    "turn_id": "turn_000001"}, sort_keys=True),
        json.dumps({
            "role": "assistant", "content": "tool done",
            "turn_id": "turn_000002",
            "tool_context": [
                {
                    "role": "assistant", "content": "",
                    "tool_calls": [{
                        "id": "call_1", "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": "{\"path\":\"facts.txt\"}",
                        },
                    }],
                },
                {
                    "role": "tool", "tool_call_id": "call_1",
                    "name": "read_file",
                    "content": "{\"status\":\"completed\"}",
                },
            ],
        }, sort_keys=True),
        json.dumps({"role": "user", "content": "continue",
                    "turn_id": "turn_000003"}, sort_keys=True),
        json.dumps({"role": "assistant", "content": "final",
                    "turn_id": "turn_000004"}, sort_keys=True),
    ]) + "\n", encoding="utf-8")
    trace.write_text("\n".join([
        json.dumps({"schema_version": "1", "event_id": "evt_000001",
                    "run_id": "run_task", "type": "run.started"}),
        json.dumps({"schema_version": "1", "event_id": "evt_000002",
                    "run_id": "run_task", "type": "run.finished"}),
    ]) + "\n", encoding="utf-8")
    return session


class TaskRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = raw_session(self.root)
        self.policy = parse_policy(POLICY)
        self.record = create_record(self.policy)
        transition(self.record, "start", "task run started")
        record_step(self.record, "model", state="completed", run_id="run_task")
        record_step(self.record, "tool", state="completed", tool="read_file",
                    run_id="run_task", detail="facts.txt")
        save_record(self.root, self.record)

    def test_checkpoint_refuses_partial_or_malformed_task_binding(self):
        cases = (
            {"task_id": self.record.task_id, "step_id": 2},
            {"task_id": "task_not-hex", "step_id": 2,
             "policy_sha256": self.record.policy_sha256},
        )
        from pocketchat.recovery import create_checkpoint
        for index, fields in enumerate(cases):
            with self.subTest(fields=fields):
                checkpoint_id = "cp_bad_%d" % index
                with self.assertRaises(RecoveryError):
                    create_checkpoint(
                        self.root, self.session, "turn_000002",
                        checkpoint_id, **fields)
                self.assertFalse((self.root / ".pocketchat" / "recovery" /
                                  "checkpoints" /
                                  (checkpoint_id + ".json")).exists())

    def test_checkpoint_requires_completed_step_boundary(self):
        path = self.root / ".pocketchat" / "tasks" / (
            self.record.task_id + ".json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["steps"][-1]["state"] = "running"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(TaskError) as caught:
            create_task_checkpoint(
                self.root, self.session,
                task_id=self.record.task_id, step_id=2,
                policy_sha256=self.record.policy_sha256,
                turn_id="turn_000002", checkpoint_id="cp_task")
        self.assertIn("completed step boundary", str(caught.exception))

    def test_checkpoint_refuses_when_the_step_budget_has_no_room(self):
        self.record.max_steps = 2
        update_record(self.root, self.record)
        with self.assertRaises(TaskError) as caught:
            create_task_checkpoint(
                self.root, self.session,
                task_id=self.record.task_id, step_id=2,
                policy_sha256=self.record.policy_sha256,
                turn_id="turn_000002", checkpoint_id="cp_full")
        self.assertIn("checkpoint", str(caught.exception))
        self.assertFalse((self.root / ".pocketchat" / "recovery" /
                          "checkpoints" / "cp_full.json").exists())

    def test_resume_rejects_policy_hash_mismatch(self):
        checkpoint = create_task_checkpoint(
            self.root, self.session,
            task_id=self.record.task_id, step_id=2,
            policy_sha256=self.record.policy_sha256,
            turn_id="turn_000002", checkpoint_id="cp_task")
        other = parse_policy({**POLICY, "objective": "different objective"})
        with self.assertRaises(RecoveryError) as caught:
            resume_task(self.root, checkpoint["checkpoint_id"], other,
                        name="resumed")
        self.assertIn("policy", str(caught.exception))
        self.assertFalse((self.root / "sessions" / "resumed.jsonl").exists())

    def test_resume_restores_ordered_tool_context(self):
        checkpoint = create_task_checkpoint(
            self.root, self.session,
            task_id=self.record.task_id, step_id=2,
            policy_sha256=self.record.policy_sha256,
            turn_id="turn_000002", checkpoint_id="cp_task")
        result = resume_task(self.root, checkpoint["checkpoint_id"],
                             self.policy, name="resumed")
        self.assertTrue(result["ok"])
        self.assertEqual(
            ["user", "assistant", "tool", "assistant"],
            [message["role"] for message in result["context"]],
        )
        self.assertEqual("call_1", result["context"][1]["tool_calls"][0]["id"])
        self.assertEqual("call_1", result["context"][2]["tool_call_id"])

    def test_resume_recovers_prepared_checkpoint_transaction(self):
        checkpoint = create_task_checkpoint(
            self.root, self.session,
            task_id=self.record.task_id, step_id=2,
            policy_sha256=self.record.policy_sha256,
            turn_id="turn_000002", checkpoint_id="cp_task")
        with mock.patch(
                "pocketchat.recovery.create_branch",
                side_effect=OSError("simulated crash before target")):
            with self.assertRaises(OSError):
                resume_task(self.root, checkpoint["checkpoint_id"],
                            self.policy, name="resumed")
        result = resume_task(self.root, checkpoint["checkpoint_id"],
                             self.policy, name="resumed")
        self.assertTrue(result["ok"])
        self.assertTrue(result["recovered"])
        self.assertTrue((self.root / result["session"]).is_file())

    def test_resume_rejects_when_recovery_step_exceeds_budget_before_write(self):
        checkpoint = create_task_checkpoint(
            self.root, self.session,
            task_id=self.record.task_id, step_id=2,
            policy_sha256=self.record.policy_sha256,
            turn_id="turn_000002", checkpoint_id="cp_task")
        current = load_record(self.root, self.record.task_id)
        current.max_steps = 3
        update_record(self.root, current)
        with self.assertRaises(RecoveryError) as caught:
            resume_task(self.root, checkpoint["checkpoint_id"],
                        self.policy, name="resumed")
        self.assertIn("recovery step", str(caught.exception))
        self.assertFalse((self.root / "sessions" / "resumed.jsonl").exists())

    def test_resume_rejects_partial_or_conflicting_task_record(self):
        checkpoint = create_task_checkpoint(
            self.root, self.session,
            task_id=self.record.task_id, step_id=2,
            policy_sha256=self.record.policy_sha256,
            turn_id="turn_000002", checkpoint_id="cp_task")
        path = self.root / ".pocketchat" / "tasks" / (
            self.record.task_id + ".json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["steps"] = payload["steps"][:-1]
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(RecoveryError) as caught:
            resume_task(self.root, checkpoint["checkpoint_id"],
                        self.policy, name="resumed")
        self.assertIn("task record", str(caught.exception))
        self.assertFalse((self.root / "sessions" / "resumed.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
