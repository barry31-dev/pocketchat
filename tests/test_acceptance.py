"""Operator-declared acceptance: bounded reads and independent honest receipts."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from pocketchat import acceptance as ac
except ImportError:
    ac = None

from pocketchat.task_policy import parse_policy
from pocketchat.tasking import create_record, save_record, transition


def plan_payload(*checks, **overrides):
    return dict({"schema_version": "pocketchat-acceptance-plan.v1", "checks": list(checks) or [
        {"id": "report", "kind": "file_exists", "path": "report.md"}]}, **overrides)


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ac, "deterministic task acceptance is not implemented")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def evaluate(self, *checks, **options):
        return ac.evaluate_plan(self.root, ac.parse_plan(plan_payload(*checks, **options)))

    def test_plan_defaults_are_finite_and_defensively_copied(self):
        raw = plan_payload()
        plan = ac.parse_plan(raw)
        raw["checks"][0]["path"] = "changed.md"
        self.assertEqual(plan.max_file_bytes, 65536)
        self.assertEqual(plan.source["checks"][0]["path"], "report.md")

    def test_plan_rejects_unknown_version_fields_and_check_kinds(self):
        for update in ({"schema_version": "future"}, {"execute_shell": True},
                       {"checks": [{"id": "x", "kind": "shell", "path": "x"}]},
                       {"checks": [{"id": "x", "kind": "file_exists", "path": "x", "text": "ignored"}]}):
            with self.subTest(update=update), self.assertRaises(ac.AcceptanceError):
                ac.parse_plan(plan_payload(**update))

    def test_plan_rejects_empty_duplicate_and_oversized_check_sets(self):
        check = {"id": "x", "kind": "file_exists", "path": "x"}
        for checks in ([], [check, check], [dict(check, id="c%d" % i) for i in range(33)]):
            with self.subTest(count=len(checks)), self.assertRaises(ac.AcceptanceError):
                ac.parse_plan(plan_payload(checks=checks))

    def test_plan_rejects_unsafe_paths_and_identifiers(self):
        for path in ("", "/tmp/report", "../report", "a/../b", "./report", "a//b", "a\\b",
                     "C:/report", "report/", "a\x00b"):
            with self.subTest(path=path), self.assertRaises(ac.AcceptanceError):
                ac.parse_plan(plan_payload({"id": "x", "kind": "file_exists", "path": path}))
        for cid in ("../x", "", "x" * 65, "with space", 1):
            with self.subTest(id=cid), self.assertRaises(ac.AcceptanceError):
                ac.parse_plan(plan_payload({"id": cid, "kind": "file_exists", "path": "x"}))

    def test_plan_rejects_invalid_limits_and_text_conditions(self):
        for value in (True, 0, -1, 1048577, 1.5, "64"):
            with self.subTest(limit=value), self.assertRaises(ac.AcceptanceError):
                ac.parse_plan(plan_payload(max_file_bytes=value))
        for text in (None, "", 5, "x" * 4097):
            with self.subTest(text=type(text)), self.assertRaises(ac.AcceptanceError):
                ac.parse_plan(plan_payload({"id": "x", "kind": "text_contains", "path": "x", "text": text}))

    def test_plan_rejects_invalid_pointers_and_missing_expected(self):
        for pointer in ("key", "/~2", "/~", 4):
            with self.subTest(pointer=pointer), self.assertRaises(ac.AcceptanceError):
                ac.parse_plan(plan_payload({"id": "x", "kind": "json_equals", "path": "x.json",
                                           "pointer": pointer, "expected": 1}))
        with self.assertRaises(ac.AcceptanceError):
            ac.parse_plan(plan_payload({"id": "x", "kind": "json_equals", "path": "x.json", "pointer": ""}))

    def test_plan_rejects_non_json_values_and_nonfinite_numbers(self):
        for value in (float("nan"), float("inf"), {1: "value"}, {"x": object()}):
            with self.subTest(value=type(value)), self.assertRaises(ac.AcceptanceError):
                ac.parse_plan(plan_payload({"id": "x", "kind": "json_equals", "path": "x.json",
                                           "pointer": "", "expected": value}))

    def test_load_plan_refuses_duplicates_nonfinite_and_excess_bytes(self):
        path = self.root / "plan.json"
        for body in ('{"schema_version":"a","schema_version":"b"}', '{"expected":NaN}', ' ' * 65537):
            path.write_text(body, encoding="utf-8")
            with self.subTest(size=len(body)), self.assertRaises(ac.AcceptanceError):
                ac.load_plan(path)

    def test_file_records_full_bytes_and_hash_without_content(self):
        data = b"private content\n"
        (self.root / "report.md").write_bytes(data)
        result = self.evaluate()
        self.assertEqual(result["state"], "passed")
        self.assertEqual(result["checks"][0]["file"], {
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        self.assertNotIn("private content", json.dumps(result))

    def test_missing_file_and_directory_do_not_pass(self):
        self.assertEqual(self.evaluate()["state"], "failed")
        (self.root / "report.md").mkdir()
        self.assertEqual(self.evaluate()["state"], "refused")

    def test_text_contains_is_exact_and_does_not_emit_the_needle(self):
        (self.root / "report.md").write_text("## Findings\nhello", encoding="utf-8")
        good = {"id": "heading", "kind": "text_contains", "path": "report.md", "text": "## Findings"}
        self.assertEqual(self.evaluate(good)["state"], "passed")
        result = self.evaluate(dict(good, text="## findings"))
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("## findings", json.dumps(result))

    def test_invalid_utf8_and_oversized_file_are_distinct(self):
        path = self.root / "report.md"
        path.write_bytes(b"\xff")
        check = {"id": "text", "kind": "text_contains", "path": "report.md", "text": "x"}
        self.assertEqual(self.evaluate(check)["checks"][0]["reason"], "invalid_utf8")
        path.write_bytes(b"x" * 9)
        result = self.evaluate(max_file_bytes=8)
        self.assertEqual(result["state"], "refused")
        self.assertEqual(result["checks"][0]["reason"], "file_too_large")
        self.assertNotIn("file", result["checks"][0])

    def test_json_pointer_decodes_escape_and_array_index(self):
        (self.root / "data.json").write_text('{"a/b":{"~key":[null,true]}}', encoding="utf-8")
        checks = [{"id": "null", "kind": "json_equals", "path": "data.json",
                   "pointer": "/a~1b/~0key/0", "expected": None},
                  {"id": "bool", "kind": "json_equals", "path": "data.json",
                   "pointer": "/a~1b/~0key/1", "expected": True}]
        result = self.evaluate(*checks)
        self.assertEqual(result["state"], "passed")
        self.assertEqual(result["checks"][0]["file"], result["checks"][1]["file"])

    def test_json_equality_does_not_confuse_boolean_and_number(self):
        (self.root / "data.json").write_text('{"x":[true],"n":1.0}', encoding="utf-8")
        check = {"id": "x", "kind": "json_equals", "path": "data.json", "pointer": "/x", "expected": [1]}
        self.assertEqual(self.evaluate(check)["state"], "failed")
        self.assertEqual(self.evaluate(dict(check, pointer="/n", expected=1))["state"], "passed")

    def test_json_missing_pointer_invalid_json_and_duplicate_keys_fail(self):
        check = {"id": "json", "kind": "json_equals", "path": "data.json", "pointer": "/items/01", "expected": 1}
        for content in ('{"items":[0,1]}', '{', '{"x":1,"x":2}', '{"x":NaN}'):
            (self.root / "data.json").write_text(content, encoding="utf-8")
            with self.subTest(content=content):
                self.assertEqual(self.evaluate(check)["state"], "failed")

    def test_sensitive_and_internal_paths_are_refused_without_reading(self):
        for path in (".env", ".env.local", "private/.ssh/key", "traces/run.jsonl",
                     ".pocketchat/tasks/task_x.json", "pocketchat.json", "credentials.json"):
            with self.subTest(path=path):
                result = self.evaluate({"id": "x", "kind": "file_exists", "path": path})
                self.assertEqual(result["state"], "refused")
                self.assertNotIn("file", result["checks"][0])

    def test_symlink_file_and_parent_are_refused_even_inside_workspace(self):
        (self.root / "real").mkdir()
        (self.root / "real" / "file").write_bytes(b"secret")
        (self.root / "link").symlink_to(self.root / "real", target_is_directory=True)
        (self.root / "report.md").symlink_to(self.root / "real" / "file")
        for path in ("report.md", "link/file"):
            with self.subTest(path=path):
                self.assertEqual(self.evaluate({"id": "x", "kind": "file_exists", "path": path})["state"], "refused")

    def test_fifo_is_refused_without_blocking(self):
        import os
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO creation is not available")
        os.mkfifo(self.root / "report.md")
        self.assertEqual(self.evaluate()["state"], "refused")

    def test_overall_state_does_not_hide_refusal_behind_missing_file(self):
        result = self.evaluate({"id": "missing", "kind": "file_exists", "path": "x"},
                               {"id": "secret", "kind": "file_exists", "path": ".env"})
        self.assertEqual(result["state"], "refused")
        self.assertEqual([c["state"] for c in result["checks"]], ["failed", "refused"])

    def task(self, *, root=".", complete=True):
        policy = parse_policy({"schema_version": "pocketchat-task-policy.v1", "objective": "write a report",
                               "workspace_root": root, "max_steps": 8, "max_tool_calls": 4,
                               "max_context_chars": 8000, "max_turn_tokens": 8000})
        record = create_record(policy)
        transition(record, "start", "started")
        if complete:
            transition(record, "complete", "execution completed")
        save_record(self.root, record)
        return record

    def test_verify_persists_independent_task_bound_receipt(self):
        record = self.task()
        task_path = self.root / ".pocketchat" / "tasks" / (record.task_id + ".json")
        before = task_path.read_bytes()
        (self.root / "report.md").write_bytes(b"report")
        result = ac.verify_task(self.root, record.task_id, ac.parse_plan(plan_payload()))
        self.assertEqual(result["state"], "passed")
        receipt = json.loads((self.root / result["record"]).read_text(encoding="utf-8"))
        self.assertEqual(receipt["task_id"], record.task_id)
        self.assertEqual(receipt["policy_sha256"], record.policy_sha256)
        self.assertEqual(receipt["state"], "passed")
        self.assertEqual(len(receipt["task_record_sha256"]), 64)
        self.assertEqual(task_path.read_bytes(), before)
        self.assertNotIn("plan", result)
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_reverification_appends_without_rewriting_old_observations(self):
        record = self.task()
        plan = ac.parse_plan(plan_payload())
        with patch("pocketchat.acceptance.utc_now", return_value="2026-10-30T00:00:00.000Z"):
            first = ac.verify_task(self.root, record.task_id, plan)
        before = (self.root / first["record"]).read_bytes()
        (self.root / "report.md").write_bytes(b"now present")
        with patch("pocketchat.acceptance.utc_now", return_value="2026-10-30T00:00:01.000Z"):
            second = ac.verify_task(self.root, record.task_id, plan)
        self.assertEqual(first["state"], "failed")
        self.assertEqual(second["state"], "passed")
        self.assertNotEqual(first["acceptance_id"], second["acceptance_id"])
        self.assertEqual((self.root / first["record"]).read_bytes(), before)
        history = ac.acceptance_history(self.root, record.task_id)
        self.assertEqual(history["state"], "passed")
        self.assertEqual(len(history["records"]), 2)

    def test_verify_uses_task_root_and_rejects_active_task(self):
        (self.root / "work").mkdir()
        (self.root / "work" / "report.md").write_bytes(b"in task root")
        record = self.task(root="work")
        self.assertEqual(ac.verify_task(self.root, record.task_id, ac.parse_plan(plan_payload()))["state"], "passed")
        active = self.task(complete=False)
        with self.assertRaises(ac.AcceptanceError):
            ac.verify_task(self.root, active.task_id, ac.parse_plan(plan_payload()))
        self.assertFalse((self.root / ".pocketchat" / "acceptance" / active.task_id).exists())

    def test_history_is_recorded_evidence_not_fresh_file_check(self):
        record = self.task()
        (self.root / "report.md").write_bytes(b"original")
        ac.verify_task(self.root, record.task_id, ac.parse_plan(plan_payload()))
        (self.root / "report.md").unlink()
        self.assertEqual(ac.acceptance_history(self.root, record.task_id)["state"], "passed")
        self.assertEqual(ac.acceptance_history(self.root, record.task_id)["evidence_issues"], [])

    def test_no_receipts_is_not_recorded_not_passed(self):
        record = self.task()
        self.assertEqual(ac.acceptance_history(self.root, record.task_id), {
            "state": "not_recorded", "records": [], "evidence_issues": []})

    def test_history_refuses_bad_identity_plan_hash_result_or_aggregate(self):
        record = self.task()
        (self.root / "report.md").write_bytes(b"report")
        result = ac.verify_task(self.root, record.task_id, ac.parse_plan(plan_payload()))
        path = self.root / result["record"]
        original = json.loads(path.read_text(encoding="utf-8"))
        changed = copy.deepcopy(original["checks"])
        changed[0]["state"] = "failed"
        variants = ({"task_id": "task_other"}, {"plan_sha256": "0" * 64}, {"state": "not_recorded"},
                    {"checks": changed}, {"extra": "invented"}, {"checks": []})
        for update in variants:
            path.write_text(json.dumps(dict(original, **update)), encoding="utf-8")
            with self.subTest(update=update):
                history = ac.acceptance_history(self.root, record.task_id)
                self.assertEqual(history["state"], "refused")
                self.assertEqual(history["records"], [])
                self.assertTrue(history["evidence_issues"])

    def test_history_names_corrupt_or_mismatched_task_evidence(self):
        record = self.task()
        result = ac.verify_task(self.root, record.task_id, ac.parse_plan(plan_payload()))
        path = self.root / result["record"]
        before = path.read_bytes()
        path.write_bytes(b"{")
        self.assertEqual(ac.acceptance_history(self.root, record.task_id)["state"], "refused")
        path.write_bytes(before)
        task_path = self.root / ".pocketchat" / "tasks" / (record.task_id + ".json")
        data = json.loads(task_path.read_text(encoding="utf-8"))
        data["objective"] = "changed task"
        task_path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(ac.acceptance_history(self.root, record.task_id)["state"], "refused")

    def test_receipt_storage_rejects_symlinks(self):
        record = self.task()
        outside = self.root / "elsewhere"
        outside.mkdir()
        (self.root / ".pocketchat" / "acceptance").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ac.AcceptanceError):
            ac.verify_task(self.root, record.task_id, ac.parse_plan(plan_payload()))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(ac.acceptance_history(self.root, record.task_id)["state"], "refused")

    def test_history_refuses_untyped_result_reason_instead_of_crashing(self):
        record = self.task()
        result = ac.verify_task(self.root, record.task_id, ac.parse_plan(plan_payload()))
        path = self.root / result["record"]
        value = json.loads(path.read_text(encoding="utf-8"))
        value["checks"][0]["reason"] = {"invented": True}
        path.write_text(json.dumps(value), encoding="utf-8")
        self.assertEqual(ac.acceptance_history(self.root, record.task_id)["state"], "refused")

    def test_history_cannot_claim_order_for_conflicting_same_time_receipts(self):
        record = self.task()
        plan = ac.parse_plan(plan_payload())
        with patch("pocketchat.acceptance.utc_now", return_value="2026-10-30T00:00:00.000Z"):
            ac.verify_task(self.root, record.task_id, plan)
            (self.root / "report.md").write_bytes(b"later report")
            ac.verify_task(self.root, record.task_id, plan)
        history = ac.acceptance_history(self.root, record.task_id)
        self.assertEqual(history["state"], "unknown")
        self.assertEqual(history["evidence_issues"][0]["reason"], "ambiguous_record_order")

    def test_verify_reads_task_from_bounded_snapshot_not_unbounded_path_read(self):
        record = self.task()
        with patch("pocketchat.tasking.Path.read_text", side_effect=AssertionError("unbounded task read")):
            result = ac.verify_task(self.root, record.task_id, ac.parse_plan(plan_payload()))
        self.assertEqual(result["state"], "failed")

    def test_plan_and_artifact_json_nesting_is_bounded(self):
        value = None
        for _ in range(66):
            value = [value]
        with self.assertRaises(ac.AcceptanceError):
            ac.parse_plan(plan_payload({"id": "x", "kind": "json_equals", "path": "x.json",
                                       "pointer": "", "expected": value}))
        (self.root / "x.json").write_text(json.dumps(value), encoding="utf-8")
        result = self.evaluate({"id": "x", "kind": "json_equals", "path": "x.json",
                                "pointer": "", "expected": None})
        self.assertEqual(result["state"], "failed")

    def test_history_orders_offset_timestamps_by_actual_time(self):
        record = self.task()
        plan = ac.parse_plan(plan_payload())
        (self.root / "report.md").write_bytes(b"present at earlier instant")
        with patch("pocketchat.acceptance.utc_now", return_value="2026-10-30T08:00:00.000+08:00"):
            first = ac.verify_task(self.root, record.task_id, plan)
        (self.root / "report.md").unlink()
        with patch("pocketchat.acceptance.utc_now", return_value="2026-10-30T01:00:00.000Z"):
            second = ac.verify_task(self.root, record.task_id, plan)
        history = ac.acceptance_history(self.root, record.task_id)
        self.assertEqual(history["state"], "failed")
        self.assertEqual([r["acceptance_id"] for r in history["records"]],
                         [first["acceptance_id"], second["acceptance_id"]])

    def test_equivalent_instant_offsets_do_not_invent_an_order(self):
        record = self.task()
        plan = ac.parse_plan(plan_payload())
        with patch("pocketchat.acceptance.utc_now", return_value="2026-10-30T08:00:00.000+08:00"):
            ac.verify_task(self.root, record.task_id, plan)
        (self.root / "report.md").write_bytes(b"present at same recorded instant")
        with patch("pocketchat.acceptance.utc_now", return_value="2026-10-30T00:00:00.000Z"):
            ac.verify_task(self.root, record.task_id, plan)
        history = ac.acceptance_history(self.root, record.task_id)
        self.assertEqual(history["state"], "unknown")
        self.assertEqual(history["evidence_issues"][0]["reason"], "ambiguous_record_order")
