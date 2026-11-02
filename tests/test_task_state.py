"""Pure task-state reconstruction from bound execution evidence."""

from __future__ import annotations

import unittest

from pocketchat.task_state import TaskStateError, project_task_state


class TaskStateTests(unittest.TestCase):
    def test_selected_dependency_evidence_is_projected_without_other_steps(self):
        report = {
            "spec_sha256": "a" * 64,
            "plan_sha256": "b" * 64,
            "steps": [
                {"step_id": "read", "status": "completed",
                 "task_id": "task-1", "trace": "traces/read.jsonl"},
                {"step_id": "other", "status": "completed",
                 "task_id": "task-2", "trace": "traces/other.jsonl"},
            ],
            "changes": [{"path": "facts.txt", "sha256": "c" * 64}],
            "evidence_issues": [],
        }
        state = project_task_state(
            report, step_id="next", depends_on=["read"],
            allowed_paths=["facts.txt"])
        self.assertEqual("next", state["step_id"])
        self.assertEqual(["read"], [item["step_id"] for item in state["evidence"]])
        self.assertEqual(["facts.txt"],
                         [item["path"] for item in state["files"]])
        self.assertEqual("a" * 64, state["spec_sha256"])
        self.assertEqual("b" * 64, state["plan_sha256"])
        self.assertNotIn("other", str(state))

    def test_unbound_or_unfinished_dependency_is_refused(self):
        report = {
            "spec_sha256": "a" * 64, "plan_sha256": "b" * 64,
            "steps": [{"step_id": "read", "status": "failed"},
                      {"step_id": "open", "status": "not_executed"}],
            "changes": [], "evidence_issues": [],
        }
        for dependency in ("missing", "read", "open"):
            with self.subTest(dependency=dependency):
                with self.assertRaises(TaskStateError) as caught:
                    project_task_state(report, step_id="next",
                                       depends_on=[dependency])
                self.assertEqual("dependency_not_completed",
                                 caught.exception.error_type)

    def test_evidence_issue_or_missing_digest_is_refused(self):
        base = {"spec_sha256": "a" * 64, "plan_sha256": "b" * 64,
                "steps": [{"step_id": "read", "status": "completed"}],
                "changes": [], "evidence_issues": []}
        with self.assertRaises(TaskStateError):
            project_task_state({**base, "evidence_issues": [
                {"state": "unknown", "reason": "changed"}]},
                step_id="next", depends_on=["read"])
        with self.assertRaises(TaskStateError):
            project_task_state({k: v for k, v in base.items()
                                if k != "plan_sha256"},
                               step_id="next", depends_on=["read"])

    def test_file_evidence_must_be_declared_and_relative(self):
        report = {
            "spec_sha256": "a" * 64, "plan_sha256": "b" * 64,
            "steps": [{"step_id": "read", "status": "completed"}],
            "changes": [{"path": "../outside", "sha256": "c" * 64}],
            "evidence_issues": [],
        }
        with self.assertRaises(TaskStateError):
            project_task_state(report, step_id="next", depends_on=["read"])

    def test_maximum_projection_is_bounded(self):
        report = {
            "spec_sha256": "a" * 64, "plan_sha256": "b" * 64,
            "steps": [{"step_id": "read", "status": "completed",
                       "objective": "x" * 1000}],
            "changes": [], "evidence_issues": [],
        }
        with self.assertRaises(TaskStateError) as caught:
            project_task_state(report, step_id="next",
                               depends_on=["read"], max_chars=100)
        self.assertEqual("task_state_too_large", caught.exception.error_type)


if __name__ == "__main__":
    unittest.main()
