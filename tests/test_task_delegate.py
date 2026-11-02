"""Parent-plan-authorized child delegation."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.task_delegate import (DELEGATION_REQUEST_SCHEMA,
                                      TaskDelegateError,
                                      authorize_delegation,
                                      parse_delegation_request)
from pocketchat.task_exec import ExecutionPlan, ExecutionStep
from pocketchat.task_spec import load_spec


def spec(path, **overrides):
    payload = {
        "schema_version": "pocketchat-task-spec.v1",
        "objective": "parent task",
        "workspace_root": ".",
        "inputs": ["src", "docs", "out"],
        "budgets": {"max_steps": 6, "max_tool_calls": 6,
                    "max_context_chars": 8000, "max_turn_tokens": 4000,
                    "max_session_tokens": 8000},
        "tools": {
            "read_file": {"allow_paths": ["src", "docs"]},
            "write_file": {"allow_paths": ["out"], "create_only": True},
        },
        "commands": [],
        "acceptance": {"path": "acceptance.json"},
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_spec(path)


def execution(parsed, step_count=3):
    steps = []
    for index in range(1, step_count + 1):
        steps.append(ExecutionStep(
            "s%d" % index, "read_file", None, "prompt", []))
    return ExecutionPlan(parsed.source and "a" * 64, "b" * 64,
                         "acceptance.json", tuple(steps))


def _plan_digest_spec(parsed):
    from pocketchat.task_plan import spec_sha256
    return spec_sha256(parsed)


def request(**overrides):
    payload = {
        "schema_version": DELEGATION_REQUEST_SCHEMA,
        "delegation_id": "deleg-1",
        "parent_plan_sha256": "b" * 64,
        "parent_step_id": "s1",
        "children": [
            {"child_id": "c1", "objective": "read the input",
             "action": "read_file", "inputs": ["src/a.py"]},
            {"child_id": "c2", "objective": "read the doc",
             "action": "read_file", "inputs": ["docs/b.md"]},
        ],
    }
    payload.update(overrides)
    return payload


class ParseTests(unittest.TestCase):
    def test_valid_request_is_closed_and_rederived(self):
        payload = request()
        self.assertEqual(payload, parse_delegation_request(payload))

    def test_malformed_request_is_refused(self):
        good = request()
        cases = []
        for mutate in (
            lambda row: row.update(extra=True),
            lambda row: row.update(schema_version="wrong"),
            lambda row: row.update(delegation_id=""),
            lambda row: row.update(parent_plan_sha256="0" * 63),
            lambda row: row.update(parent_step_id=""),
            lambda row: row.update(children=[]),
            lambda row: row["children"][0].update(action="delete_file"),
            lambda row: row["children"][0].update(inputs=["../escape.py"]),
            lambda row: row["children"][0].update(inputs=[]),
            lambda row: row["children"][0].update(extra=1),
            lambda row: row["children"][1].update(child_id="c1"),
            lambda row: row["children"][0].update(child_id=""),
        ):
            broken = json.loads(json.dumps(good))
            mutate(broken)
            cases.append(broken)
        for broken in cases:
            with self.subTest(broken=broken):
                with self.assertRaises(TaskDelegateError):
                    parse_delegation_request(broken)


class AuthorizeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.parsed = spec(self.root / "spec.json")
        self.plan = execution(self.parsed)

    def tearDown(self):
        self._tmp.cleanup()

    def test_authorized_children_are_narrowed_and_bound(self):
        result = authorize_delegation(request(), self.plan, self.parsed)
        self.assertEqual(DELEGATION_REQUEST_SCHEMA, result["schema_version"])
        self.assertEqual(["c1", "c2"],
                         [row["child_id"] for row in result["children"]])
        self.assertEqual("b" * 64, result["parent_plan_sha256"])
        for row in result["children"]:
            self.assertEqual("s1", row["parent_step_id"])

    def test_child_cannot_widen_parent_permissions(self):
        for mutate in (
            lambda row: row["children"][0].update(inputs=["secrets/x.py"]),
            lambda row: row["children"][0].update(inputs=["src/../docs/b.md"]),
            lambda row: row["children"][0].update(action="write_file"),
            lambda row: row["children"][0].update(action="run_command"),
            lambda row: row["children"][0].update(inputs=["/etc/passwd"]),
        ):
            broken = request()
            mutate(broken)
            with self.subTest(broken=broken):
                with self.assertRaises(TaskDelegateError):
                    authorize_delegation(broken, self.plan, self.parsed)

    def test_parent_step_must_exist_in_the_plan(self):
        broken = request(parent_step_id="missing")
        with self.assertRaises(TaskDelegateError):
            authorize_delegation(broken, self.plan, self.parsed)

    def test_plan_digest_must_match(self):
        with self.assertRaisesRegex(TaskDelegateError, "plan"):
            authorize_delegation(request(parent_plan_sha256="f" * 64),
                                 self.plan, self.parsed)

    def test_child_count_is_bounded(self):
        many = request()
        many["children"] = [
            {"child_id": "c%d" % index, "objective": "read",
             "action": "read_file", "inputs": ["src/a.py"]}
            for index in range(40)]
        with self.assertRaises(TaskDelegateError):
            authorize_delegation(many, self.plan, self.parsed)


class CliTests(unittest.TestCase):
    def test_preview_is_read_only_and_reports_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parsed = spec(root / "spec.json")
            from pocketchat.task_plan import spec_sha256 as _spec_digest
            plan = {
                "schema_version": "pocketchat-task-plan.v1",
                "spec_sha256": _spec_digest(parsed),
                "revision": 0,
                "steps": [
                    {"id": "s1", "objective": "read a",
                     "action": "read_file", "inputs": ["src"],
                     "expected_evidence": "content", "depends_on": [],
                     "retry_limit": 0},
                    {"id": "s2", "objective": "read b",
                     "action": "read_file", "inputs": ["docs"],
                     "expected_evidence": "content", "depends_on": ["s1"],
                     "retry_limit": 0},
                ],
            }
            (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            from pocketchat.task_plan import load_plan as _load_plan
            from pocketchat.task_plan import plan_sha256 as _plan_digest
            loaded = _load_plan(root / "plan.json", spec=parsed)
            deleg = request(parent_plan_sha256=_plan_digest(loaded))
            (root / "deleg.json").write_text(json.dumps(deleg), encoding="utf-8")
            from tests._helpers import run_cli
            result = run_cli(("task", "delegate", "preview", "deleg.json",
                              "--plan", "plan.json", "--spec", "spec.json",
                              "--json"), root)
            self.assertEqual(0, result.returncode,
                             result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(2, len(payload["children"]))
            self.assertIn("s1", payload["parent_step_id"])
            self.assertFalse((root / ".pocketchat").exists())
            self.assertFalse((root / "traces").exists())


    def test_overreaching_child_is_exit_two_without_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parsed = spec(root / "spec.json")
            from pocketchat.task_plan import (load_plan as _load_plan,
                                              plan_sha256 as _plan_digest)
            plan = {
                "schema_version": "pocketchat-task-plan.v1",
                "spec_sha256": _plan_digest_spec(parsed),
                "revision": 0,
                "steps": [
                    {"id": "s1", "objective": "read a",
                     "action": "read_file", "inputs": ["src"],
                     "expected_evidence": "content", "depends_on": [],
                     "retry_limit": 0},
                ],
            }
            (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            loaded = _load_plan(root / "plan.json", spec=parsed)
            deleg = request(parent_plan_sha256=_plan_digest(loaded))
            deleg["children"][0]["inputs"] = ["secrets/leak.py"]
            (root / "deleg.json").write_text(json.dumps(deleg), encoding="utf-8")
            from tests._helpers import run_cli
            result = run_cli(("task", "delegate", "preview", "deleg.json",
                              "--plan", "plan.json", "--spec", "spec.json",
                              "--json"), root)
            self.assertEqual(2, result.returncode)
            self.assertEqual("task", json.loads(result.stdout)["error"])
            self.assertFalse((root / ".pocketchat").exists())
            self.assertFalse((root / "traces").exists())


if __name__ == "__main__":
    unittest.main()
