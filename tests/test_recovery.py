"""Checkpoint/rewind state-machine and CLI contract tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import _helpers as hp
from unittest import mock

from pocketchat import recovery
from tests._helpers import run_cli


class RecoveryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = self.root / "sessions" / "notes.jsonl"
        self.trace = self.root / "traces" / "run_notes.jsonl"
        self.session.parent.mkdir(parents=True)
        self.trace.parent.mkdir(parents=True)
        self.session.write_text("\n".join([
            json.dumps({
                "session": "run_notes", "schema_version": "1",
                "created": "2026-10-29T00:00:00.000Z",
                "pocketchat_version": "test", "trace": "../traces/run_notes.jsonl",
                "conversation_id": "conv_notes", "branch_id": "branch_notes",
            }, sort_keys=True),
            json.dumps({"role": "user", "content": "one",
                        "turn_id": "turn_000001"}, sort_keys=True),
            json.dumps({"role": "assistant", "content": "answer one",
                        "turn_id": "turn_000002"}, sort_keys=True),
            json.dumps({"role": "user", "content": "two",
                        "turn_id": "turn_000003"}, sort_keys=True),
            json.dumps({"role": "assistant", "content": "answer two",
                        "turn_id": "turn_000004"}, sort_keys=True),
        ]) + "\n", encoding="utf-8")
        self.trace.write_text("\n".join([
            json.dumps({"schema_version": "1", "event_id": "evt_000001",
                        "run_id": "run_notes", "type": "run.started"}),
            json.dumps({"schema_version": "1", "event_id": "evt_000002",
                        "run_id": "run_notes", "type": "run.finished"}),
        ]) + "\n", encoding="utf-8")


class CheckpointTests(RecoveryFixture):
    def test_checkpoint_records_boundary_and_manifest(self):
        record = recovery.create_checkpoint(self.root, self.session, "turn_000002")
        self.assertEqual(record["turn_id"], "turn_000002")
        self.assertEqual(record["messages"], 2)
        self.assertTrue((self.root / record["manifest"]).is_file())
        stored = json.loads((self.root / record["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(stored["boundary_sha256"], record["boundary_sha256"])

    def test_checkpoint_rejects_an_unanswered_turn(self):
        self.session.write_text(
            self.session.read_text(encoding="utf-8").split("turn_000003", 1)[0]
            + "\n", encoding="utf-8")
        with self.assertRaises(recovery.RecoveryError):
            recovery.create_checkpoint(self.root, self.session, "turn_000001")

    def test_checkpoint_rejects_missing_trace_evidence(self):
        self.trace.unlink()
        with self.assertRaises(recovery.RecoveryError):
            recovery.create_checkpoint(self.root, self.session, "turn_000002")

    def test_checkpoint_rejects_an_invalid_id(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.create_checkpoint(
                self.root, self.session, "turn_000002", "bad id")


class RewindTests(RecoveryFixture):
    def test_rewind_creates_a_branch_without_rewriting_source(self):
        before = self.session.read_bytes()
        checkpoint = recovery.create_checkpoint(self.root, self.session, "turn_000002")
        result = recovery.rewind_to_checkpoint(
            self.root, checkpoint["checkpoint_id"], "notes-rewound")
        self.assertTrue(result["ok"])
        target = self.root / result["session"]
        self.assertTrue(target.is_file())
        self.assertEqual(before, self.session.read_bytes())
        self.assertIn("notes-rewound", str(target))

    def test_rewind_rejects_missing_checkpoint(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.rewind_to_checkpoint(self.root, "checkpoint_missing", "child")

    def test_rewind_rejects_changed_boundary_evidence(self):
        checkpoint = recovery.create_checkpoint(self.root, self.session, "turn_000002")
        text = self.session.read_text(encoding="utf-8").replace(
            "answer one", "changed answer")
        self.session.write_text(text, encoding="utf-8")
        with self.assertRaises(recovery.RecoveryError):
            recovery.rewind_to_checkpoint(
                self.root, checkpoint["checkpoint_id"], "child")

    def test_rewind_recovers_a_prepared_transaction_before_target_write(self):
        checkpoint = recovery.create_checkpoint(self.root, self.session, "turn_000002")
        with mock.patch.object(
                recovery, "create_branch",
                side_effect=OSError("simulated crash before target")):
            with self.assertRaises(OSError):
                recovery.rewind_to_checkpoint(
                    self.root, checkpoint["checkpoint_id"], "child")
        result = recovery.rewind_to_checkpoint(
            self.root, checkpoint["checkpoint_id"], "child")
        self.assertTrue(result["recovered"])
        self.assertTrue((self.root / result["session"]).is_file())

    def test_rewind_recovers_a_partial_target(self):
        checkpoint = recovery.create_checkpoint(self.root, self.session, "turn_000002")

        def partial_then_crash(target, *args, **kwargs):
            target.write_text('{"partial": true}\\n', encoding="utf-8")
            raise OSError("simulated crash during target write")

        with mock.patch.object(recovery, "create_branch", side_effect=partial_then_crash):
            with self.assertRaises(OSError):
                recovery.rewind_to_checkpoint(
                    self.root, checkpoint["checkpoint_id"], "child")
        result = recovery.rewind_to_checkpoint(
            self.root, checkpoint["checkpoint_id"], "child")
        self.assertTrue(result["recovered"])
        self.assertTrue((self.root / result["session"]).is_file())

    def test_rewind_cli_emits_json_and_refuses_bad_ids(self):
        checkpoint = recovery.create_checkpoint(self.root, self.session, "turn_000002")
        result = run_cli(
            ("rewind", checkpoint["checkpoint_id"], "--name", "child", "--json"),
            self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["checkpoint_id"], checkpoint["checkpoint_id"])
        bad = run_cli(("rewind", "checkpoint_missing", "--name", "bad", "--json"),
                      self.root)
        self.assertEqual(bad.returncode, 2)
        self.assertFalse(json.loads(bad.stdout)["ok"])

    def test_recovery_cli_round_trip_runs_in_a_real_subprocess(self):
        package_root = Path(__file__).resolve().parents[1]
        env = {"PYTHONPATH": str(package_root)}
        checkpoint = subprocess.run(
            [sys.executable, "-m", "pocketchat", "checkpoint", "notes",
             "--at", "turn_000002", "--id", "cp_cli", "--json"],
            cwd=str(self.root), env=hp.subprocess_environment(env), capture_output=True, text=True,
            check=False)
        self.assertEqual(checkpoint.returncode, 0,
                         checkpoint.stdout + checkpoint.stderr)
        self.assertTrue(json.loads(checkpoint.stdout)["ok"])
        rewind = subprocess.run(
            [sys.executable, "-m", "pocketchat", "rewind", "cp_cli",
             "--name", "child-cli", "--json"],
            cwd=str(self.root), env=hp.subprocess_environment(env), capture_output=True, text=True,
            check=False)
        self.assertEqual(rewind.returncode, 0, rewind.stdout + rewind.stderr)
        payload = json.loads(rewind.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["session"], "sessions/child-cli.jsonl")


if __name__ == "__main__":
    unittest.main()
