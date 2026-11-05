"""Pure offline artifact secret audit tests."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest
from unittest import mock

from pocketchat.artifact_audit import ArtifactAuditError, audit_artifacts


def artifact(artifact_id="artifact-1", kind="session", path="sessions/a.jsonl",
             content=b"plain content", entry_type="regular_file"):
    return {
        "artifact_id": artifact_id,
        "kind": kind,
        "path": path,
        "entry_type": entry_type,
        "content": content,
    }


def report_digest(report):
    body = {key: value for key, value in report.items()
            if key != "report_sha256"}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ArtifactAuditTests(unittest.TestCase):
    def test_clean_artifact_has_no_matches(self):
        report = audit_artifacts([artifact()], [b"missing"])
        self.assertEqual("clean", report["status"])
        self.assertEqual(0, report["match_count"])
        self.assertEqual([], report["matches"])
        self.assertEqual(report_digest(report), report["report_sha256"])

    def test_ascii_secret_reports_only_bounded_identity(self):
        secret = b"SENTINEL-ASCII-SECRET"
        report = audit_artifacts(
            [artifact(content=b"before " + secret + b" after")], [secret])
        self.assertEqual("found", report["status"])
        self.assertEqual(1, report["match_count"])
        self.assertEqual([{
            "artifact_id": "artifact-1",
            "location_category": "session",
            "artifact_sha256": hashlib.sha256(
                b"before " + secret + b" after").hexdigest(),
        }], report["matches"])
        rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("SENTINEL-ASCII-SECRET", rendered)
        self.assertNotIn("before ", rendered)
        self.assertNotIn("sessions/a.jsonl", rendered)

    def test_unicode_and_multibyte_secret_match_bytes(self):
        secret = "秘密🧪"
        payload = "前缀".encode("utf-8") + secret.encode("utf-8") + "后缀".encode("utf-8")
        report = audit_artifacts([artifact(content=payload)], [secret])
        self.assertEqual("found", report["status"])
        self.assertEqual(1, report["match_count"])
        self.assertNotIn(secret, json.dumps(report, ensure_ascii=False))

    def test_secret_crossing_internal_chunk_boundary_is_found(self):
        secret = b"\xf0\x9f\xa7\xaa"
        payload = b"x" * 7 + secret + b"y" * 5
        report = audit_artifacts([artifact(content=payload)], [secret])
        self.assertEqual("found", report["status"])
        self.assertEqual(1, report["match_count"])

    def test_duplicate_hits_in_one_artifact_are_deduplicated(self):
        secret = b"repeat"
        report = audit_artifacts(
            [artifact(content=secret + b"-" + secret)], [secret])
        self.assertEqual(1, report["match_count"])
        self.assertEqual(1, len(report["matches"]))

    def test_all_allowed_artifact_kinds_are_accepted(self):
        secret = b"secret"
        rows = [
            artifact("a", "request", "request.json", secret),
            artifact("b", "trace", "trace.jsonl", secret),
            artifact("c", "session", "session.jsonl", secret),
            artifact("d", "comparison", "comparison.json", secret),
            artifact("e", "execution_report", "report.json", secret),
            artifact("f", "checkpoint", "checkpoint.json", secret),
        ]
        report = audit_artifacts(rows, [secret])
        self.assertEqual("found", report["status"])
        self.assertEqual(6, report["match_count"])
        self.assertEqual(
            ["a", "b", "c", "d", "e", "f"],
            [row["artifact_id"] for row in report["matches"]])

    def test_empty_secret_is_refused_without_echo(self):
        report = audit_artifacts([artifact()], [b""])
        self.assertEqual("refused", report["status"])
        self.assertEqual(0, report["match_count"])
        self.assertNotIn("artifact-1", json.dumps(report))

    def test_duplicate_artifact_id_is_refused(self):
        report = audit_artifacts(
            [artifact("same"), artifact("same", path="other.json")], [b"x"])
        self.assertEqual("refused", report["status"])

    def test_duplicate_artifact_path_is_refused(self):
        report = audit_artifacts(
            [artifact("one"), artifact("two", path="sessions/a.jsonl")], [b"x"])
        self.assertEqual("refused", report["status"])

    def test_unsafe_path_is_refused_without_path_echo(self):
        report = audit_artifacts(
            [artifact(path="../outside.txt")], [b"x"])
        self.assertEqual("refused", report["status"])
        self.assertNotIn("outside.txt", json.dumps(report))

    def test_non_regular_artifact_is_refused(self):
        report = audit_artifacts(
            [artifact(entry_type="symlink")], [b"x"])
        self.assertEqual("refused", report["status"])

    def test_unknown_artifact_kind_is_refused(self):
        report = audit_artifacts([artifact(kind="unknown")], [b"x"])
        self.assertEqual("refused", report["status"])

    def test_artifact_limit_is_inclusive(self):
        rows = [artifact("a", content=b"secret"), artifact("b", path="b.json", content=b"secret")]
        self.assertEqual("found", audit_artifacts(rows, [b"secret"], max_artifacts=2)["status"])
        self.assertEqual("refused", audit_artifacts(rows, [b"secret"], max_artifacts=1)["status"])

    def test_artifact_byte_limit_is_inclusive(self):
        content = b"12345"
        self.assertEqual("clean", audit_artifacts(
            [artifact(content=content)], [b"missing"],
            max_artifact_bytes=len(content))["status"])
        self.assertEqual("refused", audit_artifacts(
            [artifact(content=content)], [b"missing"],
            max_artifact_bytes=len(content) - 1)["status"])

    def test_total_byte_limit_is_enforced(self):
        rows = [artifact("a", content=b"123"), artifact("b", path="b", content=b"456")]
        self.assertEqual("clean", audit_artifacts(
            rows, [b"missing"], max_total_bytes=6)["status"])
        self.assertEqual("refused", audit_artifacts(
            rows, [b"missing"], max_total_bytes=5)["status"])

    def test_match_limit_is_enforced_after_deduplication(self):
        rows = [
            artifact("a", content=b"secret"),
            artifact("b", path="b", content=b"secret"),
        ]
        self.assertEqual("found", audit_artifacts(
            rows, [b"secret"], max_matches=2)["status"])
        self.assertEqual("refused", audit_artifacts(
            rows, [b"secret"], max_matches=1)["status"])

    def test_report_limit_refuses_without_truncating_to_clean(self):
        report = audit_artifacts(
            [artifact(content=b"secret")], [b"secret"], max_report_bytes=32)
        self.assertEqual("refused", report["status"])
        self.assertEqual(0, report["match_count"])
        self.assertEqual([], report["matches"])

    def test_invalid_invocation_limits_raise_typed_error(self):
        for kwargs in (
                {"max_artifacts": 0},
                {"max_artifacts": True},
                {"max_artifact_bytes": 0},
                {"max_total_bytes": -1},
                {"max_matches": 0},
                {"max_report_bytes": "64"},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ArtifactAuditError):
                    audit_artifacts([], [], **kwargs)

    def test_inputs_are_not_mutated(self):
        rows = [artifact(content=b"secret")]
        secrets = [b"secret"]
        before_rows = copy.deepcopy(rows)
        before_secrets = list(secrets)
        audit_artifacts(rows, secrets)
        self.assertEqual(before_rows, rows)
        self.assertEqual(before_secrets, secrets)

    def test_output_is_stable_for_equivalent_input_order(self):
        rows = [
            artifact("b", path="b", content=b"secret"),
            artifact("a", path="a", content=b"secret"),
        ]
        first = audit_artifacts(rows, [b"secret"])
        second = audit_artifacts(list(reversed(rows)), [b"secret"])
        self.assertEqual(first, second)

    def test_no_io_or_provider_calls(self):
        with mock.patch("builtins.open") as opened, \
                mock.patch("socket.socket") as socket_factory, \
                mock.patch("subprocess.run") as run, \
                mock.patch("os.open") as os_open:
            report = audit_artifacts([artifact(content=b"secret")], [b"secret"])
        self.assertEqual("found", report["status"])
        opened.assert_not_called()
        socket_factory.assert_not_called()
        run.assert_not_called()
        os_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
