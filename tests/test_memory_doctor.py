"""Read-only memory v2 integrity audit tests."""

from __future__ import annotations

import hashlib
import json
import socket
import unittest
from unittest import mock

from pocketchat.memory_doctor import (
    MemoryDoctorError,
    audit_memory_records,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def record(memory_id: str = "mem_alpha", **overrides):
    payload = {
        "schema_version": "pocketchat-memory.v2",
        "memory_id": memory_id,
        "revision": 1,
        "workspace_id": "workspace-1",
        "category": "user_preference",
        "content": "Prefer concise answers.",
        "content_sha256": digest("Prefer concise answers."),
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
    payload.update(overrides)
    return payload


def source_ref(ref: str = "notes.md", sha: str | None = None):
    return {
        "kind": "file",
        "ref": ref,
        "sha256": sha or digest("source body"),
        "scope": "whole-file",
    }


def observation(ref: str = "notes.md", sha: str | None = None, **overrides):
    payload = {
        "source_kind": "file",
        "ref": ref,
        "scope": "whole-file",
        "sha256": sha or digest("source body"),
        "coverage": "complete",
    }
    payload.update(overrides)
    return payload


class MemoryDoctorTests(unittest.TestCase):
    def test_empty_store_is_complete(self):
        report = audit_memory_records(
            [], workspace_id="workspace-1", source_observations=[])
        self.assertEqual("pocketchat-memory-doctor.v1", report["schema"])
        self.assertEqual(1, report["schema_version"])
        self.assertEqual("complete", report["status"])
        self.assertEqual(0, report["counts"]["records"])
        self.assertEqual(0, report["counts"]["findings"])

    def test_valid_records_are_counted_without_echoing_identities(self):
        report = audit_memory_records(
            [record("mem_private"), record("mem_private_2")],
            workspace_id="workspace-1",
            source_observations=[])
        self.assertEqual("complete", report["status"])
        self.assertEqual(2, report["counts"]["records"])
        rendered = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("mem_private", rendered)
        self.assertNotIn("Prefer concise answers.", rendered)
        self.assertNotIn("user", rendered)

    def test_lifecycle_and_sensitive_records_are_counted(self):
        candidate = record(
            "mem_candidate", admission_status="candidate", accepted_by="",
            sensitive=True)
        report = audit_memory_records(
            [record(), candidate], workspace_id="workspace-1",
            source_observations=[])
        self.assertEqual(1, report["counts"]["admission_accepted"])
        self.assertEqual(1, report["counts"]["admission_candidate"])
        self.assertEqual(1, report["counts"]["sensitive"])

    def test_foreign_workspace_is_an_explicit_omission(self):
        report = audit_memory_records(
            [record(workspace_id="workspace-other")],
            workspace_id="workspace-1",
            source_observations=[])
        self.assertEqual("partial", report["status"])
        self.assertEqual(1, report["counts"]["foreign_workspace_omitted"])
        self.assertEqual("foreign_workspace", report["omissions"][0]["code"])

    def test_malformed_record_is_refused_without_echoing_content(self):
        malformed = record(content="SECRET-CANARY")
        malformed["content_sha256"] = digest("different")
        report = audit_memory_records(
            [malformed], workspace_id="workspace-1",
            source_observations=[])
        self.assertEqual("refused", report["status"])
        self.assertEqual("invalid_record", report["findings"][0]["code"])
        self.assertNotIn("SECRET-CANARY", json.dumps(report))

    def test_duplicate_memory_id_is_refused(self):
        report = audit_memory_records(
            [record(), record()], workspace_id="workspace-1",
            source_observations=[])
        self.assertEqual("refused", report["status"])
        self.assertIn(report["findings"][-1]["code"],
                      {"duplicate_memory_id", "duplicate_revision"})

    def test_two_records_may_bind_the_same_source_ref(self):
        first = record(source_refs=[source_ref()])
        second = record("mem_second", source_refs=[source_ref()])
        report = audit_memory_records(
            [first, second], workspace_id="workspace-1",
            source_observations=[observation()])
        self.assertEqual("complete", report["status"])
        self.assertEqual(2, report["counts"]["sources_verified"])

    def test_unhashable_duplicate_value_is_refused_not_raised_as_type_error(self):
        malformed = record()
        malformed["tags"] = [{"raw": "SECRET-CANARY"}]
        report = audit_memory_records(
            [malformed], workspace_id="workspace-1",
            source_observations=[])
        self.assertEqual("refused", report["status"])
        self.assertNotIn("SECRET-CANARY", json.dumps(report))

    def test_non_mapping_record_is_refused_without_traceback_text(self):
        report = audit_memory_records(
            ["SECRET-CANARY"], workspace_id="workspace-1",
            source_observations=[])
        self.assertEqual("refused", report["status"])
        self.assertEqual("invalid_record", report["findings"][0]["code"])
        self.assertNotIn("SECRET-CANARY", json.dumps(report))

    def test_exact_source_observation_is_verified(self):
        item = record(source_refs=[source_ref()])
        report = audit_memory_records(
            [item], workspace_id="workspace-1",
            source_observations=[observation()])
        self.assertEqual("complete", report["status"])
        self.assertEqual(1, report["counts"]["sources_verified"])

    def test_partial_source_coverage_is_unknown(self):
        item = record(source_refs=[source_ref()])
        report = audit_memory_records(
            [item], workspace_id="workspace-1",
            source_observations=[observation(coverage="partial")])
        self.assertEqual("partial", report["status"])
        self.assertEqual(1, report["counts"]["sources_unknown"])
        self.assertEqual("source_coverage_partial", report["findings"][0]["code"])

    def test_missing_source_coverage_is_missing_not_partial(self):
        item = record(source_refs=[source_ref()])
        report = audit_memory_records(
            [item], workspace_id="workspace-1",
            source_observations=[observation(coverage="missing")])
        self.assertEqual("partial", report["status"])
        self.assertEqual("source_missing", report["findings"][0]["code"])

    def test_missing_source_observation_is_unknown_not_verified(self):
        item = record(source_refs=[source_ref()])
        report = audit_memory_records(
            [item], workspace_id="workspace-1", source_observations=[])
        self.assertEqual("partial", report["status"])
        self.assertEqual(1, report["counts"]["sources_unknown"])

    def test_changed_source_digest_is_refused_without_digest_echo(self):
        item = record(source_refs=[source_ref()])
        other = observation(sha=digest("changed"))
        report = audit_memory_records(
            [item], workspace_id="workspace-1",
            source_observations=[other])
        self.assertEqual("refused", report["status"])
        self.assertEqual("source_digest_mismatch",
                         report["findings"][0]["code"])
        self.assertNotIn(digest("changed"), json.dumps(report))

    def test_duplicate_source_observation_is_refused(self):
        report = audit_memory_records(
            [], workspace_id="workspace-1",
            source_observations=[observation(), observation()])
        self.assertEqual("refused", report["status"])
        self.assertEqual("duplicate_source_observation",
                         report["findings"][0]["code"])

    def test_conflicting_source_observations_are_refused(self):
        report = audit_memory_records(
            [], workspace_id="workspace-1",
            source_observations=[
                observation(), observation(sha=digest("changed"))])
        self.assertEqual("refused", report["status"])
        self.assertEqual("source_observation_conflict",
                         report["findings"][0]["code"])

    def test_source_observation_unknown_field_is_refused(self):
        bogus = observation()
        bogus["raw_body"] = "SECRET"
        report = audit_memory_records(
            [], workspace_id="workspace-1",
            source_observations=[bogus])
        self.assertEqual("refused", report["status"])
        self.assertEqual("invalid_source_observation",
                         report["findings"][0]["code"])
        self.assertNotIn("SECRET", json.dumps(report))

    def test_record_limit_is_inclusive_and_one_over_is_refused(self):
        report = audit_memory_records(
            [record()], workspace_id="workspace-1",
            source_observations=[], max_records=1, max_findings=1,
            max_report_bytes=65536)
        self.assertEqual("complete", report["status"])
        with self.assertRaises(MemoryDoctorError) as error:
            audit_memory_records(
                [record(), record("mem_second")], workspace_id="workspace-1",
                source_observations=[], max_records=1)
        self.assertEqual("record_limit_exceeded", error.exception.error_type)

    def test_finding_limit_retains_a_prefix_and_counts_omissions(self):
        malformed = ["first", "second", "third"]
        report = audit_memory_records(
            malformed, workspace_id="workspace-1", source_observations=[],
            max_findings=2)
        self.assertEqual("refused", report["status"])
        self.assertEqual(2, report["counts"]["findings"])
        self.assertEqual(1, report["counts"]["omitted_findings"])
        self.assertEqual(2, len(report["findings"]))

    def test_report_limit_is_inclusive_and_one_over_is_refused(self):
        baseline = audit_memory_records(
            [], workspace_id="workspace-1", source_observations=[])
        size = len(json.dumps(baseline, ensure_ascii=False,
                              sort_keys=True,
                              separators=(",", ":")).encode("utf-8"))
        report = audit_memory_records(
            [], workspace_id="workspace-1", source_observations=[],
            max_report_bytes=size)
        self.assertEqual(size, len(json.dumps(report, ensure_ascii=False,
                                              sort_keys=True,
                                              separators=(",", ":")).encode("utf-8")))
        with self.assertRaises(MemoryDoctorError) as error:
            audit_memory_records(
                [], workspace_id="workspace-1", source_observations=[],
                max_report_bytes=size - 1)
        self.assertEqual("report_limit_exceeded", error.exception.error_type)

    def test_source_limit_is_inclusive_and_one_over_is_refused(self):
        report = audit_memory_records(
            [], workspace_id="workspace-1",
            source_observations=[observation()], max_sources=1)
        self.assertEqual("complete", report["status"])
        with self.assertRaises(MemoryDoctorError) as error:
            audit_memory_records(
                [], workspace_id="workspace-1",
                source_observations=[observation(), observation("second.md")],
                max_sources=1)
        self.assertEqual("source_limit_exceeded", error.exception.error_type)

    def test_six_thousand_duplicate_observations_are_capped_by_finding_limit(self):
        observations = [observation() for _ in range(6000)]
        report = audit_memory_records(
            [], workspace_id="workspace-1", source_observations=observations,
            max_sources=6000, max_findings=256)
        self.assertEqual("refused", report["status"])
        self.assertEqual(256, report["counts"]["findings"])
        self.assertEqual(5743, report["counts"]["omitted_findings"])
        self.assertLess(len(json.dumps(report)), 65536)

    def test_six_thousand_source_observations_preserve_the_finding_bound(self):
        observations = [
            observation(f"file-{index}.md") for index in range(6000)]
        report = audit_memory_records(
            [], workspace_id="workspace-1",
            source_observations=observations, max_sources=6000)
        self.assertEqual("complete", report["status"])
        self.assertLess(len(json.dumps(report)), 65536)

    def test_record_generator_is_consumed_only_through_one_over_limit(self):
        consumed = []

        def records():
            for index in range(100):
                consumed.append(index)
                yield record(f"mem_{index}")

        with self.assertRaises(MemoryDoctorError) as error:
            audit_memory_records(
                records(), workspace_id="workspace-1",
                source_observations=[], max_records=2)
        self.assertEqual("record_limit_exceeded", error.exception.error_type)
        self.assertEqual([0, 1, 2], consumed)

    def test_source_generator_is_consumed_only_through_one_over_limit(self):
        consumed = []

        def observations():
            for index in range(100):
                consumed.append(index)
                yield observation(f"file-{index}.md")

        with self.assertRaises(MemoryDoctorError) as error:
            audit_memory_records(
                [], workspace_id="workspace-1",
                source_observations=observations(), max_sources=2)
        self.assertEqual("source_limit_exceeded", error.exception.error_type)
        self.assertEqual([0, 1, 2], consumed)

    def test_boolean_and_nonpositive_limits_are_refused(self):
        for name, value in (("max_records", True), ("max_findings", 0),
                            ("max_report_bytes", -1), ("max_sources", False)):
            with self.subTest(name=name):
                with self.assertRaises(MemoryDoctorError):
                    audit_memory_records(
                        [], workspace_id="workspace-1",
                        source_observations=[], **{name: value})

    def test_report_is_deterministic_and_inputs_are_unchanged(self):
        first = record(source_refs=[source_ref()])
        second = record("mem_second", source_refs=[source_ref("other.txt")])
        records = [first, second]
        observations = [observation(), observation("other.txt")]
        before_records = json.loads(json.dumps(records))
        before_observations = json.loads(json.dumps(observations))
        left = audit_memory_records(
            records, workspace_id="workspace-1",
            source_observations=observations)
        right = audit_memory_records(
            list(reversed(records)), workspace_id="workspace-1",
            source_observations=list(reversed(observations)))
        self.assertEqual(
            json.dumps(left, ensure_ascii=False, sort_keys=True),
            json.dumps(right, ensure_ascii=False, sort_keys=True))
        self.assertEqual(before_records, records)
        self.assertEqual(before_observations, observations)

    def test_refused_report_is_deterministic_across_record_order(self):
        malformed = record(content="SECRET-CANARY")
        malformed["content_sha256"] = digest("different")
        duplicate = record()
        left = audit_memory_records(
            [malformed, duplicate, duplicate], workspace_id="workspace-1",
            source_observations=[])
        right = audit_memory_records(
            [duplicate, malformed, duplicate], workspace_id="workspace-1",
            source_observations=[])
        self.assertEqual(left, right)
        self.assertEqual("refused", left["status"])

    def test_invalid_observations_and_records_retain_a_bounded_prefix(self):
        report = audit_memory_records(
            ["one", "two"], workspace_id="workspace-1",
            source_observations=[], max_findings=1)
        self.assertEqual("refused", report["status"])
        self.assertEqual(1, report["counts"]["findings"])
        self.assertEqual(1, report["counts"]["omitted_findings"])
        self.assertEqual(1, len(report["findings"]))

    def test_foreign_record_is_omitted_even_with_invalid_source_ref(self):
        foreign = record(
            memory_id="mem_foreign",
            workspace_id="workspace-other",
            verification_status="verified",
            source_kind="official_docs",
            source_refs=[{
                "kind": "url",
                "ref": "https://example.com/foreign",
                "sha256": digest("foreign"),
                "scope": "whole-document",
            }],
        )
        report = audit_memory_records(
            [foreign], workspace_id="workspace-1",
            source_observations=[])
        self.assertEqual("partial", report["status"])
        self.assertEqual(0, report["counts"]["sources_required"])
        self.assertEqual(1, report["counts"]["foreign_workspace_omitted"])
        self.assertEqual([], report["findings"])

    def test_unused_source_observation_does_not_change_workspace_counts(self):
        report = audit_memory_records(
            [], workspace_id="workspace-1",
            source_observations=[observation(ref="unused.md")])
        self.assertEqual("complete", report["status"])
        self.assertEqual(0, report["counts"]["sources_required"])
        self.assertEqual(0, report["counts"]["sources_verified"])

    def test_invalid_source_observation_report_does_not_echo_value(self):
        report = audit_memory_records(
            [], workspace_id="workspace-1",
            source_observations=[{"ref": "SECRET-CANARY"}])
        self.assertEqual("refused", report["status"])
        self.assertNotIn("SECRET-CANARY", json.dumps(report))

    def test_non_iterable_inputs_have_distinct_typed_errors(self):
        with self.assertRaises(MemoryDoctorError) as records_error:
            audit_memory_records(
                None, workspace_id="workspace-1", source_observations=[])
        self.assertEqual("invalid_records", records_error.exception.error_type)
        with self.assertRaises(MemoryDoctorError) as sources_error:
            audit_memory_records(
                [], workspace_id="workspace-1", source_observations=None)
        self.assertEqual("invalid_source_observations",
                         sources_error.exception.error_type)

    def test_audit_does_not_touch_files_environment_sockets_or_commands(self):
        with mock.patch("builtins.open", side_effect=AssertionError("open")), \
                mock.patch("os.getenv", side_effect=AssertionError("env")), \
                mock.patch.object(socket, "socket",
                                  side_effect=AssertionError("socket")), \
                mock.patch("subprocess.run",
                           side_effect=AssertionError("command")):
            report = audit_memory_records(
                [record()], workspace_id="workspace-1",
                source_observations=[])
        self.assertEqual("complete", report["status"])


if __name__ == "__main__":
    unittest.main()
