"""Cooperative cancellation keeps confirmed effects and only resumes a suffix."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from pocketchat import task_exec, tasking
from pocketchat.agent import run_task
from pocketchat.task_checkpoint import (CheckpointError, create_checkpoint,
                                        resume_from_checkpoint)
from pocketchat.task_command import run_command_template
from pocketchat.task_exec import authorize, execute_plan, load_task_execution
from pocketchat.task_plan import parse_plan, plan_sha256, spec_sha256
from pocketchat.task_recovery import TaskRecoveryError
from pocketchat.task_spec import parse_spec
from tests import _helpers as hp
from tests.test_task_run import FakeTaskAdapter, final_response, model_tool
from tests.test_task_spec import minimal

SPEC_SHA = "a" * 64
PLAN_SHA = "b" * 64
TOKEN = "1" * 32


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


class CancellationOwnerTests(unittest.TestCase):
    def owner(self, **overrides):
        self.assertTrue(hasattr(tasking, "TaskCancellation"),
                        "the task cancellation owner is not implemented")
        values = dict(spec_sha256=SPEC_SHA, plan_sha256=PLAN_SHA,
                      owner_token=TOKEN)
        values.update(overrides)
        return tasking.TaskCancellation(**values)

    def request(self, owner, **overrides):
        values = dict(spec_sha256=SPEC_SHA, plan_sha256=PLAN_SHA,
                      owner_token=TOKEN)
        values.update(overrides)
        return owner.request(**values)

    def test_owner_binds_all_identities_and_repeated_request_is_idempotent(self):
        owner = self.owner()
        self.assertFalse(owner.requested)
        for overrides in ({"spec_sha256": "c" * 64},
                          {"plan_sha256": "c" * 64},
                          {"owner_token": "2" * 32}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(tasking.TaskError) as caught:
                    self.request(owner, **overrides)
                self.assertFalse(owner.requested)
                self.assertNotIn(TOKEN, str(caught.exception))
        self.assertTrue(self.request(owner))
        self.assertFalse(self.request(owner))
        self.assertTrue(owner.requested)

    def test_default_owner_is_unique_and_invalid_identifiers_are_refused(self):
        owner = self.owner(owner_token=None)
        other = self.owner(owner_token=None)
        self.assertEqual(32, len(owner.owner_token))
        self.assertNotEqual(owner.owner_token, other.owner_token)
        self.assertEqual(hashlib.sha256(owner.owner_token.encode()).hexdigest(),
                         owner.owner_sha256)
        for name, values in (
                ("owner_token", ("", "A" * 32, "../private-canary", True)),
                ("spec_sha256", ("", "A" * 64, True, "x" * 65)),
                ("plan_sha256", ("", "A" * 64, None, 1))):
            for value in values:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(tasking.TaskError) as caught:
                        self.owner(**{name: value})
                    self.assertNotIn("private-canary", str(caught.exception))


class CancellationExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.template = {
            "id": "count", "argv": [sys.executable, "-c",
                "from pathlib import Path; p=Path('count.txt'); "
                "p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"],
            "cwd": ".", "timeout_seconds": 5, "max_output_bytes": 64,
        }
        self.spec = parse_spec(minimal(
            inputs=["report.md", "last.md"],
            tools={"write_file": {"allow_paths": ["report.md", "last.md"]}},
            commands=[self.template]))
        steps = []
        for name, action, inputs in (
                ("write_report", "write_file", ["report.md"]),
                ("count_once", "run_command", ["count"]),
                ("finish", "write_file", ["last.md"])):
            steps.append({"id": name, "action": action, "inputs": inputs,
                          "objective": name, "expected_evidence": name,
                          "depends_on": [steps[-1]["id"]] if steps else [],
                          "retry_limit": 0})
        self.plan = parse_plan({"schema_version": "pocketchat-task-plan.v1",
                               "spec_sha256": spec_sha256(self.spec),
                               "revision": 0, "steps": steps}, spec=self.spec)
        self.execution = authorize(self.plan, self.spec,
                                   expected_plan_sha256=plan_sha256(self.plan))
        self.adapter = FakeTaskAdapter([
            model_tool("write", "write_file", {"path": "report.md", "content": "first\n"}),
            final_response(),
            model_tool("last", "write_file", {"path": "last.md", "content": "last\n"}),
            final_response(),
        ])
        self.config = {
            "profile": "stub", "model": "task-model",
            "base_url": "http://stub.invalid/v1", "api_key_env": "",
            "requires_api_key": False, "timeout_seconds": 5,
            "summary_prefix_chars": 80, "provider_adapter": self.adapter,
        }
        self.runner = Mock(side_effect=lambda template_id, trace, run_id:
                           run_command_template(self.root, self.template,
                                                trace_path=trace, run_id=run_id))

    def select_steps(self, start=0, stop=None):
        payload = copy.deepcopy(self.plan.source)
        payload["steps"] = payload["steps"][start:stop]
        payload["steps"][0]["depends_on"] = []
        self.plan = parse_plan(payload, spec=self.spec)
        self.execution = authorize(self.plan, self.spec,
                                   expected_plan_sha256=plan_sha256(self.plan))

    def owner(self, token=TOKEN, **overrides):
        self.assertTrue(hasattr(tasking, "TaskCancellation"),
                        "the task cancellation owner is not implemented")
        values = dict(spec_sha256=self.execution.spec_sha256,
                      plan_sha256=self.execution.plan_sha256, owner_token=token)
        values.update(overrides)
        return tasking.TaskCancellation(**values)

    def request(self, owner):
        owner.request(spec_sha256=self.execution.spec_sha256,
                      plan_sha256=self.execution.plan_sha256,
                      owner_token=owner.owner_token)

    def execute(self, **overrides):
        values = dict(run_task=run_task, workspace=self.root, config=self.config,
                      trace_dir=self.root / "traces", session_dir=self.root / "sessions",
                      command_runner=self.runner)
        values.update(overrides)
        return execute_plan(self.execution, **values)

    def cancelled(self, owner, **overrides):
        self.assertTrue(hasattr(task_exec, "TaskCancelledError"),
                        "the cancellation exception is not implemented")
        with self.assertRaises(task_exec.TaskCancelledError) as caught:
            self.execute(cancellation=owner, **overrides)
        error = caught.exception
        self.assertIsInstance(error, task_exec.TaskExecutionError)
        self.assertEqual("task cancelled at a confirmed step boundary", str(error))
        path = self.root / error.report
        payload = json.loads(path.read_text())
        self.assertNotIn("error", payload)
        self.assertNotIn(owner.owner_token, path.read_text())
        self.assertEqual("cancelled", payload["cancellation"]["state"])
        self.assertEqual(owner.owner_sha256, payload["cancellation"]["owner_sha256"])
        return path, payload, error.results

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS,
                         "requires secure descriptor-relative reads")
    def cancel_after_command(self):
        owner = self.owner()
        def runner(template_id, trace, run_id):
            result = run_command_template(self.root, self.template,
                                          trace_path=trace, run_id=run_id)
            self.request(owner)
            return result
        self.runner.side_effect = runner
        path, payload, results = self.cancelled(owner)
        return owner, path, payload, results

    def checkpoint(self, path, payload):
        checkpoint = create_checkpoint(payload, report_path=path,
                                       expected_report_sha256=digest(path))
        target = self.root / "checkpoint.json"
        target.write_text(json.dumps(checkpoint), encoding="utf-8")
        return target, checkpoint

    def test_cancel_before_provider_creates_only_one_cancelled_report(self):
        owner = self.owner()
        self.request(owner)
        path, payload, results = self.cancelled(owner)
        self.assertEqual(0, payload["cancellation"]["boundary_index"])
        self.assertEqual(["not_executed"] * 3, [r["status"] for r in results])
        self.assertEqual(["task_cancelled"] * 3, [r["reason"] for r in results])
        self.assertEqual([], self.adapter.calls)
        self.runner.assert_not_called()
        self.assertEqual({path.relative_to(self.root).as_posix()}, set(files(self.root)))

    def test_cancel_before_first_command_does_not_call_the_runner(self):
        self.select_steps(1, 2)
        owner = self.owner()
        self.request(owner)
        _, payload, _ = self.cancelled(owner)
        self.assertEqual(0, payload["cancellation"]["boundary_index"])
        self.runner.assert_not_called()
        self.assertFalse((self.root / "count.txt").exists())

    def test_foreign_owner_is_refused_before_reads_or_any_outputs(self):
        owner = self.owner(plan_sha256="c" * 64)
        before = files(self.root)
        with self.assertRaises(task_exec.TaskExecutionError):
            self.execute(cancellation=owner,
                         resume_report=".pocketchat/task-reports/missing.json",
                         expected_report_sha256="d" * 64)
        self.assertEqual(before, files(self.root))
        self.assertEqual([], self.adapter.calls)
        self.runner.assert_not_called()

    def test_cancel_after_confirmed_command_keeps_prefix_and_no_acceptance(self):
        _, _, payload, results = self.cancel_after_command()
        self.assertEqual(2, payload["cancellation"]["boundary_index"])
        self.assertEqual(["completed", "completed", "not_executed"],
                         [r["status"] for r in results])
        self.assertEqual("1", (self.root / "count.txt").read_text())
        self.assertEqual(2, len(self.adapter.calls))
        self.assertFalse((self.root / "last.md").exists())
        self.assertEqual("not_evaluated", payload["steps"][1]["evidence"]["task_acceptance"])
        task = tasking.load_record(self.root, results[0]["task_id"])
        self.assertEqual("completed", task.state)

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS, "requires secure descriptor-relative reads")
    def test_resume_with_fresh_owner_does_not_repeat_confirmed_side_effects(self):
        _, path, _, results = self.cancel_after_command()
        before = files(self.root)
        self.runner.side_effect = lambda template_id, trace, run_id: run_command_template(
            self.root, self.template, trace_path=trace, run_id=run_id)
        resumed = self.execute(cancellation=self.owner("2" * 32),
                               resume_report=path, expected_report_sha256=digest(path))
        self.assertEqual(["completed"] * 3, [r["status"] for r in resumed])
        self.assertEqual(results[0]["task_id"], resumed[0]["task_id"])
        self.assertEqual(1, self.runner.call_count)
        self.assertEqual("1", (self.root / "count.txt").read_text())
        self.assertEqual(4, len(self.adapter.calls))
        self.assertTrue(all(files(self.root)[name] == data for name, data in before.items()))
        summary = load_task_execution(self.root, results[0]["task_id"])
        self.assertEqual([], summary["evidence_issues"])
        self.assertEqual(1, len(summary["commands"]))
        self.assertEqual(3, len(summary["execution_steps"]))

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS, "requires secure descriptor-relative reads")
    def test_cancelled_report_requires_a_fresh_owner_before_runner_calls(self):
        owner, path, _, _ = self.cancel_after_command()
        for cancellation in (None, owner, self.owner()):
            with self.subTest(cancellation=cancellation):
                before = files(self.root)
                with self.assertRaises(TaskRecoveryError):
                    self.execute(cancellation=cancellation, resume_report=path,
                                 expected_report_sha256=digest(path))
                self.assertEqual(before, files(self.root))
                self.assertEqual(1, self.runner.call_count)
                self.assertEqual(2, len(self.adapter.calls))

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS, "requires secure descriptor-relative reads")
    def test_checkpoint_cancelled_boundary_resumes_only_the_unstarted_suffix(self):
        _, path, payload, _ = self.cancel_after_command()
        checkpoint_path, checkpoint = self.checkpoint(path, payload)
        self.assertEqual(["write_report", "count_once"], checkpoint["completed_step_ids"])
        self.assertEqual(["finish"], checkpoint["pending_step_ids"])
        resolved = resume_from_checkpoint(self.root, checkpoint_path,
                                          expected_spec_sha256=self.execution.spec_sha256,
                                          expected_plan_sha256=self.execution.plan_sha256)
        self.execute(cancellation=self.owner("2" * 32),
                     resume_report=resolved["report_path"],
                     expected_report_sha256=resolved["expected_report_sha256"])
        self.assertEqual(1, self.runner.call_count)
        self.assertEqual("1", (self.root / "count.txt").read_text())

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS, "requires secure descriptor-relative reads")
    def test_changed_prior_effect_is_refused_even_with_fresh_owner(self):
        _, path, _, _ = self.cancel_after_command()
        (self.root / "report.md").write_text("external change\n")
        before = files(self.root)
        with self.assertRaises(TaskRecoveryError):
            self.execute(cancellation=self.owner("2" * 32), resume_report=path,
                         expected_report_sha256=digest(path))
        self.assertEqual(before, files(self.root))
        self.assertEqual(2, len(self.adapter.calls))
        self.assertEqual(1, self.runner.call_count)

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS, "requires secure descriptor-relative reads")
    def test_cancel_after_final_confirmed_step_is_a_verified_resume_noop(self):
        self.select_steps(0, 2)
        _, path, payload, _ = self.cancel_after_command()
        self.assertEqual(2, payload["cancellation"]["boundary_index"])
        self.assertEqual(["completed", "completed"], [r["status"] for r in payload["steps"]])
        before = files(self.root)
        self.execute(cancellation=self.owner("2" * 32), resume_report=path,
                     expected_report_sha256=digest(path))
        self.assertEqual(before, files(self.root))
        self.assertEqual(1, self.runner.call_count)

    def test_failed_command_with_cancel_request_is_not_a_safe_boundary(self):
        self.select_steps(1)
        self.template["argv"] = [sys.executable, "-c", "raise SystemExit(3)"]
        owner = self.owner()
        def runner(template_id, trace, run_id):
            result = run_command_template(self.root, self.template,
                                          trace_path=trace, run_id=run_id)
            self.request(owner)
            return result
        self.runner.side_effect = runner
        results = self.execute(cancellation=owner)
        self.assertEqual("failed", results[0]["status"])
        path = self.root / results[0]["execution_report"]
        payload = json.loads(path.read_text())
        self.assertNotIn("cancellation", payload)
        before = files(self.root)
        with self.assertRaises(TaskRecoveryError):
            self.execute(cancellation=self.owner("2" * 32), resume_report=path,
                         expected_report_sha256=digest(path))
        self.assertEqual(before, files(self.root))
        self.assertEqual(1, self.runner.call_count)

    def test_model_final_message_without_tool_cannot_claim_safe_cancellation(self):
        self.select_steps(0, 1)
        self.adapter.responses = [final_response()]
        owner = self.owner()
        def finished_without_effect(**values):
            result = run_task(**values)
            self.request(owner)
            return result
        with self.assertRaises(task_exec.TaskExecutionError) as caught:
            self.execute(cancellation=owner, run_task=finished_without_effect)
        self.assertNotIsInstance(caught.exception, task_exec.TaskCancelledError)
        self.assertEqual("task cancellation needs confirmed step evidence", str(caught.exception))
        path = self.root / caught.exception.report
        report = json.loads(path.read_text())
        self.assertNotIn("cancellation", report)
        self.assertIn("error", report)
        self.assertFalse((self.root / "report.md").exists())

    def test_unknown_inflight_effect_preserves_failure_not_cancellation(self):
        self.select_steps(1, 2)
        owner = self.owner()
        def runner(*_):
            self.request(owner)
            raise KeyboardInterrupt("unknown in-flight command")
        self.runner.side_effect = runner
        with self.assertRaises(KeyboardInterrupt):
            self.execute(cancellation=owner)
        path = next((self.root / ".pocketchat/task-reports").glob("*.json"))
        payload = json.loads(path.read_text())
        self.assertEqual("KeyboardInterrupt", payload["error"]["type"])
        self.assertNotIn("cancellation", payload)
        before = files(self.root)
        with self.assertRaises(TaskRecoveryError):
            self.execute(cancellation=self.owner("2" * 32), resume_report=path,
                         expected_report_sha256=digest(path))
        self.assertEqual(before, files(self.root))
        self.assertEqual(1, self.runner.call_count)

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS, "requires secure descriptor-relative reads")
    def test_budgeted_cancellation_checkpoint_preserves_prior_consumption(self):
        from pocketchat.task_budget import TaskBudget
        owner = self.owner()
        def runner(template_id, trace, run_id):
            command = run_command_template(self.root, self.template,
                                           trace_path=trace, run_id=run_id)
            self.request(owner)
            return command
        self.runner.side_effect = runner
        path, payload, _ = self.cancelled(owner, task_budget=TaskBudget(limit_tokens=100000))
        ledger = payload["task_budget"]
        self.assertEqual(2, len(ledger["requests"]))
        checkpoint_path, checkpoint = self.checkpoint(path, payload)
        canonical = json.dumps(ledger, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(),
                         checkpoint["task_budget_sha256"])
        for budget in (None, TaskBudget(limit_tokens=99999)):
            with self.subTest(budget=budget):
                before = files(self.root)
                with self.assertRaises(TaskRecoveryError):
                    self.execute(cancellation=self.owner("2" * 32), task_budget=budget,
                                 resume_report=path, expected_report_sha256=digest(path))
                self.assertEqual(before, files(self.root))
                self.assertEqual(2, len(self.adapter.calls))
        resolved = resume_from_checkpoint(self.root, checkpoint_path)
        result = self.execute(cancellation=self.owner("2" * 32),
                              task_budget=TaskBudget(limit_tokens=100000),
                              resume_report=resolved["report_path"],
                              expected_report_sha256=resolved["expected_report_sha256"])
        successor = json.loads((self.root / result[-1]["execution_report"]).read_text())
        self.assertEqual(4, len(successor["task_budget"]["requests"]))
        self.assertEqual(1, self.runner.call_count)
        self.assertEqual("1", (self.root / "count.txt").read_text())
        previous = {row["request_id"]: row for row in ledger["requests"]}
        later = {row["request_id"]: row for row in successor["task_budget"]["requests"]}
        self.assertTrue(all(later[key] == value for key, value in previous.items()))

    def _assert_model_repair_cancellation_refused_before_effects(self, *, requested):
        owner = self.owner()
        if requested:
            self.request(owner)
        before = files(self.root)
        with self.assertRaisesRegex(task_exec.TaskExecutionError,
                                    "cancellation cannot be combined with model repair") as caught:
            self.execute(cancellation=owner, model_repair="model",
                         spec=self.spec, plan=self.plan)
        self.assertNotIsInstance(caught.exception, task_exec.TaskCancelledError)
        self.assertIsNone(caught.exception.report)
        self.assertEqual(before, files(self.root))
        self.assertEqual([], self.adapter.calls)
        self.runner.assert_not_called()

    def test_pending_owner_with_model_repair_is_refused_before_any_effect(self):
        self._assert_model_repair_cancellation_refused_before_effects(requested=False)

    def test_requested_owner_with_model_repair_creates_no_cancellation_report(self):
        self._assert_model_repair_cancellation_refused_before_effects(requested=True)

    def _assert_budget_failure_is_not_safe_cancellation(self, *, reported):
        from pocketchat.task_budget import TaskBudget
        self.select_steps(stop=2)
        owner = self.owner()
        self.adapter.responses[1]["usage_reported"] = reported
        if reported:
            self.adapter.responses[1]["usage"]["input_tokens"] = 100000
        complete = self.adapter.complete

        def request_on_final_response(*args, **kwargs):
            response = complete(*args, **kwargs)
            if response["finish_reason"] == "stop":
                self.request(owner)
            return response

        self.adapter.complete = request_on_final_response
        results = self.execute(cancellation=owner,
                               task_budget=TaskBudget(limit_tokens=100000))
        self.assertTrue(owner.requested)
        self.assertEqual("failed", results[0]["status"])
        self.assertEqual(["not_executed"],
                         [row["status"] for row in results[1:]])
        path = self.root / results[0]["execution_report"]
        payload = json.loads(path.read_text())
        self.assertNotIn("cancellation", payload)
        self.assertEqual("first\n", (self.root / "report.md").read_text())
        ledger = payload["task_budget"]
        self.assertEqual(2, len(ledger["requests"]))
        if reported:
            self.assertGreater(ledger["reserved_tokens"], ledger["limit_tokens"])
            self.assertTrue(all(row["usage_known"] for row in ledger["requests"]))
        else:
            self.assertEqual(1, sum(row["state"] == "unknown"
                                    for row in ledger["requests"]))
            self.assertGreater(ledger["reserved_tokens"], 3)
        _, checkpoint = self.checkpoint(path, payload)
        self.assertEqual([row["step_id"] for row in results],
                         checkpoint["pending_step_ids"])
        before = files(self.root)
        with self.assertRaises(TaskRecoveryError):
            self.execute(cancellation=self.owner("2" * 32),
                         task_budget=TaskBudget(limit_tokens=100000),
                         resume_report=path, expected_report_sha256=digest(path))
        self.assertEqual(before, files(self.root))
        self.assertEqual(2, len(self.adapter.calls))
        self.runner.assert_not_called()

    def test_unknown_final_usage_with_cancel_keeps_failure_and_existing_effect(self):
        self._assert_budget_failure_is_not_safe_cancellation(reported=False)

    def test_over_limit_final_usage_with_cancel_keeps_failure_and_consumption(self):
        self._assert_budget_failure_is_not_safe_cancellation(reported=True)

    def test_cancelled_command_resume_does_not_refund_an_exhausted_ledger(self):
        from pocketchat.task_budget import TaskBudget
        self.select_steps(start=1)
        owner = self.owner()
        budget = TaskBudget(limit_tokens=10000)
        budget.reserve(10000, request_id="prior-request")
        budget.settle("prior-request", input_tokens=9999, output_tokens=1,
                      reported=True)

        def runner(template_id, trace, run_id):
            command = run_command_template(self.root, self.template,
                                           trace_path=trace, run_id=run_id)
            self.request(owner)
            return command

        self.runner.side_effect = runner
        path, payload, _ = self.cancelled(owner, task_budget=budget)
        self.assertEqual(1, payload["cancellation"]["boundary_index"])
        self.assertEqual([], self.adapter.calls)
        original = path.read_bytes()
        results = self.execute(cancellation=self.owner("2" * 32),
                               task_budget=TaskBudget(limit_tokens=10000),
                               resume_report=path,
                               expected_report_sha256=digest(path))
        self.assertEqual(["completed", "failed"],
                         [row["status"] for row in results])
        successor = json.loads((self.root / results[-1]["execution_report"]).read_text())
        self.assertNotIn("cancellation", successor)
        self.assertEqual(payload["task_budget"], successor["task_budget"])
        self.assertEqual(original, path.read_bytes())
        self.assertEqual("1", (self.root / "count.txt").read_text())
        self.assertFalse((self.root / "last.md").exists())
        self.assertEqual([], self.adapter.calls)
        self.assertEqual(1, self.runner.call_count)
        successor_path = self.root / results[-1]["execution_report"]
        _, checkpoint = self.checkpoint(successor_path, successor)
        self.assertEqual(["finish"], checkpoint["pending_step_ids"])
        before = files(self.root)
        with self.assertRaises(TaskRecoveryError):
            self.execute(cancellation=self.owner("3" * 32),
                         task_budget=TaskBudget(limit_tokens=10000),
                         resume_report=successor_path,
                         expected_report_sha256=digest(successor_path))
        self.assertEqual(before, files(self.root))
        self.assertEqual([], self.adapter.calls)
        self.assertEqual(1, self.runner.call_count)

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS,
                         "acceptance requires secure descriptor-relative reads")
    def test_cancel_resume_keeps_prior_failed_acceptance_and_cumulative_evidence(self):
        from pocketchat.acceptance import (acceptance_history,
                                          parse_plan as parse_acceptance,
                                          verify_task)
        from pocketchat.cli import _task_report_payload
        _, path, _, results = self.cancel_after_command()
        task_id = results[0]["task_id"]
        acceptance = parse_acceptance({
            "schema_version": "pocketchat-acceptance-plan.v1",
            "checks": [{"id": "missing", "kind": "file_exists",
                        "path": "missing.md"}],
        })
        receipt = verify_task(self.root, task_id, acceptance)
        self.assertEqual("failed", receipt["state"])
        receipt_path = self.root / receipt["record"]
        receipt_bytes = receipt_path.read_bytes()
        record_bytes = (self.root / ".pocketchat" / "tasks" / (task_id + ".json")).read_bytes()
        prior_report = path.read_bytes()
        history = acceptance_history(self.root, task_id)
        self.execute(cancellation=self.owner("2" * 32), resume_report=path,
                     expected_report_sha256=digest(path))
        self.assertEqual(history, acceptance_history(self.root, task_id))
        report = _task_report_payload(self.root, tasking.load_record(self.root, task_id))
        self.assertEqual("failed", report["acceptance"]["state"])
        self.assertEqual(["write_report", "count_once", "finish"],
                         [row["step_id"] for row in report["execution_steps"]])
        self.assertEqual([], report["evidence_issues"])
        self.assertEqual(receipt_bytes, receipt_path.read_bytes())
        self.assertEqual(record_bytes,
                         (self.root / ".pocketchat" / "tasks" / (task_id + ".json")).read_bytes())
        self.assertEqual(prior_report, path.read_bytes())
        self.assertEqual(1, len(list(receipt_path.parent.glob("*.json"))))
        self.assertEqual(1, self.runner.call_count)
        self.assertEqual("1", (self.root / "count.txt").read_text())

    def test_checkpoint_refuses_conflicting_current_and_legacy_archive_digests(self):
        _, path, payload, _ = self.cancel_after_command()
        payload["steps"][1]["evidence"]["archive_sha256"] = "e" * 64
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(CheckpointError,
                                    "cancelled completed command has contradictory evidence"):
            create_checkpoint(payload, report_path=path,
                              expected_report_sha256=digest(path))

    def test_default_execution_does_not_add_cancellation_metadata(self):
        results = self.execute()
        payload = json.loads((self.root / results[0]["execution_report"]).read_text())
        self.assertNotIn("cancellation", payload)
        self.assertEqual(["completed"] * 3, [r["status"] for r in results])

    def test_existing_side_effect_readers_keep_the_cancelled_completed_prefix(self):
        from pocketchat.task_recovery import completed_side_effects, recovery_remaining_steps
        _, _, _, results = self.cancel_after_command()
        planned = [step.id for step in self.execution.steps]
        observed = completed_side_effects(self.root, results[0]["task_id"], planned=planned)
        self.assertEqual("completed", observed["state"])
        self.assertEqual(["write_report", "count_once"],
                         [step["step_id"] for step in observed["steps"]])
        self.assertEqual(["finish"], observed["remaining_steps"])
        self.assertEqual(["finish"], recovery_remaining_steps(
            self.root, results[0]["task_id"], planned))
        self.assertEqual([], observed["evidence_issues"])
        self.assertEqual(1, self.runner.call_count)

    def test_completed_command_contradictions_are_refused_by_report_and_checkpoint(self):
        _, path, original, _ = self.cancel_after_command()
        mutations = (
            lambda row: (row.update(exit_code=3), row["evidence"].update(exit_code=3)),
            lambda row: row.update(exit_code=True),
            lambda row: row["evidence"].update(timed_out=True),
            lambda row: row["evidence"].update(timed_out=0),
            lambda row: row.update(archive_sha256="e" * 64),
            lambda row: row["evidence"].update(archive_sha256="e" * 64),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                altered = copy.deepcopy(original)
                mutation(altered["steps"][1])
                path.write_text(json.dumps(altered), encoding="utf-8")
                with self.assertRaisesRegex(task_exec.TaskExecutionError,
                                            "cancelled completed command has contradictory evidence"):
                    task_exec._parse_execution_report(altered, path.name)
                with self.assertRaisesRegex(CheckpointError,
                                            "cancelled completed command has contradictory evidence"):
                    create_checkpoint(altered, report_path=path,
                                      expected_report_sha256=digest(path))
        self.assertEqual(1, self.runner.call_count)

    def test_cancellation_metadata_is_closed_and_consistent_in_all_readers(self):
        owner = self.owner()
        self.request(owner)
        path, payload, _ = self.cancelled(owner)
        original = copy.deepcopy(payload)
        mutations = (
            lambda p: p["cancellation"].update(state="completed"),
            lambda p: p["cancellation"].update(boundary_index=True),
            lambda p: p["cancellation"].update(boundary_index=1),
            lambda p: p["cancellation"].update(spec_sha256="c" * 64),
            lambda p: p["cancellation"].update(owner_sha256="bad"),
            lambda p: p["cancellation"].update(extra="private-canary"),
            lambda p: p["steps"][0].update(reason="step_boundary"),
            lambda p: p["steps"][1].update(step_id=p["steps"][0]["step_id"]),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                altered = copy.deepcopy(original)
                mutation(altered)
                path.write_text(json.dumps(altered), encoding="utf-8")
                with self.assertRaises(task_exec.TaskExecutionError):
                    task_exec._parse_execution_report(altered, path.name)
                with self.assertRaises(CheckpointError):
                    create_checkpoint(altered, report_path=path,
                                      expected_report_sha256=digest(path))
                before = files(self.root)
                with self.assertRaises(TaskRecoveryError):
                    self.execute(cancellation=self.owner("2" * 32), resume_report=path,
                                 expected_report_sha256=digest(path))
                self.assertEqual(before, files(self.root))
        self.assertEqual([], self.adapter.calls)
        self.runner.assert_not_called()
