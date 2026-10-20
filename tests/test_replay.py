"""Replay dry-run tests: verify a saved request without sending it again.

A request snapshot is evidence only if a reader can independently rebuild the
request it describes and compare the digest. Replay does that and stops there:
no provider, no credential lookup, no socket.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pocketchat.request import canonical_bytes, write_request_snapshot

ROOT = Path(__file__).resolve().parents[1]


def _payload(text="hello"):
    return {
        "model": "example-model",
        "messages": [{"role": "user", "content": text}],
        "stream": False,
    }


class ReplayKernelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def _write(self, name="run_x.turn-1.json", payload=None):
        path = self.base / "traces" / "requests" / name
        digest = write_request_snapshot(
            path,
            run_id="run_x",
            profile="local",
            created="2026-10-18T00:00:00.000Z",
            payload=payload if payload is not None else _payload(),
        )
        return path, digest

    def test_replay_dry_run_rebuilds_request_without_network(self):
        from pocketchat.replay import replay_dry_run

        path, digest = self._write()
        with mock.patch("socket.socket", side_effect=AssertionError("opened a socket")):
            result = replay_dry_run(path)

        self.assertEqual(result["run_id"], "run_x")
        self.assertEqual(result["request_sha256"], digest)
        self.assertEqual(result["recomputed_sha256"], digest)
        self.assertTrue(result["match"])
        self.assertFalse(result["sent"])

    def test_a_tampered_request_reports_both_digests(self):
        from pocketchat.replay import replay_dry_run

        path, digest = self._write()
        record = json.loads(path.read_text(encoding="utf-8"))
        record["request"]["model"] = "something-else"
        path.write_bytes(canonical_bytes(record))

        result = replay_dry_run(path)

        self.assertFalse(result["match"])
        self.assertEqual(result["request_sha256"], digest)
        self.assertNotEqual(result["recomputed_sha256"], digest)
        self.assertFalse(result["sent"])

    def test_a_snapshot_without_a_digest_is_rejected(self):
        from pocketchat.replay import ReplayError, replay_dry_run

        path, _ = self._write()
        record = json.loads(path.read_text(encoding="utf-8"))
        record.pop("request_sha256")
        path.write_bytes(canonical_bytes(record))

        with self.assertRaises(ReplayError):
            replay_dry_run(path)


class ReplayCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def _write(self, name, text):
        path = self.base / "traces" / "requests" / name
        return path, write_request_snapshot(
            path,
            run_id="run_x",
            profile="local",
            created="2026-10-18T00:00:00.000Z",
            payload=_payload(text),
        )

    def _run(self, *args):
        env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}
        return subprocess.run(
            [sys.executable, "-m", "pocketchat"] + list(args),
            cwd=str(self.base),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_cli_replays_every_turn_of_a_run_without_a_key_or_config(self):
        self._write("run_x.turn-2.json", "second")
        self._write("run_x.turn-1.json", "first")

        result = self._run("replay", "run_x", "--dry-run", "--json")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["run_id"], "run_x")
        self.assertFalse(payload["sent"])
        self.assertEqual(
            [Path(item["request_path"]).name for item in payload["requests"]],
            ["run_x.turn-1.json", "run_x.turn-2.json"],
        )
        self.assertTrue(all(item["match"] for item in payload["requests"]))

    def test_cli_hash_mismatch_exits_two_and_prints_expected_and_actual(self):
        path, stored = self._write("run_x.turn-1.json", "hello")
        record = json.loads(path.read_text(encoding="utf-8"))
        record["request"]["messages"][0]["content"] = "changed"
        path.write_bytes(canonical_bytes(record))

        result = self._run("replay", "run_x", "--dry-run")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("expected %s" % stored, result.stderr)
        self.assertIn("actual ", result.stderr)

    def test_cli_missing_snapshot_exits_two(self):
        result = self._run("replay", "run_missing", "--dry-run")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("run_missing", result.stderr)


if __name__ == "__main__":
    unittest.main()
