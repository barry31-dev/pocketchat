"""Explicit local memory store and CLI tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.memory import MemoryError, add_memory, list_memory, remove_memory, search_memory
from tests._helpers import run_cli


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_add_requires_explicit_content_and_returns_an_id(self):
        record = add_memory(self.root, "Use short answers.", tags=["style"])
        self.assertTrue(record["memory_id"].startswith("mem_"))
        self.assertEqual("Use short answers.", record["content"])
        self.assertEqual(["style"], record["tags"])
        self.assertTrue((self.root / ".pocketchat" / "memory.jsonl").is_file())

    def test_list_and_search_are_read_only(self):
        add_memory(self.root, "alpha fact", tags=["a"])
        add_memory(self.root, "beta fact", tags=["b"])
        before = (self.root / ".pocketchat" / "memory.jsonl").read_bytes()
        self.assertEqual(2, len(list_memory(self.root)))
        self.assertEqual(["alpha fact"],
                         [item["content"] for item in search_memory(self.root, "alpha")])
        self.assertEqual(before, (self.root / ".pocketchat" / "memory.jsonl").read_bytes())

    def test_remove_is_explicit_and_missing_id_is_refused(self):
        record = add_memory(self.root, "delete me")
        remove_memory(self.root, record["memory_id"])
        self.assertEqual([], list_memory(self.root))
        with self.assertRaises(MemoryError):
            remove_memory(self.root, record["memory_id"])

    def test_corrupt_store_fails_closed(self):
        path = self.root / ".pocketchat" / "memory.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text('{"bad":\n', encoding="utf-8")
        for operation in (lambda: list_memory(self.root),
                          lambda: search_memory(self.root, "x"),
                          lambda: add_memory(self.root, "new")):
            with self.subTest(operation=operation):
                with self.assertRaises(MemoryError):
                    operation()

    def test_empty_tag_and_oversized_content_are_refused(self):
        with self.assertRaises(MemoryError):
            add_memory(self.root, "")
        with self.assertRaises(MemoryError):
            add_memory(self.root, "x", tags=[""])
        with self.assertRaises(MemoryError):
            add_memory(self.root, "x" * (16 * 1024 + 1))

    def test_cli_add_list_search_remove_json(self):
        added = run_cli(("memory", "add", "hello memory", "--tag", "test", "--json"),
                        self.root)
        self.assertEqual(0, added.returncode, added.stdout + added.stderr)
        memory_id = json.loads(added.stdout)["memory_id"]
        listed = run_cli(("memory", "list", "--json"), self.root)
        self.assertEqual(0, listed.returncode, listed.stdout + listed.stderr)
        self.assertEqual(memory_id, json.loads(listed.stdout)["items"][0]["memory_id"])
        found = run_cli(("memory", "search", "hello", "--json"), self.root)
        self.assertEqual(memory_id, json.loads(found.stdout)["items"][0]["memory_id"])
        removed = run_cli(("memory", "remove", memory_id, "--json"), self.root)
        self.assertEqual(0, removed.returncode, removed.stdout + removed.stderr)


if __name__ == "__main__":
    unittest.main()
