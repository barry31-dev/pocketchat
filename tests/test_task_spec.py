"""TaskSpec v1 contract: strict input, reuse of task policy, read-only preview."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.task_policy import POLICY_SCHEMA, parse_policy
from pocketchat.task_spec import (SPEC_SCHEMA, TaskSpecError, load_spec,
                                  parse_spec)
from tests._helpers import run_cli


def minimal(**overrides):
    spec = {
        "schema_version": SPEC_SCHEMA,
        "objective": "write one bounded report",
        "workspace_root": ".",
        "inputs": ["input.md"],
        "budgets": {
            "max_steps": 8,
            "max_tool_calls": 4,
            "max_context_chars": 8000,
            "max_turn_tokens": 2000,
            "max_session_tokens": 6000,
        },
        "tools": {"read_file": {"allow_paths": ["input.md"]}},
        "commands": [],
        "acceptance": {"path": "acceptance.json"},
    }
    spec.update(overrides)
    return spec


class SchemaAgreementTests(unittest.TestCase):
    def test_schema_is_closed_and_uses_the_parser_version(self):
        path = Path(__file__).resolve().parents[1] / "pocketchat" / "schemas" / "task-spec.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(SPEC_SCHEMA, schema["properties"]["schema_version"]["const"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual("array", schema["properties"]["commands"]["type"])
        self.assertFalse(schema["$defs"]["tool_grant"]["additionalProperties"])

class ParseTests(unittest.TestCase):
    def test_minimal_spec_converts_to_an_equivalent_policy(self):
        spec = parse_spec(minimal())
        policy = spec.to_policy()
        self.assertEqual(POLICY_SCHEMA, policy.schema_version)
        self.assertEqual("write one bounded report", policy.objective)
        self.assertEqual(8, policy.max_steps)
        self.assertEqual(["input.md"], policy.read_allow_paths)
        self.assertEqual("acceptance.json", spec.acceptance_path)
        self.assertEqual(parse_policy(policy.source), policy)

    def test_unknown_fields_and_unsupported_capabilities_are_refused(self):
        for payload in (
            minimal(provider="x"),
            minimal(commands=[{"argv": ["rm", "-rf", "x"]}]),
            minimal(tools={"write_file": {"allow_paths": ["x"], "overwrite": True}}),
            minimal(tools={"execute_shell": {}}),
            minimal(tools={"write_file": {"allow_paths": ["x"], "create_only": False}}),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(TaskSpecError):
                    parse_spec(payload)

    def test_paths_budgets_and_types_fail_closed(self):
        for payload in (
            minimal(workspace_root="../out"),
            minimal(inputs=["/etc/passwd"]),
            minimal(tools={"read_file": {"allow_paths": ["a/../../b"]}}),
            minimal(budgets={"max_steps": 0}),
            minimal(budgets={"max_steps": "8"}),
            minimal(acceptance={"path": "../acceptance.json"}),
            minimal(acceptance={}),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(TaskSpecError):
                    parse_spec(payload)

    def test_a_spec_file_loads_from_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            path.write_text(json.dumps(minimal()), encoding="utf-8")
            self.assertEqual(load_spec(path).objective, "write one bounded report")


    def test_command_templates_are_closed_and_bound_to_the_spec(self):
        command = {
            "id": "run_tests",
            "argv": ["python3", "-m", "unittest"],
            "cwd": ".",
            "timeout_seconds": 30,
            "max_output_bytes": 4096,
        }
        parsed = parse_spec(minimal(commands=[command]))
        self.assertEqual(["run_tests"], [item["id"] for item in parsed.source["commands"]])
        for bad in (
            {"id": "run_tests", "argv": [], "cwd": ".", "timeout_seconds": 30, "max_output_bytes": 4096},
            {"id": "run_tests", "argv": ["sh", "-c", "rm -rf x"], "cwd": ".", "timeout_seconds": 30, "max_output_bytes": 4096},
            {"id": "run_tests", "argv": ["python3"], "cwd": "../x", "timeout_seconds": 30, "max_output_bytes": 4096},
            {"id": "run_tests", "argv": ["python3"], "cwd": ".", "timeout_seconds": 0, "max_output_bytes": 4096},
            {"id": "run_tests", "argv": ["python3"], "cwd": ".", "timeout_seconds": 30, "max_output_bytes": 1},
            {"id": "run_tests", "argv": ["python3"], "cwd": ".", "timeout_seconds": 30, "max_output_bytes": 4096, "shell": True},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(TaskSpecError):
                    parse_spec(minimal(commands=[bad]))


class CliPreviewTests(unittest.TestCase):
    def test_preview_is_read_only_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "spec.json").write_text(json.dumps(minimal()), encoding="utf-8")
            result = run_cli(("task", "spec", "preview", "spec.json", "--json"), root)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual("", result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse((root / ".pocketchat").exists())
            self.assertFalse((root / "traces").exists())
            self.assertEqual("write one bounded report", payload["policy"]["objective"])

    def test_preview_refusal_is_exit_two_without_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = minimal(budgets={"max_steps": 0})
            (root / "spec.json").write_text(json.dumps(payload), encoding="utf-8")
            result = run_cli(("task", "spec", "preview", "spec.json", "--json"), root)
            self.assertEqual(2, result.returncode)
            self.assertEqual("", result.stderr)
            self.assertFalse((root / ".pocketchat").exists())
            self.assertFalse((root / "traces").exists())
            self.assertEqual("task", json.loads(result.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
