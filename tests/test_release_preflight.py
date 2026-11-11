"""Read-only release acceptance preflight over caller-supplied evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pocketchat import release_preflight
from pocketchat.release_preflight import (SCHEMA, evaluate_release_acceptance,
                                          verify_release_acceptance)
from tests._helpers import run_cli

POSIX_READER = (
    os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and all(hasattr(os, flag) for flag in
            ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"))
)


SOURCE_SHA = "a" * 40
WHEEL_SHA256 = "b" * 64


def complete_observations():
    return {
        "schema_version": "pocketchat-release-acceptance-observations.v1",
        "release": {
            "version": "1.2.0",
            "source_sha": SOURCE_SHA,
            "wheel_sha256": WHEEL_SHA256,
        },
        "evidence": {
            "offline_fixture": {
                "version": "1.2.0",
                "source_sha": SOURCE_SHA,
                "wheel_sha256": WHEEL_SHA256,
                "evidence_sha256": "1" * 64,
            },
            "exact_ci": {
                "source_sha": SOURCE_SHA,
                "run_id": 123456,
                "check_count": 7,
                "evidence_sha256": "2" * 64,
            },
            "real_provider": {
                "source_sha": SOURCE_SHA,
                "wheel_sha256": WHEEL_SHA256,
                "provider_scope": "local-router",
                "model_scope": "fixture-model",
                "evidence_sha256": "3" * 64,
            },
            "manual_tty": {
                "source_sha": SOURCE_SHA,
                "wheel_sha256": WHEEL_SHA256,
                "operator_scope": "trusted-local-operator",
                "evidence_sha256": "4" * 64,
            },
            "publication_authority": {
                "source_sha": SOURCE_SHA,
                "wheel_sha256": WHEEL_SHA256,
                "action": "tag_release",
                "authority_sha256": "5" * 64,
            },
            "publication": {
                "source_sha": SOURCE_SHA,
                "wheel_sha256": WHEEL_SHA256,
                "tag": "v1.2.0",
                "evidence_sha256": "6" * 64,
            },
            "public_install_readback": {
                "version": "1.2.0",
                "wheel_sha256": WHEEL_SHA256,
                "evidence_sha256": "7" * 64,
            },
        },
        "blockers": [],
    }


class ReleasePreflightCoreTests(unittest.TestCase):
    def test_complete_supplied_evidence_is_complete_but_not_a_proof(self):
        payload = complete_observations()
        before = copy.deepcopy(payload)

        result = evaluate_release_acceptance(payload)

        self.assertEqual(SCHEMA, result["schema"])
        self.assertEqual("complete", result["status"])
        self.assertTrue(result["ok"])
        self.assertEqual("supplied_observations", result["scope"])
        self.assertIs(False, result["files_rechecked"])
        self.assertEqual(payload["release"], result["release"])
        self.assertEqual(
            ["offline_fixture", "exact_ci", "real_provider", "manual_tty",
             "publication_authority", "publication", "public_install_readback"],
            [row["gate"] for row in result["gates"]],
        )
        self.assertTrue(all(row["state"] == "satisfied" for row in result["gates"]))
        self.assertEqual([], result["pending"])
        self.assertEqual([], result["unknown"])
        self.assertEqual([], result["blocked"])
        self.assertEqual([], result["next_actions"])
        self.assertEqual(before, payload)

    def test_missing_supplied_evidence_is_pending_not_complete(self):
        payload = complete_observations()
        for gate in ("real_provider", "manual_tty", "publication_authority",
                     "publication", "public_install_readback"):
            payload["evidence"].pop(gate)

        result = evaluate_release_acceptance(payload)

        self.assertEqual("pending", result["status"])
        self.assertFalse(result["ok"])
        self.assertIn("real_provider", result["pending"])
        self.assertIn("manual_tty", result["pending"])
        self.assertIn("publication_authority", result["pending"])
        self.assertIn("publication", result["pending"])
        self.assertIn("public_install_readback", result["pending"])
        self.assertIn("run_authorized_real_provider_acceptance",
                      result["next_actions"])
        states = {row["gate"]: row["state"] for row in result["gates"]}
        self.assertEqual("satisfied", states["exact_ci"])
        self.assertEqual("pending", states["real_provider"])

    def test_unknown_and_not_run_blockers_are_distinct(self):
        payload = complete_observations()
        for gate in ("publication_authority", "publication",
                     "public_install_readback"):
            payload["evidence"].pop(gate)
        payload["blockers"] = [
            {"gate": "publication_authority", "state": "unknown"},
            {"gate": "publication", "state": "not_run"},
            {"gate": "public_install_readback", "state": "not_run"},
        ]

        result = evaluate_release_acceptance(payload)

        self.assertEqual("unknown", result["status"])
        self.assertEqual(["publication_authority"], result["unknown"])
        self.assertEqual(["publication", "public_install_readback"],
                         result["pending"])
        states = {row["gate"]: row["state"] for row in result["gates"]}
        self.assertEqual("unknown", states["publication_authority"])
        self.assertEqual("not_run", states["publication"])

    def test_explicit_failure_blocks_even_when_other_inputs_are_missing(self):
        payload = complete_observations()
        for gate in ("publication_authority", "publication",
                     "public_install_readback"):
            payload["evidence"].pop(gate)
        payload["blockers"] = [
            {"gate": "publication_authority", "state": "failed"},
            {"gate": "publication", "state": "not_run"},
            {"gate": "public_install_readback", "state": "not_run"},
        ]

        result = evaluate_release_acceptance(payload)

        self.assertEqual("blocked", result["status"])
        self.assertEqual(["publication_authority"], result["blocked"])
        self.assertEqual("failed", result["gates"][4]["state"])
        self.assertIn("obtain_exact_tag_release_authorization",
                      result["next_actions"])

    def test_identity_mismatch_and_later_evidence_before_earlier_gate_refuse(self):
        mismatches = []
        version = complete_observations()
        version["evidence"]["offline_fixture"]["version"] = "1.1.0"
        mismatches.append(version)
        source = complete_observations()
        source["evidence"]["exact_ci"]["source_sha"] = "9" * 40
        mismatches.append(source)
        wheel = complete_observations()
        wheel["evidence"]["public_install_readback"]["wheel_sha256"] = "8" * 64
        mismatches.append(wheel)
        tag = complete_observations()
        tag["evidence"]["publication"]["tag"] = "v1.3.0"
        mismatches.append(tag)
        ci_count = complete_observations()
        ci_count["evidence"]["exact_ci"]["check_count"] = 6
        mismatches.append(ci_count)

        for payload in mismatches:
            with self.subTest(payload=payload):
                with self.assertRaises(Exception) as caught:
                    evaluate_release_acceptance(payload)
                self.assertEqual("identity_mismatch", caught.exception.reason)

        order = complete_observations()
        for gate in ("real_provider", "manual_tty", "publication_authority"):
            order["evidence"].pop(gate)
        with self.assertRaises(Exception) as caught:
            evaluate_release_acceptance(order)
        self.assertEqual("evidence_order", caught.exception.reason)

    def test_closed_shapes_duplicate_blockers_and_invalid_digests_refuse(self):
        extra = complete_observations()
        extra["extra"] = "secret-canary"
        evidence_extra = complete_observations()
        evidence_extra["evidence"]["real_provider"]["extra"] = "secret-canary"
        bad_digest = complete_observations()
        bad_digest["release"]["wheel_sha256"] = "not-a-digest-secret-canary"
        duplicate_blocker = complete_observations()
        duplicate_blocker["evidence"].pop("publication_authority")
        duplicate_blocker["evidence"].pop("publication")
        duplicate_blocker["evidence"].pop("public_install_readback")
        duplicate_blocker["blockers"] = [
            {"gate": "publication_authority", "state": "not_run"},
            {"gate": "publication_authority", "state": "unknown"},
        ]
        blocker_and_evidence = complete_observations()
        blocker_and_evidence["blockers"] = [
            {"gate": "public_install_readback", "state": "failed"},
        ]
        for payload in (extra, evidence_extra, bad_digest,
                        duplicate_blocker, blocker_and_evidence):
            with self.subTest(payload=payload):
                with self.assertRaises(Exception) as caught:
                    evaluate_release_acceptance(payload)
                self.assertNotIn("secret-canary", str(caught.exception))
                self.assertNotIn("secret-canary", repr(caught.exception.reason))

    def test_non_digest_scope_text_is_validated_but_not_echoed(self):
        payload = complete_observations()
        payload["evidence"]["real_provider"]["provider_scope"] = "route-canary"
        payload["evidence"]["manual_tty"]["operator_scope"] = "operator-canary"
        result = evaluate_release_acceptance(payload)
        self.assertEqual("complete", result["status"])
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("route-canary", rendered)
        self.assertNotIn("operator-canary", rendered)

    def test_report_is_byte_stable_and_bounded(self):
        payload = complete_observations()
        first = json.dumps(evaluate_release_acceptance(payload), sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
        second = json.dumps(evaluate_release_acceptance(payload), sort_keys=True,
                            separators=(",", ":")).encode("utf-8")
        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 64 * 1024)
        result = evaluate_release_acceptance(payload)
        body = {key: value for key, value in result.items()
                if key != "report_sha256"}
        self.assertEqual(
            hashlib.sha256(json.dumps(
                body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")).hexdigest(),
            result["report_sha256"],
        )


@unittest.skipUnless(POSIX_READER, "requires secure descriptor-relative reads")
class ReleasePreflightCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.input = self.root / "release-preflight.json"

    def write(self, payload=None, data=None):
        if data is None:
            data = json.dumps(payload or complete_observations()).encode("utf-8")
        self.input.write_bytes(data)

    def command(self, *extra, as_json=True):
        args = ["release-preflight", "release-preflight.json", "--workspace",
                str(self.root)]
        if as_json:
            args.append("--json")
        args.extend(extra)
        return run_cli(args, self.root)

    def test_complete_json_and_human_output_share_status_and_do_not_write(self):
        self.write()
        before = sorted((path.relative_to(self.root).as_posix(),
                         hashlib.sha256(path.read_bytes()).hexdigest())
                        for path in self.root.rglob("*") if path.is_file())

        machine = self.command()
        human = self.command(as_json=False)

        self.assertEqual(0, machine.returncode, machine.stdout + machine.stderr)
        self.assertEqual("", machine.stderr)
        payload = json.loads(machine.stdout)
        self.assertEqual("complete", payload["status"])
        self.assertTrue(payload["ok"])
        self.assertEqual(hashlib.sha256(self.input.read_bytes()).hexdigest(),
                         payload["source_sha256"])
        self.assertIn("Release acceptance preflight: complete", human.stdout)
        self.assertEqual(0, human.returncode, human.stdout + human.stderr)
        after = sorted((path.relative_to(self.root).as_posix(),
                        hashlib.sha256(path.read_bytes()).hexdigest())
                       for path in self.root.rglob("*") if path.is_file())
        self.assertEqual(before, after)

    def test_pending_unknown_blocked_and_refused_exit_codes(self):
        pending = complete_observations()
        pending["evidence"].pop("real_provider")
        for gate in ("manual_tty", "publication_authority", "publication",
                     "public_install_readback"):
            pending["evidence"].pop(gate)
        self.write(pending)
        answer = self.command()
        self.assertEqual(1, answer.returncode, answer.stdout + answer.stderr)
        self.assertEqual("pending", json.loads(answer.stdout)["status"])

        unknown = copy.deepcopy(pending)
        unknown["blockers"] = [
            {"gate": "real_provider", "state": "unknown"},
            {"gate": "manual_tty", "state": "not_run"},
            {"gate": "publication_authority", "state": "not_run"},
            {"gate": "publication", "state": "not_run"},
            {"gate": "public_install_readback", "state": "not_run"},
        ]
        self.write(unknown)
        answer = self.command()
        self.assertEqual(1, answer.returncode, answer.stdout + answer.stderr)
        self.assertEqual("unknown", json.loads(answer.stdout)["status"])

        blocked = copy.deepcopy(unknown)
        blocked["blockers"][0]["state"] = "failed"
        self.write(blocked)
        answer = self.command()
        self.assertEqual(1, answer.returncode, answer.stdout + answer.stderr)
        self.assertEqual("blocked", json.loads(answer.stdout)["status"])

        bad = complete_observations()
        bad["release"]["source_sha"] = "not-a-source"
        self.write(bad)
        answer = self.command()
        self.assertEqual(2, answer.returncode, answer.stdout + answer.stderr)
        self.assertEqual("refused", json.loads(answer.stdout)["status"])

    def test_invalid_json_duplicate_keys_and_oversize_refuse_without_echo(self):
        cases = [
            b"\xff",
            b'{"schema_version":"x","schema_version":"y"}',
            b'{"schema_version":NaN}',
            b'[' * 70 + b'0' + b']' * 70,
            b'{"secret-canary": "' + b'x' * (65 * 1024) + b'"}',
        ]
        for data in cases:
            with self.subTest(data=data[:30]):
                self.write(data=data)
                answer = self.command()
                self.assertEqual(2, answer.returncode, answer.stdout + answer.stderr)
                self.assertEqual("", answer.stderr)
                payload = json.loads(answer.stdout)
                self.assertEqual("refused", payload["status"])
                self.assertNotIn("secret-canary", answer.stdout)

    def test_symlink_outside_workspace_and_nonregular_input_refuse(self):
        outside_temp = tempfile.TemporaryDirectory()
        self.addCleanup(outside_temp.cleanup)
        outside = Path(outside_temp.name).resolve()
        target = outside / "outside.json"
        target.write_text(json.dumps(complete_observations()), encoding="utf-8")
        self.input.symlink_to(target)
        answer = self.command()
        self.assertEqual(2, answer.returncode, answer.stdout + answer.stderr)
        self.assertEqual("refused", json.loads(answer.stdout)["status"])

        outside_input = outside / "input.json"
        outside_input.write_text(json.dumps(complete_observations()), encoding="utf-8")
        answer = run_cli(
            ["release-preflight", str(outside_input), "--workspace", str(self.root),
             "--json"], self.root)
        self.assertEqual(2, answer.returncode, answer.stdout + answer.stderr)
        self.assertEqual("refused", json.loads(answer.stdout)["status"])

    def test_verify_release_acceptance_reads_only_the_named_input(self):
        self.write()
        before = hashlib.sha256(self.input.read_bytes()).hexdigest()
        result = verify_release_acceptance("release-preflight.json",
                                           workspace=self.root)
        self.assertEqual("complete", result["status"])
        self.assertEqual(before, result["source_sha256"])
        self.assertEqual(before, hashlib.sha256(self.input.read_bytes()).hexdigest())
        body = {key: value for key, value in result.items()
                if key != "report_sha256"}
        self.assertEqual(
            hashlib.sha256(json.dumps(
                body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")).hexdigest(),
            result["report_sha256"],
        )

    def test_unsupported_descriptor_platform_is_refused(self):
        with mock.patch.object(release_preflight.os, "supports_dir_fd", set()):
            result = release_preflight.verify_release_acceptance(
                "release-preflight.json", workspace=self.root)
        self.assertEqual("refused", result["status"])
        self.assertEqual("unsupported_platform", result["reason"])


if __name__ == "__main__":
    unittest.main()
