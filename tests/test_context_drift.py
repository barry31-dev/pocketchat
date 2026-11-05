"""Pure drift checks over supplied selected-context observations."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import unittest
from unittest import mock

from pocketchat.context_drift import ContextDriftError, check_context_drift
from pocketchat.task_snapshot import CONTEXT_SNAPSHOT_SCHEMA, _snapshot_digest

README_TEXT = b"hello"
TASK_TEXT = b"task body"
DIGEST = hashlib.sha256(README_TEXT).hexdigest()
SECOND_DIGEST = hashlib.sha256(TASK_TEXT).hexdigest()


def snapshot(files=None):
    rows = files if files is not None else [
        {
            "path": "README.md",
            "selection_bytes": len(README_TEXT),
            "bytes": len(README_TEXT),
            "selection_size_changed": False,
            "sha256": DIGEST,
            "text": README_TEXT.decode("utf-8"),
        },
        {
            "path": "src/task.py",
            "selection_bytes": len(TASK_TEXT),
            "bytes": len(TASK_TEXT),
            "selection_size_changed": False,
            "sha256": SECOND_DIGEST,
            "text": TASK_TEXT.decode("utf-8"),
        },
    ]
    total_bytes = sum(row["bytes"] for row in rows)
    value = {
        "schema_version": CONTEXT_SNAPSHOT_SCHEMA,
        "tree_schema": "pocketchat-task-context-tree.v1",
        "tree_sha256": "c" * 64,
        "selection_sha256": "d" * 64,
        "query_terms": ["readme", "task"],
        "selection_truncated": False,
        "total_matches": len(rows),
        "selected_files": len(rows),
        "max_bytes": max(1024, total_bytes),
        "total_bytes": total_bytes,
        "selection_size_changed_files": sum(
            row["selection_size_changed"] for row in rows),
        "files": rows,
    }
    value["snapshot_sha256"] = _snapshot_digest(value)
    return value


def observation(*, coverage="complete", files=None):
    return {
        "coverage": coverage,
        "files": files if files is not None else [
            {"path": "README.md", "entry_type": "regular_file",
             "bytes": len(README_TEXT), "sha256": DIGEST},
            {"path": "src/task.py", "entry_type": "regular_file",
             "bytes": len(TASK_TEXT), "sha256": SECOND_DIGEST},
        ],
    }


class ContextDriftTests(unittest.TestCase):
    def assert_refused(self, *, snapshot_value=None, observation_value=None, **kwargs):
        with self.assertRaises(ContextDriftError) as caught:
            check_context_drift(
                snapshot_value if snapshot_value is not None else snapshot(),
                observation_value if observation_value is not None else observation(),
                **kwargs)
        return caught.exception

    def test_unchanged_selected_files_allow_and_digest_is_stable(self):
        first = check_context_drift(snapshot(), observation())
        second = check_context_drift(snapshot(), observation())
        self.assertEqual("allow", first["status"])
        self.assertEqual("continue", first["next_action"])
        self.assertNotIn("report_bytes", first)
        self.assertEqual(2, first["checked_files"])
        self.assertEqual(2, first["unchanged_files"])
        self.assertEqual([], first["changes"])
        self.assertEqual([], first["unknowns"])
        self.assertEqual(first["report_sha256"], second["report_sha256"])
        self.assertEqual(
            first["report_sha256"],
            hashlib.sha256(
                json.dumps(
                    {key: value for key, value in first.items()
                     if key != "report_sha256"},
                    ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        )

    def test_content_change_blocks_and_requires_replanning(self):
        changed = observation()
        changed["files"][0]["sha256"] = "e" * 64
        report = check_context_drift(snapshot(), changed)
        self.assertEqual("refused", report["status"])
        self.assertEqual("replan", report["next_action"])
        self.assertEqual(
            [{"path": "README.md", "reason": "content_changed"}],
            report["changes"],
        )

    def test_checked_files_counts_selected_paths_with_observations(self):
        partial = observation(coverage="partial", files=[
            {"path": "README.md", "entry_type": "regular_file",
             "bytes": len(README_TEXT), "sha256": DIGEST},
        ])
        report = check_context_drift(snapshot(), partial)
        self.assertEqual(1, report["checked_files"])
        self.assertEqual(1, report["unchanged_files"])

    def test_byte_change_with_same_hash_still_blocks(self):
        changed = observation()
        changed["files"][0]["bytes"] = 6
        report = check_context_drift(snapshot(), changed)
        self.assertEqual("refused", report["status"])
        self.assertEqual("content_changed", report["changes"][0]["reason"])

    def test_missing_and_type_replacement_block_with_named_reasons(self):
        for entry_type in ("symlink", "directory", "other"):
            with self.subTest(entry_type=entry_type):
                changed = observation(files=[
                    {"path": "README.md", "entry_type": "missing",
                     "bytes": None, "sha256": None},
                    {"path": "src/task.py", "entry_type": entry_type,
                     "bytes": None, "sha256": None},
                ])
                report = check_context_drift(snapshot(), changed)
                self.assertEqual("refused", report["status"])
                self.assertEqual(
                    [
                        {"path": "README.md", "reason": "path_missing"},
                        {"path": "src/task.py", "reason": "entry_type_changed"},
                    ],
                    report["changes"],
                )

    def test_unselected_changes_are_ignored(self):
        observed = observation(files=[
            {"path": "README.md", "entry_type": "regular_file",
             "bytes": len(README_TEXT), "sha256": DIGEST},
            {"path": "src/task.py", "entry_type": "regular_file",
             "bytes": len(TASK_TEXT), "sha256": SECOND_DIGEST},
            {"path": "docs/unselected.md", "entry_type": "regular_file",
             "bytes": 7, "sha256": "f" * 64},
        ])
        report = check_context_drift(snapshot(), observed)
        self.assertEqual("allow", report["status"])
        self.assertEqual(1, report["ignored_observations"])

    def test_partial_coverage_is_unknown_even_when_all_selected_rows_are_present(self):
        partial = observation(coverage="partial")
        report = check_context_drift(snapshot(), partial)
        self.assertEqual("unknown", report["status"])
        self.assertEqual(
            [{"path": "*", "reason": "coverage_partial"}],
            report["unknowns"],
        )

    def test_partial_coverage_without_selected_observation_is_unknown(self):
        partial = observation(files=[
            {"path": "README.md", "entry_type": "regular_file",
             "bytes": len(README_TEXT), "sha256": DIGEST},
        ], coverage="partial")
        report = check_context_drift(snapshot(), partial)
        self.assertEqual("unknown", report["status"])
        self.assertEqual("reobserve", report["next_action"])
        self.assertEqual(
            [{"path": "src/task.py", "reason": "unobserved"}],
            report["unknowns"],
        )

    def test_complete_coverage_without_every_selected_path_is_refused(self):
        incomplete = observation(files=[
            {"path": "README.md", "entry_type": "regular_file",
             "bytes": len(README_TEXT), "sha256": DIGEST},
        ])
        report = check_context_drift(snapshot(), incomplete)
        self.assertEqual("refused", report["status"])
        self.assertEqual("replan", report["next_action"])
        self.assertEqual(
            [{"path": "src/task.py", "reason": "observation_incomplete"}],
            report["unknowns"],
        )

    def test_known_change_refuses_even_when_coverage_is_partial(self):
        partial = observation(coverage="partial", files=[
            {"path": "README.md", "entry_type": "regular_file",
             "bytes": 5, "sha256": "e" * 64},
        ])
        report = check_context_drift(snapshot(), partial)
        self.assertEqual("refused", report["status"])
        self.assertEqual("content_changed", report["changes"][0]["reason"])

    def test_empty_selected_snapshot_is_allow_only_with_complete_coverage(self):
        empty = snapshot(files=[])
        allowed = check_context_drift(empty, observation(files=[]))
        self.assertEqual("allow", allowed["status"])
        unknown = check_context_drift(empty, observation(coverage="partial", files=[]))
        self.assertEqual("unknown", unknown["status"])
        self.assertEqual(
            [{"path": "*", "reason": "coverage_partial"}],
            unknown["unknowns"],
        )

    def test_duplicate_and_unsafe_paths_are_refused(self):
        duplicate = observation(files=[
            {"path": "README.md", "entry_type": "regular_file",
             "bytes": len(README_TEXT), "sha256": DIGEST},
            {"path": "README.md", "entry_type": "regular_file",
             "bytes": len(README_TEXT), "sha256": DIGEST},
        ])
        unsafe = observation()
        unsafe["files"][0]["path"] = "../README.md"
        for observed in (duplicate, unsafe):
            with self.subTest(observed=observed):
                self.assert_refused(observation_value=observed)

    def test_malformed_nonregular_rows_and_unknown_fields_are_refused(self):
        cases = []
        missing_bytes = observation()
        missing_bytes["files"][0].pop("bytes")
        cases.append(missing_bytes)
        regular_null = observation()
        regular_null["files"][0]["sha256"] = None
        cases.append(regular_null)
        missing_hash = observation()
        missing_hash["files"][0]["sha256"] = "not-a-hash"
        cases.append(missing_hash)
        extra = observation()
        extra["files"][0]["extra"] = True
        cases.append(extra)
        missing_entry = observation()
        missing_entry["files"][0].pop("entry_type")
        cases.append(missing_entry)
        for observed in cases:
            with self.subTest(observed=observed):
                self.assert_refused(observation_value=observed)

    def test_snapshot_file_order_is_not_required_to_be_lexicographic(self):
        frozen = snapshot()
        frozen["files"] = list(reversed(frozen["files"]))
        frozen["snapshot_sha256"] = _snapshot_digest(frozen)
        report = check_context_drift(frozen, observation())
        self.assertEqual("allow", report["status"])

    def test_snapshot_must_be_closed_consistent_and_bound_to_its_digest(self):
        cases = []
        extra = snapshot()
        extra["extra"] = True
        cases.append(extra)
        forged = snapshot()
        forged["tree_sha256"] = "e" * 64
        cases.append(forged)
        wrong_count = snapshot()
        wrong_count["selected_files"] = 99
        cases.append(wrong_count)
        mismatched_text = snapshot()
        mismatched_text["files"][0]["text"] = "HELLO"
        mismatched_text["snapshot_sha256"] = _snapshot_digest(mismatched_text)
        cases.append(mismatched_text)
        wrong_schema = snapshot()
        wrong_schema["schema_version"] = "other"
        cases.append(wrong_schema)
        duplicate = snapshot(files=[snapshot()["files"][0], snapshot()["files"][0]])
        cases.append(duplicate)
        for value in cases:
            with self.subTest(value=value):
                self.assert_refused(snapshot_value=value)

    def test_limits_are_explicit_positive_integers_and_inclusive(self):
        allowed = check_context_drift(snapshot(), observation(), max_observations=2)
        self.assertEqual("allow", allowed["status"])
        for kwargs in (
            {"max_observations": 0},
            {"max_observations": True},
            {"max_observations": "2"},
            {"max_report_bytes": 0},
            {"max_report_bytes": False},
            {"max_report_bytes": 1.5},
        ):
            with self.subTest(kwargs=kwargs):
                self.assert_refused(**kwargs)
        too_many = observation(files=observation()["files"] + [
            {"path": "docs/other.md", "entry_type": "regular_file",
             "bytes": 1, "sha256": "f" * 64},
        ])
        report = check_context_drift(snapshot(), too_many, max_observations=2)
        self.assertEqual("refused", report["status"])
        self.assertIn(
            {"path": "docs/other.md", "reason": "observation_limit"},
            report["unknowns"],
        )

    def test_report_budget_is_inclusive_and_refuses_one_byte_over(self):
        exact_size = None
        for limit in range(1, 2048):
            candidate = check_context_drift(
                snapshot(), observation(), max_report_bytes=limit)
            if candidate["status"] == "allow":
                exact_size = limit
                break
        self.assertIsNotNone(exact_size)
        exact = check_context_drift(
            snapshot(), observation(), max_report_bytes=exact_size)
        self.assertEqual("allow", exact["status"])
        refused = check_context_drift(
            snapshot(), observation(), max_report_bytes=exact_size - 1)
        self.assertEqual("refused", refused["status"])
        self.assertEqual("replan", refused["next_action"])
        self.assertEqual(
            [{"path": "*", "reason": "report_limit"}],
            refused["unknowns"],
        )

    def test_inputs_are_not_mutated(self):
        frozen = snapshot()
        observed = observation(files=observation()["files"] + [
            {"path": "docs/other.md", "entry_type": "regular_file",
             "bytes": 1, "sha256": "f" * 64},
        ])
        frozen_before = copy.deepcopy(frozen)
        observed_before = copy.deepcopy(observed)
        check_context_drift(frozen, observed)
        self.assertEqual(frozen_before, frozen)
        self.assertEqual(observed_before, observed)

    def test_no_external_io_is_attempted(self):
        with mock.patch("builtins.open", side_effect=AssertionError("open called")), \
                mock.patch.object(os.environ, "get",
                                  side_effect=AssertionError("env read")), \
                mock.patch("os.getenv",
                           side_effect=AssertionError("env read")), \
                mock.patch("socket.socket",
                           side_effect=AssertionError("socket called")), \
                mock.patch("subprocess.run",
                           side_effect=AssertionError("command called")):
            report = check_context_drift(snapshot(), observation())
        self.assertEqual("allow", report["status"])

    def test_report_does_not_echo_file_text_or_arbitrary_observation_values(self):
        canary = "private-member-value"
        frozen = snapshot()
        frozen["files"][0]["text"] = canary
        frozen["files"][0]["bytes"] = len(canary.encode("utf-8"))
        frozen["files"][0]["selection_bytes"] = len(canary.encode("utf-8"))
        frozen["files"][0]["sha256"] = hashlib.sha256(
            canary.encode("utf-8")).hexdigest()
        frozen["total_bytes"] = sum(row["bytes"] for row in frozen["files"])
        frozen["snapshot_sha256"] = _snapshot_digest(frozen)
        observed = observation()
        observed["files"][0]["bytes"] = len(canary.encode("utf-8"))
        observed["files"][0]["sha256"] = hashlib.sha256(
            canary.encode("utf-8")).hexdigest()
        report = check_context_drift(frozen, observed)
        self.assertNotIn(canary, json.dumps(report, ensure_ascii=False))
        bad = observation()
        bad["note"] = canary
        with self.assertRaises(ContextDriftError) as caught:
            check_context_drift(snapshot(), bad)
        self.assertNotIn(canary, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
