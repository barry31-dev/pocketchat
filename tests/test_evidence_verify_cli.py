"""Process-level checks for the explicit, read-only observation verifier."""

import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
INPUT_LIMIT = 1024 * 1024
POSIX_READER = (
    os.name == "posix"
    and os.open in os.supports_dir_fd
    and all(hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"))
)


def artifact(identifier="session-a", kind="session"):
    return {
        "id": identifier, "kind": kind, "path": f"unread/{identifier}.json",
        "whole_file_sha256": "a" * 64,
        "observed_whole_file_sha256": "a" * 64,
        "semantic_sha256": "b" * 64, "semantic_scope": "canonical-json",
        "read_coverage": "complete", "identity": {"run_id": "run-a"},
    }


def observations():
    return {
        "schema_version": 1,
        "artifacts": [artifact(), artifact("trace-a", "trace")],
        "relations": [{"id": "link-a", "source": "session-a", "target": "trace-a",
                       "kind": "same-run", "fields": ["run_id"]}],
    }


@unittest.skipUnless(POSIX_READER, "requires secure descriptor-relative reads")
class EvidenceVerifyCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.input = self.workspace / "observations.json"
        self.write(observations())

    def write(self, value):
        self.input.write_text(json.dumps(value), encoding="utf-8")

    def run_cli(self, *args, code=None):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [sys.executable, "-B"]
        command += ["-c", code] if code else ["-m", "pocketchat"]
        command += list(args)
        return subprocess.run(command, cwd=self.workspace, env=env,
                              capture_output=True, text=True, timeout=15, check=False)

    def verify(self, *, target="observations.json", as_json=True, code=None):
        args = ["evidence", "verify", target, "--workspace", str(self.workspace)]
        if as_json:
            args.append("--json")
        return self.run_cli(*args, code=code)

    def result(self, status, exit_code, **kwargs):
        result = self.verify(**kwargs)
        self.assertEqual(exit_code, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(status, payload["status"])
        self.assertEqual(exit_code == 0, payload["ok"])
        self.assertEqual("1", payload["schema_version"])
        self.assertEqual("supplied_observations", payload["scope"])
        self.assertIs(False, payload["files_rechecked"])
        self.assertLessEqual(len(result.stdout.encode("utf-8")), 65536)
        return payload

    def tree(self):
        return {str(path.relative_to(self.workspace)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.workspace.rglob("*") if path.is_file()}

    def test_complete_is_one_json_document_and_does_not_read_original_artifacts(self):
        before = self.tree()
        payload = self.result("complete", 0)
        self.assertEqual("pocketchat-evidence-links.v1", payload["verification"]["schema"])
        self.assertEqual(2, len(payload["verification"]["artifacts"]))
        self.assertEqual(hashlib.sha256(self.input.read_bytes()).hexdigest(),
                         payload["source_sha256"])
        self.assertEqual(before, self.tree())
        self.assertFalse((self.workspace / "unread").exists())

    def test_partial_coverage_is_not_success(self):
        value = observations()
        value["artifacts"][0]["read_coverage"] = "partial"
        self.write(value)
        self.result("partial", 1)

    def test_missing_optional_artifact_is_partial(self):
        value = observations()
        missing = artifact("optional-a", "checkpoint")
        missing.update(required=False, present=False)
        value["artifacts"].append(missing)
        self.write(value)
        self.result("partial", 1)

    def test_digest_mismatch_is_refused(self):
        value = observations()
        value["artifacts"][0]["observed_whole_file_sha256"] = "c" * 64
        self.write(value)
        payload = self.result("refused", 2)
        self.assertIn("digest_mismatch", json.dumps(payload))

    def test_wrong_run_identity_is_refused(self):
        value = observations()
        value["artifacts"][1]["identity"]["run_id"] = "other-run"
        self.write(value)
        self.result("refused", 2)

    def test_nonempty_artifacts_without_links_are_explicitly_partial(self):
        value = observations()
        value["relations"] = []
        self.write(value)
        payload = self.result("partial", 1)
        self.assertIn("no_relations", json.dumps(payload))

    def test_empty_observation_set_is_refused(self):
        self.write({"schema_version": 1, "artifacts": [], "relations": []})
        self.result("refused", 2)

    def test_human_output_uses_the_same_status_and_exit_code(self):
        value = observations()
        value["artifacts"][0]["read_coverage"] = "partial"
        self.write(value)
        machine = self.result("partial", 1)
        human = self.verify(as_json=False)
        self.assertEqual(1, human.returncode)
        self.assertEqual("", human.stderr)
        self.assertIn(machine["status"], human.stdout)
        self.assertIn("supplied observations", human.stdout)
        self.assertIn("not rechecked", human.stdout)

    def test_output_never_echoes_ids_identity_paths_or_diagnostics(self):
        canary = "credential-canary-not-real"
        value = observations()
        value["artifacts"][0].update(id=canary, path=canary + "/file.json")
        value["artifacts"][0]["identity"]["run_id"] = canary
        value["relations"][0]["source"] = canary
        value["artifacts"][1]["identity"]["run_id"] = "different-run"
        self.write(value)
        payload = self.result("refused", 2)
        text = json.dumps(payload)
        self.assertNotIn(canary, text)
        self.assertNotIn(str(self.workspace), text)
        self.assertNotIn("detail", text)
        self.assertNotIn("run-a", text)
        self.assertIn("index", text)

    def test_unknown_artifact_kind_never_becomes_output_text(self):
        value = observations()
        value["artifacts"][0]["kind"] = "credential-kind-canary"
        self.write(value)
        result = self.verify()
        self.assertEqual(2, result.returncode)
        self.assertNotIn("credential-kind-canary", result.stdout + result.stderr)

    def test_real_missing_descriptor_primitive_is_unsupported(self):
        from pocketchat import service
        with mock.patch.object(os, "supports_dir_fd", set()):
            result = service.verify_evidence_observations(
                "observations.json", workspace=self.workspace)
        self.assertEqual("refused", result["status"])
        self.assertEqual("unsupported_platform", result["reason"])

    def test_same_input_has_byte_stable_output(self):
        first = self.verify()
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(first.stdout, self.verify().stdout)

    def test_strict_json_and_closed_outer_contract(self):
        cases = [b"\xff", b'{"schema_version":1,"schema_version":1}',
                 b'{"schema_version":NaN}', b'{"schema_version":1e999}',
                 b"[", b"[" * 66 + b"0" + b"]" * 66,
                 json.dumps(dict(observations(), schema_version=True)).encode(),
                 json.dumps(dict(observations(), schema_version=2)).encode(),
                 json.dumps(dict(observations(), body="secret-body-canary")).encode()]
        for data in cases:
            with self.subTest(data=data[:45]):
                self.input.write_bytes(data)
                payload = self.result("refused", 2)
                self.assertEqual("evidence", payload["error"])
                self.assertNotIn("secret-body-canary", json.dumps(payload))

    def test_closed_metadata_and_malformed_values_do_not_escape(self):
        for field, value in [("body", "secret-body-canary"), ("id", []),
                             ("kind", {}), ("path", "../private"),
                             ("identity", {"run_id": {}}),
                             ("required", "yes"), ("present", 0),
                             ("semantic_scope", []), ("read_coverage", [])]:
            with self.subTest(field=field):
                payload = observations()
                payload["artifacts"][0][field] = value
                self.write(payload)
                answer = self.result("refused", 2)
                self.assertNotIn("secret-body-canary", json.dumps(answer))

    def test_escaped_unpaired_surrogates_are_safe_json_refusals(self):
        for field in ("path", "kind", "semantic_scope", "read_coverage"):
            with self.subTest(field=field):
                payload = observations()
                payload["artifacts"][0][field] = "escaped-\ud800"
                self.write(payload)
                answer = self.result("refused", 2)
                self.assertEqual("invalid_json", answer["reason"])

    def test_relation_metadata_and_orphan_links_are_refused(self):
        for field, value in [("source", "missing-a"), ("fields", ["body"]),
                             ("fields", [[]]), ("kind", []),
                             ("body", "secret-body-canary")]:
            with self.subTest(field=field):
                payload = observations()
                payload["relations"][0][field] = value
                self.write(payload)
                self.result("refused", 2)

    def test_duplicate_artifact_and_relation_ids_are_refused(self):
        for group in ("artifacts", "relations"):
            with self.subTest(group=group):
                payload = observations()
                payload[group].append(copy.deepcopy(payload[group][0]))
                self.write(payload)
                self.result("refused", 2)

    def test_input_limit_is_inclusive(self):
        raw = self.input.read_bytes()
        self.input.write_bytes(raw + b" " * (INPUT_LIMIT - len(raw)))
        self.result("complete", 0)
        with self.input.open("ab") as output:
            output.write(b" ")
        self.result("refused", 2)

    def test_artifact_count_limit_is_inclusive(self):
        value = {"schema_version": 1, "artifacts": [artifact(f"a-{i}")
                 for i in range(256)], "relations": []}
        self.write(value)
        answer = self.result("refused", 2)
        self.assertIn("report_limit", json.dumps(answer))
        self.assertNotIn("artifact_limit", json.dumps(answer))
        value["artifacts"].append(artifact("one-over"))
        self.write(value)
        answer = self.result("refused", 2)
        self.assertIn("artifact_limit", json.dumps(answer))

    def test_relation_count_limit_is_inclusive(self):
        value = observations()
        value["relations"] = [dict(value["relations"][0], id=f"link-{i}")
                              for i in range(1024)]
        self.write(value)
        payload = self.result("complete", 0)
        self.assertEqual(1024, len(payload["verification"]["edges"]))
        value["relations"].append(dict(value["relations"][0], id="one-over"))
        self.write(value)
        self.result("refused", 2)

    def test_missing_nonregular_and_unsafe_input_paths_are_safe_refusals(self):
        (self.workspace / "directory").mkdir()
        for target in ("missing.json", "directory", "../observations.json",
                       str(self.input), "./observations.json", "a//b", ".env"):
            with self.subTest(target=target):
                self.result("refused", 2, target=target)

    def test_symlink_and_symlink_parent_are_refused(self):
        (self.workspace / "link.json").symlink_to(self.input)
        (self.workspace / "nested").mkdir()
        (self.workspace / "alias").symlink_to(self.workspace / "nested", target_is_directory=True)
        for target in ("link.json", "alias/value.json"):
            with self.subTest(target=target):
                self.result("refused", 2, target=target)

    def test_default_workspace_is_the_process_working_directory(self):
        result = self.run_cli("evidence", "verify", "observations.json", "--json")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIs(False, json.loads(result.stdout)["files_rechecked"])

    def test_no_provider_command_config_or_artifact_output_side_effects(self):
        code = """
import sys
from unittest import mock
from pocketchat import cli

def forbidden(*args, **kwargs):
    raise AssertionError('an observation verifier must not execute this hook')
with mock.patch('socket.socket', forbidden), mock.patch('subprocess.Popen', forbidden), \\
     mock.patch.object(cli, 'load_config', forbidden), \\
     mock.patch.object(cli, 'load_api_key', forbidden), \\
     mock.patch.object(cli, 'run_task', forbidden), \\
     mock.patch('pocketchat.provider.complete', forbidden):
    sys.exit(cli.main(sys.argv[1:]))
"""
        before = self.tree()
        self.result("complete", 0, code=code)
        self.assertEqual(before, self.tree())

    def test_unsupported_reader_and_changed_input_have_safe_reports(self):
        from pocketchat import service
        from pocketchat.acceptance import _ReadIssue
        for reason in ("unsupported_platform", "file_changed", "unreadable_file"):
            with self.subTest(reason=reason):
                with mock.patch('pocketchat.acceptance._read_file',
                                side_effect=_ReadIssue("refused", reason)):
                    payload = service.verify_evidence_observations(
                        "observations.json", workspace=self.workspace)
                self.assertEqual("refused", payload["status"])
                self.assertEqual(reason, payload["reason"])

    def test_global_report_limit_cannot_be_bound_to_an_artifact_named_report(self):
        value = {"schema_version": 1,
                 "artifacts": [artifact("report")] + [artifact(f"a-{i}") for i in range(255)],
                 "relations": []}
        self.write(value)
        payload = self.result("refused", 2)
        self.assertEqual([{"target": "input", "index": None, "reason": "report_limit"}],
                         payload["verification"]["unknowns"])

    def test_missing_nonblock_flag_has_no_unsafe_reader_fallback(self):
        from pocketchat import service
        flag = os.O_NONBLOCK
        try:
            del os.O_NONBLOCK
            result = service.verify_evidence_observations(
                "observations.json", workspace=self.workspace)
        finally:
            os.O_NONBLOCK = flag
        self.assertEqual("refused", result["status"])
        self.assertEqual("unsupported_platform", result["reason"])

    def test_missing_directory_flag_has_no_unsafe_reader_fallback(self):
        from pocketchat import service
        flag = os.O_DIRECTORY
        try:
            del os.O_DIRECTORY
            result = service.verify_evidence_observations(
                "observations.json", workspace=self.workspace)
        finally:
            os.O_DIRECTORY = flag
        self.assertEqual("refused", result["status"])
        self.assertEqual("unsupported_platform", result["reason"])

    def test_service_input_is_preserved_and_report_overflow_is_refused(self):
        from pocketchat import service
        from pocketchat.evidence_links import verify_evidence_links
        value = observations()
        before = copy.deepcopy(value)
        with mock.patch('pocketchat.acceptance._decode', return_value=value):
            result = service.verify_evidence_observations(
                "observations.json", workspace=self.workspace)
        self.assertEqual("complete", result["status"])
        self.assertEqual(before, value)
        oversized = verify_evidence_links(value["artifacts"], value["relations"])
        oversized["unknowns"] = [{"id": "session-a", "reason": "invalid_id",
                                  "detail": "private-not-returned"}] * 2000
        with mock.patch('pocketchat.evidence_links.verify_evidence_links', return_value=oversized):
            result = service.verify_evidence_observations(
                "observations.json", workspace=self.workspace)
        self.assertEqual("refused", result["status"])
        self.assertEqual("report_limit", result["reason"])
        self.assertNotIn("private-not-returned", json.dumps(result))

    def test_json_command_has_one_handled_error_document(self):
        from pocketchat import cli
        output, error = io.StringIO(), io.StringIO()
        with mock.patch('sys.stdout', output), mock.patch('sys.stderr', error):
            code = cli.main(["evidence", "verify", "missing.json", "--workspace",
                             str(self.workspace), "--json"])
        self.assertEqual(2, code)
        self.assertEqual("", error.getvalue())
        self.assertEqual("evidence", json.loads(output.getvalue())["error"])


class EvidenceVerifyPortabilityTests(unittest.TestCase):
    def test_platform_without_posix_reader_refuses_safely(self):
        from pocketchat import service
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            input_path = workspace / "observations.json"
            input_path.write_text(json.dumps(observations()), encoding="utf-8")
            if POSIX_READER:
                unsupported = mock.patch.object(os, "supports_dir_fd", set())
            else:
                unsupported = mock.patch.object(sys, "platform", "win32")
            with unsupported:
                result = service.verify_evidence_observations(
                    "observations.json", workspace=workspace)
        self.assertEqual("refused", result["status"])
        self.assertEqual("unsupported_platform", result["reason"])


if __name__ == "__main__":
    unittest.main()
