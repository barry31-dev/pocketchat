"""TaskPlan execution conversion: authorize, narrow, then reuse run_task."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pocketchat.agent import run_task
from pocketchat.task_exec import (TaskExecutionError, authorize, execute_plan)
from pocketchat.task_plan import (TaskPlanError, parse_plan, plan_sha256,
                                  spec_sha256)
from pocketchat.task_spec import parse_spec
from tests.test_task_plan import plan_payload
from tests.test_task_run import FakeTaskAdapter, final_response, model_tool
from tests.test_task_spec import minimal


def make_spec(**overrides):
    return parse_spec(minimal(**overrides))


def authorize_payload(spec, *, revision=0):
    payload = plan_payload(spec_sha256(spec), revision=revision)
    return parse_plan(payload, spec=spec)


class AuthorizationTests(unittest.TestCase):
    def test_authorize_binds_both_hashes_and_narrows_each_step(self):
        spec = make_spec(
            inputs=["input.md", "second.md"],
            tools={
                "read_file": {"allow_paths": ["input.md", "second.md"]},
                "write_file": {"allow_paths": ["report.md"]},
            })
        payload = plan_payload(spec_sha256(spec))
        payload["steps"].append({
            "id": "write_report",
            "objective": "write the bounded report",
            "action": "write_file",
            "inputs": ["report.md"],
            "expected_evidence": "report.md contents",
            "depends_on": ["read_input"],
            "retry_limit": 0,
        })
        plan = parse_plan(payload, spec=spec)
        execution = authorize(plan, spec, expected_plan_sha256=plan_sha256(plan))
        self.assertEqual(spec_sha256(spec), execution.spec_sha256)
        self.assertEqual(plan_sha256(plan), execution.plan_sha256)
        self.assertEqual("acceptance.json", execution.acceptance_path)
        self.assertEqual(["read_input", "write_report"],
                         [step.id for step in execution.steps])
        self.assertEqual(["input.md"],
                         execution.steps[0].policy.read_allow_paths)
        self.assertEqual(["report.md"],
                         execution.steps[1].policy.write_allow_paths)
        self.assertTrue(execution.steps[1].policy.write_create_only)
        self.assertEqual(1, execution.steps[1].policy.max_tool_calls)

    def test_plan_mutation_and_wrong_authorization_are_refused_before_run(self):
        spec = make_spec()
        plan = authorize_payload(spec)
        good = plan_sha256(plan)
        with self.assertRaises(TaskExecutionError):
            authorize(plan, spec, expected_plan_sha256="not-a-digest")
        with self.assertRaises(TaskExecutionError):
            authorize(plan, spec, expected_plan_sha256="0" * 64)
        plan.source["steps"][0]["inputs"] = ["outside.txt"]
        with self.assertRaises(TaskExecutionError):
            authorize(plan, spec, expected_plan_sha256=good)

    def test_changed_spec_is_refused(self):
        old_spec = make_spec()
        plan = authorize_payload(old_spec)
        new_spec = make_spec(objective="a different objective")
        with self.assertRaises(TaskExecutionError):
            authorize(plan, new_spec, expected_plan_sha256=plan_sha256(plan))

    def test_unauthorized_action_is_refused_before_conversion(self):
        spec = make_spec()
        payload = plan_payload(spec_sha256(spec))
        payload["steps"][0]["action"] = "shell"
        with self.assertRaises(TaskPlanError):
            parse_plan(payload, spec=spec)


    def test_command_step_requires_an_explicit_runner_and_passes_only_the_template_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = make_spec(commands=[{
                "id": "run_tests", "argv": ["python3", "-c", "print('ok')"],
                "cwd": ".", "timeout_seconds": 5, "max_output_bytes": 64,
            }])
            payload = plan_payload(spec_sha256(spec))
            payload["steps"][0].update({
                "action": "run_command", "inputs": ["run_tests"],
                "expected_evidence": "command evidence"})
            plan = parse_plan(payload, spec=spec)
            execution = authorize(plan, spec,
                                  expected_plan_sha256=plan_sha256(plan))
            self.assertEqual("run_command", execution.steps[0].action)
            with self.assertRaises(TaskExecutionError):
                execute_plan(execution, run_task=lambda **_: None,
                             workspace=root, config={},
                             trace_dir=root / "traces",
                             session_dir=root / "sessions")
            calls = []
            def runner(template_id, trace_path, run_id):
                calls.append((template_id, trace_path, run_id))
                return type("Command", (), {
                    "ok": True, "trace_path": str(trace_path), "exit_code": 0,
                    "to_dict": lambda self: {"template_id": template_id,
                                             "exit_code": 0,
                                             "stdout_sha256": "a" * 64},
                })()
            results = execute_plan(
                execution, run_task=lambda **_: None, command_runner=runner,
                workspace=root, config={}, trace_dir=root / "traces",
                session_dir=root / "sessions")
            self.assertEqual(1, len(calls))
            self.assertEqual("run_tests", calls[0][0])
            self.assertEqual("completed", results[0]["status"])
            self.assertEqual("a" * 64,
                             results[0]["evidence"]["stdout_sha256"])


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "input.md").write_text("bounded input\n", encoding="utf-8")
        self.spec = make_spec()
        self.plan = authorize_payload(self.spec)
        self.execution = authorize(
            self.plan, self.spec, expected_plan_sha256=plan_sha256(self.plan))

    def test_execute_plan_reuses_run_task_in_dependency_order(self):
        calls = []

        def fake_run_task(**kwargs):
            calls.append(kwargs)
            return type("Result", (), {
                "task_id": "task_%d" % len(calls),
                "status": "completed",
            })()

        results = execute_plan(
            self.execution, run_task=fake_run_task, workspace=self.root,
            config={}, trace_dir=self.root / "traces",
            session_dir=self.root / "sessions")
        self.assertEqual(1, len(calls))
        self.assertEqual("completed", results[0]["status"])
        self.assertEqual(["input.md"],
                         calls[0]["policy"].read_allow_paths)
        self.assertIn("Expected evidence", calls[0]["prompts"][0])
        self.assertEqual(self.root / "traces" / "read_input.jsonl",
                         calls[0]["trace_path"])
        self.assertEqual(self.root / "sessions" / "read_input.jsonl",
                         calls[0]["session_path"])

    def test_failure_stops_later_steps(self):
        spec = make_spec(tools={
            "read_file": {"allow_paths": ["input.md"]},
        })
        payload = plan_payload(spec_sha256(spec))
        payload["steps"].append({
            "id": "second",
            "objective": "second read",
            "action": "read_file",
            "inputs": ["input.md"],
            "expected_evidence": "second content",
            "depends_on": ["read_input"],
            "retry_limit": 0,
        })
        plan = parse_plan(payload, spec=spec)
        execution = authorize(plan, spec, expected_plan_sha256=plan_sha256(plan))
        calls = []

        def fake_run_task(**kwargs):
            calls.append(kwargs)
            return type("Result", (), {"task_id": "task_one", "status": "failed"})()

        results = execute_plan(
            execution, run_task=fake_run_task, workspace=self.root,
            config={}, trace_dir=self.root / "traces",
            session_dir=self.root / "sessions")
        self.assertEqual(1, len(calls))
        self.assertEqual("not_executed", results[1]["status"])
        self.assertEqual("previous_step_did_not_complete", results[1]["reason"])

    def test_real_run_task_with_stub_adapter_completes_one_step(self):
        class Adapter(FakeTaskAdapter):
            name = "task-exec-stub"

        adapter = Adapter([
            model_tool("read", "read_file", {"path": "input.md"}),
            final_response(),
        ])
        config = {
            "profile": "stub", "model": "task-model",
            "base_url": "http://example.invalid/v1", "api_key_env": "",
            "requires_api_key": False, "timeout_seconds": 5,
            "max_context_chars": 8000, "summary_prefix_chars": 80,
            "provider_adapter": adapter,
        }
        results = execute_plan(
            self.execution, run_task=run_task, workspace=self.root,
            config=config, trace_dir=self.root / "traces",
            session_dir=self.root / "sessions")
        self.assertEqual("completed", results[0]["status"], results[0])
        self.assertTrue((self.root / "traces" / "read_input.jsonl").is_file())
        self.assertTrue((self.root / "sessions" / "read_input.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
