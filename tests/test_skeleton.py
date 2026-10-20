"""Skeleton-slice tests: the single-exit trace contract.

The one property worth a test here is that no entry point can start a run
without ending it. Everything else in this file guards that: a run interrupted
mid-turn, a run whose turn raises, and a run whose config never loaded must all
still leave a terminal event, because a trace with ``run.started`` and nothing
after it reads as "we do not know how this ended".
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(module_args, cwd, env=None):
    environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}
    if env:
        environ.update(env)
    return subprocess.run(
        [sys.executable, "-m", "pocketchat"] + module_args,
        cwd=str(cwd), env=environ, capture_output=True, text=True, timeout=120,
    )


class SkeletonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def _config(self, **overrides):
        config = {
            "schema_version": "1",
            "provider": {
                "base_url": "http://127.0.0.1:8123/v1",
                "model": "example-model",
                "api_key_env": "POCKETCHAT_API_KEY",
            },
        }
        config.update(overrides)
        path = self.base / "pocketchat.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def _events(self, trace_path):
        return [json.loads(line) for line in
                Path(trace_path).read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_help_runs(self):
        result = _run(["--help"], self.base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pocketchat", result.stdout)

    def test_run_fails_closed_when_no_credential(self):
        # A missing credential is a usage error before the trace exists: the
        # config is fine, the environment is not, and no run is recorded.
        config = self._config()
        result = _run(["run", "hello"], self.base, {"POCKETCHAT_API_KEY": ""})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("POCKETCHAT_API_KEY", result.stderr)
        self.assertFalse((self.base / "traces").exists())

    def test_run_ends_the_trace_even_when_the_turn_raises(self):
        config = self._config()
        env = {"POCKETCHAT_API_KEY": "test-key"}
        result = _run(["run", "hello", "--config", str(config)], self.base, env)
        # The model half is not implemented in this slice, so the run fails:
        # what is under test is that it fails *with a terminal event*.
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        traces = list((self.base / "traces").glob("*.jsonl"))
        self.assertEqual(len(traces), 1)
        events = self._events(traces[0])
        self.assertEqual(events[0]["type"], "run.started")
        self.assertEqual(events[-1]["type"], "run.failed")
        self.assertEqual(events[-1]["payload"]["status"], "failed")
        self.assertEqual(events[-1]["payload"]["related_event_id"], events[0]["event_id"])

    def test_event_ids_are_monotonic_and_unique(self):
        config = self._config()
        _run(["run", "hello", "--config", str(config)], self.base,
             {"POCKETCHAT_API_KEY": "test-key"})
        events = self._events(next((self.base / "traces").glob("*.jsonl")))
        ids = [event["event_id"] for event in events]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(i.startswith("evt_") for i in ids))

    def test_credential_value_is_never_written_to_the_trace(self):
        config = self._config()
        secret = "sk-do-not-record-this-value"
        _run(["run", "hello", "--config", str(config)], self.base,
             {"POCKETCHAT_API_KEY": secret})
        trace_text = next((self.base / "traces").glob("*.jsonl")).read_text(encoding="utf-8")
        self.assertNotIn(secret, trace_text)
        # The variable's name is recorded; its value is not.
        self.assertIn("POCKETCHAT_API_KEY", trace_text)

    def test_unknown_config_field_is_rejected(self):
        config = self._config()
        record = json.loads(config.read_text(encoding="utf-8"))
        record["provider"]["modle"] = "typo"
        config.write_text(json.dumps(record), encoding="utf-8")
        result = _run(["run", "hello", "--config", str(config)], self.base,
                      {"POCKETCHAT_API_KEY": "test-key"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("modle", result.stderr)

    def test_interrupted_turn_ends_the_run_as_cancelled(self):
        # Ctrl-C during an in-flight turn: the run ends as cancelled, not
        # silently. The turn is stubbed to sleep, because this slice has no
        # model call yet -- the point is the terminal event, not the request.
        config = self._config()
        trace_path = self.base / "interrupted.jsonl"
        script = (
            "import time, sys;"
            "sys.path.insert(0, %r);"
            "from pocketchat.session import Session;"
            "from pocketchat.config import load_config;"
            "from pathlib import Path;"
            "session = Session(config=load_config(Path(%r)),"
            " trace_path=Path(%r));"
            "session._turn = lambda prompt: time.sleep(5);"
            "print(session.run(['hello']))" % (str(ROOT), str(config), str(trace_path))
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        env["POCKETCHAT_API_KEY"] = "test-key"
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            cwd=str(self.base), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        time.sleep(0.5)
        proc.send_signal(signal.SIGINT)
        proc.communicate(timeout=30)
        self.assertTrue(trace_path.exists())
        events = self._events(trace_path)
        self.assertEqual(events[0]["type"], "run.started")
        self.assertEqual(events[-1]["type"], "run.cancelled")
        self.assertEqual(events[-1]["payload"]["reason"], "interrupted by the user")


if __name__ == "__main__":
    unittest.main()
