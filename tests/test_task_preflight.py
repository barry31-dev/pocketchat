"""Pure task preflight: supplied observations, never the live workspace."""

from __future__ import annotations

import copy
import unittest
from unittest import mock

from pocketchat.task_plan import parse_plan, plan_sha256, spec_sha256
from pocketchat.task_preflight import check_task_preflight
from pocketchat.task_spec import parse_spec
from pocketchat.task_snapshot import CONTEXT_SNAPSHOT_SCHEMA, _snapshot_digest

DIGEST = "a" * 64
SNAPSHOT_BODY = {
    "schema_version": CONTEXT_SNAPSHOT_SCHEMA,
    "tree_schema": "pocketchat-task-context-tree.v1",
    "tree_sha256": "b" * 64,
    "selection_sha256": "c" * 64,
    "query_terms": [],
    "selection_truncated": False,
    "total_matches": 0,
    "selected_files": 0,
    "max_bytes": 1024,
    "total_bytes": 0,
    "selection_size_changed_files": 0,
    "files": [],
}
SNAPSHOT = _snapshot_digest(SNAPSHOT_BODY)


def spec():
    return parse_spec({
        "schema_version": "pocketchat-task-spec.v1",
        "objective": "Update one declared file.",
        "workspace_root": ".",
        "inputs": ["README.md", "docs/out.md"],
        "budgets": {
            "max_steps": 2,
            "max_tool_calls": 2,
            "max_context_chars": 100,
            "max_turn_tokens": 100,
        },
        "tools": {
            "read_file": {"allow_paths": ["README.md"]},
            "write_file": {"allow_paths": ["docs/out.md"], "create_only": True},
        },
        "commands": [],
        "acceptance": {"path": "acceptance.json"},
    })


def plan(task_spec):
    return parse_plan({
        "schema_version": "pocketchat-task-plan.v1",
        "spec_sha256": spec_sha256(task_spec),
        "steps": [{
            "id": "write-doc",
            "objective": "Create the documented output.",
            "action": "write_file",
            "inputs": ["docs/out.md"],
            "expected_evidence": "The file exists.",
            "depends_on": [],
            "retry_limit": 0,
        }],
        "revision": 0,
    }, spec=task_spec)


def snapshot():
    value = copy.deepcopy(SNAPSHOT_BODY)
    value["snapshot_sha256"] = SNAPSHOT
    return value


def observation(task_spec, task_plan, **changes):
    value = {
        "workspace_id": "workspace-a",
        "workspace_roots": ["workspace-a"],
        "base_commit": DIGEST,
        "head_commit": DIGEST,
        "is_git": True,
        "tracked_paths": ["README.md"],
        "untracked_paths": [],
        "dirty_paths": [],
        "dirty_coverage": "complete",
        "source_coverage": "complete",
        "spec_sha256": spec_sha256(task_spec),
        "plan_sha256": plan_sha256(task_plan),
        "snapshot_sha256": SNAPSHOT,
    }
    value.update(changes)
    return value


class TaskPreflightTests(unittest.TestCase):
    def test_mutated_spec_still_has_to_satisfy_the_existing_parser(self):
        task_spec = spec()
        task_spec.source["budgets"]["max_tool_calls"] = True
        task_plan = plan(task_spec)
        report = check_task_preflight(
            task_spec, task_plan, snapshot(), observation(task_spec, task_plan))
        self.assertEqual("refused", report["status"])
        self.assertIn("invalid_spec", {item["reason"] for item in report["refusals"]})

    def test_plan_from_a_different_spec_is_not_bound_by_separate_hashes(self):
        task_spec = spec()
        foreign_source = copy.deepcopy(task_spec.source)
        foreign_source["objective"] = "An independently approved task."
        foreign_spec = parse_spec(foreign_source)
        foreign_plan = plan(foreign_spec)
        report = check_task_preflight(
            task_spec, foreign_plan, snapshot(),
            observation(task_spec, foreign_plan))
        self.assertEqual("refused", report["status"])
        self.assertIn("plan_spec_digest_mismatch",
                      {item["reason"] for item in report["refusals"]})

    def test_read_step_cannot_be_claimed_as_an_observed_write(self):
        source = copy.deepcopy(spec().source)
        source["tools"]["read_file"]["allow_paths"].append("docs/out.md")
        task_spec = parse_spec(source)
        source = copy.deepcopy(plan(task_spec).source)
        source["steps"][0]["action"] = "read_file"
        task_plan = parse_plan(source, spec=task_spec)
        report = check_task_preflight(
            task_spec, task_plan, snapshot(),
            observation(task_spec, task_plan, write_paths=["docs/out.md"]))
        self.assertEqual("refused", report["status"])
        self.assertIn("out_of_scope_write",
                      {item["reason"] for item in report["refusals"]})

    def test_mutated_plan_read_input_is_revalidated_against_the_read_grant(self):
        task_spec = spec()
        task_plan = plan(task_spec)
        task_plan.source["steps"][0]["action"] = "read_file"
        task_plan.source["steps"][0]["inputs"] = ["docs/foreign.md"]
        report = check_task_preflight(
            task_spec, task_plan, snapshot(), observation(task_spec, task_plan))
        self.assertEqual("refused", report["status"])
        self.assertIn("invalid_plan", {item["reason"] for item in report["refusals"]})

    def test_explicit_empty_write_observation_cannot_hide_a_planned_write(self):
        task_spec = spec()
        task_plan = plan(task_spec)
        report = check_task_preflight(
            task_spec, task_plan, snapshot(),
            observation(task_spec, task_plan, write_paths=[]))
        self.assertEqual("refused", report["status"])
        self.assertIn("write_observation_mismatch",
                      {item["reason"] for item in report["refusals"]})

    def test_missing_path_sets_are_unknown_even_with_complete_coverage_labels(self):
        task_spec = spec()
        task_plan = plan(task_spec)
        for field in ("tracked_paths", "untracked_paths", "dirty_paths"):
            with self.subTest(field=field):
                value = observation(task_spec, task_plan)
                del value[field]
                report = check_task_preflight(task_spec, task_plan, snapshot(), value)
                self.assertEqual("unknown", report["status"])
                self.assertIn(field, {item["id"] for item in report["unknowns"]})
                self.assertIn(field, {item["id"] for item in report["omissions"]})

    def test_git_state_is_a_boolean_observation_not_a_truthy_string(self):
        task_spec = spec()
        task_plan = plan(task_spec)
        report = check_task_preflight(
            task_spec, task_plan, snapshot(),
            observation(task_spec, task_plan, is_git="false"))
        self.assertEqual("refused", report["status"])
        self.assertIn("invalid_git_state", {item["reason"] for item in report["refusals"]})

    def test_raw_path_budget_precedes_deduplication_and_is_aggregate(self):
        task_spec = spec()
        task_plan = plan(task_spec)
        cases = (
            {"tracked_paths": ["README.md"] * 4097},
            {"tracked_paths": ["README.md"] * 2048,
             "dirty_paths": ["docs/out.md"] * 2048,
             "write_paths": ["docs/out.md"]},
        )
        for changes in cases:
            with self.subTest(fields=tuple(changes)):
                report = check_task_preflight(
                    task_spec, task_plan, snapshot(),
                    observation(task_spec, task_plan, **changes))
                self.assertEqual("refused", report["status"])
                self.assertIn("path_limit", {item["reason"] for item in report["refusals"]})

    def test_snapshot_body_digest_is_recomputed_without_echoing_content(self):
        task_spec = spec()
        task_plan = plan(task_spec)
        value = snapshot()
        value["query_terms"] = ["SENTINEL-SNAPSHOT-BODY"]
        report = check_task_preflight(
            task_spec, task_plan, value, observation(task_spec, task_plan))
        self.assertEqual("refused", report["status"])
        self.assertIn("snapshot_digest_mismatch",
                      {item["reason"] for item in report["refusals"]})
        self.assertNotIn("SENTINEL-SNAPSHOT-BODY", str(report))

    def test_noncanonical_snapshot_body_is_refused_without_echo(self):
        task_spec = spec()
        task_plan = plan(task_spec)
        value = snapshot()
        value["query_terms"] = ["SENTINEL-SNAPSHOT-BODY" + chr(0xD800)]
        report = check_task_preflight(
            task_spec, task_plan, value, observation(task_spec, task_plan))
        self.assertEqual("refused", report["status"])
        self.assertNotIn("SENTINEL-SNAPSHOT-BODY", str(report))

    def test_wrong_snapshot_schema_is_not_a_verified_snapshot(self):
        task_spec = spec()
        task_plan = plan(task_spec)
        value = snapshot()
        value["schema_version"] = "foreign.v1"
        value["snapshot_sha256"] = _snapshot_digest(value)
        report = check_task_preflight(
            task_spec, task_plan, value,
            observation(task_spec, task_plan,
                        snapshot_sha256=value["snapshot_sha256"]))
        self.assertEqual("refused", report["status"])
        self.assertIn("snapshot_schema_mismatch",
                      {item["reason"] for item in report["refusals"]})

    def test_git_sha1_commit_observations_can_be_complete(self):
        task_spec = spec()
        task_plan = plan(task_spec)
        report = check_task_preflight(
            task_spec, task_plan, snapshot(),
            observation(task_spec, task_plan,
                        base_commit="c" * 40, head_commit="c" * 40))
        self.assertEqual("allow", report["status"])

    def test_git_sha1_head_drift_is_refused(self):
        task_spec = spec()
        task_plan = plan(task_spec)
        report = check_task_preflight(
            task_spec, task_plan, snapshot(),
            observation(task_spec, task_plan,
                        base_commit="c" * 40, head_commit="d" * 40))
        self.assertEqual("refused", report["status"])
        self.assertIn("head_drift", {item["reason"] for item in report["refusals"]})

    def test_empty_dirty_paths_with_partial_coverage_is_unknown(self):
        spec_value = spec()
        plan_value = plan(spec_value)
        report = check_task_preflight(
            spec_value, plan_value, snapshot(),
            observation(spec_value, plan_value, dirty_coverage="partial"))
        self.assertEqual("unknown", report["status"])
        self.assertEqual("partial_dirty_coverage", report["unknowns"][0]["reason"])

    def test_complete_clean_observation_is_allowed(self):
        spec_value = spec()
        plan_value = plan(spec_value)
        report = check_task_preflight(
            spec_value, plan_value, snapshot(), observation(spec_value, plan_value))
        self.assertEqual("allow", report["status"])
        self.assertEqual([], report["unknowns"])
        self.assertEqual([], report["refusals"])

    def test_head_and_source_drift_are_refused(self):
        spec_value = spec()
        plan_value = plan(spec_value)
        report = check_task_preflight(
            spec_value, plan_value, snapshot(),
            observation(spec_value, plan_value, head_commit="" ))
        self.assertEqual("unknown", report["status"])
        self.assertEqual("missing_head", report["unknowns"][0]["reason"])

        report = check_task_preflight(
            spec_value, plan_value, snapshot(),
            observation(spec_value, plan_value, spec_sha256=DIGEST))
        self.assertEqual("refused", report["status"])
        self.assertEqual("spec_digest_mismatch", report["refusals"][0]["reason"])

    def test_out_of_scope_untracked_write_is_refused(self):
        spec_value = spec()
        plan_value = plan(spec_value)
        report = check_task_preflight(
            spec_value, plan_value, snapshot(),
            observation(spec_value, plan_value,
                        untracked_paths=["outside/other.txt"]))
        self.assertEqual("refused", report["status"])
        self.assertEqual("dirty_outside_write_scope", report["refusals"][0]["reason"])

    def test_scope_ancestor_overlap_and_case_collision_are_refused(self):
        spec_value = spec()
        plan_value = plan(spec_value)
        changed = observation(spec_value, plan_value)
        changed["write_paths"] = ["docs", "docs/out.md"]
        report = check_task_preflight(spec_value, plan_value, snapshot(), changed)
        self.assertEqual("refused", report["status"])
        self.assertEqual("scope_overlap", report["refusals"][0]["reason"])

        changed = observation(spec_value, plan_value)
        changed["write_paths"] = ["docs/out.md", "Docs/out.md"]
        report = check_task_preflight(spec_value, plan_value, snapshot(), changed)
        self.assertEqual("refused", report["status"])
        self.assertEqual("case_collision", report["refusals"][0]["reason"])

    def test_non_git_or_partial_source_is_unknown(self):
        spec_value = spec()
        plan_value = plan(spec_value)
        for changes, reason in (
            ({"is_git": False}, "not_a_git_workspace"),
            ({"source_coverage": "partial"}, "partial_source_coverage"),
        ):
            report = check_task_preflight(
                spec_value, plan_value, snapshot(),
                observation(spec_value, plan_value, **changes))
            self.assertEqual("unknown", report["status"])
            self.assertEqual(reason, report["unknowns"][0]["reason"])

    def test_inputs_are_not_mutated_and_no_io_is_attempted(self):
        spec_value = spec()
        plan_value = plan(spec_value)
        snap = snapshot()
        obs = observation(spec_value, plan_value)
        before = copy.deepcopy((spec_value, plan_value, snap, obs))
        with mock.patch("builtins.open", side_effect=AssertionError("open called")), \
                mock.patch("socket.socket", side_effect=AssertionError("socket called")), \
                mock.patch("subprocess.run", side_effect=AssertionError("subprocess called")):
            report = check_task_preflight(spec_value, plan_value, snap, obs)
        self.assertEqual("allow", report["status"])
        self.assertEqual(before, (spec_value, plan_value, snap, obs))

    def test_observation_is_closed_and_never_echoes_free_text(self):
        spec_value = spec()
        plan_value = plan(spec_value)
        canary = "sk-private-message-canary"
        changed = observation(spec_value, plan_value,
                              message_body=canary, credential=canary)
        report = check_task_preflight(spec_value, plan_value, snapshot(), changed)
        self.assertEqual("refused", report["status"])
        self.assertEqual("unexpected_field", report["refusals"][0]["reason"])
        self.assertNotIn(canary, repr(report))

    def test_report_is_bounded_and_stable(self):
        spec_value = spec()
        plan_value = plan(spec_value)
        obs = observation(spec_value, plan_value)
        first = check_task_preflight(spec_value, plan_value, snapshot(), obs)
        second = check_task_preflight(spec_value, plan_value, snapshot(), obs)
        self.assertEqual(first, second)
        self.assertEqual(1, first["schema_version"])

    def test_workspace_identity_must_match_the_explicit_root_observation(self):
        spec_value = spec()
        plan_value = plan(spec_value)
        changed = observation(spec_value, plan_value)
        changed["workspace_roots"] = ["workspace-b"]
        report = check_task_preflight(spec_value, plan_value, snapshot(), changed)
        self.assertEqual("refused", report["status"])
        self.assertEqual("workspace_identity_mismatch", report["refusals"][0]["reason"])

        changed = observation(spec_value, plan_value)
        changed["workspace_roots"] = ["workspace-a", "workspace-b"]
        report = check_task_preflight(spec_value, plan_value, snapshot(), changed)
        self.assertEqual("refused", report["status"])
        self.assertEqual("foreign_workspace_observation", report["refusals"][0]["reason"])

    def test_case_collision_is_detected_before_plan_membership(self):
        spec_value = spec()
        plan_value = plan(spec_value)
        changed = observation(spec_value, plan_value)
        changed["write_paths"] = ["docs/out.md", "Docs/out.md"]
        report = check_task_preflight(spec_value, plan_value, snapshot(), changed)
        self.assertEqual("refused", report["status"])
        self.assertEqual("case_collision", report["refusals"][0]["reason"])

    def test_unobserved_plan_snapshot_and_oversized_report_refuse(self):
        spec_value = spec()
        plan_value = plan(spec_value)
        missing_plan = observation(spec_value, plan_value, plan_sha256="")
        report = check_task_preflight(spec_value, plan_value, snapshot(), missing_plan)
        self.assertEqual("unknown", report["status"])
        self.assertEqual("missing_plan_digest", report["unknowns"][0]["reason"])

        missing_snapshot = observation(spec_value, plan_value, snapshot_sha256="")
        report = check_task_preflight(spec_value, plan_value, snapshot(), missing_snapshot)
        self.assertEqual("unknown", report["status"])

        drift_snapshot = observation(spec_value, plan_value, snapshot_sha256=DIGEST)
        report = check_task_preflight(spec_value, plan_value, snapshot(), drift_snapshot)
        self.assertEqual("refused", report["status"])
        self.assertEqual("snapshot_digest_mismatch", report["refusals"][0]["reason"])

        with mock.patch("pocketchat.task_preflight.MAX_REPORT_BYTES", 64):
            report = check_task_preflight(spec_value, plan_value, snapshot(),
                                          observation(spec_value, plan_value))
        self.assertEqual("refused", report["status"])
        self.assertEqual("report_limit", report["refusals"][0]["reason"])

    def test_path_budget_refuses_instead_of_truncating(self):
        spec_value = spec()
        plan_value = plan(spec_value)
        with mock.patch("pocketchat.task_preflight.MAX_PATH_OBSERVATIONS", 1):
            report = check_task_preflight(
                spec_value, plan_value, snapshot(),
                observation(spec_value, plan_value,
                            tracked_paths=["README.md"]))
        self.assertEqual("refused", report["status"])
        self.assertEqual("path_limit", report["refusals"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
