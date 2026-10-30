"""The task policy contract: closed fields, real bounds, fail-closed reads.

A task policy is what the operator signed: which tools may run, on which
paths, under which budgets. An unknown field cannot be silently accepted,
because the policy is the permission document -- a typo that reads as a
broader grant than the operator wrote is the failure this guards.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest

from pocketchat.task_policy import (POLICY_SCHEMA, TaskPolicyError,
                                    load_policy, parse_policy)


def minimal(**overrides):
    policy = {
        "schema_version": POLICY_SCHEMA,
        "objective": "compare two documents",
        "max_steps": 8,
        "max_tool_calls": 12,
        "max_context_chars": 48000,
        "max_turn_tokens": 8000,
        "max_session_tokens": 24000,
        "tools": {},
    }
    policy.update(overrides)
    return policy


class ParseTests(unittest.TestCase):
    def test_a_minimal_policy_parses_with_its_declared_values(self):
        policy = parse_policy(minimal())
        self.assertEqual(policy.objective, "compare two documents")
        self.assertEqual(policy.max_steps, 8)
        self.assertEqual(policy.max_tool_calls, 12)
        self.assertEqual(policy.max_context_chars, 48000)
        self.assertEqual(policy.max_turn_tokens, 8000)
        self.assertEqual(policy.max_session_tokens, 24000)

    def test_optional_budgets_default_to_disabled_not_to_unbounded(self):
        payload = minimal()
        del payload["max_session_tokens"]
        policy = parse_policy(payload)
        self.assertIsNone(policy.max_session_tokens)
        self.assertFalse(policy.delegate_enabled)

    def test_the_objective_must_be_a_non_empty_string(self):
        for bad in ("", "   ", None, 7, ["x"]):
            with self.subTest(objective=bad):
                with self.assertRaises(TaskPolicyError):
                    parse_policy(minimal(objective=bad))


    def test_the_workspace_root_defaults_to_the_current_directory(self):
        self.assertEqual(".", parse_policy(minimal()).workspace_root)
        policy = parse_policy(minimal(workspace_root="notes"))
        self.assertEqual("notes", policy.workspace_root)

    def test_an_unsafe_workspace_root_is_refused(self):
        for bad in ("", "/", "../out", "a/../b"):
            with self.subTest(workspace_root=bad):
                with self.assertRaises(TaskPolicyError):
                    parse_policy(minimal(workspace_root=bad))


class ClosedFieldTests(unittest.TestCase):
    def test_unknown_policy_field_is_refused(self):
        payload = minimal(execute_shell=True)
        with self.assertRaises(TaskPolicyError) as caught:
            parse_policy(payload)
        self.assertIn("execute_shell", str(caught.exception))

    def test_an_unknown_schema_version_names_the_version_and_is_refused(self):
        payload = minimal(schema_version="pocketchat-task-policy.v9")
        with self.assertRaises(TaskPolicyError) as caught:
            parse_policy(payload)
        self.assertIn("pocketchat-task-policy.v9", str(caught.exception))

    def test_an_unknown_tool_is_refused_rather_than_ignored(self):
        payload = minimal(tools={"run_command": {"allow_paths": ["a"]}})
        with self.assertRaises(TaskPolicyError) as caught:
            parse_policy(payload)
        self.assertIn("run_command", str(caught.exception))

    def test_an_unknown_field_inside_a_tool_is_refused(self):
        payload = minimal(tools={"read_file": {"allow_paths": ["a"],
                                               "recursive": True}})
        with self.assertRaises(TaskPolicyError) as caught:
            parse_policy(payload)
        self.assertIn("recursive", str(caught.exception))

    def test_a_non_object_policy_is_refused(self):
        with self.assertRaises(TaskPolicyError):
            parse_policy([minimal()])


class BoundsTests(unittest.TestCase):
    def test_out_of_bounds_budget_is_refused(self):
        for field, bad in (("max_steps", 0), ("max_steps", -1),
                           ("max_steps", 1000),
                           ("max_tool_calls", 0), ("max_tool_calls", 500),
                           ("max_context_chars", 0),
                           ("max_turn_tokens", 0),
                           ("max_session_tokens", 0),
                           ("max_steps", "8"), ("max_tool_calls", 1.5)):
            with self.subTest(field=field, value=bad):
                with self.assertRaises(TaskPolicyError):
                    parse_policy(minimal(**{field: bad}))

    def test_a_session_budget_below_one_turn_budget_is_refused(self):
        """A session that cannot hold its own first turn is a broken policy."""
        with self.assertRaises(TaskPolicyError):
            parse_policy(minimal(max_turn_tokens=8000, max_session_tokens=10))

    def test_memory_search_results_are_bounded(self):
        with self.assertRaises(TaskPolicyError):
            parse_policy(minimal(tools={"memory_search": {"max_results": 0}}))
        with self.assertRaises(TaskPolicyError):
            parse_policy(minimal(tools={"memory_search": {"max_results": 10000}}))


class ToolGrantTests(unittest.TestCase):
    def test_read_and_write_grants_keep_their_allowlists_in_order(self):
        policy = parse_policy(minimal(tools={
            "read_file": {"allow_paths": ["input/a.md", "input/b.md"]},
            "write_file": {"allow_paths": ["report.md"]},
        }))
        self.assertTrue(policy.tool_enabled("read_file"))
        self.assertEqual(policy.read_allow_paths, ["input/a.md", "input/b.md"])
        self.assertEqual(policy.write_allow_paths, ["report.md"])
        self.assertTrue(policy.write_create_only)

    def test_a_tool_without_an_allowlist_grants_nothing(self):
        policy = parse_policy(minimal(tools={"read_file": {}}))
        self.assertTrue(policy.tool_enabled("read_file"))
        self.assertEqual(policy.read_allow_paths, [])

    def test_an_empty_or_absolute_or_escaping_path_is_refused(self):
        for bad in ("", ".", "..", "/etc/passwd", "a/../../b", "a//b"):
            with self.subTest(path=bad):
                with self.assertRaises(TaskPolicyError):
                    parse_policy(minimal(
                        tools={"read_file": {"allow_paths": [bad]}}))

    def test_write_grants_are_create_only_and_cannot_be_widened(self):
        with self.assertRaises(TaskPolicyError):
            parse_policy(minimal(tools={"write_file": {"allow_paths": ["a"],
                                                       "create_only": False}}))

    def test_delegate_children_are_bounded_and_disabled_by_default(self):
        self.assertFalse(parse_policy(minimal()).delegate_enabled)
        policy = parse_policy(minimal(delegate={"enabled": True,
                                                "max_children": 2}))
        self.assertTrue(policy.delegate_enabled)
        self.assertEqual(policy.delegate_max_children, 2)
        with self.assertRaises(TaskPolicyError):
            parse_policy(minimal(delegate={"enabled": True, "max_children": 0}))
        with self.assertRaises(TaskPolicyError):
            parse_policy(minimal(delegate={"enabled": True, "max_children": 64}))


class LoadTests(unittest.TestCase):
    def test_a_policy_file_loads_from_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task-policy.json"
            path.write_text(json.dumps(minimal()), encoding="utf-8")
            self.assertEqual(load_policy(path).max_steps, 8)

    def test_a_missing_or_unparsable_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TaskPolicyError):
                load_policy(Path(directory) / "absent.json")
            broken = Path(directory) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            with self.assertRaises(TaskPolicyError):
                load_policy(broken)


class SchemaAgreementTests(unittest.TestCase):
    """The shipped schema and the parser must not drift apart."""

    def setUp(self):
        path = (Path(__file__).resolve().parents[1] / "pocketchat"
                / "schemas" / "task.schema.json")
        self.schema = json.loads(path.read_text(encoding="utf-8"))

    def test_the_schema_refuses_unknown_fields_at_every_level(self):
        """The root is a closed oneOf; each member closes its own fields."""
        self.assertEqual(
            [{"$ref": "#/$defs/policy"}, {"$ref": "#/$defs/task_record"}],
            self.schema["oneOf"])
        for name in ("policy", "task_record", "tool_grant", "step"):
            definition = self.schema["$defs"][name]
            self.assertFalse(definition["additionalProperties"], name)
        self.assertEqual({"schema_version", "objective", "max_steps",
                          "max_tool_calls", "max_context_chars",
                          "max_turn_tokens"},
                         set(self.schema["$defs"]["policy"]["required"]))

    def test_the_schema_declares_the_one_schema_version(self):
        self.assertEqual(self.schema["$defs"]["policy"]["properties"]
                         ["schema_version"]["const"], POLICY_SCHEMA)

    def test_the_schema_bounds_match_the_parser_bounds(self):
        from pocketchat import task_policy
        properties = self.schema["$defs"]["policy"]["properties"]
        self.assertEqual(properties["max_steps"]["minimum"],
                         task_policy.MIN_STEPS)
        self.assertEqual(properties["max_steps"]["maximum"],
                         task_policy.MAX_STEPS)
        self.assertEqual(properties["max_tool_calls"]["maximum"],
                         task_policy.MAX_TOOL_CALLS)
        self.assertEqual(properties["max_context_chars"]["maximum"],
                         task_policy.MAX_CONTEXT_CHARS)

    def test_the_schema_lists_exactly_the_tools_the_parser_accepts(self):
        from pocketchat import task_policy
        declared = set(self.schema["$defs"]["policy"]["properties"]["tools"]
                       ["properties"])
        self.assertEqual(declared, set(task_policy.TOOLS))

    def test_the_shipped_example_is_accepted_by_the_parser(self):
        example = (Path(__file__).resolve().parents[1] / "examples"
                   / "task-policy.json")
        self.assertTrue(example.is_file(), example)
        policy = load_policy(example)
        self.assertTrue(policy.tool_enabled("read_file"))


if __name__ == "__main__":
    unittest.main()
