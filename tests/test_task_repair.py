"""Bounded command repair: one retry, one fingerprint, then stop."""
from __future__ import annotations

import unittest
from pathlib import Path

from pocketchat.task_repair import (MAX_REPAIR_ATTEMPTS, RepairDecision,
                                    _write_repair_evidence, failure_fingerprint,
                                    run_bounded_repair)


class FakeCommand:
    def __init__(self, exit_code=0, *, stderr_sha256="a" * 64,
                 timed_out=False):
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.stderr_sha256 = stderr_sha256
        self.stdout_sha256 = "b" * 64
        self.template_id = "check"
        self.trace_path = "trace.jsonl"
        self.task_acceptance = "not_evaluated"

    @property
    def ok(self):
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self):
        return {
            "template_id": self.template_id,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
            "trace": self.trace_path,
            "task_acceptance": self.task_acceptance,
        }


class RepairTests(unittest.TestCase):
    def test_success_needs_no_repair(self):
        calls = []
        def runner(template_id, trace_path, run_id):
            calls.append((template_id, run_id))
            return FakeCommand(0)
        result, details = run_bounded_repair(
            runner, "check", "trace.jsonl", "step",
            repair=lambda evidence: RepairDecision(True, "would repair"))
        self.assertTrue(result.ok)
        self.assertFalse(details["attempted"])
        self.assertEqual(1, len(calls))

    def test_one_failed_command_is_retried_once_after_repair(self):
        responses = [FakeCommand(1), FakeCommand(0)]
        calls = []
        def runner(template_id, trace_path, run_id):
            calls.append(run_id)
            return responses.pop(0)
        result, details = run_bounded_repair(
            runner, "check", "trace.jsonl", "step",
            repair=lambda evidence: RepairDecision(True, "updated input"))
        self.assertTrue(result.ok)
        self.assertEqual("repaired", details["outcome"])
        self.assertEqual(2, len(calls))
        self.assertEqual(MAX_REPAIR_ATTEMPTS + 1, details["attempts"])
        self.assertFalse(details["task_acceptance_pass"])

    def test_same_failure_stops_after_one_retry(self):
        calls = []
        def runner(template_id, trace_path, run_id):
            calls.append(run_id)
            return FakeCommand(1)
        result, details = run_bounded_repair(
            runner, "check", "trace.jsonl", "step",
            repair=lambda evidence: RepairDecision(True, "updated input"))
        self.assertFalse(result.ok)
        self.assertEqual("same_failure", details["outcome"])
        self.assertEqual(2, len(calls))
        self.assertEqual(details["failure_fingerprint"],
                         details["retry_failure_fingerprint"])

    def test_refused_repair_does_not_rerun(self):
        calls = []
        def runner(template_id, trace_path, run_id):
            calls.append(run_id)
            return FakeCommand(1)
        _, details = run_bounded_repair(
            runner, "check", "trace.jsonl", "step",
            repair=lambda evidence: RepairDecision(False, "not safely repairable"))
        self.assertEqual("not_repairable", details["outcome"])
        self.assertEqual(1, len(calls))
        self.assertEqual("not safely repairable", details["reason"])

    def test_retry_uses_a_sibling_trace_and_cannot_invent_a_template(self):
        seen = []
        def runner(template_id, trace_path, run_id):
            seen.append((template_id, str(trace_path), run_id))
            return FakeCommand(1 if len(seen) == 1 else 0)
        _, details = run_bounded_repair(
            runner, "check", "/tmp/traces/check.jsonl", "step",
            repair=lambda evidence: RepairDecision(True, "same template"))
        self.assertEqual([("check", "/tmp/traces/check.jsonl", "step"),
                          ("check", "/tmp/traces/check.retry-1.jsonl",
                           "step.retry-1")], seen)
        self.assertEqual("repaired", details["outcome"])

    def test_bad_repair_callback_and_bad_decision_fail_closed(self):
        calls = []
        def runner(template_id, trace_path, run_id):
            calls.append(run_id)
            return FakeCommand(1)
        with self.assertRaises(Exception):
            run_bounded_repair(runner, "check", "trace.jsonl", "step",
                               repair=lambda evidence: True)
        self.assertEqual(1, len(calls))
        with self.assertRaises(Exception):
            run_bounded_repair(None, "check", "trace.jsonl", "step",
                               repair=lambda evidence: RepairDecision(True, "x"))

    def test_bounded_evidence_is_persisted_once(self):
        import json
        import tempfile
        root = Path(tempfile.mkdtemp())
        def runner(template_id, trace_path, run_id):
            return FakeCommand(1)
        _, details = run_bounded_repair(
            runner, "check", root / "traces" / "check.jsonl", "step",
            repair=lambda evidence: RepairDecision(True, "same template"),
            root=root)
        path = root / ".pocketchat" / "task-repair" / "step.json"
        self.assertTrue(path.is_file())
        self.assertEqual(details, json.loads(path.read_text()))
        with self.assertRaises(Exception):
            _write_repair_evidence(root, "step", details)

    def test_fingerprint_ignores_preview_text_and_uses_hashes(self):
        first = FakeCommand(1, stderr_sha256="c" * 64)
        second = FakeCommand(1, stderr_sha256="c" * 64)
        third = FakeCommand(1, stderr_sha256="d" * 64)
        self.assertEqual(failure_fingerprint(first), failure_fingerprint(second))
        self.assertNotEqual(failure_fingerprint(first), failure_fingerprint(third))


if __name__ == "__main__":
    unittest.main()
