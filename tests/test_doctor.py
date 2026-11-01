"""Doctor tests: a read-only answer to "can this shell start a run?".

Doctor reports what it checked, not what it hopes. It loads the same config a
run loads, resolves the same profile, and looks only for the credential's
presence. It never prints the credential value, opens a socket, or writes a
file. A configuration or environment problem is the command's result, so the
exit status is 2 and the report names the next action.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import _helpers as hp

ROOT = Path(__file__).resolve().parents[1]


def _config():
    return {
        "schema_version": "2",
        "name": "workbench",
        "default_profile": "local",
        "profiles": {
            "local": {
                "base_url": "http://127.0.0.1:8123/v1",
                "model": "stub",
                "api_key_env": "POCKETCHAT_API_KEY",
            },
            "hosted": {
                "base_url": "https://example.invalid/v1",
                "model": "hosted-model",
                "api_key_env": "POCKETCHAT_HOSTED_KEY",
            },
        },
    }


class DoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = self.base / "pocketchat.json"
        self.config.write_text(json.dumps(_config()), encoding="utf-8")

    def _doctor(self, *extra, env=None):
        return hp.run_cli(["doctor", "--config", str(self.config)] + list(extra),
                          self.base, env=env)

    def _json(self, result):
        self.assertEqual(result.returncode in (0, 2), True,
                         result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_json_report_carries_the_documented_envelope_version(self):
        result = self._doctor("--json", env={"POCKETCHAT_API_KEY": "test-key"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = self._json(result)
        self.assertEqual(payload["schema_version"], "1")
        self.assertTrue(payload["ok"])

    def test_json_ready_report_has_an_empty_next_actions_list(self):
        result = self._doctor("--json", env={"POCKETCHAT_API_KEY": "test-key"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self._json(result)["next_actions"], [])

    def test_json_missing_config_lists_the_init_action(self):
        self.config.unlink()
        result = self._doctor("--json", env={"POCKETCHAT_API_KEY": None})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = self._json(result)
        self.assertEqual(payload["next_actions"],
                         [payload["checks"][0]["fix"]])
        self.assertIn("pocketchat init", payload["next_actions"][0])

    def test_json_unknown_profile_lists_the_profile_action(self):
        result = self._doctor("--profile", "missing", "--json",
                              env={"POCKETCHAT_API_KEY": "test-key"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = self._json(result)
        self.assertEqual(payload["next_actions"],
                         [payload["checks"][1]["fix"]])
        self.assertIn("--profile", payload["next_actions"][0])

    def test_json_missing_credential_lists_the_export_action(self):
        result = self._doctor("--json", env={"POCKETCHAT_API_KEY": None})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = self._json(result)
        self.assertEqual(payload["next_actions"],
                         [payload["checks"][2]["fix"]])
        self.assertIn("export POCKETCHAT_API_KEY", payload["next_actions"][0])

    def test_ready_doctor_reports_the_selected_profile_without_the_secret(self):
        secret = "sk-not-a-real-key-0123456789"
        result = self._doctor("--json", env={"POCKETCHAT_API_KEY": secret})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = self._json(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["profile"], "local")
        self.assertEqual(payload["model"], "stub")
        self.assertEqual([check["name"] for check in payload["checks"]],
                         ["config", "profile", "credential"])
        self.assertEqual([check["status"] for check in payload["checks"]],
                         ["ok", "ok", "ok"])
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_json_report_exposes_a_stable_named_status_map(self):
        result = self._doctor("--json", env={"POCKETCHAT_API_KEY": "test-key"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = self._json(result)
        self.assertEqual(payload["check_status"], {
            "config": "ok", "profile": "ok", "credential": "ok"})

    def test_named_profile_is_the_one_checked(self):
        result = self._doctor("--profile", "hosted", "--json",
                              env={"POCKETCHAT_HOSTED_KEY": "hosted-secret"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = self._json(result)
        self.assertEqual(payload["profile"], "hosted")
        self.assertEqual(payload["model"], "hosted-model")
        self.assertEqual(payload["api_key_env"], "POCKETCHAT_HOSTED_KEY")

    def test_missing_config_still_has_the_named_status_map(self):
        self.config.unlink()
        result = self._doctor("--json", env={"POCKETCHAT_API_KEY": None})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = self._json(result)
        self.assertEqual(payload["check_status"], {
            "config": "failed", "profile": "skipped", "credential": "skipped"})

    def test_a_missing_credential_fails_with_a_specific_next_action(self):
        result = self._doctor("--json", env={"POCKETCHAT_API_KEY": None})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = self._json(result)
        self.assertFalse(payload["ok"])
        by_name = {check["name"]: check for check in payload["checks"]}
        self.assertEqual(by_name["credential"]["status"], "failed")
        self.assertIn("POCKETCHAT_API_KEY", by_name["credential"]["detail"])
        self.assertIn("export POCKETCHAT_API_KEY", by_name["credential"]["fix"])

    def test_an_unknown_profile_does_not_fall_back_to_the_default(self):
        result = self._doctor("--profile", "missing", "--json",
                              env={"POCKETCHAT_API_KEY": "test-key"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = self._json(result)
        self.assertFalse(payload["ok"])
        self.assertIsNone(payload["profile"])
        by_name = {check["name"]: check for check in payload["checks"]}
        self.assertEqual(by_name["config"]["status"], "ok")
        self.assertEqual(by_name["profile"]["status"], "failed")
        self.assertIn("missing", by_name["profile"]["detail"])
        self.assertEqual(by_name["credential"]["status"], "skipped")

    def test_a_missing_config_is_a_stable_report_not_a_traceback(self):
        self.config.unlink()
        result = self._doctor("--json", env={"POCKETCHAT_API_KEY": None})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = self._json(result)
        self.assertFalse(payload["ok"])
        self.assertEqual([check["name"] for check in payload["checks"]],
                         ["config", "profile", "credential"])
        self.assertEqual([check["status"] for check in payload["checks"]],
                         ["failed", "skipped", "skipped"])
        self.assertIn("pocketchat init", payload["checks"][0]["fix"])
        self.assertEqual(list(self.base.iterdir()), [])

    def test_an_invalid_config_does_not_echo_a_secret_looking_value(self):
        secret = "sk-secret-in-an-unknown-field"
        record = _config()
        record["profiles"]["local"]["api_key"] = secret
        self.config.write_text(json.dumps(record), encoding="utf-8")
        result = self._doctor("--json", env={"POCKETCHAT_API_KEY": "test-key"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = self._json(result)
        self.assertEqual(payload["checks"][0]["status"], "failed")
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_invalid_utf8_is_a_config_failure_not_a_traceback(self):
        self.config.write_bytes(b"\xff\xfe")
        result = self._doctor("--json", env={"POCKETCHAT_API_KEY": "test-key"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = self._json(result)
        self.assertEqual(payload["checks"][0]["status"], "failed")
        self.assertEqual(payload["checks"][1]["status"], "skipped")
        self.assertNotIn("Traceback", result.stderr)

    def test_an_unreadable_config_path_is_a_config_failure(self):
        self.config.unlink()
        self.config.mkdir()
        result = self._doctor("--json", env={"POCKETCHAT_API_KEY": "test-key"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = self._json(result)
        self.assertEqual(payload["checks"][0]["status"], "failed")
        self.assertIn("fix", payload["checks"][0])
        self.assertNotIn("Traceback", result.stderr)

    def test_doctor_is_read_only_and_opens_no_socket(self):
        before = {}
        for path in sorted(self.base.rglob("*")):
            if path.is_file():
                before[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        with mock.patch("socket.socket", side_effect=AssertionError("opened a socket")), \
                mock.patch("socket.create_connection",
                           side_effect=AssertionError("opened a socket")):
            result = self._doctor(env={"POCKETCHAT_API_KEY": "test-key"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = {}
        for path in sorted(self.base.rglob("*")):
            if path.is_file():
                after[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(after, before)

    def test_human_output_names_the_failed_check_and_the_fix(self):
        result = self._doctor(env={"POCKETCHAT_API_KEY": None})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("config", result.stdout)
        self.assertIn("profile", result.stdout)
        self.assertIn("credential", result.stdout)
        self.assertIn("POCKETCHAT_API_KEY", result.stdout)
        self.assertIn("doctor: not ready", result.stdout)

    def test_docs_name_the_doctor_json_envelope_fields(self):
        for relative in ("docs/cli.md", "docs/cli.zh-CN.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("schema_version", text)
            self.assertIn("check_status", text)
            self.assertIn("next_actions", text)
            self.assertIn("doctor", text)

    def test_help_runs(self):
        result = hp.run_cli(["doctor", "--help"], self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--profile", result.stdout)
        self.assertIn("--json", result.stdout)

    def test_doctor_runs_end_to_end_as_a_subprocess(self):
        environ = {
            "PYTHONPATH": str(ROOT),
            "PATH": "/usr/bin:/bin",
            "POCKETCHAT_API_KEY": "test-key",
        }
        result = subprocess.run(
            [sys.executable, "-m", "pocketchat", "doctor", "--config",
             str(self.config), "--json"],
            cwd=str(self.base), env=hp.subprocess_environment(environ), capture_output=True, text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
