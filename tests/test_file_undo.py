"""Persistent, identity-bound undo tests for file changes."""

from __future__ import annotations

import hashlib
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pocketchat.file_change_store import FileChangeStore, FileChangeStoreError
from pocketchat import file_changes


POLICY_SHA256 = hashlib.sha256(b"policy-v1").hexdigest()
OWNER = {
    "workspace_sha256": hashlib.sha256(b"workspace").hexdigest(),
    "session_id": "session-undo",
    "conversation_id": "conversation-undo",
    "branch": "main",
}
ORIGIN = {
    "run_id": "run-undo",
    "tool_call_id": "tool-undo",
    "profile_id": "profile-undo",
}


class FileChangeUndoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = FileChangeStore(self.root)

    def _apply_replace(self, change_id: str, before: str, after: str):
        target = self.root / "notes.txt"
        target.write_text(before, encoding="utf-8")
        change = file_changes.propose_file_change(
            self.root,
            "notes.txt",
            after,
            store=self.store,
            owner=OWNER,
            origin=ORIGIN,
            policy_sha256=POLICY_SHA256,
            read_paths=["notes.txt"],
            write_paths=["notes.txt"],
            change_id=change_id,
        )["change"]
        file_changes.confirm_file_change(
            self.store,
            change_id,
            proposal_sha256=change["proposal_sha256"],
            policy_sha256=POLICY_SHA256,
            approval_id="apply-%s" % change_id[-4:],
        )
        file_changes.apply_file_change(
            self.root,
            self.store,
            change_id,
            current_policy_sha256=POLICY_SHA256,
            write_paths=["notes.txt"],
        )
        return target

    def _apply_create(self, change_id: str, content: str):
        target = self.root / "created.txt"
        change = file_changes.propose_file_change(
            self.root,
            "created.txt",
            content,
            store=self.store,
            owner=OWNER,
            origin=ORIGIN,
            policy_sha256=POLICY_SHA256,
            read_paths=[],
            write_paths=["created.txt"],
            change_id=change_id,
        )["change"]
        file_changes.confirm_file_change(
            self.store,
            change_id,
            proposal_sha256=change["proposal_sha256"],
            policy_sha256=POLICY_SHA256,
            approval_id="apply-%s" % change_id[-4:],
        )
        file_changes.apply_file_change(
            self.root,
            self.store,
            change_id,
            current_policy_sha256=POLICY_SHA256,
            write_paths=["created.txt"],
        )
        return target

    def _apply_current_replace(self, change_id: str, after: str):
        change = file_changes.propose_file_change(
            self.root,
            "notes.txt",
            after,
            store=self.store,
            owner=OWNER,
            origin=ORIGIN,
            policy_sha256=POLICY_SHA256,
            read_paths=["notes.txt"],
            write_paths=["notes.txt"],
            change_id=change_id,
        )["change"]
        file_changes.confirm_file_change(
            self.store,
            change_id,
            proposal_sha256=change["proposal_sha256"],
            policy_sha256=POLICY_SHA256,
            approval_id="apply-%s" % change_id[-4:],
        )
        file_changes.apply_file_change(
            self.root, self.store, change_id,
            current_policy_sha256=POLICY_SHA256,
            write_paths=["notes.txt"],
        )

    def test_undo_requires_a_current_write_grant(self):
        change_id = "change_09090909090909090909090909090909"
        target = self._apply_replace(change_id, "old\n", "new\n")

        self.assertIn(
            "write_paths", inspect.signature(file_changes.undo_file_change).parameters,
            "undo omitted the current write-grant boundary",
        )
        with self.assertRaises(file_changes.FileChangeError) as refused:
            file_changes.undo_file_change(
                self.root, self.store, change_id,
                approval_id="undo-revoked", write_paths=[],
            )

        self.assertEqual("file_permission_denied", refused.exception.error_type)
        self.assertEqual("new\n", target.read_text(encoding="utf-8"))
        self.assertEqual(
            "rejected", self.store.load_record(change_id)["events"][-1]["type"])

    def test_replace_undo_survives_restart_and_is_idempotent(self):
        change_id = "change_01010101010101010101010101010101"
        target = self._apply_replace(change_id, "old\n", "new\n")
        restarted_store = FileChangeStore(self.root)

        self.assertTrue(
            hasattr(file_changes, "undo_file_change"),
            "persistent undo service is not implemented",
        )
        receipt = file_changes.undo_file_change(
            self.root,
            restarted_store,
            change_id,
            approval_id="undo-restart-1",
            write_paths=["notes.txt"],
        )

        self.assertEqual("old\n", target.read_text(encoding="utf-8"))
        self.assertEqual("undone", receipt["status"])
        self.assertEqual(change_id, receipt["change_id"])
        self.assertEqual(hashlib.sha256(b"new\n").hexdigest(),
                         receipt["undone_sha256"])
        self.assertEqual(hashlib.sha256(b"old\n").hexdigest(),
                         receipt["restored_sha256"])
        self.assertIsNone(receipt["restores_change_id"])
        self.assertIsNotNone(receipt["restored_file_identity"])
        self.assertEqual(
            ["proposed", "confirmed", "prepared", "applied",
             "undo_prepared", "undone"],
            [event["type"] for event in
             restarted_store.load_record(change_id)["events"]],
        )

        repeated = file_changes.undo_file_change(
            self.root,
            restarted_store,
            change_id,
            approval_id="undo-restart-2",
            write_paths=["notes.txt"],
        )
        self.assertEqual(receipt, repeated)
        self.assertEqual("old\n", target.read_text(encoding="utf-8"))

    def test_created_file_undo_removes_only_the_owned_unchanged_file(self):
        change_id = "change_02020202020202020202020202020202"
        target = self._apply_create(change_id, "created\n")

        try:
            receipt = file_changes.undo_file_change(
                self.root, self.store, change_id, approval_id="undo-create-1",
                write_paths=["created.txt"])
        except file_changes.FileChangeError as exc:
            self.fail("created-file undo should succeed: %s" % exc)

        self.assertFalse(target.exists())
        self.assertEqual("undone", receipt["status"])
        self.assertEqual("remove_created", receipt["undo_action"])
        self.assertIsNone(receipt["restored_sha256"])
        self.assertIsNone(receipt["restored_file_identity"])

    def test_chained_undo_uses_verified_restored_identity(self):
        first = "change_11111111111111111111111111111111"
        second = "change_22222222222222222222222222222222"
        target = self.root / "notes.txt"
        target.write_text("A\n", encoding="utf-8")
        self._apply_current_replace(first, "B\n")
        self._apply_current_replace(second, "C\n")

        second_receipt = file_changes.undo_file_change(
            self.root, self.store, second, approval_id="undo-second",
            write_paths=["notes.txt"])

        self.assertEqual("B\n", target.read_text(encoding="utf-8"))
        self.assertEqual(first, second_receipt["restores_change_id"])
        self.assertEqual(
            second_receipt["restored_file_identity"],
            file_changes._read_target(
                self.root, "notes.txt", allow_missing=False)[1],
        )

        first_receipt = file_changes.undo_file_change(
            self.root, self.store, first, approval_id="undo-first",
            write_paths=["notes.txt"])

        self.assertEqual("A\n", target.read_text(encoding="utf-8"))
        self.assertIsNone(first_receipt["restores_change_id"])

    def test_undo_prepare_store_failure_has_zero_effect(self):
        change_id = "change_51515151515151515151515151515151"
        target = self._apply_replace(change_id, "old\n", "new\n")

        with mock.patch.object(
                self.store, "update_record",
                side_effect=FileChangeStoreError("simulated undo prepare failure")):
            with self.assertRaises(file_changes.FileChangeError) as failed:
                file_changes.undo_file_change(
                    self.root, self.store, change_id,
                    approval_id="undo-prepare-failure",
                    write_paths=["notes.txt"],
                )

        self.assertEqual("file_invalid_record", failed.exception.error_type)
        self.assertEqual("none", failed.exception.effect_disposition)
        self.assertEqual("new\n", target.read_text(encoding="utf-8"))
        self.assertFalse((self.store.root / "writer.lock").exists())

    def test_undo_receipt_store_failure_preserves_recovery_evidence(self):
        change_id = "change_52525252525252525252525252525252"
        target = self._apply_replace(change_id, "old\n", "new\n")
        original_update = self.store.update_record

        def fail_undone_receipt(record):
            if record["events"][-1]["type"] == "undone":
                raise FileChangeStoreError("simulated undo receipt failure")
            return original_update(record)

        with mock.patch.object(
                self.store, "update_record", side_effect=fail_undone_receipt):
            with self.assertRaises(file_changes.FileChangeError) as failed:
                file_changes.undo_file_change(
                    self.root, self.store, change_id,
                    approval_id="undo-receipt-failure",
                    write_paths=["notes.txt"],
                )

        self.assertEqual("file_recovery_required", failed.exception.error_type)
        self.assertEqual("applied", failed.exception.effect_disposition)
        self.assertEqual("old\n", target.read_text(encoding="utf-8"))
        self.assertEqual(
            "undo_prepared",
            self.store.load_record(change_id)["events"][-1]["type"],
        )
        self.assertTrue((self.store.root / "writer.lock").is_file())

    def test_forged_successor_receipt_cannot_authorize_same_hash_identity(self):
        first = "change_71717171717171717171717171717171"
        second = "change_72727272727272727272727272727272"
        target = self.root / "notes.txt"
        target.write_text("A\n", encoding="utf-8")
        self._apply_current_replace(first, "B\n")
        replacement = self.root / "replacement.txt"
        replacement.write_text("B\n", encoding="utf-8")
        replacement.replace(target)
        self._apply_current_replace(second, "C\n")
        receipt = file_changes.undo_file_change(
            self.root, self.store, second, approval_id="undo-unrelated",
            write_paths=["notes.txt"],
        )
        self.assertIsNone(receipt["restores_change_id"])

        forged = self.store.load_record(second)
        forged["events"][-2]["restores_change_id"] = first
        forged["events"][-1]["receipt"]["restores_change_id"] = first
        self.store.update_record(forged)

        with self.assertRaises(file_changes.FileChangeError) as conflict:
            file_changes.undo_file_change(
                self.root, self.store, first, approval_id="undo-forged",
                write_paths=["notes.txt"],
            )

        self.assertEqual("file_conflict", conflict.exception.error_type)
        self.assertEqual("B\n", target.read_text(encoding="utf-8"))

    def test_session_undo_refuses_unresolved_undo_receipt_state(self):
        change_id = "change_61616161616161616161616161616161"
        target = self._apply_replace(change_id, "old\n", "new\n")
        original_update = self.store.update_record

        def fail_undone_receipt(record):
            if record["events"][-1]["type"] == "undone":
                raise FileChangeStoreError("simulated undo receipt failure")
            return original_update(record)

        with mock.patch.object(
                self.store, "update_record", side_effect=fail_undone_receipt):
            with self.assertRaises(file_changes.FileChangeError):
                file_changes.undo_file_change(
                    self.root, self.store, change_id,
                    approval_id="undo-unresolved",
                    write_paths=["notes.txt"],
                )

        result = file_changes.undo_session_file_changes(
            self.root, self.store, OWNER["session_id"],
            approval_id="undo-session-after-recovery",
            write_paths=["notes.txt"],
        )

        self.assertEqual("recovery_required", result["status"])
        self.assertEqual(change_id, result["failed_change_id"])
        self.assertEqual("file_recovery_required", result["error_type"])
        self.assertEqual([change_id], result["remaining_change_ids"])
        self.assertEqual("old\n", target.read_text(encoding="utf-8"))

    def test_session_undo_queries_persisted_chain_after_restart(self):
        first = "change_31313131313131313131313131313131"
        second = "change_32323232323232323232323232323232"
        target = self.root / "notes.txt"
        target.write_text("A\n", encoding="utf-8")
        self._apply_current_replace(first, "B\n")
        self._apply_current_replace(second, "C\n")
        restarted_store = FileChangeStore(self.root)

        self.assertTrue(
            hasattr(file_changes, "undo_session_file_changes"),
            "session compensation service is not implemented",
        )
        result = file_changes.undo_session_file_changes(
            self.root, restarted_store, OWNER["session_id"],
            approval_id="undo-session-all",
            write_paths=["notes.txt"],
        )

        self.assertEqual("undone", result["status"])
        self.assertEqual([second, first],
                         [item["change_id"] for item in result["undone"]])
        self.assertEqual([], result["remaining_change_ids"])
        self.assertEqual("A\n", target.read_text(encoding="utf-8"))

    def test_session_undo_stops_at_first_conflict(self):
        first = "change_41414141414141414141414141414141"
        second = "change_42424242424242424242424242424242"
        target = self.root / "notes.txt"
        target.write_text("A\n", encoding="utf-8")
        self._apply_current_replace(first, "B\n")
        self._apply_current_replace(second, "C\n")
        target.write_text("external\n", encoding="utf-8")

        self.assertTrue(
            hasattr(file_changes, "undo_session_file_changes"),
            "session compensation service is not implemented",
        )
        result = file_changes.undo_session_file_changes(
            self.root, self.store, OWNER["session_id"],
            approval_id="undo-session-conflict",
            write_paths=["notes.txt"],
        )

        self.assertEqual("conflict", result["status"])
        self.assertEqual(second, result["failed_change_id"])
        self.assertEqual("file_conflict", result["error_type"])
        self.assertEqual([], result["undone"])
        self.assertEqual([second, first], result["remaining_change_ids"])
        self.assertEqual("external\n", target.read_text(encoding="utf-8"))
        self.assertEqual(
            "applied", self.store.load_record(first)["events"][-1]["type"])

    def test_created_file_same_hash_replacement_is_an_undo_conflict(self):
        change_id = "change_03030303030303030303030303030303"
        target = self._apply_create(change_id, "created\n")
        replacement = self.root / "replacement.txt"
        replacement.write_text("created\n", encoding="utf-8")
        replacement.replace(target)

        with self.assertRaises(file_changes.FileChangeError) as conflict:
            file_changes.undo_file_change(
                self.root, self.store, change_id, approval_id="undo-create-2",
                write_paths=["created.txt"])

        self.assertEqual("file_conflict", conflict.exception.error_type)
        self.assertEqual("created\n", target.read_text(encoding="utf-8"))
        self.assertEqual(
            "conflict",
            self.store.load_record(change_id)["events"][-1]["type"],
        )


if __name__ == "__main__":
    unittest.main()
