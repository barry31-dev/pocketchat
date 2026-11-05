"""Pure task-diff evidence tests over supplied context snapshots."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest import mock

from pocketchat.task_diff import TaskDiffError, build_task_diff
from pocketchat.task_snapshot import CONTEXT_SNAPSHOT_SCHEMA


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def canonical_sha256(payload):
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _has_entry_at_budget(baseline, final, size):
    try:
        report = build_task_diff(baseline, final, max_files=8,
                                 max_bytes=size)
    except TaskDiffError as exc:
        if exc.error_type == "byte_limit_exceeded":
            return False
        raise
    return bool(report["entries"])


def file_row(path, text, *, sha256=None, size=None, selection_bytes=None):
    encoded = text.encode("utf-8")
    current_size = len(encoded) if size is None else size
    selected_size = current_size if selection_bytes is None else selection_bytes
    return {
        "path": path,
        "selection_bytes": selected_size,
        "bytes": current_size,
        "selection_size_changed": selected_size != current_size,
        "sha256": hashlib.sha256(encoded).hexdigest() if sha256 is None else sha256,
        "text": text,
    }


def snapshot(files=None, *, selection_sha256=DIGEST_A, tree_sha256=DIGEST_B,
             selection_truncated=False, selection_size_changed_files=0,
             total_matches=None):
    rows = list(files or [])
    body = {
        "schema_version": CONTEXT_SNAPSHOT_SCHEMA,
        "tree_schema": "pocketchat-task-context-tree.v1",
        "tree_sha256": tree_sha256,
        "selection_sha256": selection_sha256,
        "query_terms": ["task"],
        "selection_truncated": selection_truncated,
        "total_matches": len(rows) if total_matches is None else total_matches,
        "selected_files": len(rows),
        "max_bytes": 1024 * 1024,
        "total_bytes": sum(row["bytes"] for row in rows),
        "selection_size_changed_files": selection_size_changed_files,
        "files": rows,
    }
    body["snapshot_sha256"] = canonical_sha256(body)
    return body


class TaskDiffContractTests(unittest.TestCase):
    def test_equal_snapshots_produce_a_complete_empty_diff(self):
        baseline = snapshot([file_row("README.md", "hello\n")])
        final = copy.deepcopy(baseline)
        report = build_task_diff(baseline, final, max_files=8, max_bytes=4096)
        self.assertEqual("pocketchat-task-diff.v1", report["schema_version"])
        self.assertEqual("complete", report["status"])
        self.assertEqual([], report["entries"])
        self.assertEqual([], report["omissions"])
        self.assertFalse(report["whole_workspace_compared"])

    def test_added_deleted_modified_and_unchanged_are_stable(self):
        baseline = snapshot([
            file_row("a.txt", "old\n"),
            file_row("b.txt", "gone\n"),
            file_row("c.txt", "same\n"),
        ])
        final = snapshot([
            file_row("a.txt", "new\n"),
            file_row("c.txt", "same\n"),
            file_row("d.txt", "new file\n"),
        ])
        report = build_task_diff(baseline, final, max_files=8, max_bytes=8192)
        self.assertEqual("complete", report["status"])
        self.assertEqual(["a.txt", "b.txt", "d.txt"],
                         [entry["path"] for entry in report["entries"]])
        by_path = {entry["path"]: entry for entry in report["entries"]}
        self.assertEqual("modified", by_path["a.txt"]["change"])
        self.assertEqual("deleted", by_path["b.txt"]["change"])
        self.assertEqual("added", by_path["d.txt"]["change"])
        self.assertEqual((1, 1), (by_path["a.txt"]["lines_added"],
                                  by_path["a.txt"]["lines_removed"]))
        self.assertTrue(by_path["a.txt"]["text_compared"])
        self.assertFalse(by_path["b.txt"]["text_compared"])
        self.assertFalse(by_path["d.txt"]["text_compared"])
        self.assertEqual(3, report["compared_files"])
        self.assertNotIn("c.txt", by_path)

    def test_entries_are_sorted_and_inputs_are_not_mutated(self):
        baseline = snapshot([file_row("z.txt", "z\n"), file_row("a.txt", "a\n")])
        final = snapshot([file_row("z.txt", "zz\n"), file_row("a.txt", "aa\n")])
        before = copy.deepcopy((baseline, final))
        first = build_task_diff(baseline, final, max_files=8, max_bytes=8192)
        second = build_task_diff(baseline, final, max_files=8, max_bytes=8192)
        self.assertEqual(["a.txt", "z.txt"],
                         [entry["path"] for entry in first["entries"]])
        self.assertEqual(before, (baseline, final))
        self.assertEqual(first, second)

    def test_report_does_not_echo_source_text_or_rejected_values(self):
        canary = "private-source-value"
        baseline = snapshot([file_row("README.md", canary)])
        final = snapshot([file_row("README.md", canary + "-changed")])
        rendered = repr(build_task_diff(baseline, final, max_files=8,
                                        max_bytes=8192))
        self.assertNotIn(canary, rendered)


class TaskDiffCoverageTests(unittest.TestCase):
    def test_selection_mismatch_and_truncation_are_partial(self):
        baseline = snapshot([file_row("README.md", "old\n")],
                            selection_truncated=True, total_matches=2)
        final = snapshot([file_row("CHANGELOG.md", "new\n")],
                         selection_sha256=DIGEST_C,
                         tree_sha256=DIGEST_C,
                         selection_truncated=True,
                         total_matches=2)
        report = build_task_diff(baseline, final, max_files=8, max_bytes=8192)
        self.assertEqual("partial", report["status"])
        reasons = {item["reason"] for item in report["omissions"]}
        self.assertIn("selection_truncated", reasons)
        self.assertIn("selection_mismatch", reasons)

    def test_normal_file_size_change_is_not_a_selection_mismatch(self):
        baseline = snapshot([file_row("README.md", "old\n")])
        final = snapshot([file_row("README.md", "a longer new value\n")],
                         selection_sha256=DIGEST_C,
                         selection_size_changed_files=0)
        report = build_task_diff(baseline, final, max_files=8, max_bytes=8192)
        reasons = {item["reason"] for item in report["omissions"]}
        self.assertNotIn("selection_mismatch", reasons)
        self.assertEqual(["README.md"],
                         [entry["path"] for entry in report["entries"]])

    def test_size_changed_snapshot_is_partial(self):
        baseline_row = file_row("README.md", "old\n", selection_bytes=8)
        baseline = snapshot([baseline_row], selection_size_changed_files=1)
        final = snapshot([file_row("README.md", "new\n")])
        report = build_task_diff(baseline, final, max_files=8, max_bytes=8192)
        self.assertEqual("partial", report["status"])
        self.assertIn("selection_size_changed",
                      {item["reason"] for item in report["omissions"]})

    def test_missing_text_is_refused_as_a_non_snapshot_record(self):
        baseline = snapshot([file_row("README.md", "old\n")])
        final_row = file_row("README.md", "new\n")
        final_row["text"] = None
        final = snapshot([final_row])
        with self.assertRaises(TaskDiffError) as caught:
            build_task_diff(baseline, final, max_files=8, max_bytes=8192)
        self.assertEqual("invalid_snapshot", caught.exception.error_type)

    def test_empty_snapshots_are_partial_not_a_clean_no_diff_claim(self):
        report = build_task_diff(snapshot([]), snapshot([]), max_files=8,
                                 max_bytes=8192)
        self.assertEqual("partial", report["status"])
        self.assertEqual("empty_snapshot", report["omissions"][0]["reason"])



class TaskDiffLimitTests(unittest.TestCase):
    def test_tiny_budget_never_returns_an_oversized_report(self):
        baseline = snapshot([])
        final = snapshot([file_row("a.txt", "a\n")])
        accepted = 0
        for size in range(1, 512):
            with self.subTest(size=size):
                try:
                    report = build_task_diff(baseline, final, max_files=8,
                                             max_bytes=size)
                except TaskDiffError as exc:
                    self.assertEqual("byte_limit_exceeded", exc.error_type)
                    continue
                accepted += 1
                actual = len(json.dumps(
                    report, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":")).encode("utf-8"))
                content = {key: value for key, value in report.items()
                           if key not in ("report_bytes", "report_sha256")}
                content_bytes = len(json.dumps(
                    content, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":")).encode("utf-8"))
                self.assertLessEqual(actual, size)
                self.assertEqual(content_bytes, report["report_bytes"])
        self.assertGreater(accepted, 0)

    def test_exact_file_limit_is_accepted_and_one_over_is_partial(self):
        baseline = snapshot([])
        final = snapshot([file_row("a.txt", "a\n"), file_row("b.txt", "b\n")])
        exact = build_task_diff(baseline, final, max_files=2, max_bytes=8192)
        over = build_task_diff(baseline, final, max_files=1, max_bytes=8192)
        self.assertEqual("partial", exact["status"])  # empty baseline coverage
        self.assertEqual(2, len(exact["entries"]))
        self.assertEqual(1, len(over["entries"]))
        self.assertIn("max_files", {item["reason"] for item in over["omissions"]})
        self.assertEqual(1, over["omitted_files"])
        self.assertEqual(["b.txt"], over["omitted_paths"])

    def test_exact_byte_budget_is_accepted_and_one_less_is_partial(self):
        baseline = snapshot([])
        final = snapshot([file_row("a.txt", "a\n")])
        # Find the smallest budget that still carries the changed entry. The
        # budget itself is serialized, so a larger-budget report's byte count is
        # not a valid substitution for this boundary.
        exact_size = next(
            size for size in range(1, 8193)
            if _has_entry_at_budget(baseline, final, size)
        )
        accepted = build_task_diff(baseline, final, max_files=8,
                                   max_bytes=exact_size)
        refused = build_task_diff(baseline, final, max_files=8,
                                  max_bytes=exact_size - 1)
        self.assertEqual(1, len(accepted["entries"]))
        self.assertLessEqual(accepted["report_bytes"], exact_size)
        self.assertEqual("partial", refused["status"])
        self.assertLessEqual(refused["report_bytes"], exact_size - 1)
        self.assertIn("max_bytes", {item["reason"] for item in refused["omissions"]})

    def test_invalid_limits_are_refused(self):
        baseline = snapshot([file_row("README.md", "x\n")])
        for value in (0, -1, True, 1.5, "8"):
            with self.subTest(max_files=value):
                with self.assertRaises(TaskDiffError) as caught:
                    build_task_diff(baseline, baseline, max_files=value,
                                    max_bytes=8192)
                self.assertEqual("invalid_limits", caught.exception.error_type)
        for value in (0, -1, True, 1.5, "8"):
            with self.subTest(max_bytes=value):
                with self.assertRaises(TaskDiffError) as caught:
                    build_task_diff(baseline, baseline, max_files=8,
                                    max_bytes=value)
                self.assertEqual("invalid_limits", caught.exception.error_type)


class TaskDiffSnapshotValidationTests(unittest.TestCase):
    def test_malformed_or_unknown_snapshots_are_refused(self):
        good = snapshot([file_row("README.md", "x\n")])
        malformed = copy.deepcopy(good)
        malformed["schema_version"] = "unknown"
        for bad in (None, [], {}, malformed):
            with self.subTest(bad=bad):
                with self.assertRaises(TaskDiffError) as caught:
                    build_task_diff(bad, good, max_files=8, max_bytes=8192)
                self.assertEqual("invalid_snapshot", caught.exception.error_type)

    def test_digest_and_byte_contradictions_are_refused(self):
        good = snapshot([file_row("README.md", "x\n")])
        bad_digest = copy.deepcopy(good)
        bad_digest["snapshot_sha256"] = "0" * 64
        with self.assertRaises(TaskDiffError) as caught:
            build_task_diff(good, bad_digest, max_files=8, max_bytes=8192)
        self.assertEqual("snapshot_contradiction", caught.exception.error_type)

        bad_size = copy.deepcopy(good)
        bad_size["total_bytes"] += 1
        bad_size["snapshot_sha256"] = canonical_sha256(
            {key: value for key, value in bad_size.items()
             if key != "snapshot_sha256"})
        with self.assertRaises(TaskDiffError) as caught:
            build_task_diff(good, bad_size, max_files=8, max_bytes=8192)
        self.assertEqual("snapshot_contradiction", caught.exception.error_type)

    def test_snapshot_tree_and_size_change_counters_are_consistent(self):
        bad_tree = snapshot([file_row("README.md", "x\n")])
        bad_tree["tree_schema"] = "unknown-tree"
        bad_tree["snapshot_sha256"] = canonical_sha256(
            {key: value for key, value in bad_tree.items()
             if key != "snapshot_sha256"})
        with self.assertRaises(TaskDiffError) as caught:
            build_task_diff(snapshot([]), bad_tree, max_files=8,
                            max_bytes=8192)
        self.assertEqual("invalid_snapshot", caught.exception.error_type)

        bad_count = snapshot([file_row("README.md", "x\n")])
        bad_count["files"][0]["selection_size_changed"] = True
        bad_count["snapshot_sha256"] = canonical_sha256(
            {key: value for key, value in bad_count.items()
             if key != "snapshot_sha256"})
        with self.assertRaises(TaskDiffError) as caught:
            build_task_diff(snapshot([]), bad_count, max_files=8,
                            max_bytes=8192)
        self.assertEqual("snapshot_contradiction", caught.exception.error_type)

        over_budget = snapshot([file_row("README.md", "x\n")])
        over_budget["max_bytes"] = 1
        over_budget["snapshot_sha256"] = canonical_sha256(
            {key: value for key, value in over_budget.items()
             if key != "snapshot_sha256"})
        with self.assertRaises(TaskDiffError) as caught:
            build_task_diff(snapshot([]), over_budget, max_files=8,
                            max_bytes=8192)
        self.assertEqual("snapshot_contradiction", caught.exception.error_type)

    def test_duplicate_and_unsafe_paths_are_refused(self):
        duplicate = snapshot([file_row("a.txt", "a\n"), file_row("a.txt", "b\n")])
        with self.assertRaises(TaskDiffError) as caught:
            build_task_diff(snapshot([]), duplicate, max_files=8, max_bytes=8192)
        self.assertEqual("duplicate_path", caught.exception.error_type)
        for path in ("../outside.txt", "/absolute.txt", "a\\b.txt", "a/../b.txt"):
            unsafe = snapshot([file_row(path, "x\n")])
            with self.subTest(path=path):
                with self.assertRaises(TaskDiffError) as caught:
                    build_task_diff(snapshot([]), unsafe, max_files=8,
                                    max_bytes=8192)
                self.assertEqual("unsafe_path", caught.exception.error_type)

    def test_input_row_digest_and_selection_counters_are_consistent(self):
        bad = snapshot([file_row("README.md", "x\n", sha256="not-a-digest")])
        with self.assertRaises(TaskDiffError) as caught:
            build_task_diff(snapshot([]), bad, max_files=8, max_bytes=8192)
        self.assertEqual("invalid_snapshot", caught.exception.error_type)

        bad = snapshot([file_row("README.md", "x\n")], total_matches=0)
        with self.assertRaises(TaskDiffError) as caught:
            build_task_diff(snapshot([]), bad, max_files=8, max_bytes=8192)
        self.assertEqual("invalid_snapshot", caught.exception.error_type)


class TaskDiffReviewRecordTests(unittest.TestCase):
    def test_review_record_has_no_open_blocking_finding(self):
        path = Path(__file__).resolve().parent.parent / "docs" / "review-task-diff-v1.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("pocketchat-review-record.v1", record["schema"])
        self.assertEqual("medium", record["tier"])
        self.assertEqual("standard", record["review_depth"])
        self.assertLessEqual(record["rounds"], 3)
        self.assertFalse(any(
            finding["severity"] in ("blocker", "major")
            and finding["status"] == "open"
            for finding in record["findings"]
        ))
        for finding in record["findings"]:
            self.assertTrue(finding["fingerprint"])
            if finding["status"] in ("dismissed", "deferred"):
                self.assertTrue(finding["reason"])


class TaskDiffPurityTests(unittest.TestCase):
    def test_no_filesystem_network_environment_or_command_calls(self):
        baseline = snapshot([file_row("README.md", "old\n")])
        final = snapshot([file_row("README.md", "new\n")])
        with mock.patch("builtins.open", side_effect=AssertionError("open called")), \
                mock.patch("socket.socket", side_effect=AssertionError("socket called")), \
                mock.patch("subprocess.run", side_effect=AssertionError("command called")), \
                mock.patch("os.environ.__getitem__",
                          side_effect=AssertionError("environment read")):
            report = build_task_diff(baseline, final, max_files=8,
                                     max_bytes=8192)
        self.assertEqual("complete", report["status"])


if __name__ == "__main__":
    unittest.main()
