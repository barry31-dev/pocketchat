"""Public acceptance schema and demo must match the executable contract."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pocketchat import acceptance as ac

ROOT = Path(__file__).resolve().parents[1]


class AcceptanceContractTests(unittest.TestCase):
    def test_schema_limits_and_closed_check_variants_match_parser(self):
        path = ROOT / "pocketchat" / "schemas" / "acceptance.schema.json"
        self.assertTrue(path.is_file(), "acceptance schema is not implemented")
        schema = json.loads(path.read_text(encoding="utf-8"))
        definitions = schema["$defs"]
        plan = definitions["plan"]
        self.assertEqual(plan["properties"]["schema_version"]["const"], ac.PLAN_SCHEMA)
        self.assertFalse(plan["additionalProperties"])
        self.assertEqual(plan["properties"]["checks"]["maxItems"], ac.MAX_CHECKS)
        self.assertEqual(plan["properties"]["max_file_bytes"]["maximum"], ac.MAX_FILE_BYTES)
        self.assertEqual(plan["properties"]["max_file_bytes"]["default"], ac.DEFAULT_FILE_BYTES)
        expected = {"file_exists": {"id", "kind", "path"},
                    "text_contains": {"id", "kind", "path", "text"},
                    "json_equals": {"id", "kind", "path", "pointer", "expected"}}
        for name, fields in expected.items():
            check = definitions[name]
            self.assertFalse(check["additionalProperties"])
            self.assertEqual(set(check["required"]), fields)
            self.assertEqual(set(check["properties"]), fields)
            self.assertEqual(check["properties"]["kind"]["const"], name)
        command = definitions["command_succeeded"]
        self.assertFalse(command["additionalProperties"])
        self.assertEqual(set(command["required"]),
                         {"id", "kind", "step_id"})
        self.assertEqual(set(command["properties"]),
                         {"id", "kind", "step_id"})
        receipt = definitions["receipt"]
        self.assertFalse(receipt["additionalProperties"])
        self.assertEqual(set(receipt["required"]), set(receipt["properties"]))
        self.assertEqual(receipt["properties"]["schema_version"]["const"], ac.RECORD_SCHEMA)

    def test_schema_path_and_pointer_patterns_refuse_ambiguous_inputs(self):
        import re
        schema = json.loads((ROOT / "pocketchat" / "schemas" / "acceptance.schema.json").read_text(encoding="utf-8"))
        pattern = schema["$defs"]["file_exists"]["properties"]["path"]["pattern"]
        for path in ("x", ".report", "nested/report.md"):
            self.assertIsNotNone(re.search(pattern, path), path)
        for path in (" x", "/x", "../x", "x\\y", "C:/x", "a./x", "x "):
            self.assertIsNone(re.search(pattern, path), path)
        pointer = schema["$defs"]["json_equals"]["properties"]["pointer"]["pattern"]
        for value in ("", "/a~1b/~0x/0"):
            self.assertIsNotNone(re.search(pointer, value))
        for value in ("a", "/~2", "/~"):
            self.assertIsNone(re.search(pointer, value))

    def test_example_plan_is_readable_and_covers_three_conditions(self):
        path = ROOT / "examples" / "task-acceptance" / "acceptance.json"
        self.assertTrue(path.is_file(), "acceptance example is not implemented")
        plan = ac.load_plan(path)
        self.assertEqual({check["kind"] for check in plan.checks},
                         {"file_exists", "text_contains", "json_equals"})

    def test_demo_is_offline_and_preserves_four_evidence_paths(self):
        path = ROOT / "examples" / "task-acceptance" / "demo.py"
        self.assertTrue(path.is_file(), "acceptance demo is not implemented")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence"
            proc = subprocess.run([sys.executable, str(path), "--output", str(output)],
                                  cwd=ROOT, capture_output=True, text=True, timeout=15)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(proc.stderr, "")
            rows = [json.loads(line) for line in proc.stdout.splitlines()]
            self.assertEqual([row["case"] for row in rows],
                             ["success", "missing_artifact", "json_mismatch", "permission_refusal"])
            self.assertEqual([row["execution"] for row in rows], ["completed"] * 4)
            self.assertEqual([row["acceptance"]["state"] for row in rows],
                             ["passed", "failed", "failed", "refused"])
            for row in rows:
                self.assertTrue((output / row["case"] / row["acceptance"]["record"]).is_file())
                self.assertTrue((output / row["case"] / "traces" / "task.jsonl").is_file())

    def test_bilingual_docs_name_contracts_and_do_not_overclaim(self):
        for suffix in (".md", ".zh-CN.md"):
            path = ROOT / "docs" / ("task-acceptance" + suffix)
            self.assertTrue(path.is_file(), "acceptance docs are not implemented")
            text = path.read_text(encoding="utf-8")
            for phrase in ("task verify", "--acceptance", ac.PLAN_SCHEMA, ac.RECORD_SCHEMA,
                           "completed", "passed", "not_recorded", "not_run", "unknown",
                           "65536", "1048576", ".pocketchat/acceptance/", "0", "1", "2"):
                self.assertIn(phrase, text)
            self.assertNotIn("/" + "Users" + "/", text)
        en = (ROOT / "docs" / "task-acceptance.md").read_text(encoding="utf-8").lower()
        zh = (ROOT / "docs" / "task-acceptance.zh-CN.md").read_text(encoding="utf-8")
        self.assertIn("not a signature", en)
        self.assertIn("does not recheck", en)
        self.assertIn("unsupported_platform", en)
        self.assertIn("不是签名", zh)
        self.assertIn("不重新检查", zh)
        self.assertIn("unsupported_platform", zh)
