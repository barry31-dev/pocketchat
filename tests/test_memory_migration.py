"""Memory v2 parsing and pure v1-to-v2 migration tests."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from pocketchat.memory import MAX_STORE_BYTES
from tests._helpers import SECURE_DESCRIPTOR_READS
from pocketchat.memory_migration import (
    MemoryMigrationError,
    _read_v1,
    migrate_v1_to_v2,
    parse_v2_record,
)


def legacy_record(**overrides):
    record = {
        "schema_version": "pocketchat-memory.v1",
        "memory_id": "mem_legacy",
        "created": "2026-01-01T00:00:00.000Z",
        "content": "Use short answers.",
        "tags": ["style"],
    }
    record.update(overrides)
    return record


class MemoryV2ParserTests(unittest.TestCase):
    def valid_record(self):
        return {
            "schema_version": "pocketchat-memory.v2",
            "memory_id": "mem_v2",
            "revision": 1,
            "workspace_id": "workspace-1",
            "category": "user_preference",
            "content": "Prefer concise answers.",
            "content_sha256": hashlib.sha256(
                b"Prefer concise answers.").hexdigest(),
            "created": "2026-01-01T00:00:00.000Z",
            "updated": "2026-01-01T00:00:00.000Z",
            "source_kind": "user_explicit",
            "source_refs": [],
            "verification_status": "not_required",
            "admission_status": "accepted",
            "accepted_by": "user",
            "supersedes": [],
            "sensitive": False,
        }

    def test_valid_record_is_closed_and_nested_values_are_copied(self):
        record = self.valid_record()
        parsed = parse_v2_record(record)
        self.assertEqual("pocketchat-memory.v2", parsed["schema_version"])
        record["source_refs"].append({"kind": "file"})
        self.assertEqual([], parsed["source_refs"])

    def test_accepted_state_requires_an_explicit_approver(self):
        record = self.valid_record()
        record["accepted_by"] = ""
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)

    def test_candidate_cannot_claim_an_approver(self):
        record = self.valid_record()
        record["admission_status"] = "candidate"
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)

    def test_sensitive_is_optional_and_defaults_false(self):
        record = self.valid_record()
        record.pop("sensitive")
        self.assertFalse(parse_v2_record(record)["sensitive"])

    def test_updated_by_is_an_optional_normalized_string(self):
        record = self.valid_record()
        record["updated_by"] = " reviewer "
        self.assertEqual("reviewer", parse_v2_record(record)["updated_by"])
        record["updated_by"] = {"raw": "anything"}
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)

    def test_record_cannot_supersede_or_conflict_with_itself(self):
        supersedes = self.valid_record()
        supersedes["supersedes"] = ["mem_v2"]
        conflict = self.valid_record()
        conflict["admission_status"] = "conflicted"
        conflict["conflict_with"] = ["mem_v2"]
        for record in (supersedes, conflict):
            with self.subTest(record=record):
                with self.assertRaises(MemoryMigrationError):
                    parse_v2_record(record)

    def test_status_metadata_must_match_admission_state(self):
        cases = []
        retired_without_metadata = self.valid_record()
        retired_without_metadata["admission_status"] = "retired"
        cases.append(retired_without_metadata)

        candidate_with_retirement_metadata = self.valid_record()
        candidate_with_retirement_metadata["admission_status"] = "candidate"
        candidate_with_retirement_metadata["retired_at"] = "2026-01-02T00:00:00.000Z"
        cases.append(candidate_with_retirement_metadata)

        accepted_with_conflict = self.valid_record()
        accepted_with_conflict["conflict_with"] = ["mem_other"]
        cases.append(accepted_with_conflict)

        for record in cases:
            with self.subTest(record=record):
                with self.assertRaises(MemoryMigrationError):
                    parse_v2_record(record)

    def test_record_must_fit_the_jsonl_line_budget(self):
        record = self.valid_record()
        record["tags"] = ["x" * 100000]
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)

    def test_unknown_fields_and_missing_required_fields_are_refused(self):
        extra = self.valid_record()
        extra["future"] = True
        missing = self.valid_record()
        missing.pop("content_sha256")
        for record in (extra, missing):
            with self.subTest(record=record):
                with self.assertRaises(MemoryMigrationError):
                    parse_v2_record(record)

    def test_source_and_verification_kinds_must_fit_the_record(self):
        stale_summary = self.valid_record()
        stale_summary["verification_status"] = "verified"
        stale_summary["source_kind"] = "model_summary"
        stale_summary["source_refs"] = [{
            "kind": "event", "ref": "run-1", "sha256": "1" * 64,
            "scope": "summary",
        }]
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(stale_summary)

        approved_model_summary = self.valid_record()
        approved_model_summary["source_kind"] = "model_summary"
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(approved_model_summary)

    def test_accepted_record_must_include_fresh_verification(self):
        for status in ("pending", "unverified", "not_run"):
            with self.subTest(status=status):
                record = self.valid_record()
                record["verification_status"] = status
                with self.assertRaises(MemoryMigrationError):
                    parse_v2_record(record)

    def test_retirement_timestamp_must_not_precede_update(self):
        record = self.valid_record()
        record["admission_status"] = "retired"
        record["retired_at"] = "2025-12-31T23:59:59.000Z"
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)

    def test_update_before_retirement_is_required(self):
        record = self.valid_record()
        record["admission_status"] = "retired"
        record["retired_at"] = "2025-12-31T23:59:59.000Z"
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)

    def test_expired_record_requires_retirement(self):
        record = self.valid_record()
        record["expires_at"] = "2026-01-02T00:00:00.000Z"
        parsed = parse_v2_record(record)
        self.assertEqual("accepted", parsed["admission_status"])

        record = self.valid_record()
        record["expires_at"] = "2026-01-01T00:00:00.000Z"
        record["updated"] = "2026-01-02T00:00:00.000Z"
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)

    def test_lifecycle_timestamps_follow_record_order(self):
        record = self.valid_record()
        record["updated"] = "2025-12-31T23:59:59.000Z"
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)

        record = self.valid_record()
        record["expires_at"] = "2025-12-31T23:59:59.000Z"
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)

        record = self.valid_record()
        record["admission_status"] = "retired"
        record["retired_at"] = "2025-12-31T23:59:59.000Z"
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)

    def test_timestamps_must_be_rfc3339_utc(self):
        for field, value in (
                ("created", "yesterday"),
                ("created", "2026-01-01T00:00:00+00:00"),
                ("updated", "2026-01-01T00:00:00+08:00"),
                ("expires_at", "not-a-time"),
                ("created", "2026-01-01 00:00:00Z"),
                ("created", "2026-01-01T00:00Z")):
            with self.subTest(field=field):
                record = self.valid_record()
                record[field] = value
                with self.assertRaises(MemoryMigrationError):
                    parse_v2_record(record)

        record = self.valid_record()
        record["updated"] = "2026-01-01T00:00:00.123456789Z"
        self.assertEqual("2026-01-01T00:00:00.123456789Z",
                         parse_v2_record(record)["updated"])

        record = self.valid_record()
        record["created"] = "2026-01-01T00:00:00.000000900Z"
        record["updated"] = "2026-01-01T00:00:00.000000100Z"
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)

    def test_conflicted_record_requires_a_conflict_target(self):
        record = self.valid_record()
        record["admission_status"] = "conflicted"
        record["conflict_with"] = []
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)
        record["conflict_with"] = [" "]
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)
        record["conflict_with"] = ["mem_other"]
        self.assertEqual(["mem_other"],
                         parse_v2_record(record)["conflict_with"])

    def test_verified_source_requires_digest_bound_reference(self):
        record = self.valid_record()
        record["verification_status"] = "verified"
        record["source_kind"] = "official_docs"
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)
        record["source_refs"] = [{
            "kind": "url",
            "ref": "https://example.com/spec",
            "sha256": "1" * 64,
            "scope": "section 1",
        }]
        parsed = parse_v2_record(record)
        self.assertEqual("verified", parsed["verification_status"])

    def test_conflicted_and_superseded_records_keep_explicit_status(self):
        record = self.valid_record()
        record["admission_status"] = "conflicted"
        record["conflict_with"] = ["mem_other"]
        parsed = parse_v2_record(record)
        self.assertEqual("conflicted", parsed["admission_status"])
        self.assertEqual(["mem_other"], parsed["conflict_with"])

        retired = self.valid_record()
        retired["admission_status"] = "retired"
        retired["superseded_by"] = "mem_new"
        parsed = parse_v2_record(retired)
        self.assertEqual("retired", parsed["admission_status"])
        self.assertEqual("mem_new", parsed["superseded_by"])

        invalid = self.valid_record()
        invalid["admission_status"] = "conflicted"
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(invalid)


@unittest.skipUnless(
    SECURE_DESCRIPTOR_READS, "requires no-follow descriptor support")
class MemoryMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / ".pocketchat" / "memory.jsonl"
        self.path.parent.mkdir(parents=True)

    def outside_path(self, name):
        return self.root.parent / f"{self.root.name}-{name}"

    def test_migration_refuses_non_utc_v1_timestamp(self):
        self.path.write_text(
            json.dumps(legacy_record(created="2026-01-01 00:00:00")) + "\n",
            encoding="utf-8")
        target = self.root / ".pocketchat" / "memory.v2.jsonl"
        with self.assertRaises(MemoryMigrationError):
            migrate_v1_to_v2(
                self.root, workspace_id="workspace-1", out=target)
        self.assertFalse(target.exists())

    def test_migration_does_not_split_valid_v1_content_at_unicode_line_breaks(self):
        self.path.write_text(
            json.dumps(legacy_record(content="line one\u2028line two"),
                       ensure_ascii=False) + "\n",
            encoding="utf-8")
        target = self.root / ".pocketchat" / "memory.v2.jsonl"
        migrate_v1_to_v2(self.root, workspace_id="workspace-1", out=target)
        migrated = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual("line one\u2028line two", migrated["content"])

    def test_migration_does_not_follow_a_replaced_target_parent(self):
        self.path.write_text(json.dumps(legacy_record()) + "\n", encoding="utf-8")
        parent = self.root / ".pocketchat" / "nested"
        outside = self.outside_path("outside-replaced-parent")
        outside.mkdir()
        self.addCleanup(lambda: outside.rmdir() if outside.exists() else None)

        def replace_parent(part, *args, **kwargs):
            result = original_mkdir(part, *args, **kwargs)
            if part == "nested":
                (self.root / ".pocketchat" / "nested").rmdir()
                (self.root / ".pocketchat" / "nested").symlink_to(
                    outside, target_is_directory=True)
            return result

        original_mkdir = os.mkdir
        from unittest import mock
        with mock.patch("pocketchat.memory_migration.os.mkdir", replace_parent):
            with self.assertRaises(MemoryMigrationError):
                migrate_v1_to_v2(
                    self.root, workspace_id="workspace-1",
                    out=parent / "memory.v2.jsonl")
        self.assertFalse((outside / "memory.v2.jsonl").exists())

    def test_migration_refuses_platform_without_no_follow_directories(self):
        self.path.write_text(json.dumps(legacy_record()) + "\n", encoding="utf-8")
        target = self.root / ".pocketchat" / "memory.v2.jsonl"
        from unittest import mock
        with mock.patch("pocketchat.memory_migration.os.supports_dir_fd", set()):
            with self.assertRaisesRegex(MemoryMigrationError,
                                        "no-follow directory support"):
                migrate_v1_to_v2(
                    self.root, workspace_id="workspace-1", out=target)
        self.assertFalse(target.exists())

    def test_migration_writes_new_v2_file_and_preserves_v1_bytes(self):
        old = legacy_record()
        original = (json.dumps(old, ensure_ascii=False) + "\n").encode("utf-8")
        self.path.write_bytes(original)
        result = migrate_v1_to_v2(
            self.root, workspace_id="workspace-1",
            out=self.root / ".pocketchat" / "memory.v2.jsonl")
        self.assertEqual(1, result["migrated"])
        self.assertEqual(original, self.path.read_bytes())
        target = self.root / ".pocketchat" / "memory.v2.jsonl"
        record = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual("pocketchat-memory.v2", record["schema_version"])
        self.assertEqual("legacy_explicit", record["source_kind"])
        self.assertEqual("unverified", record["verification_status"])
        self.assertEqual("candidate", record["admission_status"])
        self.assertEqual("mem_legacy", record["memory_id"])

    def test_migration_refuses_existing_target_and_duplicate_ids(self):
        self.path.write_text(json.dumps(legacy_record()) + "\n" +
                             json.dumps(legacy_record()) + "\n", encoding="utf-8")
        target = self.root / ".pocketchat" / "memory.v2.jsonl"
        target.write_text("existing\n", encoding="utf-8")
        with self.assertRaises(MemoryMigrationError):
            migrate_v1_to_v2(self.root, workspace_id="workspace-1", out=target)
        target.unlink()
        with self.assertRaises(MemoryMigrationError):
            migrate_v1_to_v2(self.root, workspace_id="workspace-1", out=target)

    def test_migration_target_must_stay_inside_the_workspace(self):
        self.path.write_text(
            json.dumps(legacy_record()) + "\n", encoding="utf-8")
        outside = self.outside_path("outside-memory-v2.jsonl")
        try:
            with self.assertRaises(MemoryMigrationError):
                migrate_v1_to_v2(
                    self.root, workspace_id="workspace-1", out=outside)
            self.assertFalse(outside.exists())
        finally:
            if outside.exists():
                outside.unlink()

    def test_migration_refuses_missing_or_corrupt_v1_store(self):
        with self.assertRaises(MemoryMigrationError):
            migrate_v1_to_v2(
                self.root, workspace_id="workspace-1",
                out=self.root / ".pocketchat" / "memory.v2.jsonl")
        self.path.write_text('{"bad":\n', encoding="utf-8")
        with self.assertRaises(MemoryMigrationError):
            migrate_v1_to_v2(
                self.root, workspace_id="workspace-1",
                out=self.root / ".pocketchat" / "memory.v2.jsonl")
        self.assertEqual(1, len(list((self.root / ".pocketchat").iterdir())))

    def test_migration_refuses_a_symlinked_v1_store_without_nofollow(self):
        outside = self.outside_path("outside-v1-memory.jsonl")
        outside.write_text(json.dumps(legacy_record()) + "\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink() if outside.exists() else None)
        self.path.symlink_to(outside)
        from unittest import mock
        with mock.patch("pocketchat.memory_migration.getattr",
                        side_effect=lambda obj, name, default=None:
                        default if name == "O_NOFOLLOW" else getattr(obj, name, default)):
            with mock.patch("pocketchat.memory_migration.os.open") as opened:
                with self.assertRaises(MemoryMigrationError):
                    migrate_v1_to_v2(
                        self.root, workspace_id="workspace-1",
                        out=self.root / ".pocketchat" / "memory.v2.jsonl")
        opened.assert_not_called()
        self.assertFalse(
            (self.root / ".pocketchat" / "memory.v2.jsonl").exists())

    def test_read_v1_without_nofollow_refuses_before_open(self):
        self.path.write_text(
            json.dumps(legacy_record()) + "\n", encoding="utf-8")
        payload = self.path.read_bytes()
        from unittest import mock
        info = mock.Mock(st_mode=0o100644, st_size=len(payload))
        with mock.patch("pocketchat.memory_migration.getattr",
                        side_effect=lambda obj, name, default=None:
                        default if name == "O_NOFOLLOW" else getattr(obj, name, default)), \
                mock.patch("pathlib.Path.is_symlink", return_value=False), \
                mock.patch("pocketchat.memory_migration.os.open",
                           return_value=41) as opened, \
                mock.patch("pocketchat.memory_migration.os.fstat",
                           return_value=info), \
                mock.patch("pocketchat.memory_migration.os.read",
                           side_effect=[payload, b""]), \
                mock.patch("pocketchat.memory_migration.os.close"):
            with self.assertRaisesRegex(MemoryMigrationError,
                                        "no-follow open support"):
                _read_v1(self.root)
        opened.assert_not_called()

    def test_migration_refuses_symlinked_target_parent(self):
        self.path.write_text(
            json.dumps(legacy_record()) + "\n", encoding="utf-8")
        outside = self.outside_path("outside-migration-directory")
        outside.mkdir()
        self.addCleanup(lambda: outside.rmdir() if outside.exists() else None)
        link = self.root / "outside"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(MemoryMigrationError):
            migrate_v1_to_v2(
                self.root, workspace_id="workspace-1",
                out=link / "memory.v2.jsonl")
        self.assertFalse((outside / "memory.v2.jsonl").exists())

    def test_migration_refuses_v1_records_with_unbounded_fields(self):
        self.path.write_text(json.dumps(legacy_record()) + "\n", encoding="utf-8")
        cases = (
            legacy_record(content=""),
            legacy_record(created=""),
            legacy_record(tags="style"),
            legacy_record(tags=["style", "style"]),
        )
        for record in cases:
            with self.subTest(record=record):
                with self.assertRaises(MemoryMigrationError):
                    migrate_v1_to_v2(
                        self.root, workspace_id="workspace-1",
                        out=self.root / ".pocketchat" / "memory.v2.jsonl",
                        records=[record])
        self.assertEqual([], list(self.path.parent.glob("memory.v2.jsonl")))

    def test_migration_refuses_a_v2_line_over_the_store_line_budget(self):
        self.path.write_text(json.dumps(legacy_record()) + "\n", encoding="utf-8")
        target = self.root / ".pocketchat" / "memory.v2.jsonl"
        from unittest import mock
        with mock.patch("pocketchat.memory_migration.MAX_LINE_BYTES", 256):
            with self.assertRaises(MemoryMigrationError):
                migrate_v1_to_v2(
                    self.root, workspace_id="workspace-1", out=target)
        self.assertFalse(target.exists())

    def test_fdopen_failure_closes_target_and_parent_descriptors(self):
        from unittest import mock

        def fail_after_read(descriptor, mode):
            raise OSError("fdopen failed")

        with mock.patch("pocketchat.memory_migration._open_target_parent",
                        return_value=41), \
                mock.patch("pocketchat.memory_migration.os.open",
                           return_value=42), \
                mock.patch("pocketchat.memory_migration.os.fdopen",
                           side_effect=fail_after_read), \
                mock.patch("pocketchat.memory_migration.os.close") as close:
            with self.assertRaises(MemoryMigrationError):
                migrate_v1_to_v2(
                    self.root, workspace_id="workspace-1",
                    out=self.root / ".pocketchat" / "memory.v2.jsonl",
                    records=[legacy_record()])
        self.assertCountEqual([mock.call(42), mock.call(41)],
                              close.call_args_list)

    def test_write_store_fdopen_failure_closes_the_target_descriptor(self):
        from unittest import mock
        from pocketchat.memory_migration import _write_store_from_parent

        def fail_fdopen(descriptor, mode):
            raise OSError("fdopen failed")

        with mock.patch("pocketchat.memory_migration.os.open",
                        return_value=42), \
                mock.patch("pocketchat.memory_migration.os.fdopen",
                           side_effect=fail_fdopen), \
                mock.patch("pocketchat.memory_migration.os.unlink"), \
                mock.patch("pocketchat.memory_migration.os.close") as close:
            with self.assertRaises(MemoryMigrationError):
                _write_store_from_parent("memory.v2.jsonl", 41, b"")
        close.assert_called_once_with(42)

    def test_lock_fdopen_failure_closes_lock_and_parent_descriptors(self):
        from unittest import mock
        from pocketchat.memory_migration import _mutate_v2_store

        def fail_fdopen(descriptor, mode):
            raise OSError("fdopen failed")

        with mock.patch(
                "pocketchat.memory_migration._open_target_parent",
                return_value=41), \
                mock.patch("pocketchat.memory_migration.os.stat",
                           return_value=mock.Mock(st_mode=0o100600)), \
                mock.patch("pocketchat.memory_migration.os.open",
                           return_value=42), \
                mock.patch("pocketchat.memory_migration.os.fdopen",
                           side_effect=fail_fdopen), \
                mock.patch("pocketchat.memory_migration.os.close") as close:
            with self.assertRaises(MemoryMigrationError):
                _mutate_v2_store(
                    self.root, Path(".pocketchat/memory.v2.jsonl"),
                    lambda records, write: None)
        self.assertCountEqual([mock.call(42), mock.call(41)],
                              close.call_args_list)

    def test_read_v1_closes_the_source_descriptor_after_success(self):
        from unittest import mock

        payload = (json.dumps(legacy_record()) + "\n").encode("utf-8")
        info = mock.Mock(st_mode=0o100644, st_size=len(payload))
        with mock.patch("pocketchat.memory_migration.os.open",
                        return_value=41), \
                mock.patch("pocketchat.memory_migration.os.fstat",
                           return_value=info), \
                mock.patch("pocketchat.memory_migration.os.read",
                           side_effect=[payload, b""]), \
                mock.patch("pocketchat.memory_migration.os.close") as close:
            records = _read_v1(self.root)
        self.assertEqual(["mem_legacy"], [item["memory_id"] for item in records])
        close.assert_called_once_with(41)

    def test_read_v1_rejects_an_oversized_store_and_closes_the_descriptor(self):
        from unittest import mock

        info = mock.Mock(st_mode=0o100644, st_size=MAX_STORE_BYTES + 1)
        with mock.patch("pocketchat.memory_migration.os.open",
                        return_value=41), \
                mock.patch("pocketchat.memory_migration.os.fstat",
                           return_value=info), \
                mock.patch("pocketchat.memory_migration.os.close") as close:
            with self.assertRaisesRegex(MemoryMigrationError,
                                        "exceeds the read budget"):
                _read_v1(self.root)
        close.assert_called_once_with(41)

    def test_write_failure_unlinks_the_created_entry_through_its_parent(self):
        from unittest import mock

        def fail_fsync(*args, **kwargs):
            raise OSError("fsync failed")

        def open_descriptor(*args, **kwargs):
            return 42

        target = self.root / ".pocketchat" / "nested" / "memory.v2.jsonl"
        with mock.patch("pocketchat.memory_migration._open_target_parent",
                        return_value=41), \
                mock.patch("pocketchat.memory_migration.os.open",
                           side_effect=open_descriptor), \
                mock.patch("pocketchat.memory_migration.os.fdopen",
                           side_effect=lambda fd, mode: os.fdopen(
                               os.dup(fd), mode)), \
                mock.patch("pocketchat.memory_migration.os.close") as close, \
                mock.patch("pocketchat.memory_migration.os.fsync",
                           side_effect=fail_fsync), \
                mock.patch("pocketchat.memory_migration.os.unlink") as unlink:
            with self.assertRaises(MemoryMigrationError):
                migrate_v1_to_v2(
                    self.root, workspace_id="workspace-1", out=target,
                    records=[legacy_record()])
        unlink.assert_called_once_with("memory.v2.jsonl", dir_fd=41)
        close.assert_any_call(41)

    def test_write_failure_removes_partial_target(self):
        self.path.write_text(json.dumps(legacy_record()) + "\n", encoding="utf-8")
        target = self.root / ".pocketchat" / "memory.v2.jsonl"
        from unittest import mock
        with mock.patch("pocketchat.memory_migration.os.fsync",
                        side_effect=OSError("fsync failed")):
            with self.assertRaises(MemoryMigrationError):
                migrate_v1_to_v2(
                    self.root, workspace_id="workspace-1", out=target)
        self.assertFalse(target.exists())

    def test_migration_refuses_unknown_v1_fields_and_duplicate_source_refs(self):
        with self.assertRaises(MemoryMigrationError):
            migrate_v1_to_v2(
                self.root, workspace_id="workspace-1",
                out=self.root / ".pocketchat" / "memory.v2.jsonl",
                records=[legacy_record(future=True)])

        record = {
            "schema_version": "pocketchat-memory.v2",
            "memory_id": "mem_v2",
            "revision": 1,
            "workspace_id": "workspace-1",
            "category": "source_note",
            "content": "fact",
            "content_sha256": hashlib.sha256(b"fact").hexdigest(),
            "created": "2026-01-01T00:00:00.000Z",
            "updated": "2026-01-01T00:00:00.000Z",
            "source_kind": "url",
            "source_refs": [
                {"kind": "url", "ref": "https://example.com", "sha256": "1" * 64,
                 "scope": "page"},
                {"kind": "url", "ref": "https://example.com", "sha256": "1" * 64,
                 "scope": "page"},
            ],
            "verification_status": "verified",
            "admission_status": "candidate",
            "accepted_by": "",
            "supersedes": [],
            "sensitive": False,
        }
        with self.assertRaises(MemoryMigrationError):
            parse_v2_record(record)


if __name__ == "__main__":
    unittest.main()
