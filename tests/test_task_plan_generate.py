"""CLI boundary for bounded TaskPlan generation."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat import cli
from pocketchat.provider import ProviderError, build_payload
from pocketchat.task_plan import (load_plan, parse_plan, plan_sha256,
                                  spec_sha256)
from pocketchat.task_spec import parse_spec
from tests._helpers import run_cli
from tests.test_task_plan import plan_payload
from tests.test_task_spec import minimal


class StubAdapter:
    name = "plan-generate-stub"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.payloads = []

    def build_payload(self, messages, config):
        payload = build_payload(messages, config)
        self.payloads.append(payload)
        return payload

    def complete(self, messages, config, api_key, on_delta=None):
        self.calls.append(list(messages))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return {"content": response}


class PlanGenerateCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "input.md").write_text("bounded input\n", encoding="utf-8")
        self.spec_payload = minimal()
        self.spec = parse_spec(self.spec_payload)
        self.spec_path = self.root / "spec.json"
        self.spec_path.write_text(json.dumps(self.spec_payload), encoding="utf-8")
        self.config_path = self.root / "pocketchat.json"
        self.config_path.write_text(json.dumps({
            "schema_version": "1",
            "provider": {
                "base_url": "http://example.invalid/v1",
                "model": "plan-model",
                "api_key_env": "POCKETCHAT_API_KEY",
            },
        }), encoding="utf-8")

    def args(self, *extra):
        return (
            "task", "plan", "generate",
            "--spec", str(self.spec_path),
            "--config", str(self.config_path),
            "--run-id", "plan_cli",
            *extra,
        )

    def bind_adapter(self, adapter):
        # ``profile_config`` rebuilds the flattened profile and therefore does
        # not preserve an injected adapter. Patch the owner function only for
        # the final selected profile, leaving fallback validation untouched.
        original = cli.profile_config
        def with_adapter(config, profile):
            record = original(config, profile)
            record["provider_adapter"] = adapter
            return record
        cli.profile_config = with_adapter
        self.addCleanup(setattr, cli, "profile_config", original)

    def valid_plan(self, revision=0):
        return json.dumps(plan_payload(spec_sha256(self.spec),
                                       revision=revision))

    def test_generate_persists_a_plan_and_prints_the_exact_exec_digest(self):
        adapter = StubAdapter([self.valid_plan()])
        self.bind_adapter(adapter)
        result = run_cli(self.args("--json"), self.root,
                         env={"POCKETCHAT_API_KEY": "not-a-real-key"})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual("generated", payload["status"])
        self.assertEqual(spec_sha256(self.spec), payload["spec_sha256"])
        plan_path = self.root / payload["plan"]
        self.assertTrue(plan_path.is_file())
        plan = parse_plan(payload["plan_document"], spec=self.spec)
        self.assertEqual(plan_sha256(plan), payload["plan_sha256"])
        self.assertEqual(plan_sha256(plan), plan_sha256(
            load_plan(plan_path, spec=self.spec)))
        self.assertEqual(["read_input"], [step["id"] for step in plan.steps])
        self.assertEqual(["traces/requests/plan_cli.attempt-1.json"],
                         payload["request_snapshots"])
        self.assertTrue((self.root / payload["request_snapshots"][0]).is_file())
        self.assertTrue((self.root / payload["preview_snapshot"]).is_file())
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(1, len(adapter.payloads))
        self.assertNotIn("not-a-real-key", result.stdout)
        self.assertFalse((self.root / ".pocketchat/task-reports").exists())
        self.assertFalse((self.root / ".pocketchat/tasks").exists())

    def test_generate_is_read_only_until_an_explicit_command(self):
        adapter = StubAdapter([self.valid_plan()])
        self.bind_adapter(adapter)
        result = run_cli(self.args("--json"), self.root,
                         env={"POCKETCHAT_API_KEY": "secret"})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        for path in self.root.rglob("*"):
            if path.is_file() and path.name not in ("spec.json", "pocketchat.json", "input.md"):
                self.assertNotIn("secret", path.read_text(errors="ignore"))
        self.assertFalse((self.root / "traces").joinpath("read_input.jsonl").exists())
        self.assertFalse((self.root / "sessions").exists())
        self.assertEqual(1, len(payload["request_snapshots"]))

    def test_invalid_plan_gets_one_revision_then_stops_without_a_plan(self):
        adapter = StubAdapter(["not json", self.valid_plan(revision=1)])
        self.bind_adapter(adapter)
        result = run_cli(self.args("--json"), self.root,
                         env={"POCKETCHAT_API_KEY": "not-a-real-key"})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(2, payload["attempts"])
        self.assertEqual("generated", payload["status"])
        self.assertEqual(2, len(payload["request_snapshots"]))
        self.assertEqual(1, payload["plan_document"]["revision"])
        self.assertEqual(2, len(adapter.calls))

    def test_provider_failure_is_exit_one_and_leaves_no_plan_snapshot(self):
        adapter = StubAdapter([ProviderError("offline", "no provider")])
        self.bind_adapter(adapter)
        result = run_cli(self.args("--json"), self.root,
                         env={"POCKETCHAT_API_KEY": "not-a-real-key"})
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual("provider_failed", payload["error"])
        self.assertFalse((self.root / "traces/requests/plan_cli.plan.json").exists())
        self.assertFalse((self.root / ".pocketchat/task-preview/plan_cli.json").exists())

    def test_bad_config_and_missing_credential_are_refused_before_provider(self):
        self.config_path.write_text("{not json\n", encoding="utf-8")
        result = run_cli(self.args("--json"), self.root, env={})
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertFalse((self.root / "traces/requests").exists())

        self.config_path.write_text(json.dumps({
            "schema_version": "1",
            "provider": {
                "base_url": "http://example.invalid/v1",
                "model": "plan-model",
                "api_key_env": "POCKETCHAT_API_KEY",
            },
        }), encoding="utf-8")
        result = run_cli(self.args("--json"), self.root, env={})
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("POCKETCHAT_API_KEY", payload["message"])
        self.assertFalse((self.root / "traces/requests").exists())

    def test_same_run_id_cannot_overwrite_existing_plan_or_request_snapshots(self):
        adapter = StubAdapter([self.valid_plan()])
        self.bind_adapter(adapter)
        first = run_cli(self.args("--json"), self.root,
                        env={"POCKETCHAT_API_KEY": "not-a-real-key"})
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        before = {path: path.read_bytes() for path in self.root.rglob("*")
                  if path.is_file()}
        second = run_cli(self.args("--json"), self.root,
                        env={"POCKETCHAT_API_KEY": "not-a-real-key"})
        self.assertEqual(2, second.returncode, second.stdout + second.stderr)
        after = {path: path.read_bytes() for path in self.root.rglob("*")
                 if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(1, len(adapter.calls))

    def test_explicit_out_path_must_stay_in_the_workspace(self):
        adapter = StubAdapter([self.valid_plan()])
        self.bind_adapter(adapter)
        result = run_cli(self.args("--json", "--out", "../outside.json"),
                         self.root, env={"POCKETCHAT_API_KEY": "not-a-real-key"})
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual([], adapter.calls)
        self.assertFalse((self.root.parent / "outside.json").exists())

    def test_generated_plan_and_digest_are_accepted_by_task_exec(self):
        adapter = StubAdapter([self.valid_plan()])
        self.bind_adapter(adapter)
        generated = run_cli(self.args("--json"), self.root,
                            env={"POCKETCHAT_API_KEY": "not-a-real-key"})
        self.assertEqual(0, generated.returncode,
                         generated.stdout + generated.stderr)
        payload = json.loads(generated.stdout)
        calls = []
        def fake_execute(execution, **kwargs):
            calls.append(execution)
            return [{"step_id": "read_input", "action": "read_file",
                     "status": "completed", "execution_report": "report.json"}]
        original = cli.execute_plan
        cli.execute_plan = fake_execute
        self.addCleanup(setattr, cli, "execute_plan", original)
        result = run_cli((
            "task", "exec", "--spec", str(self.spec_path),
            "--plan", payload["plan"],
            "--plan-sha256", payload["plan_sha256"],
            "--config", str(self.config_path), "--json",
        ), self.root, env={"POCKETCHAT_API_KEY": "not-a-real-key"})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(payload["plan_sha256"], calls[0].plan_sha256)

    def test_plain_output_path_is_created_only_after_validation(self):
        adapter = StubAdapter([self.valid_plan()])
        self.bind_adapter(adapter)
        target = self.root / "custom" / "nested" / "plan.json"
        result = run_cli(self.args("--json", "--out", "custom/nested/plan.json"),
                         self.root, env={"POCKETCHAT_API_KEY": "not-a-real-key"})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(target.is_file())
        payload = json.loads(result.stdout)
        self.assertEqual("custom/nested/plan.json", payload["plan"])

    def test_help_exposes_generate_and_required_inputs(self):
        result = run_cli(("task", "plan", "generate", "--help"), self.root)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("--spec", result.stdout)
        self.assertIn("--run-id", result.stdout)
        self.assertIn("--config", result.stdout)


if __name__ == "__main__":
    unittest.main()
