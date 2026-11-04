"""Malformed session input must stay a typed, non-crashing refusal."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import _helpers as hp


class MalformedSessionReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        sessions = self.base / "sessions"
        sessions.mkdir()
        self.session = sessions / "broken.jsonl"
        self.original = b'{"session":"broken","trace":"../traces/missing.jsonl"}\n\xff'
        self.session.write_bytes(self.original)

    def test_show_json_refuses_malformed_utf8_without_touching_input(self):
        result = hp.run_cli(["show", "broken", "--json"], self.base)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("1", payload["schema_version"])
        self.assertFalse(payload["ok"])
        self.assertEqual("session_file", payload["error"])
        self.assertIn("cannot read", payload["message"].lower())
        self.assertEqual(self.original, self.session.read_bytes())
