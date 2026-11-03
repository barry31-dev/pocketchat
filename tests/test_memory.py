"""Explicit local memory store and CLI tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from pocketchat import memory
from pocketchat.memory import MemoryError, add_memory, list_memory, remove_memory, search_memory
from tests._helpers import run_cli


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_memory_import_does_not_require_fcntl(self):
        code = (
            "import sys; sys.modules['fcntl'] = None; "
            "import pocketchat.memory; print('ok')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("ok", result.stdout.strip())

    def test_windows_memory_lock_uses_msvcrt(self):
        locking = mock.Mock()
        msvcrt = types.SimpleNamespace(
            LK_NBLCK=2, LK_UNLCK=0, locking=locking)
        with mock.patch.object(memory.os, "name", "nt"), \
                mock.patch.dict(sys.modules, {"msvcrt": msvcrt}):
            with tempfile.TemporaryFile() as lock_file:
                memory._lock_nonblocking(lock_file)
                memory._unlock(lock_file)
                descriptor = lock_file.fileno()
        self.assertEqual(
            [mock.call(descriptor, msvcrt.LK_NBLCK, 1),
             mock.call(descriptor, msvcrt.LK_UNLCK, 1)],
            locking.call_args_list)

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

    def test_store_total_size_is_bounded(self):
        path = self.root / ".pocketchat" / "memory.jsonl"
        path.parent.mkdir(parents=True)
        record = {
            "schema_version": "pocketchat-memory.v1",
            "memory_id": "mem_" + "x" * 40,
            "created": "2026-01-01T00:00:00Z",
            "content": "x" * 1024,
            "tags": [],
        }
        line = json.dumps(record) + "\n"
        path.write_text(line * (4 * 1024 * 1024 // len(line.encode("utf-8")) + 5),
                        encoding="utf-8")
        with self.assertRaises(MemoryError) as caught:
            list_memory(self.root)
        self.assertIn("exceeds", str(caught.exception))

    def test_symlinked_store_is_refused_without_following_it(self):
        outside = self.root / "outside.jsonl"
        outside.write_text('{"schema_version":"pocketchat-memory.v1"}\n',
                           encoding="utf-8")
        path = self.root / ".pocketchat" / "memory.jsonl"
        path.parent.mkdir(parents=True)
        path.symlink_to(outside)
        with self.assertRaises(MemoryError):
            list_memory(self.root)

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

    def test_concurrent_writers_do_not_lose_an_update(self):
        from concurrent.futures import ThreadPoolExecutor
        from pocketchat.memory import add_memory
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(add_memory, self.root, value)
                       for value in ("first", "second")]
            results = []
            for future in futures:
                try:
                    results.append(future.result())
                except MemoryError as exc:
                    self.assertIn("busy", str(exc).lower())
        contents = sorted(item["content"] for item in list_memory(self.root))
        # The two writers either both commit, or one gets an explicit busy
        # result. The important property is that a completed write is never
        # silently lost behind another writer's replace.
        self.assertEqual(sorted(item for item in ("first", "second")
                                if item in contents), contents)
        self.assertEqual(len(results), len(contents))
        if len(results) == 2:
            self.assertEqual(["first", "second"], contents)

    def test_refused_mutation_leaves_previous_store_intact(self):
        add_memory(self.root, "keep me")
        path = self.root / ".pocketchat" / "memory.jsonl"
        before = path.read_bytes()
        with self.assertRaises(MemoryError):
            add_memory(self.root, "x" * (16 * 1024 + 1))
        self.assertEqual(before, path.read_bytes())

    def test_stale_lock_holder_is_reported_as_busy(self):
        from pocketchat.memory import memory_path
        path = memory_path(self.root)
        path.parent.mkdir(parents=True)
        lock = path.with_name("memory.jsonl.lock")
        descriptor = lock.open("a+b")
        self.addCleanup(descriptor.close)
        memory._lock_nonblocking(descriptor)
        with self.assertRaises(MemoryError) as caught:
            add_memory(self.root, "blocked")
        self.assertIn("busy", str(caught.exception).lower())

    def test_cli_v2_operations_require_an_explicit_store(self):
        for command in (
                ("memory", "accept", "mem_legacy", "--by", "operator", "--json"),
                ("memory", "retire", "mem_legacy", "--json"),
                ("memory", "forget", "mem_legacy", "--json"),
        ):
            with self.subTest(command=command):
                result = run_cli(command, self.root)
                self.assertEqual(2, result.returncode)
                self.assertEqual("", result.stderr)
                payload = json.loads(result.stdout)
                self.assertFalse(payload["ok"])
                self.assertIn("--store", payload["message"])

    def test_cli_v2_migrate_accept_retire_forget_are_explicit(self):
        added = run_cli(
            ("memory", "add", "legacy preference", "--tag", "style", "--json"),
            self.root)
        self.assertEqual(0, added.returncode, added.stdout + added.stderr)
        memory_id = json.loads(added.stdout)["memory_id"]
        store = ".pocketchat/memory.v2.jsonl"
        migrated = run_cli(
            ("memory", "migrate", "--workspace-id", "workspace-1",
             "--store", store, "--json"),
            self.root)
        self.assertEqual(0, migrated.returncode, migrated.stdout + migrated.stderr)
        migrated_payload = json.loads(migrated.stdout)
        self.assertEqual(memory_id, migrated_payload["items"][0]["memory_id"])
        self.assertEqual("candidate",
                         migrated_payload["items"][0]["admission_status"])

        refused = run_cli(
            ("memory", "accept", memory_id, "--by", "operator",
             "--store", store, "--json"),
            self.root)
        self.assertEqual(2, refused.returncode)
        self.assertFalse(json.loads(refused.stdout)["ok"])

        accepted = run_cli(
            ("memory", "accept", memory_id, "--by", "operator",
             "--verification", "not_required",
             "--store", store, "--json"),
            self.root)
        self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
        accepted_payload = json.loads(accepted.stdout)
        self.assertEqual("accepted", accepted_payload["item"]["admission_status"])
        self.assertEqual("operator", accepted_payload["item"]["accepted_by"])

        retired = run_cli(
            ("memory", "retire", memory_id, "--store", store, "--json"),
            self.root)
        self.assertEqual(0, retired.returncode, retired.stdout + retired.stderr)
        retired_payload = json.loads(retired.stdout)
        self.assertEqual("retired", retired_payload["item"]["admission_status"])
        self.assertIn("retired_at", retired_payload["item"])

        forgotten = run_cli(
            ("memory", "forget", memory_id, "--store", store, "--json"),
            self.root)
        self.assertEqual(0, forgotten.returncode, forgotten.stdout + forgotten.stderr)
        self.assertEqual(memory_id, json.loads(forgotten.stdout)["forgotten"])
        v2_path = self.root / store
        self.assertEqual("", v2_path.read_text(encoding="utf-8"))
        self.assertNotIn("legacy preference", v2_path.read_text(encoding="utf-8"))

    def test_cli_v2_store_cannot_escape_the_workspace(self):
        outside = self.root.parent / f"{self.root.name}-outside-memory.v2.jsonl"
        self.addCleanup(lambda: outside.unlink() if outside.exists() else None)
        result = run_cli(
            ("memory", "accept", "mem_legacy", "--by", "operator",
             "--store", str(outside), "--json"),
            self.root)
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("inside the workspace", payload["message"])
        self.assertFalse(outside.exists())

    def test_v2_read_refuses_platform_without_no_follow(self):
        store = self.root / ".pocketchat" / "memory.v2.jsonl"
        store.parent.mkdir(parents=True)
        store.write_text("", encoding="utf-8")
        with mock.patch("pocketchat.memory_migration.hasattr",
                        side_effect=lambda obj, name: (
                            False if name == "O_NOFOLLOW" else
                            __import__("builtins").hasattr(obj, name))):
            result = run_cli(
                ("memory", "forget", "mem_legacy",
                 "--store", ".pocketchat/memory.v2.jsonl", "--json"),
                self.root)
        self.assertEqual(2, result.returncode)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertIn("no-follow", json.loads(result.stdout)["message"])

    def test_v2_read_refuses_a_parent_replaced_before_file_open(self):
        added = run_cli(("memory", "add", "read target", "--json"), self.root)
        memory_id = json.loads(added.stdout)["memory_id"]
        store = ".pocketchat/memory.v2.jsonl"
        migrated = run_cli(
            ("memory", "migrate", "--workspace-id", "workspace-1",
             "--store", store, "--json"), self.root)
        self.assertEqual(0, migrated.returncode, migrated.stdout + migrated.stderr)
        parent = self.root / ".pocketchat"
        outside = self.root.parent / f"{self.root.name}-outside-read"
        outside.mkdir()
        (outside / "memory.v2.jsonl").write_text("", encoding="utf-8")
        self.addCleanup(lambda: __import__("shutil").rmtree(outside,
                                                             ignore_errors=True))
        target = (parent / "memory.v2.jsonl").resolve()
        backup = self.root / ".pocketchat-backup"
        self.addCleanup(lambda: __import__("shutil").rmtree(
            backup, ignore_errors=True))
        original_open = __import__("os").open
        replaced = False

        def replace_parent(path, *args, **kwargs):
            nonlocal replaced
            target_read = (
                path == str(target)
                or (path == target.name and kwargs.get("dir_fd") is not None)
            )
            if target_read and not replaced:
                parent.rename(backup)
                parent.symlink_to(outside, target_is_directory=True)
                replaced = True
            return original_open(path, *args, **kwargs)

        replacement_open = replace_parent
        with mock.patch("pocketchat.memory_migration.os.open",
                        replacement_open), \
                mock.patch("pocketchat.memory_migration.os.supports_dir_fd",
                           {replacement_open}):
            result = run_cli(
                ("memory", "accept", memory_id, "--by", "operator",
                 "--verification", "not_required",
                 "--store", store, "--json"), self.root)
        self.assertEqual(2, result.returncode)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertNotIn("no such memory", payload["message"])
        message = payload["message"].lower()
        self.assertTrue(replaced, "parent replacement was not exercised")
        self.assertTrue("symlink" in message or "no-follow" in message, message)
        self.assertEqual("", (outside / "memory.v2.jsonl").read_text(
            encoding="utf-8"))

    def test_v2_read_does_not_recreate_a_replaced_parent(self):
        added = run_cli(("memory", "add", "read target", "--json"), self.root)
        self.assertEqual(0, added.returncode, added.stdout + added.stderr)
        store = ".pocketchat/memory.v2.jsonl"
        migrated = run_cli(
            ("memory", "migrate", "--workspace-id", "workspace-1",
             "--store", store, "--json"), self.root)
        self.assertEqual(0, migrated.returncode, migrated.stdout + migrated.stderr)
        from pocketchat.memory_migration import (
            MemoryMigrationError, _open_target_parent, read_v2_store)
        parent = self.root / ".pocketchat"
        backup = self.root / ".pocketchat-backup"
        self.addCleanup(lambda: __import__("shutil").rmtree(
            backup, ignore_errors=True))
        original_open_parent = _open_target_parent
        replaced = False

        def replace_then_open(workspace, target_parent, **kwargs):
            nonlocal replaced
            if not replaced:
                parent.rename(backup)
                replaced = True
            if kwargs:
                return original_open_parent(workspace, target_parent, **kwargs)
            return original_open_parent(workspace, target_parent)

        with mock.patch("pocketchat.memory_migration._open_target_parent",
                        replace_then_open):
            with self.assertRaises(MemoryMigrationError):
                read_v2_store(self.root, Path(store))
        self.assertTrue(replaced)
        self.assertFalse(parent.exists())

    def test_v2_mutation_does_not_create_lock_through_a_replaced_parent(self):
        added = run_cli(("memory", "add", "lock target", "--json"), self.root)
        memory_id = json.loads(added.stdout)["memory_id"]
        store = ".pocketchat/memory.v2.jsonl"
        migrated = run_cli(
            ("memory", "migrate", "--workspace-id", "workspace-1",
             "--store", store, "--json"), self.root)
        self.assertEqual(0, migrated.returncode, migrated.stdout + migrated.stderr)
        parent = self.root / ".pocketchat"
        outside = self.root.parent / f"{self.root.name}-outside-mutation-lock"
        outside.mkdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            outside, ignore_errors=True))
        backup = self.root / ".pocketchat-backup"
        self.addCleanup(lambda: __import__("shutil").rmtree(
            backup, ignore_errors=True))
        target = (self.root / store).resolve()
        lock_name = target.name + ".lock"
        original_open = __import__("os").open
        replaced = False

        def replace_before_lock(path, *args, **kwargs):
            nonlocal replaced
            lock_open = (
                path == str(target.with_name(lock_name))
                or (path == lock_name and kwargs.get("dir_fd") is not None)
            )
            if lock_open and not replaced:
                parent.rename(backup)
                parent.symlink_to(outside, target_is_directory=True)
                replaced = True
            return original_open(path, *args, **kwargs)

        replacement_open = replace_before_lock
        with mock.patch("pocketchat.memory_migration.os.open",
                        replacement_open), \
                mock.patch("pocketchat.memory_migration.os.supports_dir_fd",
                           {replacement_open}):
            result = run_cli(
                ("memory", "accept", memory_id, "--by", "operator",
                 "--verification", "not_required", "--store", store, "--json"),
                self.root)
        self.assertTrue(replaced)
        self.assertEqual(2, result.returncode)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertFalse((outside / lock_name).exists())

    def test_v2_mutation_refuses_a_symlinked_lock(self):
        added = run_cli(("memory", "add", "lock target", "--json"), self.root)
        memory_id = json.loads(added.stdout)["memory_id"]
        store = ".pocketchat/memory.v2.jsonl"
        migrated = run_cli(
            ("memory", "migrate", "--workspace-id", "workspace-1",
             "--store", store, "--json"), self.root)
        self.assertEqual(0, migrated.returncode, migrated.stdout + migrated.stderr)
        lock = self.root / ".pocketchat" / "memory.v2.jsonl.lock"
        outside = self.root.parent / f"{self.root.name}-outside-lock"
        outside.write_text("", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink() if outside.exists() else None)
        lock.symlink_to(outside)
        result = run_cli(
            ("memory", "accept", memory_id, "--by", "operator",
             "--verification", "not_required",
             "--store", store, "--json"), self.root)
        self.assertEqual(2, result.returncode)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertEqual("", outside.read_text(encoding="utf-8"))

    def test_v2_store_refuses_symlink_and_corrupt_input(self):
        store = self.root / ".pocketchat" / "memory.v2.jsonl"
        store.parent.mkdir(parents=True)
        outside = self.root.parent / f"{self.root.name}-outside-v2.jsonl"
        outside.write_text("", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink() if outside.exists() else None)
        store.symlink_to(outside)
        result = run_cli(
            ("memory", "accept", "mem_legacy", "--by", "operator",
             "--store", ".pocketchat/memory.v2.jsonl", "--json"),
            self.root)
        self.assertEqual(2, result.returncode)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertIn("symlink", json.loads(result.stdout)["message"])
        self.assertEqual("", outside.read_text(encoding="utf-8"))

        store.unlink()
        store.write_text('{"bad":\n', encoding="utf-8")
        result = run_cli(
            ("memory", "forget", "mem_legacy",
             "--store", ".pocketchat/memory.v2.jsonl", "--json"),
            self.root)
        self.assertEqual(2, result.returncode)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertIn("invalid JSON", json.loads(result.stdout)["message"])

    def test_v2_accept_refuses_non_user_source_and_unknown_retirement_target(self):
        added = run_cli(
            ("memory", "add", "legacy preference", "--json"), self.root)
        memory_id = json.loads(added.stdout)["memory_id"]
        store = ".pocketchat/memory.v2.jsonl"
        migrated = run_cli(
            ("memory", "migrate", "--workspace-id", "workspace-1",
             "--store", store, "--json"), self.root)
        self.assertEqual(0, migrated.returncode, migrated.stdout + migrated.stderr)
        path = self.root / store
        record = json.loads(path.read_text(encoding="utf-8"))
        record["source_kind"] = "model_summary"
        path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
        result = run_cli(
            ("memory", "accept", memory_id, "--by", "operator",
             "--verification", "not_required", "--store", store, "--json"),
            self.root)
        self.assertEqual(2, result.returncode)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertIn("explicit user records", json.loads(result.stdout)["message"])

        record["source_kind"] = "legacy_explicit"
        path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
        result = run_cli(
            ("memory", "retire", memory_id, "--superseded-by", "mem_missing",
             "--store", store, "--json"), self.root)
        self.assertEqual(2, result.returncode)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertIn("replacement", json.loads(result.stdout)["message"])

    def test_v2_forget_preserves_other_records(self):
        first = run_cli(("memory", "add", "first secret", "--json"), self.root)
        second = run_cli(("memory", "add", "second fact", "--json"), self.root)
        first_id = json.loads(first.stdout)["memory_id"]
        second_id = json.loads(second.stdout)["memory_id"]
        store = ".pocketchat/memory.v2.jsonl"
        migrated = run_cli(
            ("memory", "migrate", "--workspace-id", "workspace-1",
             "--store", store, "--json"), self.root)
        self.assertEqual(0, migrated.returncode, migrated.stdout + migrated.stderr)
        forgotten = run_cli(
            ("memory", "forget", first_id, "--store", store, "--json"), self.root)
        self.assertEqual(0, forgotten.returncode, forgotten.stdout + forgotten.stderr)
        records = [json.loads(line) for line in
                   (self.root / store).read_text(encoding="utf-8").splitlines()]
        self.assertEqual([second_id], [item["memory_id"] for item in records])
        self.assertNotIn("first secret", (self.root / store).read_text(encoding="utf-8"))

    def test_v2_mutation_failure_leaves_store_unchanged(self):
        added = run_cli(("memory", "add", "keep me", "--json"), self.root)
        memory_id = json.loads(added.stdout)["memory_id"]
        store = ".pocketchat/memory.v2.jsonl"
        migrated = run_cli(
            ("memory", "migrate", "--workspace-id", "workspace-1",
             "--store", store, "--json"), self.root)
        self.assertEqual(0, migrated.returncode, migrated.stdout + migrated.stderr)
        before = (self.root / store).read_bytes()
        with mock.patch("pocketchat.memory_migration.os.replace",
                        side_effect=OSError("replace failed")):
            result = run_cli(
                ("memory", "accept", memory_id, "--by", "operator",
                 "--verification", "not_required",
                 "--store", store, "--json"), self.root)
        self.assertEqual(2, result.returncode)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertEqual(before, (self.root / store).read_bytes())

    def test_v2_accept_records_the_actor_as_updated_by(self):
        added = run_cli(("memory", "add", "actor fact", "--json"), self.root)
        memory_id = json.loads(added.stdout)["memory_id"]
        store = ".pocketchat/memory.v2.jsonl"
        migrated = run_cli(
            ("memory", "migrate", "--workspace-id", "workspace-1",
             "--store", store, "--json"), self.root)
        self.assertEqual(0, migrated.returncode, migrated.stdout + migrated.stderr)
        accepted = run_cli(
            ("memory", "accept", memory_id, "--by", "operator",
             "--verification", "not_required", "--store", store, "--json"),
            self.root)
        self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
        self.assertEqual("operator", json.loads(accepted.stdout)["item"]["updated_by"])

    def test_v2_accept_changes_only_admission_fields(self):
        added = run_cli(("memory", "add", "stable content", "--json"), self.root)
        memory_id = json.loads(added.stdout)["memory_id"]
        store = ".pocketchat/memory.v2.jsonl"
        migrated = run_cli(
            ("memory", "migrate", "--workspace-id", "workspace-1",
             "--store", store, "--json"), self.root)
        self.assertEqual(0, migrated.returncode, migrated.stdout + migrated.stderr)
        before = json.loads((self.root / store).read_text(encoding="utf-8"))
        accepted = run_cli(
            ("memory", "accept", memory_id, "--by", "operator",
             "--verification", "not_required", "--store", store, "--json"),
            self.root)
        self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
        after = json.loads(accepted.stdout)["item"]
        for field in ("memory_id", "revision", "workspace_id", "category",
                      "content", "content_sha256", "created", "source_kind",
                      "source_refs", "supersedes"):
            with self.subTest(field=field):
                self.assertEqual(before[field], after[field])

    def test_v2_write_does_not_follow_a_replaced_parent_during_write(self):
        added = run_cli(("memory", "add", "keep me", "--json"), self.root)
        memory_id = json.loads(added.stdout)["memory_id"]
        store = ".pocketchat/memory.v2.jsonl"
        migrated = run_cli(
            ("memory", "migrate", "--workspace-id", "workspace-1",
             "--store", store, "--json"), self.root)
        self.assertEqual(0, migrated.returncode, migrated.stdout + migrated.stderr)
        parent = self.root / ".pocketchat"
        outside = self.root.parent / f"{self.root.name}-outside-parent"
        outside.mkdir()
        self.addCleanup(lambda: outside.rmdir() if outside.exists() else None)
        from pocketchat.memory_migration import _write_store_from_parent
        original_write = _write_store_from_parent
        replaced = False

        def replace_before_write(name, parent_descriptor, payload, **kwargs):
            nonlocal replaced
            if not replaced:
                parent.rename(backup)
                parent.symlink_to(outside, target_is_directory=True)
                replaced = True
            return original_write(
                name, parent_descriptor, payload, **kwargs)

        backup = self.root / ".pocketchat-backup"
        self.addCleanup(lambda: __import__("shutil").rmtree(
            backup, ignore_errors=True))
        with mock.patch(
                "pocketchat.memory_migration._write_store_from_parent",
                replace_before_write):
            result = run_cli(
                ("memory", "accept", memory_id, "--by", "operator",
                 "--verification", "not_required",
                 "--store", store, "--json"), self.root)
        self.assertTrue(replaced)
        self.assertEqual(2, result.returncode)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertFalse((outside / "memory.v2.jsonl").exists())

    def test_memory_recall_dry_run_is_read_only_and_bounded(self):
        added = run_cli(("memory", "add", "answer fact", "--json"), self.root)
        memory_id = json.loads(added.stdout)["memory_id"]
        store = ".pocketchat/memory.v2.jsonl"
        run_cli(("memory", "migrate", "--workspace-id", "workspace-1",
                 "--store", store, "--json"), self.root)
        run_cli(("memory", "accept", memory_id, "--by", "operator",
                 "--verification", "not_required", "--store", store, "--json"),
                self.root)
        before = (self.root / store).read_bytes()
        recalled = run_cli(("memory", "recall", "answer",
                            "--workspace-id", "workspace-1", "--store", store,
                            "--dry-run", "--json"), self.root)
        payload = json.loads(recalled.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dry_run"])
        self.assertEqual([memory_id],
                         [item["memory_id"] for item in payload["items"]])
        self.assertIn("answer fact", payload["context_message"]["content"])
        self.assertEqual(before, (self.root / store).read_bytes())

    def test_memory_recall_excludes_other_workspaces(self):
        added = run_cli(("memory", "add", "answer fact", "--json"), self.root)
        memory_id = json.loads(added.stdout)["memory_id"]
        store = ".pocketchat/memory.v2.jsonl"
        run_cli(("memory", "migrate", "--workspace-id", "workspace-1",
                 "--store", store, "--json"), self.root)
        run_cli(("memory", "accept", memory_id, "--by", "operator",
                 "--verification", "not_required", "--store", store, "--json"),
                self.root)
        recalled = run_cli(("memory", "recall", "answer",
                            "--workspace-id", "workspace-2", "--store", store,
                            "--dry-run", "--json"), self.root)
        payload = json.loads(recalled.stdout)
        self.assertEqual([], payload["items"])
        self.assertEqual({"workspace_mismatch"},
                         {reason for item in payload["excluded"]
                          for reason in item["reasons"]})
        self.assertNotIn("answer fact", payload["context_message"]["content"])

    def test_v2_memory_commands_run_end_to_end_as_subprocesses(self):
        env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        added = subprocess.run(
            [sys.executable, "-m", "pocketchat.cli", "memory", "add",
             "subprocess preference", "--json"],
            cwd=str(self.root), env=env, capture_output=True, text=True,
            check=False)
        self.assertEqual(0, added.returncode, added.stdout + added.stderr)
        memory_id = json.loads(added.stdout)["memory_id"]
        store = ".pocketchat/memory.v2.jsonl"
        migrated = subprocess.run(
            [sys.executable, "-m", "pocketchat.cli", "memory", "migrate",
             "--workspace-id", "workspace-1", "--store", store, "--json"],
            cwd=str(self.root), env=env, capture_output=True, text=True,
            check=False)
        self.assertEqual(0, migrated.returncode, migrated.stdout + migrated.stderr)
        self.assertEqual("", migrated.stderr)
        self.assertTrue(json.loads(migrated.stdout)["ok"])
        accepted = subprocess.run(
            [sys.executable, "-m", "pocketchat.cli", "memory", "accept",
             memory_id, "--by", "operator", "--verification", "not_required",
             "--store", store, "--json"],
            cwd=str(self.root), env=env, capture_output=True, text=True,
            check=False)
        self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
        self.assertEqual("", accepted.stderr)
        self.assertEqual("accepted", json.loads(accepted.stdout)["item"]["admission_status"])

    def test_v2_concurrent_mutations_do_not_silently_lose_an_update(self):
        first = run_cli(("memory", "add", "first fact", "--json"), self.root)
        second = run_cli(("memory", "add", "second fact", "--json"), self.root)
        first_id = json.loads(first.stdout)["memory_id"]
        second_id = json.loads(second.stdout)["memory_id"]
        store = ".pocketchat/memory.v2.jsonl"
        migrated = run_cli(
            ("memory", "migrate", "--workspace-id", "workspace-1",
             "--store", store, "--json"), self.root)
        self.assertEqual(0, migrated.returncode, migrated.stdout + migrated.stderr)
        from concurrent.futures import ThreadPoolExecutor
        from pocketchat.memory_migration import forget_v2_record

        def forget(memory_id):
            try:
                forget_v2_record(self.root, Path(store), memory_id)
                return "committed"
            except Exception as exc:
                return "refused:%s" % exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(forget, (first_id, second_id)))
        records = [json.loads(line) for line in
                   (self.root / store).read_text(encoding="utf-8").splitlines()]
        remaining = {item["memory_id"] for item in records}
        self.assertEqual(len(records), len(remaining))
        # At least one update may be refused, but no successful deletion may
        # be silently resurrected by another writer's stale snapshot.
        for result, memory_id in zip(results, (first_id, second_id)):
            if result == "committed":
                self.assertNotIn(memory_id, remaining)

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
