"""Profile explanation is a pure, bounded view over supplied observations."""

import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

from pocketchat.config import load_config, profile_config
from pocketchat.profile_explain import (
    MAX_FIELDS,
    MAX_REPORT_BYTES,
    PROFILE_EXPLAIN_SCHEMA,
    ProfileExplainError,
    _field_count,
    explain_profile,
)

SECRET = "sk-profile-explain-do-not-echo-0123456789"


def _raw_v2(**overrides):
    value = {
        "schema_version": "2",
        "name": "workbench",
        "default_profile": "local",
        "system_prompt": "private prompt includes " + SECRET,
        "profiles": {
            "local": {
                "base_url": "http://127.0.0.1:8123/v1?token=" + SECRET,
                "model": "stub",
                "api_key_env": "POCKETCHAT_API_KEY",
                "timeout_seconds": 30,
            },
            "hosted": {
                "base_url": "https://api.example.invalid/v1",
                "model": "hosted-model",
                "api_key_env": "POCKETCHAT_HOSTED_KEY",
                "timeout_seconds": 90,
            },
        },
    }
    value.update(overrides)
    return value


def _raw_v1(**overrides):
    value = {
        "schema_version": "1",
        "name": "legacy",
        "provider": {
            "base_url": "http://127.0.0.1:8123/v1",
            "model": "legacy-model",
            "api_key_env": "LEGACY_KEY",
        },
        "system_prompt": "private legacy prompt " + SECRET,
    }
    value.update(overrides)
    return value


def _resolved(selection="local"):
    if selection == "legacy":
        return {
            "profile": "legacy",
            "model": "legacy-model",
            "base_url": "http://127.0.0.1:8123/v1",
            "api_key_env": "LEGACY_KEY",
            "requires_api_key": True,
            "system_prompt": "private legacy prompt " + SECRET,
        }
    return {
        "profile": selection,
        "model": "stub" if selection == "local" else "hosted-model",
        "base_url": ("http://127.0.0.1:8123/v1?token=" + SECRET
                     if selection == "local" else "https://api.example.invalid/v1"),
        "api_key_env": ("POCKETCHAT_API_KEY" if selection == "local"
                        else "POCKETCHAT_HOSTED_KEY"),
        "requires_api_key": True,
        "system_prompt": "private prompt includes " + SECRET,
    }


def _credential(presence, source="environment"):
    return {"presence": presence, "source": source}


class _BombEnviron(dict):
    def __getitem__(self, key):
        raise AssertionError("environment read: %s" % key)

    def get(self, key, default=None):
        raise AssertionError("environment read: %s" % key)


class _DuplicateDefaultMapping(dict):
    def items(self):
        return [("default_profile", "local"), ("default_profile", "hosted")]


class ProfileExplainContractTests(unittest.TestCase):
    def test_explains_the_default_profile_without_echoing_secrets(self):
        report = explain_profile(_raw_v2(), _resolved("local"), None,
                                 _credential("missing"))
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["schema"], PROFILE_EXPLAIN_SCHEMA)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["profile"], "local")
        self.assertEqual(report["selection"], "default")
        self.assertEqual(report["model"], "stub")
        self.assertEqual(report["endpoint"], "http://127.0.0.1:8123")
        self.assertEqual(report["credential"], {
            "required": True,
            "api_key_env": "POCKETCHAT_API_KEY",
            "presence": "missing",
            "source": "environment",
        })
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(SECRET, serialized)
        self.assertNotIn("private prompt", serialized)
        self.assertNotIn("POCKETCHAT_API_KEY=", serialized)
        provenance = {row["field"]: row["source"] for row in report["provenance"]}
        self.assertEqual(provenance["model"], "explicit")
        self.assertEqual(provenance["timeout_seconds"], "explicit")
        self.assertEqual(provenance["retry_max_attempts"], "default")
        self.assertEqual(report["report_bytes"], len(
            json.dumps({k: v for k, v in report.items()
                        if k not in ("report_bytes", "report_sha256")},
                       ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8"))
        )

    def test_explicit_profile_uses_cli_selection_and_present_credential(self):
        report = explain_profile(_raw_v2(), _resolved("hosted"), "hosted",
                                 _credential("present"))
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["profile"], "hosted")
        self.assertEqual(report["selection"], "cli")
        self.assertEqual(report["model"], "hosted-model")
        self.assertEqual(report["endpoint"], "https://api.example.invalid")
        self.assertEqual(report["credential"]["presence"], "present")

    def test_schema_1_reuses_the_migrated_profile_view(self):
        report = explain_profile(_raw_v1(), _resolved("legacy"), None,
                                 _credential("present"))
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["profile"], "legacy")
        self.assertEqual(report["selection"], "default")
        self.assertEqual(report["model"], "legacy-model")
        self.assertNotIn(SECRET, json.dumps(report, ensure_ascii=False))

    def test_existing_load_and_profile_helpers_are_compatible(self):
        payload = _raw_v2()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pocketchat.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_config(path)
            selected = profile_config(loaded, "hosted")
        report = explain_profile(payload, selected, "hosted",
                                 _credential("present"))
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["profile"], "hosted")
        self.assertEqual(report["model"], "hosted-model")

    def test_default_and_explicit_selection_mismatches_are_refused(self):
        report = explain_profile(_raw_v2(), _resolved("hosted"), None,
                                 _credential("present"))
        self.assertEqual(report["status"], "refused")
        self.assertEqual(report["findings"][0]["reason"],
                         "profile_selection_mismatch")
        self.assertNotIn("model", report)
        self.assertNotIn("endpoint", report)
        self.assertNotIn(SECRET, json.dumps(report, ensure_ascii=False))

    def test_raw_and_resolved_profile_mismatch_is_refused(self):
        resolved = _resolved("local")
        resolved["model"] = "different-model"
        report = explain_profile(_raw_v2(), resolved, None,
                                 _credential("missing"))
        self.assertEqual(report["status"], "refused")
        self.assertIn("profile_record_mismatch",
                      {finding["reason"] for finding in report["findings"]})
        self.assertNotIn("different-model", json.dumps(report, ensure_ascii=False))

    def test_credential_requirement_must_match_the_observation(self):
        raw = _raw_v2()
        raw["profiles"]["local"]["requires_api_key"] = False
        raw["profiles"]["local"].pop("api_key_env")
        resolved = _resolved("local")
        resolved["requires_api_key"] = False
        resolved["api_key_env"] = ""
        complete = explain_profile(raw, resolved, None,
                                   _credential("unknown", "not_required"))
        self.assertEqual(complete["status"], "complete")
        self.assertEqual(complete["credential"]["source"], "not_required")
        self.assertEqual(complete["credential"]["presence"], "unknown")

        refused = explain_profile(raw, resolved, None,
                                  _credential("present", "environment"))
        self.assertEqual(refused["status"], "refused")
        self.assertIn("credential_observation_mismatch",
                      {finding["reason"] for finding in refused["findings"]})

    def test_unknown_profile_is_a_stable_refusal(self):
        report = explain_profile(_raw_v2(), _resolved("local"), "missing",
                                 _credential("unknown"))
        self.assertEqual(report["status"], "refused")
        self.assertIn("unknown_profile",
                      {finding["reason"] for finding in report["findings"]})
        self.assertNotIn("missing", json.dumps(report, ensure_ascii=False))

    def test_closed_credential_observation_rejects_unknown_secret_field(self):
        with self.assertRaises(ProfileExplainError) as caught:
            explain_profile(_raw_v2(), _resolved("local"), None,
                            {"presence": "present", "source": "environment",
                             "value": SECRET})
        self.assertEqual(caught.exception.error_type,
                         "invalid_credential_observation")
        self.assertNotIn(SECRET, str(caught.exception))

    def test_duplicate_mapping_keys_are_refused_without_echoing_values(self):
        raw = _raw_v2()
        raw["profiles"]["local"] = _DuplicateDefaultMapping(raw["profiles"]["local"])
        with self.assertRaises(ProfileExplainError) as caught:
            explain_profile(raw, _resolved("local"), None,
                            _credential("missing"))
        self.assertEqual(caught.exception.error_type, "invalid_config")
        self.assertEqual(caught.exception.category, "structure")
        self.assertNotIn(SECRET, str(caught.exception))

    def test_malformed_inputs_and_malicious_names_are_refused(self):
        cases = (
            ("missing raw", None, _resolved("local"), None),
            ("missing resolved", _raw_v2(), None, None),
            ("bad raw schema", _raw_v2(schema_version="3"), _resolved("local"), None),
            ("bad profile name", _raw_v2(profiles={"bad name": {
                "base_url": "http://127.0.0.1/v1", "model": "stub",
                "api_key_env": "KEY", "timeout_seconds": 30}}), _resolved("local"), None),
        )
        for label, raw, resolved, explicit in cases:
            with self.subTest(case=label):
                with self.assertRaises(ProfileExplainError):
                    explain_profile(raw, resolved, explicit, _credential("unknown"))

    def test_unsafe_endpoint_credentials_are_refused_without_echo(self):
        endpoint = "https://user:%s@example.invalid/v1" % SECRET
        raw = _raw_v2()
        raw["profiles"]["local"]["base_url"] = endpoint
        resolved = _resolved("local")
        resolved["base_url"] = endpoint
        with self.assertRaises(ProfileExplainError) as caught:
            explain_profile(raw, resolved, None, _credential("missing"))
        self.assertEqual(caught.exception.error_type, "unsafe_endpoint")
        self.assertEqual(caught.exception.category, "privacy")
        self.assertNotIn(SECRET, str(caught.exception))

    def test_inputs_are_not_mutated_and_output_is_deterministic(self):
        raw = _raw_v2()
        resolved = _resolved("hosted")
        credential = _credential("present")
        before = copy.deepcopy((raw, resolved, credential))
        first = explain_profile(raw, resolved, "hosted", credential)
        second = explain_profile(raw, resolved, "hosted", credential)
        self.assertEqual(before, (raw, resolved, credential))
        self.assertEqual(first, second)
        self.assertEqual(first["report_sha256"], second["report_sha256"])

    def test_report_stays_within_field_and_byte_budget(self):
        report = explain_profile(_raw_v2(), _resolved("hosted"), "hosted",
                                 _credential("present"))
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(encoded), MAX_REPORT_BYTES)
        self.assertLessEqual(_field_count(report), MAX_FIELDS)
        self.assertGreaterEqual(report["report_bytes"], 1)
        self.assertEqual(len(report["report_sha256"]), 64)

    def test_core_does_not_read_environment_files_commands_or_sockets(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(os, "environ", _BombEnviron()), \
                mock.patch("builtins.open", side_effect=AssertionError("file read")), \
                mock.patch.object(socket, "socket", side_effect=AssertionError("socket")), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("command")):
            report = explain_profile(_raw_v2(), _resolved("local"), None,
                                     _credential("missing"))
        self.assertEqual(report["status"], "complete")


if __name__ == "__main__":
    unittest.main()
