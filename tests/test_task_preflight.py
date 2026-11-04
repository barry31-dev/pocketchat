"""Pure task preflight: supplied observations, never the live workspace."""

from __future__ import annotations

import copy
import unittest
from unittest import mock

from pocketchat.task_plan import parse_plan, plan_sha256, spec_sha256
from pocketchat.task_preflight import check_task_preflight
from pocketchat.task_spec import parse_spec

DIGEST = "a" * 64
SNAPSHOT = "b" * 64


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
    return {
        "schema_version": "pocketchat-task-context-snapshot.v1",
        "snapshot_sha256": SNAPSHOT,
    }


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
