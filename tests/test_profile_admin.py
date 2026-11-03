"""CLI profile write tests: schema-2 changes are explicit and fail closed.

Profile writes are configuration edits, so every negative case must leave the
file byte-for-byte unchanged. The credential is represented only by the name of
its environment variable; no command accepts or prints its value.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import _helpers as hp

ROOT = Path(__file__).resolve().parents[1]


def _config():
    return {
        "schema_version": "2",
        "name": "workbench",
        "default_profile": "local",
        "system_prompt": "Be brief.",
        "profiles": {
            "local": {
                "base_url": "http://127.0.0.1:8123/v1",
                "model": "stub",
                "api_key_env": "POCKETCHAT_API_KEY",
                "timeout_seconds": 30,
            },
            "hosted": {
                "base_url": "https://example.invalid/v1",
                "model": "hosted-model",
                "api_key_env": "POCKETCHAT_HOSTED_KEY",
            },
        },
    }


class ProfileWriteCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = self.base / "pocketchat.json"
        self.config.write_text(json.dumps(_config()), encoding="utf-8")

    def _run(self, *args, env=None):
        return hp.run_cli(
            ["profile"] + list(args) + ["--config", str(self.config)],
            self.base, env=env)

    def _saved(self):
        return self.config.read_bytes()

    def test_help_names_the_three_write_commands(self):
        for command in ("set", "use", "remove"):
            result = hp.run_cli(["profile", command, "--help"], self.base)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = hp.run_cli(["profile", "set", "--help"], self.base)
        for option in ("--base-url", "--model", "--api-key-env", "--json",
                       "--max-turn-tokens", "--max-session-tokens",
                       "--model-context-window-tokens",
                       "--output-reserve-tokens", "--safety-margin-tokens",
                       "--max-output-tokens-supported", "--context-projection",
                       "--context-projection-max-bytes"):
            self.assertIn(option, result.stdout)

    def test_set_carries_model_window_capacity(self):
        result = self._run(
            "set", "window", "--base-url", "https://new.invalid/v1",
            "--model", "window-model", "--api-key-env", "NEW_KEY",
            "--model-context-window-tokens", "128000",
            "--output-reserve-tokens", "4096",
            "--safety-margin-tokens", "1024",
            "--max-output-tokens-supported", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        raw = json.loads(self.config.read_text(encoding="utf-8"))
        profile = raw["profiles"]["window"]
        self.assertEqual(128000, profile["model_context_window_tokens"])
        self.assertEqual(4096, profile["output_reserve_tokens"])
        self.assertEqual(1024, profile["safety_margin_tokens"])
        self.assertIs(True, profile["max_output_tokens_supported"])

    def test_set_carries_context_projection_fields(self):
        result = self._run(
            "set", "projection", "--base-url", "https://new.invalid/v1",
            "--model", "projection-model", "--api-key-env", "NEW_KEY",
            "--context-projection",
            "--context-projection-max-bytes", "32768", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        raw = json.loads(self.config.read_text(encoding="utf-8"))
        profile = raw["profiles"]["projection"]
        self.assertIs(True, profile["context_projection_enabled"])
        self.assertEqual(32768, profile["context_projection_max_bytes"])

    def test_set_carries_token_budget_fields(self):
        result = self._run(
            "set", "new", "--base-url", "https://new.invalid/v1",
            "--model", "new-model", "--api-key-env", "NEW_KEY",
            "--max-turn-tokens", "100", "--max-session-tokens", "500",
            "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        raw = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(raw["profiles"]["new"]["max_turn_tokens"], 100)
        self.assertEqual(raw["profiles"]["new"]["max_session_tokens"], 500)

    def test_set_creates_a_profile_without_changing_the_default(self):
        before = self._saved()
        result = self._run(
            "set", "new", "--base-url", "https://new.invalid/v1",
            "--model", "new-model", "--api-key-env", "NEW_KEY",
            "--timeout-seconds", "15", "--reasoning-effort-supported", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "set")
        self.assertEqual(payload["profile"], "new")
        self.assertEqual(payload["default_profile"], "local")
        raw = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(raw["default_profile"], "local")
        self.assertEqual(raw["profiles"]["new"], {
            "base_url": "https://new.invalid/v1",
            "model": "new-model",
            "api_key_env": "NEW_KEY",
            "timeout_seconds": 15,
            "reasoning_effort_supported": True,
        })
        self.assertIn("local", raw["profiles"])
        self.assertNotEqual(self._saved(), before)

    def test_set_replaces_the_named_profile_as_a_whole(self):
        result = self._run(
            "set", "hosted", "--base-url", "http://127.0.0.1:9000/v1",
            "--model", "replacement", "--api-key-env", "REPLACEMENT_KEY")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        raw = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(raw["profiles"]["hosted"], {
            "base_url": "http://127.0.0.1:9000/v1",
            "model": "replacement",
            "api_key_env": "REPLACEMENT_KEY",
            "reasoning_effort_supported": False,
        })

    def test_use_accepts_a_namespaced_router_profile(self):
        raw = json.loads(self.config.read_text(encoding="utf-8"))
        raw["profiles"]["wb-cn:model-a"] = {
            "base_url": "http://127.0.0.1:8137/v1",
            "model": "wb-cn:model-a",
            "api_key_env": "",
            "requires_api_key": False,
        }
        self.config.write_text(json.dumps(raw), encoding="utf-8")
        result = self._run("use", "wb-cn:model-a", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["default_profile"],
                         "wb-cn:model-a")
        self.assertEqual(
            json.loads(self.config.read_text())["default_profile"],
            "wb-cn:model-a")

    def test_use_switches_the_default_profile(self):
        result = self._run("use", "hosted", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "use")
        self.assertEqual(payload["default_profile"], "hosted")
        self.assertEqual(json.loads(self.config.read_text())["default_profile"],
                         "hosted")

    def test_remove_deletes_a_non_default_profile(self):
        result = self._run("remove", "hosted", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "remove")
        raw = json.loads(self.config.read_text())
        self.assertNotIn("hosted", raw["profiles"])
        self.assertEqual(raw["default_profile"], "local")

    def test_remove_refuses_the_default_profile_without_writing(self):
        before = self._saved()
        result = self._run("remove", "local", "--json")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "default profile")
        self.assertIn("use", payload["fix"])
        self.assertEqual(self._saved(), before)

    def test_use_and_remove_reject_unknown_names_without_writing(self):
        for command, name in (("use", "missing"), ("remove", "missing")):
            before = self._saved()
            result = self._run(command, name, "--json")
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"], "unknown profile")
            self.assertIn("local", payload["message"])
            self.assertEqual(self._saved(), before)

    def test_set_rejects_an_invalid_candidate_without_writing(self):
        before = self._saved()
        result = self._run(
            "set", "bad", "--base-url", "ftp://bad.invalid/v1",
            "--model", "bad-model", "--api-key-env", "BAD_KEY",
            "--api_key_env", "BAD_KEY", "--json")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("unrecognized arguments", result.stderr)
        self.assertEqual(self._saved(), before)

        result = self._run(
            "set", "bad", "--base-url", "ftp://bad.invalid/v1",
            "--model", "bad-model", "--api-key-env", "BAD_KEY", "--json")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("base_url", payload["message"])
        self.assertNotIn(str(self.base), result.stdout + result.stderr)
        self.assertEqual(self._saved(), before)

    def test_schema_1_is_read_only_and_writes_are_refused(self):
        self.config.write_text(json.dumps({
            "schema_version": "1",
            "name": "legacy",
            "provider": {
                "base_url": "http://127.0.0.1:8123/v1",
                "model": "stub",
                "api_key_env": "POCKETCHAT_API_KEY",
            },
        }), encoding="utf-8")
        before = self._saved()
        for args in (
            ("set", "new", "--base-url", "https://new.invalid/v1",
             "--model", "new", "--api-key-env", "NEW_KEY"),
            ("use", "legacy"),
            ("remove", "legacy"),
        ):
            result = self._run(*args, "--json")
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"], "schema 2 required")
            self.assertEqual(self._saved(), before)

    def test_bad_config_is_a_structured_failure_not_a_traceback(self):
        self.config.write_bytes(b"\xff")
        before = self._saved()
        for args in (
            ("set", "new", "--base-url", "https://new.invalid/v1",
             "--model", "new", "--api-key-env", "NEW_KEY"),
            ("use", "local"),
            ("remove", "hosted"),
        ):
            result = self._run(*args, "--json")
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(self._saved(), before)

    def test_success_preserves_mode_and_leaves_no_temp_file(self):
        os.chmod(self.config, 0o640)
        result = self._run(
            "set", "new", "--base-url", "https://new.invalid/v1",
            "--model", "new", "--api-key-env", "NEW_KEY")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o640)
        self.assertEqual(sorted(path.name for path in self.base.iterdir()),
                         ["pocketchat.json"])

    def test_no_credential_value_is_accepted_or_written(self):
        secret = "sk-not-a-real-key-0123456789"
        result = self._run(
            "set", "new", "--base-url", "https://new.invalid/v1",
            "--model", "new", "--api-key-env", "NEW_KEY", "--json",
            env={"NEW_KEY": secret})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn(secret, self.config.read_text(encoding="utf-8"))

    def test_write_commands_run_end_to_end_as_subprocesses(self):
        environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}
        for args in (
            ["profile", "set", "new", "--base-url", "https://new.invalid/v1",
             "--model", "new", "--api-key-env", "NEW_KEY", "--json"],
            ["profile", "use", "hosted", "--json"],
            ["profile", "remove", "new", "--json"],
        ):
            result = subprocess.run(
                [sys.executable, "-m", "pocketchat"] + args,
                cwd=str(self.base), env=hp.subprocess_environment(environ), capture_output=True, text=True,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
