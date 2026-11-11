"""Explicit v2 candidates and source-bound project recall, using real stores."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pocketchat import cli, memory_migration as migration
from pocketchat.memory import MAX_LINE_BYTES, add_memory, memory_path
from pocketchat.memory_retrieval import retrieve_memories
from tests._helpers import SECURE_DESCRIPTOR_READS, subprocess_environment


@unittest.skipUnless(SECURE_DESCRIPTOR_READS, "requires secure descriptor reads")
class MemoryProposeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = Path(".pocketchat/project-memory.jsonl")

    def propose(self, content="Prefer small dependency-free patches.", **kwargs):
        function = getattr(migration, "propose_v2_record", None)
        self.assertTrue(callable(function), "v2 has no explicit candidate creation API")
        options = {"workspace_id": "project-a", "proposed_by": "operator"}
        options.update(kwargs)
        return function(self.root, self.store, content, **options)

    def command(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    code = cli.main(["memory", *arguments, "--json"])
                except SystemExit as exc:
                    code = exc.code
        finally:
            os.chdir(previous)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_creates_unverified_candidate_without_legacy_migration(self):
        add_memory(self.root, "Leave this legacy record unchanged.")
        legacy = memory_path(self.root).read_bytes()
        content = "  Prefer small dependency-free patches.\nKeep public names English.  "
        code, output, errors = self.command(
            "propose", content, "--workspace-id", "project-a", "--by", "operator",
            "--category", "project_decision", "--tag", "patches",
            "--store", str(self.store))
        self.assertEqual(0, code, errors)
        result = json.loads(output)
        item = result["item"]
        self.assertTrue(result["ok"])
        self.assertEqual("candidate", item["admission_status"])
        self.assertEqual("", item["accepted_by"])
        self.assertEqual("unverified", item["verification_status"])
        self.assertEqual("user_explicit", item["source_kind"])
        self.assertEqual(content, item["content"])
        self.assertEqual(hashlib.sha256(content.encode()).hexdigest(), item["content_sha256"])
        self.assertEqual("project_decision", item["category"])
        self.assertEqual("operator", item["updated_by"])
        self.assertEqual(["patches"], item["tags"])
        self.assertEqual(1, item["revision"])
        self.assertEqual(legacy, memory_path(self.root).read_bytes())
        self.assertEqual([item], migration.read_v2_store(self.root, self.store))
        source = item["source_refs"][0]
        self.assertEqual("event", source["kind"])
        self.assertIn(item["memory_id"], source["ref"])
        self.assertEqual(item["content_sha256"], source["sha256"])
        self.assertEqual("project-a", source["scope"])

    def test_appending_candidate_preserves_an_accepted_record(self):
        first = self.propose()
        accepted = migration.accept_v2_record(
            self.root, self.store, first["memory_id"], "reviewer", verification_status="not_required")
        second = self.propose("Keep patches easy to review.")
        self.assertNotEqual(first["memory_id"], second["memory_id"])
        self.assertEqual([accepted, second], migration.read_v2_store(self.root, self.store))

    def test_new_candidate_requires_acceptance_and_retirement_stops_new_recall(self):
        item = self.propose()
        import datetime
        def recall(workspace="project-a"):
            return retrieve_memories(
                migration.read_v2_store(self.root, self.store), "patches",
                workspace_id=workspace, now=datetime.datetime.now(datetime.timezone.utc),
                max_results=20, max_excerpt_bytes=4096, max_recall_tokens=1024)
        self.assertEqual([], recall()["items"])
        with self.assertRaises(migration.MemoryMigrationError):
            migration.accept_v2_record(self.root, self.store, item["memory_id"], "operator")
        accepted = migration.accept_v2_record(
            self.root, self.store, item["memory_id"], "operator", verification_status="not_required")
        selected = recall()["items"][0]
        self.assertEqual(accepted["revision"], selected["revision"])
        self.assertEqual(accepted["source_refs"], selected["source_refs"])
        self.assertEqual([], recall("project-b")["items"])
        migration.retire_v2_record(self.root, self.store, item["memory_id"])
        self.assertEqual([], recall()["items"])

    def test_metadata_and_oversized_content_are_refused_before_creating_store(self):
        invalid = [
            {"workspace_id": " "}, {"proposed_by": " "},
            {"category": "made-up"}, {"tags": ["same", "same"]},
            {"expires_at": "tomorrow"}, {"sensitive": "yes"},
        ]
        for options in invalid:
            with self.subTest(options=options):
                with self.assertRaises(migration.MemoryMigrationError):
                    self.propose(**options)
                self.assertFalse((self.root / self.store).exists())
        with self.assertRaises(migration.MemoryMigrationError):
            self.propose("x" * MAX_LINE_BYTES)
        self.assertFalse((self.root / self.store).exists())

    def test_corrupt_store_is_not_replaced_by_a_new_candidate(self):
        path = self.root / self.store
        path.parent.mkdir()
        path.write_bytes(b"not JSON\n")
        before = path.read_bytes()
        with self.assertRaises(migration.MemoryMigrationError):
            self.propose()
        self.assertEqual(before, path.read_bytes())

    def test_store_and_lock_symlinks_are_refused_without_mutating_target(self):
        target = self.root / "untouched.txt"
        target.write_bytes(b"original target")
        path = self.root / self.store
        path.parent.mkdir()
        path.symlink_to(target)
        with self.assertRaises(migration.MemoryMigrationError):
            self.propose()
        self.assertEqual(b"original target", target.read_bytes())
        path.unlink()
        lock = path.with_name(path.name + ".lock")
        lock.symlink_to(target)
        with self.assertRaises(migration.MemoryMigrationError):
            self.propose()
        self.assertFalse(path.exists())
        self.assertEqual(b"original target", target.read_bytes())

    def test_outside_workspace_and_parent_symlink_are_refused(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other).resolve()
            original = self.store
            self.store = outside / "memory.jsonl"
            with self.assertRaises(migration.MemoryMigrationError):
                self.propose()
            self.assertFalse(self.store.exists())
            self.store = original
            (self.root / ".pocketchat").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(migration.MemoryMigrationError):
                self.propose()
            self.assertFalse((outside / "project-memory.jsonl").exists())

    def test_duplicate_generated_identity_does_not_replace_existing_record(self):
        first = self.propose()
        before = (self.root / self.store).read_bytes()
        class SameId:
            hex = first["memory_id"][4:]
        with patch.object(migration, "uuid4", return_value=SameId()):
            with self.assertRaises(migration.MemoryMigrationError):
                self.propose("Do not overwrite the first record.")
        self.assertEqual(before, (self.root / self.store).read_bytes())

    def test_cli_refuses_implicit_store_and_cannot_auto_accept_or_verify(self):
        for extra in ([], ["--accepted"], ["--verification", "verified"]):
            with self.subTest(extra=extra):
                code, _, _ = self.command(
                    "propose", "patches", "--workspace-id", "project-a",
                    "--by", "operator", *extra)
                self.assertEqual(2, code)
                self.assertFalse((self.root / migration.DEFAULT_V2_PATH).exists())

    def test_expired_and_sensitive_candidates_remain_excluded_after_acceptance(self):
        expired = self.propose(expires_at="2099-01-01T00:00:00.000Z")
        sensitive = self.propose("Private patches preference.", sensitive=True)
        for item in (expired, sensitive):
            migration.accept_v2_record(self.root, self.store, item["memory_id"], "operator", verification_status="not_required")
        import datetime
        result = retrieve_memories(
            migration.read_v2_store(self.root, self.store), "patches",
            workspace_id="project-a", now=datetime.datetime(2100, 1, 1, tzinfo=datetime.timezone.utc),
            max_results=20, max_excerpt_bytes=4096, max_recall_tokens=1024)
        self.assertEqual([], result["items"])
        self.assertEqual(2, len(result["excluded"]))

    def test_invalid_utf8_metadata_is_a_typed_refusal_before_store_creation(self):
        for options in ({"proposed_by": "\ud800"}, {"workspace_id": "\ud800"},
                        {"tags": ["\ud800"]}):
            with self.subTest(options=options):
                with self.assertRaises(migration.MemoryMigrationError):
                    self.propose(**options)
                self.assertFalse((self.root / self.store).exists())

    def test_existing_read_accept_and_legacy_paths_do_not_gain_implicit_creation(self):
        self.assertEqual([], migration.read_v2_store(self.root, self.store))
        with self.assertRaises(migration.MemoryMigrationError):
            migration.accept_v2_record(self.root, self.store, "mem_missing", "operator")
        self.assertFalse((self.root / self.store).exists())
        self.store = memory_path(self.root)
        with self.assertRaises(migration.MemoryMigrationError):
            self.propose()
        self.assertFalse(self.store.exists())

    def test_migration_cannot_report_success_after_proposal_replaces_its_empty_target(self):
        legacy = add_memory(self.root, "The migrated record must not disappear.")
        package = Path(__file__).resolve().parents[1]
        ready = self.root / "migration-ready"
        resume = self.root / "migration-resume"
        script = f"""
import sys, time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, {str(package)!r})
from pocketchat import memory_migration as migration
root = Path.cwd()
store = Path({self.store.as_posix()!r})
ready = Path({ready.name!r})
resume = Path({resume.name!r})
original_open = migration.os.open
original_fdopen = migration.os.fdopen
target_descriptor = None

def tracked_open(path, flags, *args, **kwargs):
    global target_descriptor
    descriptor = original_open(path, flags, *args, **kwargs)
    if path == store.name and flags & migration.os.O_EXCL \
            and flags & migration.os.O_WRONLY:
        target_descriptor = descriptor
    return descriptor

def pause_target(descriptor, mode):
    if descriptor == target_descriptor:
        ready.write_text("ready", encoding="utf-8")
        deadline = time.monotonic() + 5
        while not resume.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("migration interleave did not resume")
            time.sleep(0.01)
    return original_fdopen(descriptor, mode)

with patch.object(migration.os, "open", new=tracked_open), \
        patch.object(migration.os, "supports_dir_fd",
                     set(migration.os.supports_dir_fd) | {{tracked_open}}), \
        patch.object(migration.os, "fdopen", side_effect=pause_target):
    migration.migrate_v1_to_v2(
        root, workspace_id="project-a", out=store)
"""
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", script], cwd=self.root,
            env=subprocess_environment(), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 5
        while not ready.exists() and process.poll() is None:
            if time.monotonic() >= deadline:
                process.kill()
                self.fail("migration never reached the guarded write boundary")
            time.sleep(0.01)
        if process.poll() is not None:
            output, error = process.communicate()
            self.fail("migration stopped before interleave: %s%s" % (output, error))
        proposal_error = None
        try:
            self.propose("The proposal must not replace an active migration.")
        except Exception as exc:
            proposal_error = exc
        finally:
            resume.write_text("resume", encoding="utf-8")
        output, error = process.communicate(timeout=5)
        self.assertEqual(0, process.returncode, output + error)
        self.assertIsInstance(proposal_error, migration.MemoryMigrationError)
        self.assertEqual(
            [legacy["memory_id"]],
            [item["memory_id"] for item in migration.read_v2_store(
                self.root, self.store)])

    def test_first_create_refuses_a_concurrent_successful_migration(self):
        add_memory(self.root, "Migrated records must survive a competing proposal.")
        before_write = migration._write_store_from_parent
        migrated = []

        def interleave(name, descriptor, payload, **kwargs):
            migration.migrate_v1_to_v2(
                self.root, workspace_id="project-a", out=self.store)
            migrated.append((self.root / self.store).read_bytes())
            return before_write(name, descriptor, payload, **kwargs)

        with patch.object(
                migration, "_write_store_from_parent", side_effect=interleave):
            with self.assertRaises(migration.MemoryMigrationError):
                self.propose()
        self.assertEqual(migrated[0], (self.root / self.store).read_bytes())
        self.assertEqual(1, len(migration.read_v2_store(self.root, self.store)))

    def test_migration_publishes_the_completed_store_owner_only(self):
        add_memory(self.root, "Private migrated project context.")
        previous_umask = os.umask(0o077)
        try:
            migration.migrate_v1_to_v2(
                self.root, workspace_id="project-a", out=self.store)
        finally:
            os.umask(previous_umask)
        mode = (self.root / self.store).stat().st_mode & 0o777
        self.assertEqual(0o600, mode)
        self.assertEqual(1, len(migration.read_v2_store(
            self.root, self.store)))

    def test_duplicate_existing_ids_and_invalid_unicode_leave_store_unchanged(self):
        item = self.propose()
        path = self.root / self.store
        line = path.read_bytes()
        invalid_content = dict(
            item, content="\ud800", content_sha256="0" * 64)
        for raw in (line + line,
                    (json.dumps(dict(item, updated_by="\ud800")) + "\n").encode(),
                    (json.dumps(invalid_content) + "\n").encode()):
            with self.subTest(raw=raw[:20]):
                path.write_bytes(raw)
                with self.assertRaises(migration.MemoryMigrationError):
                    self.propose()
                self.assertEqual(raw, path.read_bytes())

    def test_workspace_whitespace_has_same_identity_as_recall(self):
        item = self.propose(workspace_id=" project-a ")
        self.assertEqual("project-a", item["workspace_id"])
        self.assertEqual("project-a", item["source_refs"][0]["scope"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires a real FIFO")
    def test_fifo_target_is_refused_without_waiting_for_a_writer(self):
        import subprocess
        import sys
        from tests._helpers import subprocess_environment
        path = self.root / self.store
        path.parent.mkdir()
        os.mkfifo(path)
        package = Path(__file__).resolve().parents[1]
        script = ("import sys; sys.path.insert(0, %r); from pocketchat.cli import main; "
                  "raise SystemExit(main(%r))") % (
                      str(package), ["memory", "propose", "patches", "--workspace-id", "project-a",
                                     "--by", "operator", "--store", str(self.store), "--json"])
        try:
            run = subprocess.run([sys.executable, "-B", "-c", script], cwd=self.root,
                                 env=subprocess_environment(), capture_output=True,
                                 text=True, timeout=2)
        except subprocess.TimeoutExpired:
            self.fail("FIFO target blocked before the regular-file refusal")
        self.assertEqual(2, run.returncode, run.stderr)
        self.assertFalse(json.loads(run.stdout)["ok"])
