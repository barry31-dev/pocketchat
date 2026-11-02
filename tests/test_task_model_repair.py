"""Model-authored repair: bounded evidence, one action, one retry."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pocketchat.provider import build_payload
from pocketchat.task_command import run_command_template
from pocketchat.task_exec import TaskExecutionError, authorize, execute_plan
from pocketchat.task_model_repair import (
    ModelRepairError,
    attempt_model_repair,
    parse_model_repair_action,
)
from pocketchat.task_plan import parse_plan, plan_sha256, spec_sha256
from pocketchat.task_spec import parse_spec
from tests.test_task_plan import plan_payload
from tests.test_task_spec import minimal


class StubAdapter:
    name = "model-repair-stub"

    def __init__(self, content):
        self.content = content
        self.calls = []
        self.payloads = []

    def build_payload(self, messages, config):
        payload = build_payload(messages, config)
        self.payloads.append(payload)
        return payload

    def complete(self, messages, config, api_key, on_delta=None):
        self.calls.append(list(messages))
        return {"content": self.content}


def make_spec(root):
    payload = minimal(
        inputs=["input.txt", "counter.txt", "answer.txt"],
        overwrite_paths=["counter.txt", "answer.txt"],
        tools={
            "read_file": {"allow_paths": ["input.txt", "counter.txt"]},
            "write_file": {"allow_paths": ["counter.txt", "answer.txt"]},
        },
        commands=[{
            "id": "check",
            "argv": ["python3", "-c", (
                "from pathlib import Path; print(Path('input.txt').read_text().strip()); "
                "raise SystemExit(0 if Path('answer.txt').read_text() == 'ok\\n' else 1)"
            )],
            "cwd": ".",
            "timeout_seconds": 5,
            "max_output_bytes": 64,
        }],
    )
    return parse_spec(payload)


def make_execution(spec, step_id="check"):
    payload = plan_payload(spec_sha256(spec))
    payload["steps"][0].update({
        "id": step_id,
        "objective": "check the bounded answer",
        "action": "run_command",
        "inputs": ["check"],
        "expected_evidence": "exit 0 and a bounded output digest",
    })
    plan = parse_plan(payload, spec=spec)
    return payload, plan


def action(*, path="answer.txt", content="ok\n", reason="replace the stale answer"):
    return {
        "action": "overwrite_file",
        "path": path,
        "content": content,
        "reason": reason,
    }


class ParseTests(unittest.TestCase):
    def test_action_needs_a_nonempty_path_grant(self):
        with self.assertRaisesRegex(ModelRepairError, "at least one"):
            parse_model_repair_action(action(), overwrite_paths=[])

    def test_action_is_closed_and_requires_an_overwrite_path(self):
        self.assertEqual("answer.txt", parse_model_repair_action(
            action(), overwrite_paths=["answer.txt"])["path"])
        for bad in (
            {**action(), "argv": ["sh", "-c", "true"]},
            {**action(), "shell": "echo ok"},
            {**action(), "path": "../answer.txt"},
            {**action(), "path": "/tmp/answer.txt"},
            {**action(), "path": "not-authorized.txt"},
            {**action(), "content": b"ok"},
            {**action(), "reason": ""},
        ):
            with self.subTest(bad=bad), self.assertRaises(ModelRepairError):
                parse_model_repair_action(bad, overwrite_paths=["answer.txt"])


class AttemptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "input.txt").write_text("private input\n", encoding="utf-8")
        (self.root / "answer.txt").write_text("wrong\n", encoding="utf-8")
        (self.root / "counter.txt").write_text("0\n", encoding="utf-8")
        for args in (("init",), ("config", "user.name", "Test"),
                     ("config", "user.email", "test@example.invalid"),
                     ("add", "input.txt", "answer.txt", "counter.txt"),
                     ("commit", "-m", "baseline")):
            proc = subprocess.run(["git", "-C", str(self.root), *args],
                                  capture_output=True, text=True, check=False)
            if proc.returncode:
                self.fail(proc.stderr)
        self.spec = make_spec(self.root)
        self.plan_payload, self.plan = make_execution(self.spec)
        self.config = {
            "profile": "stub", "model": "repair-model",
            "base_url": "http://example.invalid/v1", "api_key_env": "",
            "requires_api_key": False, "timeout_seconds": 5,
            "max_context_chars": 8000, "summary_prefix_chars": 80,
        }

    def run_first(self):
        trace = self.root / "traces" / "check.jsonl"
        return run_command_template(
            self.root, self.spec.commands[0], trace_path=trace, run_id="check")

    def test_model_action_is_applied_and_exactly_one_identical_retry_runs(self):
        template = self.spec.commands[0]
        first = self.run_first()
        self.assertFalse(first.ok)
        adapter = StubAdapter(json.dumps(action()))
        config = {**self.config, "provider_adapter": adapter}
        result, details = attempt_model_repair(
            command_runner=lambda template_id, trace, run_id: run_command_template(
                self.root, template, trace_path=trace, run_id=run_id),
            template_id="check", trace_path=self.root / "traces/check.jsonl",
            run_id="check", failed=first, spec=self.spec, plan=self.plan,
            config=config, api_key="", root=self.root)
        self.assertTrue(result.ok)
        self.assertEqual("repaired", details["outcome"])
        self.assertEqual("model", details["repair_mode"])
        self.assertEqual([1, 0], [item["exit_code"] for item in details["commands"]])
        self.assertEqual(template["argv"], details["commands"][0]["argv"])
        self.assertEqual(details["commands"][0]["template_sha256"],
                         details["commands"][1]["template_sha256"])
        self.assertEqual("ok\n", (self.root / "answer.txt").read_text())
        edit = details["action"]
        self.assertNotEqual(edit["original_sha256"], edit["new_sha256"])
        self.assertEqual(edit["new_sha256"],
                         __import__("hashlib").sha256((self.root / "answer.txt").read_bytes()).hexdigest())
        self.assertFalse(details["task_acceptance_pass"])
        self.assertEqual(1, len(adapter.calls))
        prompt = adapter.calls[0][0]["content"]
        self.assertIn("private input", prompt)
        self.assertNotIn("trace.jsonl", json.dumps(details))

    def test_unexpected_provider_build_failure_leaves_file_and_retry_untouched(self):
        first = self.run_first()
        class BrokenAdapter(StubAdapter):
            def build_payload(self, messages, config):
                raise RuntimeError("provider configuration failed")
        calls = []
        with self.assertRaisesRegex(ModelRepairError, "payload failed"):
            attempt_model_repair(
                command_runner=lambda *args: calls.append(args),
                template_id="check",
                trace_path=self.root / "traces/check.jsonl", run_id="check",
                failed=first, spec=self.spec, plan=self.plan,
                config={**self.config, "provider_adapter": BrokenAdapter("")},
                api_key="", root=self.root)
        self.assertEqual([], calls)
        self.assertEqual("wrong\n", (self.root / "answer.txt").read_text())

    def test_unexpected_command_runner_failure_does_not_get_a_second_attempt(self):
        first = self.run_first()
        adapter = StubAdapter(json.dumps(action()))
        calls = []
        def broken_runner(*args):
            calls.append(args)
            raise RuntimeError("runner failed once")
        with self.assertRaisesRegex(RuntimeError, "runner failed once"):
            attempt_model_repair(
                command_runner=broken_runner, template_id="check",
                trace_path=self.root / "traces/check.jsonl", run_id="check",
                failed=first, spec=self.spec, plan=self.plan,
                config={**self.config, "provider_adapter": adapter},
                api_key="", root=self.root)
        self.assertEqual(1, len(calls))

    def test_edit_conflict_after_proposal_causes_zero_retry(self):
        first = self.run_first()
        adapter = StubAdapter(json.dumps(action()))
        from pocketchat import task_model_repair
        original_snapshot = task_model_repair.snapshot_overwrite
        def stale_snapshot(*args, **kwargs):
            snapshot = original_snapshot(*args, **kwargs)
            (self.root / "answer.txt").write_text("concurrent\n", encoding="utf-8")
            return snapshot
        calls = []
        with mock.patch.object(task_model_repair, "snapshot_overwrite",
                               side_effect=stale_snapshot), \
                self.assertRaises(ModelRepairError):
            attempt_model_repair(
                command_runner=lambda *args: calls.append(args),
                template_id="check",
                trace_path=self.root / "traces/check.jsonl",
                run_id="check", failed=first, spec=self.spec,
                plan=self.plan,
                config={**self.config, "provider_adapter": adapter},
                api_key="", root=self.root)
        self.assertEqual([], calls)
        self.assertEqual("concurrent\n", (self.root / "answer.txt").read_text())

    def test_receipt_binds_request_snapshot_bytes(self):
        first = self.run_first()
        adapter = StubAdapter(json.dumps(action()))
        result, details = attempt_model_repair(
            command_runner=lambda template_id, trace, run_id: run_command_template(
                self.root, self.spec.commands[0], trace_path=trace,
                run_id=run_id),
            template_id="check", trace_path=self.root / "traces/check.jsonl",
            run_id="check", failed=first, spec=self.spec, plan=self.plan,
            config={**self.config, "provider_adapter": adapter},
            api_key="", root=self.root)
        self.assertTrue(result.ok)
        snapshot = self.root / details["request_snapshots"][0]
        import hashlib
        self.assertEqual(hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                         details["request_snapshot_sha256"][0])

    def test_failure_preview_containing_the_credential_is_not_sent(self):
        secret = "sk-private-preview-value"
        self.spec = make_spec(self.root)
        template = dict(self.spec.commands[0])
        template["argv"] = [
            "python3", "-c", (
                "import sys; print(sys.argv[1], file=sys.stderr); raise SystemExit(1)"
            ),
            secret,
        ]
        trace = self.root / "traces" / "secret.jsonl"
        first = run_command_template(self.root, template, trace_path=trace,
                                     run_id="secret")
        adapter = StubAdapter(json.dumps(action()))
        with self.assertRaisesRegex(ModelRepairError, "failure evidence"):
            attempt_model_repair(
                command_runner=lambda *args: (), template_id="check",
                trace_path=trace, run_id="check", failed=first,
                spec=self.spec, plan=self.plan,
                config={**self.config, "provider_adapter": adapter},
                api_key=secret, root=self.root)
        self.assertEqual([], adapter.calls)
        self.assertEqual([], adapter.payloads)

    def test_failed_template_identity_must_match_the_step(self):
        first = self.run_first()
        first = type(first)(
            "other", first.cwd, first.exit_code, first.timed_out,
            first.duration_ms, first.stdout, first.stderr,
            first.stdout_bytes, first.stderr_bytes, first.stdout_sha256,
            first.stderr_sha256, first.stdout_truncated,
            first.stderr_truncated, first.trace_path, first.task_acceptance)
        adapter = StubAdapter(json.dumps(action()))
        with self.assertRaisesRegex(ModelRepairError, "does not match"):
            attempt_model_repair(
                command_runner=lambda *args: (), template_id="check",
                trace_path=self.root / "traces/check.jsonl", run_id="check",
                failed=first, spec=self.spec, plan=self.plan,
                config={**self.config, "provider_adapter": adapter},
                api_key="", root=self.root)
        self.assertEqual([], adapter.calls)

    def test_provider_exception_does_not_change_the_file(self):
        first = self.run_first()
        from pocketchat.provider import ProviderError
        class FailingAdapter(StubAdapter):
            def complete(self, messages, config, api_key, on_delta=None):
                raise ProviderError("offline", "provider unavailable")
        calls = []
        with self.assertRaisesRegex(ModelRepairError, "provider failed"):
            attempt_model_repair(
                command_runner=lambda *args: calls.append(args),
                template_id="check", trace_path=self.root / "traces/check.jsonl",
                run_id="check", failed=first, spec=self.spec, plan=self.plan,
                config={**self.config, "provider_adapter": FailingAdapter("")},
                api_key="", root=self.root)
        self.assertEqual([], calls)
        self.assertEqual("wrong\n", (self.root / "answer.txt").read_text())

    def test_same_failure_receipt_is_preserved_when_trace_path_is_relative(self):
        first = self.run_first()
        adapter = StubAdapter(json.dumps(action(content="still wrong\n")))
        calls = []
        def runner(template_id, trace, run_id):
            calls.append((template_id, trace, run_id))
            if not trace.is_absolute():
                trace = self.root / trace
            return run_command_template(
                self.root, self.spec.commands[0], trace_path=trace,
                run_id=run_id)
        result, details = attempt_model_repair(
            command_runner=runner, template_id="check",
            trace_path="traces/check.jsonl", run_id="check", failed=first,
            spec=self.spec, plan=self.plan,
            config={**self.config, "provider_adapter": adapter},
            api_key="", root=self.root)
        self.assertEqual("same_failure", details["outcome"])
        self.assertEqual(1, len(calls))
        self.assertEqual("traces/check.retry-1.jsonl", str(calls[0][1]))
        self.assertTrue((self.root / ".pocketchat/task-repair/check.json").is_file())

    def test_model_output_is_not_persisted_after_success(self):
        first = self.run_first()
        replacement = "ok\n"
        adapter = StubAdapter(json.dumps(action(content=replacement)))
        result, details = attempt_model_repair(
            command_runner=lambda template_id, trace, run_id: run_command_template(
                self.root, self.spec.commands[0], trace_path=trace,
                run_id=run_id),
            template_id="check", trace_path=self.root / "traces/check.jsonl",
            run_id="check", failed=first, spec=self.spec, plan=self.plan,
            config={**self.config, "provider_adapter": adapter},
            api_key="", root=self.root)
        self.assertTrue(result.ok)
        serialized = json.dumps(details, sort_keys=True)
        self.assertNotIn("content", serialized)
        receipt = self.root / ".pocketchat/task-repair/check.json"
        text = receipt.read_text(errors="ignore")
        self.assertNotIn("\"content\":", text)
        self.assertNotIn(replacement, text)

    def test_command_start_failure_is_not_eligible_for_model_repair(self):
        class StartFailure:
            ok = False
            exit_code = None
            timed_out = False
        adapter = StubAdapter(json.dumps(action()))
        with self.assertRaisesRegex(ModelRepairError, "exit status"):
            attempt_model_repair(
                command_runner=lambda *args: (), template_id="check",
                trace_path=self.root / "traces/check.jsonl", run_id="check",
                failed=StartFailure(), spec=self.spec, plan=self.plan,
                config={**self.config, "provider_adapter": adapter},
                api_key="", root=self.root)
        self.assertEqual([], adapter.calls)

    def test_unauthorized_action_names_no_file_change_and_no_retry(self):
        first = self.run_first()
        calls = []
        adapter = StubAdapter(json.dumps(action(path="outside.txt")))
        with self.assertRaises(ModelRepairError):
            attempt_model_repair(
                command_runner=lambda *args: calls.append(args),
                template_id="check", trace_path=self.root / "traces/check.jsonl",
                run_id="check", failed=first, spec=self.spec, plan=self.plan,
                config={**self.config, "provider_adapter": adapter}, api_key="",
                root=self.root)
        self.assertEqual([], calls)
        self.assertEqual("wrong\n", (self.root / "answer.txt").read_text())

    def test_no_action_or_bad_json_stops_before_edit_and_retry(self):
        first = self.run_first()
        calls = []
        for content in ("", "not json", json.dumps({"action": "noop"})):
            with self.subTest(content=content):
                adapter = StubAdapter(content)
                with self.assertRaises(ModelRepairError):
                    attempt_model_repair(
                        command_runner=lambda *args: calls.append(args),
                        template_id="check",
                        trace_path=self.root / "traces/check.jsonl",
                        run_id="check", failed=first, spec=self.spec,
                        plan=self.plan,
                        config={**self.config, "provider_adapter": adapter},
                        api_key="", root=self.root)
        self.assertEqual([], calls)
        self.assertEqual("wrong\n", (self.root / "answer.txt").read_text())

    def test_same_failure_fingerprint_stops_without_second_repair(self):
        first = self.run_first()
        adapter = StubAdapter(json.dumps(action(content="still wrong\n")))
        calls = []
        result, details = attempt_model_repair(
            command_runner=lambda template_id, trace, run_id: (
                calls.append((template_id, trace, run_id)) or run_command_template(
                    self.root, self.spec.commands[0], trace_path=trace, run_id=run_id)),
            template_id="check", trace_path=self.root / "traces/check.jsonl",
            run_id="check", failed=first, spec=self.spec, plan=self.plan,
            config={**self.config, "provider_adapter": adapter}, api_key="",
            root=self.root)
        self.assertFalse(result.ok)
        self.assertEqual("same_failure", details["outcome"])
        self.assertEqual(1, len(calls))
        self.assertEqual(details["failure_fingerprint"],
                         details["retry_failure_fingerprint"])
        self.assertEqual("model", details["repair_mode"])
        self.assertFalse(details["task_acceptance_pass"])

    def test_model_cannot_smuggle_argv_shell_or_unauthorized_paths(self):
        first = self.run_first()
        for extra in (
            {"argv": ["sh", "-c", "id"]},
            {"shell": True},
            {"tools": {"write_file": {"allow_paths": ["/tmp/x"]}}},
        ):
            with self.subTest(extra=extra):
                adapter = StubAdapter(json.dumps({**action(), **extra}))
                with self.assertRaises(ModelRepairError):
                    attempt_model_repair(
                        command_runner=lambda *args: (), template_id="check",
                        trace_path=self.root / "traces/check.jsonl",
                        run_id="check", failed=first, spec=self.spec,
                        plan=self.plan,
                        config={**self.config, "provider_adapter": adapter},
                        api_key="", root=self.root)
                self.assertEqual("wrong\n", (self.root / "answer.txt").read_text())

    def test_secret_in_action_is_not_persisted_or_printed(self):
        first = self.run_first()
        secret = "sk-private-action-value"
        adapter = StubAdapter(json.dumps(action(content=secret)))
        with self.assertRaises(ModelRepairError):
            attempt_model_repair(
                command_runner=lambda *args: (), template_id="check",
                trace_path=self.root / "traces/check.jsonl", run_id="check",
                failed=first, spec=self.spec, plan=self.plan,
                config={**self.config, "provider_adapter": adapter},
                api_key=secret, root=self.root)
        for path in self.root.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                self.assertNotIn(secret, path.read_text(errors="ignore"))


class ExecutePlanIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "input.txt").write_text("bounded input\n", encoding="utf-8")
        (self.root / "answer.txt").write_text("wrong\n", encoding="utf-8")
        for args in (("init",), ("config", "user.name", "Test"),
                     ("config", "user.email", "test@example.invalid"),
                     ("add", "input.txt", "answer.txt"),
                     ("commit", "-m", "baseline")):
            proc = subprocess.run(["git", "-C", str(self.root), *args],
                                  capture_output=True, text=True, check=False)
            if proc.returncode:
                self.fail(proc.stderr)
        self.spec = make_spec(self.root)
        _, self.plan = make_execution(self.spec)
        self.execution = authorize(
            self.plan, self.spec,
            expected_plan_sha256=plan_sha256(self.plan))

    def runner(self, template_id, trace_path, run_id):
        return run_command_template(
            self.root, self.spec.commands[0], trace_path=trace_path,
            run_id=run_id)

    def test_execute_plan_completes_after_one_model_repair(self):
        adapter = StubAdapter(json.dumps(action()))
        config = {"provider_adapter": adapter, "model": "repair-model"}
        results = execute_plan(
            self.execution, run_task=lambda **_: None,
            workspace=self.root, config=config,
            trace_dir=self.root / "traces", session_dir=self.root / "sessions",
            command_runner=self.runner, model_repair="model",
            spec=self.spec, plan=self.plan)
        self.assertEqual("completed", results[0]["status"], results[0])
        repair = results[0]["evidence"]["repair"]
        self.assertEqual("model", repair["repair_mode"])
        self.assertFalse(repair["task_acceptance_pass"])
        report = json.loads((self.root / results[0]["execution_report"]).read_text())
        evidence = report["steps"][0]["evidence"]["repair"]
        self.assertEqual("repaired", evidence["outcome"])
        self.assertNotIn("content", evidence["action"])
        self.assertFalse(evidence["task_acceptance_pass"])

    def test_execute_plan_rejects_unbound_repair_sources(self):
        with self.assertRaisesRegex(TaskExecutionError, "does not match"):
            execute_plan(
                self.execution, run_task=lambda **_: None,
                workspace=self.root, config={},
                trace_dir=self.root / "traces",
                session_dir=self.root / "sessions",
                command_runner=self.runner, model_repair="model",
                spec=parse_spec(minimal()), plan=self.plan)


if __name__ == "__main__":
    unittest.main()
