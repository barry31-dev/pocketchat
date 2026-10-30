"""Bounded, serial delegation tests: child runs, not recursive agents."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pocketchat.delegate import DelegateError, run_delegation
from tests._helpers import run_cli


class FakeSession:
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.run_id = "child_%d" % (len(FakeSession.calls) + 1)
        self.trace_path = Path(kwargs["trace_path"])
        self.request_snapshots = []
        self.total_input_tokens = 1
        self.total_output_tokens = 1
        self.last_output_summary = "child reply"
        self.recorder = mock.Mock()
        self.recorder.read_events.return_value = [
            {"type": "run.finished", "payload": {"status": "completed"}}
        ]

    def run(self, prompts):
        FakeSession.calls.append((self.kwargs, list(prompts)))
        return "completed"

    def last_reply(self):
        return "child reply"


class DelegateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        FakeSession.calls.clear()

    def base(self):
        return {
            "profile": "child", "model": "m",
            "base_url": "http://example.invalid/v1", "api_key_env": "K",
            "timeout_seconds": 5, "max_context_chars": 1000,
            "summary_prefix_chars": 40, "requires_api_key": True,
        }

    def test_serial_children_get_independent_runs_and_a_manifest(self):
        with mock.patch("pocketchat.delegate.Session", FakeSession):
            result = run_delegation(self.root, ["one", "two"], self.base(),
                                    max_children=2, timeout_seconds=10)
        self.assertTrue(result["ok"])
        self.assertEqual(2, len(result["children"]))
        self.assertEqual(2, len(FakeSession.calls))
        self.assertTrue((self.root / result["manifest"]).is_file())
        self.assertEqual("completed", result["children"][0]["status"])

    def test_child_limit_is_enforced_before_execution(self):
        with self.assertRaises(DelegateError):
            run_delegation(self.root, ["one", "two"], self.base(),
                           max_children=1, timeout_seconds=10)
        self.assertEqual([], FakeSession.calls)

    def test_empty_or_nonpositive_limits_are_refused(self):
        for children, timeout in (([], 10), (["one"], 0)):
            with self.subTest(children=children, timeout=timeout):
                with self.assertRaises(DelegateError):
                    run_delegation(self.root, children, self.base(),
                                   max_children=2, timeout_seconds=timeout)

    def test_a_child_failure_is_recorded_without_aborting_the_batch(self):
        class Failing(FakeSession):
            def run(self, prompts):
                FakeSession.calls.append((self.kwargs, list(prompts)))
                return "failed"

        with mock.patch("pocketchat.delegate.Session", Failing):
            result = run_delegation(self.root, ["one", "two"], self.base(),
                                    max_children=2, timeout_seconds=10)
        self.assertFalse(result["ok"])
        self.assertEqual(["failed", "failed"],
                         [child["status"] for child in result["children"]])

    def test_cli_delegate_requires_explicit_prompts_and_emits_json(self):
        result = run_cli(("delegate", "run", "--json"), self.root)
        self.assertEqual(2, result.returncode)

    def test_keyboard_interrupt_aborts_the_batch(self):
        class Interrupting(FakeSession):
            def run(self, prompts):
                raise KeyboardInterrupt

        with mock.patch("pocketchat.delegate.Session", Interrupting):
            with self.assertRaises(KeyboardInterrupt):
                run_delegation(self.root, ["one"], self.base(),
                               max_children=2, timeout_seconds=10)


if __name__ == "__main__":
    unittest.main()
