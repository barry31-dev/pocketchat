"""Plan/command checkpoints derived from a bounded execution report."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.task_checkpoint import (CHECKPOINT_SCHEMA, CheckpointError,
                                        MAX_CHECKPOINT_BYTES,
                                        create_checkpoint, load_checkpoint,
                                        parse_checkpoint)
from pocketchat.task_exec import EXECUTION_REPORT_SCHEMA
from tests._helpers import run_cli


def report(steps=None, changes=None, spec="a" * 64, plan="b" * 64):
    steps = steps if steps is not None else [
        {"step_id": "s1", "action": "write_file", "status": "completed",
         "task_id": "task-1", "trace": "traces/s1.jsonl",
         "session": "sessions/s1.jsonl"},
        {"step_id": "s2", "action": "run_command", "status": "completed",
         "run_id": "s2", "trace": "traces/s2.jsonl",
         "evidence": {"template_id": "unit-tests", "exit_code": 0,
                      "timed_out": False, "trace": "traces/s2.cmd.jsonl",
                      "archive_sha256": "c" * 64}},
        {"step_id": "s3", "action": "read_file", "status": "not_executed",
         "reason": "step_boundary"},
    ]
    changes = changes if changes is not None else [
        {"path": "out/result.md", "original_sha256": "", "new_sha256": "d" * 64,
         "bytes": 12, "baseline": "absent", "added_lines": 1,
         "removed_lines": 0, "diff_state": "text"},
    ]
    return {
        "schema_version": EXECUTION_REPORT_SCHEMA,
        "report_id": "execution_1",
        "spec_sha256": spec,
        "plan_sha256": plan,
        "task_ids": ["task-1"],
        "steps": steps,
        "changes": changes,
        "evidence_issues": [],
    }


def checkpoint(rep=None, **overrides):
    base = {
        "schema_version": CHECKPOINT_SCHEMA,
        "checkpoint_id": "ckpt-1",
        "report_id": (rep or report())["report_id"],
        "spec_sha256": "a" * 64,
        "plan_sha256": "b" * 64,
        "completed_step_ids": ["s1", "s2"],
        "pending_step_ids": ["s3"],
        "boundary_index": 2,
        "task_ids": ["task-1"],
        "file_effects": [{
            "path": "out/result.md", "new_sha256": "d" * 64, "bytes": 12,
            "baseline": "absent",
        }],
        "command_effects": [{
            "step_id": "s2", "template_id": "unit-tests", "exit_code": 0,
            "timed_out": False, "archive_sha256": "c" * 64,
        }],
        "report_sha256": "e" * 64,
    }
    base.update(overrides)
    return base


class ParseTests(unittest.TestCase):
    def test_valid_checkpoint_is_closed_and_canonical(self):
        payload = checkpoint()
        self.assertEqual(payload, parse_checkpoint(payload))
        reordered = {key: payload[key] for key in reversed(list(payload))}
        self.assertEqual(
            json.dumps(payload, sort_keys=True),
            json.dumps(parse_checkpoint(reordered), sort_keys=True))

    def test_malformed_checkpoint_is_refused(self):
        good = checkpoint()
        cases = []
        for mutate in (
            lambda row: row.update(extra=True),
            lambda row: row.update(schema_version="wrong"),
            lambda row: row.update(spec_sha256="notahash"),
            lambda row: row.update(plan_sha256="0" * 63),
            lambda row: row.update(completed_step_ids=["s1", "s1"]),
            lambda row: row.update(completed_step_ids=["s1", "s3"]),
            lambda row: row.update(pending_step_ids=["s1"]),
            lambda row: row.update(boundary_index=0),
            lambda row: row.update(boundary_index=99),
            lambda row: row.update(file_effects=[{"path": "x"}]),
            lambda row: row.update(command_effects=[{"step_id": "s2"}]),
            lambda row: row.update(report_sha256="zzz"),
            lambda row: row.update(checkpoint_id=""),
        ):
            broken = json.loads(json.dumps(good))
            mutate(broken)
            cases.append(broken)
        for broken in cases:
            with self.subTest(broken=broken):
                with self.assertRaises(CheckpointError):
                    parse_checkpoint(broken)

class CreateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.report_path = self.root / "execution_1.json"
        self.report_path.write_text(json.dumps(report()), encoding="utf-8")
        self.digest = hashlib.sha256(
            self.report_path.read_bytes()).hexdigest()

    def tearDown(self):
        self._tmp.cleanup()

    def create(self, rep=None, **kwargs):
        """Write a report variant to disk, then checkpoint that exact file."""
        if rep is not None:
            self.report_path.write_text(json.dumps(rep), encoding="utf-8")
            self.digest = hashlib.sha256(
                self.report_path.read_bytes()).hexdigest()
        return create_checkpoint(
            json.loads(self.report_path.read_text()),
            report_path=self.report_path, **kwargs)

    def test_checkpoint_binds_the_completed_prefix_of_a_report(self):
        rep = report()
        result = self.create(rep)
        self.assertEqual(CHECKPOINT_SCHEMA, result["schema_version"])
        self.assertEqual(["s1", "s2"], result["completed_step_ids"])
        self.assertEqual(["s3"], result["pending_step_ids"])
        self.assertEqual(2, result["boundary_index"])
        self.assertEqual(rep["spec_sha256"], result["spec_sha256"])
        self.assertEqual(rep["plan_sha256"], result["plan_sha256"])
        self.assertEqual(1, len(result["file_effects"]))
        self.assertEqual("s2", result["command_effects"][0]["step_id"])
        self.assertTrue(result["report_sha256"])

    def test_report_identity_must_match(self):
        rep = report()
        for mutate in (
            lambda row: row.update(spec_sha256="f" * 64),
            lambda row: row.update(plan_sha256="f" * 64),
            lambda row: row.update(report_id="other"),
        ):
            broken = json.loads(json.dumps(rep))
            mutate(broken)
            with self.subTest(broken=broken):
                with self.assertRaises(CheckpointError):
                    self.create(broken, expected_report_sha256=self.digest)

    def test_failed_step_is_never_a_completed_boundary(self):
        rep = report(steps=[
            {"step_id": "s1", "action": "write_file", "status": "completed",
             "task_id": "t", "trace": "traces/s1.jsonl"},
            {"step_id": "s2", "action": "run_command", "status": "failed",
             "reason": "command_runner_error"},
            {"step_id": "s3", "action": "read_file", "status": "not_executed",
             "reason": "previous_step_did_not_complete"},
        ])
        result = self.create(rep)
        self.assertEqual(["s1"], result["completed_step_ids"])
        self.assertEqual(1, result["boundary_index"])
        self.assertEqual([], result["command_effects"])

    def test_report_with_evidence_issues_is_refused(self):
        rep = report()
        rep["evidence_issues"] = [{"path": "x", "state": "unknown",
                                   "reason": "cannot evidence"}]
        with self.assertRaises(CheckpointError):
            self.create(rep)

    def test_empty_and_all_completed_reports_are_explicit(self):
        rep = report(steps=[], changes=[])
        result = self.create(rep)
        self.assertEqual([], result["completed_step_ids"])
        self.assertEqual(0, result["boundary_index"])
        full = report(steps=[
            {"step_id": "s1", "action": "read_file", "status": "completed",
             "task_id": "t", "trace": "traces/s1.jsonl"},
        ], changes=[])
        done = self.create(full)
        self.assertEqual(1, done["boundary_index"])
        self.assertEqual([], done["pending_step_ids"])


class FileTests(unittest.TestCase):
    def test_checkpoint_file_is_bounded_regular_and_not_a_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "ckpt.json"
            target.write_text(json.dumps(checkpoint()), encoding="utf-8")
            self.assertEqual(checkpoint(), load_checkpoint(target))
            try:
                (root / "link.json").symlink_to(target)
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaises(CheckpointError):
                    load_checkpoint(root / "link.json")
            oversized = root / "big.json"
            oversized.write_bytes(b" " * (MAX_CHECKPOINT_BYTES + 1))
            with self.assertRaises(CheckpointError):
                load_checkpoint(oversized)

    def test_duplicate_members_and_false_envelope_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ckpt.json"
            path.write_text('{"schema_version":"a","schema_version":"b"}',
                            encoding="utf-8")
            with self.assertRaisesRegex(CheckpointError, "duplicate JSON member"):
                load_checkpoint(path)
            path.write_text(json.dumps({"ok": False, **checkpoint()}),
                            encoding="utf-8")
            with self.assertRaisesRegex(CheckpointError, "ok=true"):
                load_checkpoint(path)


class ResumeTests(CreateTests):
    def test_pending_steps_exclude_completed_and_boundary_is_the_prefix(self):
        created = self.create()
        parsed = parse_checkpoint(created)
        self.assertEqual(parsed["boundary_index"],
                         len(parsed["completed_step_ids"]))
        for step_id in parsed["completed_step_ids"]:
            self.assertNotIn(step_id, parsed["pending_step_ids"])

    def test_digest_mismatch_between_checkpoint_and_plan_is_refused(self):
        created = self.create()
        # A digest edit is structurally valid, so only binding the record to
        # the plan being resumed can expose it.
        for kwargs, mutate in (
                ({"expected_spec_sha256": "a" * 64},
                 lambda row: row.update(spec_sha256="f" * 64)),
                ({"expected_plan_sha256": "b" * 64},
                 lambda row: row.update(plan_sha256="f" * 64)),
                ({"expected_spec_sha256": "a" * 64,
                  "expected_plan_sha256": "b" * 64},
                 lambda row: row.update(plan_sha256="f" * 64))):
                broken = json.loads(json.dumps(created))
                mutate(broken)
                with self.subTest(kwargs=kwargs):
                    with self.assertRaisesRegex(CheckpointError, "does not match"):
                        parse_checkpoint(broken, **kwargs)
        # Structural violations refuse on their own.
        for mutate in (lambda row: row.update(boundary_index=0),
                       lambda row: row.update(pending_step_ids=["s1"])):
            broken = json.loads(json.dumps(created))
            mutate(broken)
            with self.subTest(broken=broken):
                with self.assertRaises(CheckpointError):
                    parse_checkpoint(broken)
        self.assertEqual(
            created,
            parse_checkpoint(created, expected_spec_sha256="a" * 64,
                             expected_plan_sha256="b" * 64))


class CheckpointCliTests(unittest.TestCase):
    def test_json_and_human_preview_are_read_only_and_share_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "execution_1.json"
            report_path.write_text(json.dumps(report()), encoding="utf-8")
            created = create_checkpoint(json.loads(report_path.read_text()),
                                        report_path=report_path)
            ckpt = root / "ckpt.json"
            ckpt.write_text(json.dumps(created), encoding="utf-8")
            machine = run_cli(("task", "checkpoint", "preview", "ckpt.json",
                               "--spec-sha256", "a" * 64,
                               "--plan-sha256", "b" * 64, "--json"), root)
            human = run_cli(("task", "checkpoint", "preview", "ckpt.json"), root)
            self.assertEqual(0, machine.returncode,
                             machine.stdout + machine.stderr)
            payload = json.loads(machine.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(["s1", "s2"],
                             payload["checkpoint"]["completed_step_ids"])
            self.assertEqual(0, human.returncode,
                             human.stdout + human.stderr)
            self.assertIn("boundary 2/3", human.stdout)
            self.assertFalse((root / ".pocketchat").exists())
            self.assertFalse((root / "traces").exists())

    def test_digest_mismatch_is_exit_two_without_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "execution_1.json"
            report_path.write_text(json.dumps(report()), encoding="utf-8")
            created = create_checkpoint(json.loads(report_path.read_text()),
                                        report_path=report_path)
            ckpt = root / "ckpt.json"
            ckpt.write_text(json.dumps(created), encoding="utf-8")
            result = run_cli(("task", "checkpoint", "preview", "ckpt.json",
                              "--spec-sha256", "f" * 64, "--json"), root)
            self.assertEqual(2, result.returncode)
            self.assertEqual("task", json.loads(result.stdout)["error"])
            self.assertFalse((root / ".pocketchat").exists())
            self.assertFalse((root / "traces").exists())

    def test_malformed_checkpoint_is_exit_two(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ckpt = root / "ckpt.json"
            ckpt.write_text('{"schema_version":"a","schema_version":"b"}',
                            encoding="utf-8")
            result = run_cli(("task", "checkpoint", "preview", "ckpt.json",
                              "--json"), root)
            self.assertEqual(2, result.returncode)
            self.assertEqual("task", json.loads(result.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
