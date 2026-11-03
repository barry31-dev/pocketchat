"""The runtime capability report is pure, explicit and JSON serializable."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from pocketchat.runtime_capabilities import collect_runtime_capabilities


class RuntimeCapabilityTests(unittest.TestCase):
    def test_report_is_json_serializable_schema_one(self):
        report = collect_runtime_capabilities()
        encoded = json.dumps(report, sort_keys=True)
        self.assertEqual("1", report["schema_version"])
        self.assertEqual("CPython", report["interpreter"]["implementation"])
        self.assertIsInstance(report["interpreter"]["version"], str)
        self.assertEqual(os.name, report["os"]["name"])
        self.assertEqual(
            {"dir_fd_open", "nofollow_open_flag", "directory_open_flag"},
            set(report["primitives"]),
        )
        self.assertIsInstance(report["not_checked"], list)
        self.assertEqual(
            ["provider_reachability", "workspace_permissions",
             "concurrency_atomicity"],
            report["not_checked"],
        )
        json.loads(encoded)

    def test_primitives_follow_the_supplied_capability_source(self):
        with mock.patch(
                "pocketchat.runtime_capabilities._capability_source",
                return_value={
                    "dir_fd_open": False,
                    "nofollow_open_flag": True,
                    "directory_open_flag": False,
                }):
            report = collect_runtime_capabilities()
        self.assertFalse(report["primitives"]["dir_fd_open"])
        self.assertTrue(report["primitives"]["nofollow_open_flag"])
        self.assertFalse(report["primitives"]["directory_open_flag"])


    def test_collection_performs_no_io_or_environment_enumeration(self):
        with mock.patch("builtins.open", side_effect=AssertionError("open called")), \
                mock.patch("socket.socket", side_effect=AssertionError("socket called")), \
                mock.patch("os.mkdir", side_effect=AssertionError("mkdir called")), \
                mock.patch("os.listdir", side_effect=AssertionError("listdir called")), \
                mock.patch("os.environ", new={"LEAK": "secret"}):
            report = collect_runtime_capabilities()
        self.assertNotIn("secret", json.dumps(report))

    def test_report_never_claims_provider_workspace_or_concurrency(self):
        report = collect_runtime_capabilities()
        text = json.dumps(report, sort_keys=True).lower()
        for forbidden in (
                "memory_is_safe", "provider_reachable", "workspace_writable",
                "function_verified"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(
            ["provider_reachability", "workspace_permissions",
             "concurrency_atomicity"],
            report["not_checked"],
        )
