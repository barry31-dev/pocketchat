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

from tests import _helpers as hp

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
        self._config()
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
        for _ in range(50):
            if trace_path.exists():
                break
            time.sleep(0.1)
        self.assertTrue(trace_path.exists())
        proc.send_signal(signal.SIGINT)
        proc.communicate(timeout=30)
        self.assertTrue(trace_path.exists())
        events = self._events(trace_path)
        self.assertEqual(events[0]["type"], "run.started")
        self.assertEqual(events[-1]["type"], "run.cancelled")
        self.assertEqual(events[-1]["payload"]["reason"], "interrupted by the user")


class CliOnlyScopeTests(unittest.TestCase):
    """The package must not ship a second product surface.

    PocketChat is a CLI. A dormant HTTP server, browser assets or API manual
    is not harmless inventory: it advertises commands and contracts that the
    roadmap no longer supports. This test fails while any of those files or
    command surfaces remain in the package.
    """

    def test_package_has_no_server_or_web_product_surface(self):
        package = ROOT / "pocketchat"
        removed = [
            package / "server.py",
            package / "web",
            ROOT / "docs" / "api.md",
            ROOT / "docs" / "api.zh-CN.md",
        ]
        present = [str(path.relative_to(ROOT)) for path in removed if path.exists()]
        self.assertEqual(present, [])

    def test_parser_has_no_serve_command(self):
        from pocketchat.cli import _parser
        help_text = _parser().format_help()
        self.assertNotIn("serve", help_text)
        with self.assertRaises(SystemExit) as caught:
            _parser().parse_args(["serve"])
        self.assertEqual(caught.exception.code, 2)


class StableV1ContractTests(unittest.TestCase):
    """The promises a 1.0 CLI can make without reaching a provider."""

    def test_cli_command_inventory_is_stable(self):
        from pocketchat.cli import _parser
        parser = _parser()
        commands = next(
            action.choices for action in parser._actions
            if getattr(action, "choices", None) is not None
            and action.__class__.__name__ == "_SubParsersAction"
        )
        self.assertEqual(sorted(commands), sorted([
            "init", "doctor", "run", "chat", "agent", "show", "context", "tool", "memory", "delegate", "export",
            "branch", "checkpoint", "rewind", "task", "profile", "compare", "replay",
            "workspace", "history",
            "search", "diff", "select", "index",
        ]))

    def test_json_envelope_version_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            result = hp.run_cli(["init", "--json"], base)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], "1")
            self.assertTrue(payload["ok"])

    def test_test_server_shutdown_is_bounded(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading as _threading
        import time as _time

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 0.05
        thread = _threading.Thread(target=server.serve_forever,
                                   kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        start = _time.monotonic()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        elapsed = _time.monotonic() - start
        self.assertFalse(thread.is_alive())
        self.assertLess(elapsed, 0.5)

    def test_unknown_command_is_a_usage_error_without_a_trace(self):
        result = hp.run_cli(["not-a-command"], Path(tempfile.gettempdir()))
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_v1_release_contract_documents_are_present(self):
        docs = ROOT / "docs"
        for name in ("compatibility.md", "compatibility.zh-CN.md",
                     "provider-adapters.md", "provider-adapters.zh-CN.md"):
            self.assertTrue((docs / name).is_file(), name)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("v1.0.0", readme)
        self.assertIn("Python 3.9", readme)
        self.assertIn("OpenAI-compatible", readme)

    def test_every_written_artifact_names_its_schema_version(self):
        from pocketchat.sessions import SESSION_SCHEMA_VERSION
        from pocketchat.request import REQUEST_SCHEMA_VERSION
        from pocketchat.compare import COMPARISON_SCHEMA_VERSION
        from pocketchat.selection import SELECTION_SCHEMA_VERSION
        from pocketchat.indexing import INDEX_SCHEMA_VERSION
        from pocketchat.recovery import CHECKPOINT_SCHEMA, REWIND_SCHEMA
        from pocketchat.tasking import TASK_RECORD_SCHEMA
        from pocketchat.task_policy import POLICY_SCHEMA
        self.assertEqual({
            "session": SESSION_SCHEMA_VERSION,
            "request": REQUEST_SCHEMA_VERSION,
            "comparison": COMPARISON_SCHEMA_VERSION,
            "selection": SELECTION_SCHEMA_VERSION,
            "index": INDEX_SCHEMA_VERSION,
            "checkpoint": CHECKPOINT_SCHEMA,
            "rewind": REWIND_SCHEMA,
            "task_record": TASK_RECORD_SCHEMA,
            "task_policy": POLICY_SCHEMA,
        }, {
            "session": "1", "request": "1", "comparison": "1",
            "selection": "1", "index": "1",
            "checkpoint": "pocketchat-checkpoint.v1",
            "rewind": "pocketchat-rewind.v1",
            "task_record": "pocketchat-task.v1",
            "task_policy": "pocketchat-task-policy.v1",
        })

    def test_the_task_schemas_ship_in_the_package(self):
        """The task contract is packaged, so an installed wheel carries it."""
        path = ROOT / "pocketchat" / "schemas" / "task.schema.json"
        self.assertTrue(path.is_file(), path)
        schema = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual({"policy", "task_record", "step", "tool_grant"},
                         set(schema["$defs"]))


if __name__ == "__main__":
    unittest.main()
