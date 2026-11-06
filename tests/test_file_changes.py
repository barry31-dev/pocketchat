"""Safe proposal, confirmation, and apply tests for file changes."""

from __future__ import annotations

import copy
import errno
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from pocketchat.file_change_store import (FileChangeStore, FileChangeStoreError,
                                          FileChangeStoreFull)
from pocketchat.file_changes import (FileChangeError, apply_file_change,
                                     confirm_file_change, parse_file_change,
                                     propose_file_change)


POLICY_SHA256 = hashlib.sha256(b"policy-v1").hexdigest()
OWNER = {
    "workspace_sha256": hashlib.sha256(b"workspace").hexdigest(),
    "session_id": "session-1",
    "conversation_id": "conversation-1",
    "branch": "main",
}
ORIGIN = {
    "run_id": "run-1",
    "tool_call_id": "tool-1",
    "profile_id": "profile-1",
}


class FileChangeProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = FileChangeStore(self.root)

    def test_replace_proposal_preserves_before_bytes_and_renders_complete_diff(self):
        target = self.root / "notes.txt"
        target.write_text("old line\n", encoding="utf-8")

        result = propose_file_change(
            self.root,
            "notes.txt",
            "new line\n",
            store=self.store,
            owner=OWNER,
            origin=ORIGIN,
            policy_sha256=POLICY_SHA256,
            read_paths=["notes.txt"],
            write_paths=["notes.txt"],
            change_id="change_0123456789abcdef0123456789abcdef",
        )

        self.assertEqual("old line\n", target.read_text(encoding="utf-8"))
        self.assertEqual("replace", result["change"]["operation"])
        self.assertIn("-old line", result["diff"])
        self.assertIn("+new line", result["diff"])
        before = result["change"]["before"]
        self.assertEqual(
            b"old line\n",
            self.store.read_object(before["object"], before["sha256"]),
        )
        stored = self.store.load_record(result["change"]["change_id"])
        self.assertEqual(result["change"], stored)
        self.assertEqual(["proposed"], [event["type"] for event in stored["events"]])

    def test_create_proposal_requires_only_a_write_grant_and_has_no_before_image(self):
        result = propose_file_change(
            self.root,
            "new.txt",
            "created\n",
            store=self.store,
            owner=OWNER,
            origin=ORIGIN,
            policy_sha256=POLICY_SHA256,
            read_paths=[],
            write_paths=["new.txt"],
            change_id="change_11111111111111111111111111111111",
        )

        self.assertFalse((self.root / "new.txt").exists())
        self.assertEqual("create", result["change"]["operation"])
        self.assertNotIn("before", result["change"])
        self.assertIn("+created", result["diff"])

    def test_unpaired_surrogate_candidate_is_a_controlled_utf8_refusal(self):
        with self.assertRaisesRegex(
                FileChangeError, "candidate content must be UTF-8 text"):
            propose_file_change(
                self.root,
                "new.txt",
                "\ud800",
                store=self.store,
                owner=OWNER,
                origin=ORIGIN,
                policy_sha256=POLICY_SHA256,
                read_paths=[],
                write_paths=["new.txt"],
                change_id="change_13131313131313131313131313131313",
            )

        self.assertFalse((self.root / "new.txt").exists())
        self.assertEqual([], list(self.store.records.iterdir()))
        self.assertEqual([], list(self.store.objects.iterdir()))

    @unittest.skipUnless(shutil.which("git"), "git is required for the dirty fixture")
    def test_dirty_git_file_is_proposed_from_current_user_bytes_not_head(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Fixture"],
                       cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "fixture@example.invalid"],
                       cwd=self.root, check=True)
        target = self.root / "notes.txt"
        target.write_text("head bytes\n", encoding="utf-8")
        subprocess.run(["git", "add", "notes.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.root, check=True)
        target.write_text("dirty bytes\n", encoding="utf-8")

        proposed = propose_file_change(
            self.root, "notes.txt", "candidate bytes\n", store=self.store,
            owner=OWNER, origin=ORIGIN, policy_sha256=POLICY_SHA256,
            read_paths=["notes.txt"], write_paths=["notes.txt"],
            change_id="change_12121212121212121212121212121212",
        )

        self.assertIn("-dirty bytes", proposed["diff"])
        self.assertNotIn("-head bytes", proposed["diff"])
        before = proposed["change"]["before"]
        self.assertEqual(b"dirty bytes\n",
                         self.store.read_object(before["object"], before["sha256"]))

    def test_confirmed_create_uses_exclusive_creation_and_persists_receipt(self):
        proposed = propose_file_change(
            self.root, "created.txt", "created\n", store=self.store,
            owner=OWNER, origin=ORIGIN, policy_sha256=POLICY_SHA256,
            read_paths=[], write_paths=["created.txt"],
            change_id="change_56565656565656565656565656565656",
        )["change"]
        confirm_file_change(
            self.store, proposed["change_id"],
            proposal_sha256=proposed["proposal_sha256"],
            policy_sha256=POLICY_SHA256, approval_id="terminal-create",
        )

        receipt = apply_file_change(
            self.root, self.store, proposed["change_id"],
            current_policy_sha256=POLICY_SHA256,
            write_paths=["created.txt"],
        )

        self.assertEqual("created\n",
                         (self.root / "created.txt").read_text(encoding="utf-8"))
        self.assertIsNone(receipt["before_sha256"])
        self.assertEqual("remove_created", receipt["undo_action"])

    def test_confirmation_binds_the_exact_proposal_and_policy(self):
        (self.root / "notes.txt").write_text("old\n", encoding="utf-8")
        proposed = propose_file_change(
            self.root, "notes.txt", "new\n", store=self.store, owner=OWNER,
            origin=ORIGIN, policy_sha256=POLICY_SHA256,
            read_paths=["notes.txt"], write_paths=["notes.txt"],
            change_id="change_22222222222222222222222222222222",
        )["change"]

        with self.assertRaises(FileChangeError) as wrong_proposal:
            confirm_file_change(
                self.store, proposed["change_id"],
                proposal_sha256="0" * 64, policy_sha256=POLICY_SHA256,
                approval_id="terminal-approval-1",
            )
        self.assertEqual("file_stale_proposal", wrong_proposal.exception.error_type)
        self.assertIsNone(self.store.load_record(proposed["change_id"])["approval"])

        confirmed = confirm_file_change(
            self.store, proposed["change_id"],
            proposal_sha256=proposed["proposal_sha256"],
            policy_sha256=POLICY_SHA256,
            approval_id="terminal-approval-1",
        )
        self.assertEqual(proposed["proposal_sha256"],
                         confirmed["approval"]["proposal_sha256"])
        self.assertEqual(["proposed", "confirmed"],
                         [event["type"] for event in confirmed["events"]])

    def test_confirmation_respects_the_same_cooperative_writer_boundary(self):
        (self.root / "notes.txt").write_text("old\n", encoding="utf-8")
        proposed = propose_file_change(
            self.root, "notes.txt", "new\n", store=self.store, owner=OWNER,
            origin=ORIGIN, policy_sha256=POLICY_SHA256,
            read_paths=["notes.txt"], write_paths=["notes.txt"],
            change_id="change_67676767676767676767676767676767",
        )["change"]
        other = self.store.acquire_writer(
            "change_68686868686868686868686868686868", owner="other-session")
        self.addCleanup(other.release)

        with self.assertRaises(FileChangeError) as conflict:
            confirm_file_change(
                self.store, proposed["change_id"],
                proposal_sha256=proposed["proposal_sha256"],
                policy_sha256=POLICY_SHA256, approval_id="terminal-blocked",
            )

        self.assertEqual("file_conflict", conflict.exception.error_type)
        self.assertIsNone(self.store.load_record(proposed["change_id"])["approval"])

    def test_confirmation_revalidates_the_persisted_closed_record(self):
        (self.root / "notes.txt").write_text("old\n", encoding="utf-8")
        proposed = propose_file_change(
            self.root, "notes.txt", "new\n", store=self.store, owner=OWNER,
            origin=ORIGIN, policy_sha256=POLICY_SHA256,
            read_paths=["notes.txt"], write_paths=["notes.txt"],
            change_id="change_78787878787878787878787878787878",
        )["change"]
        record_path = (self.store.records /
                       (proposed["change_id"] + ".json"))
        tampered = self.store.load_record(proposed["change_id"])
        tampered["unexpected"] = True
        record_path.write_text(
            __import__("json").dumps(tampered, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        record_path.chmod(0o600)

        with self.assertRaises(FileChangeError):
            confirm_file_change(
                self.store, proposed["change_id"],
                proposal_sha256=proposed["proposal_sha256"],
                policy_sha256=POLICY_SHA256, approval_id="terminal-tampered",
            )

    def test_confirmed_replace_applies_atomically_and_persists_verified_receipt(self):
        target = self.root / "notes.txt"
        target.write_text("old\n", encoding="utf-8")
        proposed = propose_file_change(
            self.root, "notes.txt", "new\n", store=self.store, owner=OWNER,
            origin=ORIGIN, policy_sha256=POLICY_SHA256,
            read_paths=["notes.txt"], write_paths=["notes.txt"],
            change_id="change_33333333333333333333333333333333",
        )["change"]
        confirm_file_change(
            self.store, proposed["change_id"],
            proposal_sha256=proposed["proposal_sha256"],
            policy_sha256=POLICY_SHA256, approval_id="terminal-approval-2",
        )

        receipt = apply_file_change(
            self.root, self.store, proposed["change_id"],
            current_policy_sha256=POLICY_SHA256,
            write_paths=["notes.txt"],
        )

        self.assertEqual("new\n", target.read_text(encoding="utf-8"))
        self.assertEqual("applied", receipt["status"])
        self.assertEqual(hashlib.sha256(b"old\n").hexdigest(),
                         receipt["before_sha256"])
        self.assertEqual(hashlib.sha256(b"new\n").hexdigest(),
                         receipt["after_sha256"])
        self.assertEqual("restore_before", receipt["undo_action"])
        stored = self.store.load_record(proposed["change_id"])
        self.assertEqual(["proposed", "confirmed", "prepared", "applied"],
                         [event["type"] for event in stored["events"]])
        self.assertEqual(receipt, stored["events"][-1]["receipt"])

    def test_safe_apply_uses_only_advertised_descriptor_relative_rename(self):
        target, proposed = self._confirmed_replace(
            "change_12121212121212121212121212121212")

        def unsupported_replace(*args, **kwargs):
            raise OSError(errno.ENOSYS, "descriptor-relative replace unavailable")

        with mock.patch("os.replace", side_effect=unsupported_replace):
            try:
                receipt = apply_file_change(
                    self.root, self.store, proposed["change_id"],
                    current_policy_sha256=POLICY_SHA256,
                    write_paths=["notes.txt"],
                )
            except FileChangeError as exc:
                self.fail("descriptor-relative rename should not use os.replace: %s" % exc)

        self.assertEqual("applied", receipt["status"])
        self.assertEqual("new\n", target.read_text(encoding="utf-8"))

    def test_proposal_refuses_a_store_bound_to_another_workspace(self):
        (self.root / "notes.txt").write_text("old\n", encoding="utf-8")
        with tempfile.TemporaryDirectory() as other_directory:
            other_store = FileChangeStore(Path(other_directory))
            with self.assertRaises(FileChangeError) as refused:
                propose_file_change(
                    self.root, "notes.txt", "new\n", store=other_store,
                    owner=OWNER, origin=ORIGIN, policy_sha256=POLICY_SHA256,
                    read_paths=["notes.txt"], write_paths=["notes.txt"],
                    change_id="change_45454545454545454545454545454545",
                )
        self.assertEqual("file_invalid_record", refused.exception.error_type)
        self.assertEqual("old\n",
                         (self.root / "notes.txt").read_text(encoding="utf-8"))

    def test_store_quota_refuses_a_proposal_without_touching_the_user_file(self):
        target = self.root / "notes.txt"
        target.write_text("old\n", encoding="utf-8")
        tiny_store = FileChangeStore(self.root, max_bytes=32)

        with self.assertRaises(FileChangeError) as refused:
            propose_file_change(
                self.root, "notes.txt", "new\n", store=tiny_store,
                owner=OWNER, origin=ORIGIN, policy_sha256=POLICY_SHA256,
                read_paths=["notes.txt"], write_paths=["notes.txt"],
                change_id="change_44444444444444444444444444444444",
            )

        self.assertEqual("file_store_full", refused.exception.error_type)
        self.assertEqual("old\n", target.read_text(encoding="utf-8"))
        with self.assertRaises(Exception):
            tiny_store.load_record("change_44444444444444444444444444444444")

    def test_closed_parser_rejects_unknown_fields_and_invalid_transitions(self):
        (self.root / "notes.txt").write_text("old\n", encoding="utf-8")
        proposed = propose_file_change(
            self.root, "notes.txt", "new\n", store=self.store, owner=OWNER,
            origin=ORIGIN, policy_sha256=POLICY_SHA256,
            read_paths=["notes.txt"], write_paths=["notes.txt"],
            change_id="change_55555555555555555555555555555555",
        )["change"]

        unknown = copy.deepcopy(proposed)
        unknown["unexpected"] = True
        with self.assertRaises(FileChangeError):
            parse_file_change(unknown)

        invalid = copy.deepcopy(proposed)
        invalid["events"].append({
            "type": "applied", "sequence": 2, "receipt": {},
        })
        with self.assertRaises(FileChangeError):
            parse_file_change(invalid)

    def test_path_replacement_is_refused_without_following_or_touching_the_symlink_target(self):
        target = self.root / "notes.txt"
        target.write_text("old\n", encoding="utf-8")
        proposed = propose_file_change(
            self.root, "notes.txt", "new\n", store=self.store, owner=OWNER,
            origin=ORIGIN, policy_sha256=POLICY_SHA256,
            read_paths=["notes.txt"], write_paths=["notes.txt"],
            change_id="change_66666666666666666666666666666666",
        )["change"]
        confirm_file_change(
            self.store, proposed["change_id"],
            proposal_sha256=proposed["proposal_sha256"],
            policy_sha256=POLICY_SHA256, approval_id="terminal-approval-3",
        )
        outside = self.root / "outside.txt"
        outside.write_text("old\n", encoding="utf-8")
        target.unlink()
        target.symlink_to(outside)
        real_read_bytes = Path.read_bytes

        def refuse_symlink_read(path):
            if path.name == "notes.txt" and path.is_symlink():
                self.fail("apply followed the replaced target before refusing it")
            return real_read_bytes(path)

        with mock.patch.object(Path, "read_bytes", refuse_symlink_read):
            with self.assertRaises(FileChangeError) as conflict:
                apply_file_change(
                    self.root, self.store, proposed["change_id"],
                    current_policy_sha256=POLICY_SHA256,
                    write_paths=["notes.txt"],
                )
        self.assertEqual("file_conflict", conflict.exception.error_type)
        self.assertEqual("old\n", outside.read_text(encoding="utf-8"))
        self.assertEqual(
            "conflict",
            self.store.load_record(proposed["change_id"])["events"][-1]["type"],
        )

    def _confirmed_replace(self, change_id: str):
        target = self.root / "notes.txt"
        target.write_text("old\n", encoding="utf-8")
        proposed = propose_file_change(
            self.root, "notes.txt", "new\n", store=self.store, owner=OWNER,
            origin=ORIGIN, policy_sha256=POLICY_SHA256,
            read_paths=["notes.txt"], write_paths=["notes.txt"],
            change_id=change_id,
        )["change"]
        confirm_file_change(
            self.store, proposed["change_id"],
            proposal_sha256=proposed["proposal_sha256"],
            policy_sha256=POLICY_SHA256, approval_id="terminal-approval-helper",
        )
        return target, proposed

    def test_revoked_grant_and_stale_policy_have_truthful_zero_effect(self):
        target, revoked_change = self._confirmed_replace(
            "change_77777777777777777777777777777777")
        with self.assertRaises(FileChangeError) as revoked:
            apply_file_change(
                self.root, self.store, revoked_change["change_id"],
                current_policy_sha256=POLICY_SHA256, write_paths=[],
            )
        self.assertEqual("file_permission_denied", revoked.exception.error_type)
        self.assertEqual("old\n", target.read_text(encoding="utf-8"))
        self.assertEqual(
            "rejected",
            self.store.load_record(revoked_change["change_id"])["events"][-1]["type"],
        )

        stale_target = self.root / "stale.txt"
        stale_target.write_text("old\n", encoding="utf-8")
        stale_change = propose_file_change(
            self.root, "stale.txt", "new\n", store=self.store, owner=OWNER,
            origin=ORIGIN, policy_sha256=POLICY_SHA256,
            read_paths=["stale.txt"], write_paths=["stale.txt"],
            change_id="change_79797979797979797979797979797979",
        )["change"]
        confirm_file_change(
            self.store, stale_change["change_id"],
            proposal_sha256=stale_change["proposal_sha256"],
            policy_sha256=POLICY_SHA256, approval_id="terminal-stale",
        )
        with self.assertRaises(FileChangeError) as stale:
            apply_file_change(
                self.root, self.store, stale_change["change_id"],
                current_policy_sha256="f" * 64, write_paths=["stale.txt"],
            )
        self.assertEqual("file_stale_proposal", stale.exception.error_type)
        self.assertEqual("old\n", stale_target.read_text(encoding="utf-8"))
        self.assertEqual(
            "rejected",
            self.store.load_record(stale_change["change_id"])["events"][-1]["type"],
        )

    def test_post_commit_readback_refusal_preserves_the_applied_effect(self):
        from pocketchat import file_changes

        for operation in ("replace", "create"):
            with self.subTest(operation=operation):
                root = self.root / operation
                root.mkdir()
                store = FileChangeStore(root)
                target = root / "notes.txt"
                if operation == "replace":
                    target.write_bytes(b"old\n")
                proposed = propose_file_change(
                    root, "notes.txt", "new\n", store=store, owner=OWNER,
                    origin=ORIGIN, policy_sha256=POLICY_SHA256,
                    read_paths=["notes.txt"] if operation == "replace" else [],
                    write_paths=["notes.txt"],
                )["change"]
                confirm_file_change(
                    store, proposed["change_id"],
                    proposal_sha256=proposed["proposal_sha256"],
                    policy_sha256=POLICY_SHA256,
                    approval_id="approval-readback-refusal",
                )
                original_read = file_changes._read_target_at

                def refuse_after_commit(parent_fd, name, *, allow_missing):
                    if target.exists() and target.read_bytes() == b"new\n":
                        raise FileChangeError(
                            "simulated post-commit readback refusal",
                            error_type="file_conflict")
                    return original_read(
                        parent_fd, name, allow_missing=allow_missing)

                with mock.patch(
                        "pocketchat.file_changes._read_target_at",
                        side_effect=refuse_after_commit):
                    with self.assertRaises(FileChangeError) as failed:
                        apply_file_change(
                            root, store, proposed["change_id"],
                            current_policy_sha256=POLICY_SHA256,
                            write_paths=["notes.txt"],
                        )

                self.assertEqual(b"new\n", target.read_bytes())
                stored = store.load_record(proposed["change_id"])
                self.assertEqual("file_recovery_required",
                                 failed.exception.error_type)
                self.assertEqual("applied", failed.exception.effect_disposition)
                self.assertEqual("prepared", stored["events"][-1]["type"])
                self.assertTrue((store.root / "writer.lock").is_file())

    def test_post_commit_readback_and_preserve_failures_keep_recovery_classification(self):
        from pocketchat import file_changes

        target, proposed = self._confirmed_replace(
            "change_8c8c8c8c8c8c8c8c8c8c8c8c8c8c8c8c")
        original_read = file_changes._read_target_at

        def refuse_after_commit(parent_fd, name, *, allow_missing):
            if target.read_bytes() == b"new\n":
                raise FileChangeError(
                    "simulated post-commit readback refusal",
                    error_type="file_conflict")
            return original_read(parent_fd, name, allow_missing=allow_missing)

        with mock.patch(
                "pocketchat.file_changes._read_target_at",
                side_effect=refuse_after_commit), mock.patch(
                "pocketchat.file_change_store.FileChangeWriterClaim.preserve",
                side_effect=FileChangeStoreError("simulated preserve failure")):
            with self.assertRaises((FileChangeError, FileChangeStoreError)) as failed:
                apply_file_change(
                    self.root, self.store, proposed["change_id"],
                    current_policy_sha256=POLICY_SHA256,
                    write_paths=["notes.txt"],
                )

        self.assertEqual(b"new\n", target.read_bytes())
        stored = self.store.load_record(proposed["change_id"])
        self.assertEqual("prepared", stored["events"][-1]["type"])
        self.assertIsInstance(failed.exception, FileChangeError)
        self.assertEqual("file_recovery_required", failed.exception.error_type)
        self.assertEqual("applied", failed.exception.effect_disposition)
        self.assertTrue((self.store.root / "writer.lock").is_file())

    def test_receipt_store_replacement_cannot_mask_the_applied_effect(self):
        target, proposed = self._confirmed_replace(
            "change_8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d")
        original_update = self.store.update_record
        moved_store = self.store.root.with_name("changes-outage")
        saved_claim = None

        def replace_store_during_receipt(record):
            nonlocal saved_claim
            if record["events"][-1]["type"] == "applied":
                saved_claim = (self.store.root / "writer.lock").read_bytes()
                self.store.root.rename(moved_store)
                self.store.root.symlink_to(moved_store, target_is_directory=True)
                raise FileChangeStoreError("simulated receipt store replacement")
            return original_update(record)

        try:
            with mock.patch.object(
                    self.store, "update_record",
                    side_effect=replace_store_during_receipt):
                with self.assertRaises((FileChangeError, FileChangeStoreError)) as failed:
                    apply_file_change(
                        self.root, self.store, proposed["change_id"],
                        current_policy_sha256=POLICY_SHA256,
                        write_paths=["notes.txt"],
                    )
        finally:
            if moved_store.exists():
                self.store.root.unlink()
                moved_store.rename(self.store.root)

        self.assertEqual(b"new\n", target.read_bytes())
        stored = self.store.load_record(proposed["change_id"])
        self.assertEqual("prepared", stored["events"][-1]["type"])
        self.assertEqual(b"old\n", self.store.read_object(
            proposed["before"]["object"], proposed["before"]["sha256"]))
        self.assertEqual(saved_claim, (self.store.root / "writer.lock").read_bytes())
        self.assertIsInstance(failed.exception, FileChangeError)
        self.assertEqual("file_recovery_required", failed.exception.error_type)
        self.assertEqual("applied", failed.exception.effect_disposition)

    def test_candidate_read_failure_after_prepare_requires_recovery(self):
        target, proposed = self._confirmed_replace(
            "change_8a8a8a8a8a8a8a8a8a8a8a8a8a8a8a8a")
        with mock.patch.object(
                self.store, "read_object",
                side_effect=FileChangeStoreError("simulated missing candidate")):
            with self.assertRaises(FileChangeError) as failed:
                apply_file_change(
                    self.root, self.store, proposed["change_id"],
                    current_policy_sha256=POLICY_SHA256,
                    write_paths=["notes.txt"],
                )

        self.assertEqual("file_recovery_required", failed.exception.error_type)
        self.assertEqual("none", failed.exception.effect_disposition)
        self.assertEqual("old\n", target.read_text(encoding="utf-8"))
        stored = self.store.load_record(proposed["change_id"])
        self.assertEqual("prepared", stored["events"][-1]["type"])
        self.assertTrue((self.store.root / "writer.lock").is_file())

    def test_candidate_store_unavailable_preserves_recovery_classification(self):
        target, proposed = self._confirmed_replace(
            "change_8b8b8b8b8b8b8b8b8b8b8b8b8b8b8b8b")
        claim = self.store.acquire_writer(
            proposed["change_id"], owner="test")
        try:
            with mock.patch.object(
                    self.store, "read_object",
                    side_effect=FileChangeStoreError("simulated store outage")), \
                    mock.patch.object(
                        self.store, "acquire_writer", return_value=claim), \
                    mock.patch.object(
                        claim, "preserve",
                        side_effect=FileChangeStoreError(
                            "simulated preserve failure")):
                with self.assertRaises(FileChangeError) as failed:
                    apply_file_change(
                        self.root, self.store, proposed["change_id"],
                        current_policy_sha256=POLICY_SHA256,
                        write_paths=["notes.txt"],
                    )
            self.assertEqual("file_recovery_required",
                             failed.exception.error_type)
            self.assertEqual("none", failed.exception.effect_disposition)
            self.assertEqual("old\n", target.read_text(encoding="utf-8"))
            stored = self.store.load_record(proposed["change_id"])
            self.assertEqual("prepared", stored["events"][-1]["type"])
        finally:
            claim.release()

    def test_prepare_record_failure_leaves_the_target_unchanged(self):
        target, proposed = self._confirmed_replace(
            "change_88888888888888888888888888888888")
        with mock.patch.object(
                self.store, "update_record",
                side_effect=FileChangeStoreError("simulated prepare failure")):
            with self.assertRaises(FileChangeError) as failed:
                apply_file_change(
                    self.root, self.store, proposed["change_id"],
                    current_policy_sha256=POLICY_SHA256,
                    write_paths=["notes.txt"],
                )
        self.assertEqual("file_invalid_record", failed.exception.error_type)
        self.assertEqual("none", failed.exception.effect_disposition)
        self.assertEqual("old\n", target.read_text(encoding="utf-8"))

    def test_receipt_failure_reports_recovery_required_after_the_real_write(self):
        target, proposed = self._confirmed_replace(
            "change_99999999999999999999999999999999")
        original_update = self.store.update_record
        calls = 0

        def fail_receipt(record):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise FileChangeStoreError("simulated receipt failure")
            return original_update(record)

        with mock.patch.object(self.store, "update_record", side_effect=fail_receipt):
            with self.assertRaises(FileChangeError) as failed:
                apply_file_change(
                    self.root, self.store, proposed["change_id"],
                    current_policy_sha256=POLICY_SHA256,
                    write_paths=["notes.txt"],
                )
        self.assertEqual("file_recovery_required", failed.exception.error_type)
        self.assertEqual("applied", failed.exception.effect_disposition)
        self.assertEqual("new\n", target.read_text(encoding="utf-8"))
        stored = self.store.load_record(proposed["change_id"])
        self.assertEqual("prepared", stored["events"][-1]["type"])
        self.assertTrue((self.store.root / "writer.lock").is_file())

    def test_store_full_during_receipt_update_preserves_the_applied_file_and_receipt(self):
        target, proposed = self._confirmed_replace(
            "change_f1f1f1f1f1f1f1f1f1f1f1f1f1f1f1f1")
        original_update = self.store.update_record
        calls = 0

        def full_receipt(record):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise FileChangeStoreFull("simulated quota exhaustion")
            return original_update(record)

        with mock.patch.object(self.store, "update_record", side_effect=full_receipt):
            with self.assertRaises(FileChangeError) as failed:
                apply_file_change(
                    self.root, self.store, proposed["change_id"],
                    current_policy_sha256=POLICY_SHA256,
                    write_paths=["notes.txt"],
                )

        self.assertEqual("file_recovery_required", failed.exception.error_type)
        self.assertEqual("applied", failed.exception.effect_disposition)
        self.assertEqual("new\n", target.read_text(encoding="utf-8"))
        self.assertEqual(
            "prepared",
            self.store.load_record(proposed["change_id"])["events"][-1]["type"],
        )
        self.assertTrue((self.store.root / "writer.lock").is_file())
        stored = self.store.load_record(proposed["change_id"])
        with mock.patch.object(self.store, "_size", return_value=self.store.max_bytes):
            with self.assertRaises(FileChangeStoreFull):
                original_update(dict(stored, recovery_note="needs recovery"))

    def test_oversized_diff_is_rejected_before_private_proposal_persistence(self):
        path = "created.txt"
        with mock.patch("pocketchat.file_changes.MAX_DIFF_BYTES", 8):
            with self.assertRaises(FileChangeError) as failed:
                propose_file_change(
                    self.root, path, "created\n", store=self.store,
                    owner=OWNER, origin=ORIGIN, policy_sha256=POLICY_SHA256,
                    read_paths=[], write_paths=[path],
                    change_id="change_e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1",
                )

        self.assertEqual("file_too_large", failed.exception.error_type)
        self.assertEqual([], list(self.store.records.iterdir()))
        self.assertEqual([], list(self.store.objects.iterdir()))

    def test_create_cleanup_failure_is_recovery_required_after_link_succeeded(self):
        proposed = propose_file_change(
            self.root, "created.txt", "new\n", store=self.store,
            owner=OWNER, origin=ORIGIN, policy_sha256=POLICY_SHA256,
            read_paths=[], write_paths=["created.txt"],
            change_id="change_e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2",
        )
        confirm_file_change(
            self.store, proposed["change"]["change_id"],
            proposal_sha256=proposed["change"]["proposal_sha256"],
            policy_sha256=POLICY_SHA256, approval_id="approval-create-cleanup",
        )
        original_unlink = os.unlink
        calls = 0

        def fail_first_temp_cleanup(name, *args, **kwargs):
            nonlocal calls
            if name.startswith(".pocketchat-change-") and calls == 0:
                calls += 1
                raise OSError("simulated temporary cleanup failure")
            return original_unlink(name, *args, **kwargs)

        supports = set(os.supports_dir_fd)
        supports.remove(original_unlink)
        supports.add(fail_first_temp_cleanup)
        with mock.patch(
                "pocketchat.file_changes.os.unlink",
                new=fail_first_temp_cleanup), mock.patch(
                "pocketchat.file_changes.os.supports_dir_fd", supports):
            with self.assertRaises(FileChangeError) as failed:
                apply_file_change(
                    self.root, self.store, proposed["change"]["change_id"],
                    current_policy_sha256=POLICY_SHA256,
                    write_paths=["created.txt"],
                )

        self.assertEqual("file_recovery_required", failed.exception.error_type)
        self.assertEqual("applied", failed.exception.effect_disposition)
        self.assertEqual("new\n", (self.root / "created.txt").read_text())
        self.assertTrue((self.store.root / "writer.lock").is_file())

    def test_writer_collision_is_a_conflict_with_no_file_effect(self):
        target, proposed = self._confirmed_replace(
            "change_cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd")
        other = self.store.acquire_writer(
            "change_efefefefefefefefefefefefefefefef", owner="other-session")
        self.addCleanup(other.release)

        with self.assertRaises(FileChangeError) as conflict:
            apply_file_change(
                self.root, self.store, proposed["change_id"],
                current_policy_sha256=POLICY_SHA256,
                write_paths=["notes.txt"],
            )

        self.assertEqual("file_conflict", conflict.exception.error_type)
        self.assertEqual("old\n", target.read_text(encoding="utf-8"))

    def test_missing_secure_runtime_refuses_before_file_mutation(self):
        target, proposed = self._confirmed_replace(
            "change_abababababababababababababababab")
        with mock.patch("pocketchat.file_changes.os.supports_dir_fd", set()):
            with self.assertRaises(FileChangeError) as failed:
                apply_file_change(
                    self.root, self.store, proposed["change_id"],
                    current_policy_sha256=POLICY_SHA256,
                    write_paths=["notes.txt"],
                )
        self.assertEqual("file_unsupported_runtime", failed.exception.error_type)
        self.assertEqual("old\n", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
