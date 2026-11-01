"""TaskPlan v1: strict planning, spec binding and one bounded revision."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.provider import ProviderError, build_payload
from pocketchat.request import verify_request_snapshot
from pocketchat.task_plan import (PLAN_SCHEMA, PlanGenerationError,
                                  TaskPlanError, generate_plan, load_plan,
                                  parse_plan, plan_sha256, spec_sha256,
                                  verify_plan_snapshot)
from pocketchat.task_spec import parse_spec
from tests._helpers import run_cli
from tests.test_task_spec import minimal


def make_spec():
    return parse_spec(minimal())


def plan_payload(spec_sha256, *, revision=0):
    return {
        "schema_version": PLAN_SCHEMA,
        "spec_sha256": spec_sha256,
        "steps": [{
            "id": "read_input",
            "objective": "read the declared input",
            "action": "read_file",
            "inputs": ["input.md"],
            "expected_evidence": "the bounded file content",
            "depends_on": [],
            "retry_limit": 0,
        }],
        "revision": revision,
    }


class StubPlanAdapter:
    name = "task-plan-stub"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def build_payload(self, messages, config):
        return build_payload(messages, config)

    def complete(self, messages, config, api_key, on_delta=None):
        self.calls.append(list(messages))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return {"content": response}


class ParseTests(unittest.TestCase):
    def test_a_valid_plan_is_strictly_parsed_and_bound_to_its_spec(self):
        spec = make_spec()
        plan = parse_plan(plan_payload(spec_sha256(spec)), spec=spec)
        self.assertEqual(spec_sha256(spec), plan.spec_sha256)
        self.assertEqual("read_input", plan.steps[0]["id"])
        self.assertEqual(0, plan.revision)

    def test_duplicate_ids_cycles_unknown_actions_and_overreach_are_refused(self):
        spec = make_spec()
        digest = spec_sha256(spec)
        duplicate = plan_payload(digest)
        duplicate["steps"].append(copy.deepcopy(duplicate["steps"][0]))
        cycle = plan_payload(digest)
        cycle["steps"][0]["depends_on"] = ["later"]
        cycle["steps"].append({
            "id": "later", "objective": "later", "action": "read_file",
            "inputs": ["input.md"], "expected_evidence": "content",
            "depends_on": ["read_input"], "retry_limit": 0,
        })
        unknown_action = plan_payload(digest)
        unknown_action["steps"][0]["action"] = "shell"
        overreach = plan_payload(digest)
        overreach["steps"][0]["inputs"] = ["outside.txt"]
        too_many_retries = plan_payload(digest)
        too_many_retries["steps"][0]["retry_limit"] = 1
        too_many_steps = plan_payload(digest)
        too_many_steps["steps"] = [{
            "id": "read_%d" % index, "objective": "read", "action": "read_file",
            "inputs": ["input.md"], "expected_evidence": "content",
            "depends_on": [], "retry_limit": 0,
        } for index in range(9)]
        too_many_tool_calls = plan_payload(digest)
        too_many_tool_calls["steps"] = [{
            "id": "read_%d" % index, "objective": "read", "action": "read_file",
            "inputs": ["input.md"], "expected_evidence": "content",
            "depends_on": [], "retry_limit": 0,
        } for index in range(5)]
        for payload in (duplicate, cycle, unknown_action, overreach,
                        too_many_retries, too_many_steps,
                        too_many_tool_calls):
            with self.subTest(payload=payload):
                with self.assertRaises(TaskPlanError):
                    parse_plan(payload, spec=spec)

    def test_spec_hash_and_revision_are_not_optional(self):
        spec = make_spec()
        changed = plan_payload("0" * 64)
        with self.assertRaises(TaskPlanError):
            parse_plan(changed, spec=spec)
        payload = plan_payload(spec_sha256(spec))
        payload["revision"] = 2
        with self.assertRaises(TaskPlanError):
            parse_plan(payload, spec=spec)
        del payload["revision"]
        with self.assertRaises(TaskPlanError):
            parse_plan(payload, spec=spec)

    def test_a_plan_created_under_an_old_spec_is_inexecutable(self):
        old_spec = make_spec()
        old_plan = plan_payload(spec_sha256(old_spec))
        new_spec = parse_spec(minimal(objective="a different objective"))
        with self.assertRaises(TaskPlanError):
            parse_plan(old_plan, spec=new_spec)

    def test_unknown_fields_missing_step_fields_and_bad_types_are_refused(self):
        spec = make_spec()
        digest = spec_sha256(spec)
        unknown_root = plan_payload(digest)
        unknown_root["provider"] = "x"
        unknown_step = plan_payload(digest)
        unknown_step["steps"][0]["shell"] = "rm -rf"
        missing_evidence = plan_payload(digest)
        del missing_evidence["steps"][0]["expected_evidence"]
        bad_retry = plan_payload(digest)
        bad_retry["steps"][0]["retry_limit"] = True
        for payload in (unknown_root, unknown_step, missing_evidence, bad_retry):
            with self.subTest(payload=payload):
                with self.assertRaises(TaskPlanError):
                    parse_plan(payload, spec=spec)

    def test_write_requires_exactly_one_target(self):
        spec = parse_spec(minimal(tools={
            "write_file": {"allow_paths": ["report.md", "second.md"]},
        }))
        write = plan_payload(spec_sha256(spec))
        write["steps"][0].update({
            "action": "write_file", "inputs": ["report.md"],
            "expected_evidence": "report content",
        })
        self.assertEqual(["report.md"],
                         parse_plan(write, spec=spec).steps[0]["inputs"])
        for inputs in ([], ["report.md", "second.md"]):
            broken = copy.deepcopy(write)
            broken["steps"][0]["inputs"] = inputs
            with self.subTest(inputs=inputs):
                with self.assertRaises(TaskPlanError):
                    parse_plan(broken, spec=spec)

    def test_run_command_steps_reference_only_a_declared_template_id(self):
        from tests.test_task_spec import minimal
        spec = parse_spec(minimal(commands=[{
            "id": "run_tests",
            "argv": ["python3", "-c", "print('ok')"],
            "cwd": ".",
            "timeout_seconds": 10,
            "max_output_bytes": 256,
        }]))
        payload = plan_payload(spec_sha256(spec))
        payload["steps"][0].update({
            "action": "run_command",
            "inputs": ["run_tests"],
            "expected_evidence": "command evidence",
        })
        plan = parse_plan(payload, spec=spec)
        self.assertEqual("run_command", plan.steps[0]["action"])
        self.assertEqual(["run_tests"], plan.steps[0]["inputs"])
        for bad_input in ([], ["missing"], ["run_tests", "extra"]):
            broken = copy.deepcopy(payload)
            broken["steps"][0]["inputs"] = bad_input
            with self.subTest(bad_input=bad_input):
                with self.assertRaises(TaskPlanError):
                    parse_plan(broken, spec=spec)
        inline = copy.deepcopy(payload)
        inline["steps"][0]["argv"] = ["rm", "-rf", "x"]
        with self.assertRaises(TaskPlanError):
            parse_plan(inline, spec=spec)

    def test_schema_matches_the_parser_contract(self):
        path = (Path(__file__).resolve().parents[1] / "pocketchat" / "schemas"
                / "task-plan.schema.json")
        schema = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(PLAN_SCHEMA,
                         schema["properties"]["schema_version"]["const"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual({"const": 0},
                         schema["$defs"]["step"]["properties"]["retry_limit"])
        self.assertFalse(schema["$defs"]["step"]["additionalProperties"])


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = make_spec()
        self.config = {"profile": "stub", "model": "stub-model"}

    def valid_payload(self, revision=0):
        return json.dumps(plan_payload(spec_sha256(self.spec),
                                       revision=revision))

    def test_stub_generation_writes_a_verifiable_request_snapshot(self):
        adapter = StubPlanAdapter([self.valid_payload()])
        result = generate_plan(self.spec, adapter, self.config, api_key="fake-key",
                               requests_dir=self.root / "requests", run_id="plan_test")
        self.assertEqual(1, result.attempts)
        self.assertEqual(0, result.plan.revision)
        self.assertTrue(result.request_snapshots[0].is_file())
        self.assertTrue(verify_request_snapshot(result.request_snapshots[0]))
        self.assertTrue(result.plan_snapshot.is_file())
        self.assertEqual(plan_sha256(result.plan), result.plan_sha256)
        self.assertEqual(plan_sha256(result.plan),
                         plan_sha256(verify_plan_snapshot(
                             result.plan_snapshot, spec=self.spec)))
        snapshot_record = json.loads(result.plan_snapshot.read_text("utf-8"))
        self.assertEqual(["plan_test.attempt-1.json"],
                         snapshot_record["request_snapshots"])
        self.assertNotIn("fake-key", result.request_snapshots[0].read_text("utf-8"))
        self.assertNotIn("fake-key", result.plan_snapshot.read_text("utf-8"))

    def test_a_provider_echoed_credential_is_not_written_to_plan_snapshot(self):
        secret = "fake-plan-secret"
        payload = json.loads(self.valid_payload())
        payload["steps"][0]["objective"] = "echo " + secret
        adapter = StubPlanAdapter([json.dumps(payload)])
        with self.assertRaises(PlanGenerationError):
            generate_plan(self.spec, adapter, self.config, api_key=secret,
                          requests_dir=self.root / "requests",
                          run_id="plan_secret")
        self.assertFalse((self.root / "requests" / "plan_secret.plan.json").exists())

    def test_plan_snapshot_tampering_is_refused(self):
        adapter = StubPlanAdapter([self.valid_payload()])
        result = generate_plan(self.spec, adapter, self.config,
                               requests_dir=self.root / "requests",
                               run_id="plan_tamper")
        record = json.loads(result.plan_snapshot.read_text(encoding="utf-8"))
        record["plan"]["steps"][0]["objective"] = "changed after generation"
        result.plan_snapshot.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(TaskPlanError):
            verify_plan_snapshot(result.plan_snapshot, spec=self.spec)
        record = json.loads(result.plan_snapshot.read_text(encoding="utf-8"))
        record["attempts"] = 2
        record["request_snapshots"] = [
            "plan_tamper.attempt-1.json", "plan_tamper.attempt-2.json"]
        result.plan_snapshot.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(TaskPlanError):
            verify_plan_snapshot(result.plan_snapshot, spec=self.spec)

    def test_no_granted_action_is_refused_before_provider(self):
        spec = parse_spec(minimal(tools={}))
        adapter = StubPlanAdapter([self.valid_payload()])
        with self.assertRaises(PlanGenerationError):
            generate_plan(spec, adapter, self.config,
                          requests_dir=self.root / "requests",
                          run_id="plan_no_tools")
        self.assertEqual([], adapter.calls)

    def test_existing_plan_snapshot_blocks_a_second_generation(self):
        adapter = StubPlanAdapter([self.valid_payload()])
        generate_plan(self.spec, adapter, self.config,
                      requests_dir=self.root / "requests", run_id="plan_once")
        with self.assertRaises(PlanGenerationError):
            generate_plan(self.spec, adapter, self.config,
                          requests_dir=self.root / "requests", run_id="plan_once")
        self.assertEqual(1, len(adapter.calls))

    def test_one_invalid_plan_gets_exactly_one_revision(self):
        adapter = StubPlanAdapter(["not json", self.valid_payload(revision=1)])
        result = generate_plan(self.spec, adapter, self.config,
                               requests_dir=self.root / "requests", run_id="plan_retry")
        self.assertEqual(2, result.attempts)
        self.assertEqual(1, result.plan.revision)
        self.assertEqual(2, len(result.request_snapshots))
        plan_record = json.loads(result.plan_snapshot.read_text("utf-8"))
        self.assertEqual(
            ["plan_retry.attempt-1.json", "plan_retry.attempt-2.json"],
            plan_record["request_snapshots"])
        self.assertIn("not valid JSON", adapter.calls[1][-1]["content"])
        self.assertIn("Previous invalid plan text:\nnot json",
                      adapter.calls[1][-1]["content"])
        for snapshot in result.request_snapshots:
            self.assertTrue(verify_request_snapshot(snapshot), snapshot)

    def test_revision_is_revalidated_against_the_immutable_spec(self):
        wider = json.loads(self.valid_payload(revision=1))
        wider["steps"][0]["inputs"] = ["outside.txt"]
        adapter = StubPlanAdapter(["not json", json.dumps(wider)])
        with self.assertRaises(PlanGenerationError) as caught:
            generate_plan(self.spec, adapter, self.config,
                          requests_dir=self.root / "requests", run_id="plan_wider")
        self.assertEqual(2, caught.exception.attempts)
        self.assertEqual(2, len(adapter.calls))

    def test_a_second_bad_plan_never_produces_an_executable_plan(self):
        adapter = StubPlanAdapter(["not json", "still not json"])
        with self.assertRaises(PlanGenerationError) as caught:
            generate_plan(self.spec, adapter, self.config,
                          requests_dir=self.root / "requests", run_id="plan_bad")
        self.assertEqual(2, caught.exception.attempts)
        self.assertEqual(2, len(adapter.calls))
        self.assertFalse((self.root / "requests" / "plan_bad.plan.json").exists())

    def test_provider_failure_does_not_retry_or_return_a_plan(self):
        adapter = StubPlanAdapter([ProviderError("offline", "no provider")])
        with self.assertRaises(PlanGenerationError) as caught:
            generate_plan(self.spec, adapter, self.config,
                          requests_dir=self.root / "requests", run_id="plan_offline")
        self.assertEqual(1, caught.exception.attempts)
        self.assertEqual(1, len(adapter.calls))
        self.assertFalse((self.root / "requests" / "plan_offline.plan.json").exists())


class CliPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = make_spec()
        (self.root / "spec.json").write_text(json.dumps(self.spec.source), encoding="utf-8")

    def test_preview_is_read_only_and_exit_zero(self):
        (self.root / "plan.json").write_text(
            json.dumps(plan_payload(spec_sha256(self.spec))), encoding="utf-8")
        result = run_cli(("task", "plan", "preview", "plan.json",
                          "--spec", "spec.json", "--json"), self.root)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("read_input", payload["plan"]["steps"][0]["id"])
        self.assertFalse((self.root / ".pocketchat").exists())
        self.assertFalse((self.root / "traces").exists())

    def test_changed_spec_is_exit_two_without_artifacts(self):
        (self.root / "plan.json").write_text(
            json.dumps(plan_payload("0" * 64)), encoding="utf-8")
        result = run_cli(("task", "plan", "preview", "plan.json",
                          "--spec", "spec.json", "--json"), self.root)
        self.assertEqual(2, result.returncode)
        self.assertEqual("task", json.loads(result.stdout)["error"])
        self.assertFalse((self.root / ".pocketchat").exists())
        self.assertFalse((self.root / "traces").exists())

    def test_load_plan_refuses_invalid_json(self):
        (self.root / "bad-plan.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(TaskPlanError):
            load_plan(self.root / "bad-plan.json", spec=self.spec)

    def test_load_plan_reads_valid_json(self):
        path = self.root / "plan.json"
        path.write_text(json.dumps(plan_payload(spec_sha256(self.spec))), encoding="utf-8")
        self.assertEqual(0, load_plan(path, spec=self.spec).revision)


if __name__ == "__main__":
    unittest.main()
