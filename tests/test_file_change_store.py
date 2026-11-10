"""Private, bounded, no-follow storage tests for file changes."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pocketchat.file_change_store import (FileChangeStore,
                                          FileChangeStoreError,
                                          FileChangeUnsupportedRuntime)

SECURE_FILE_CHANGES = (
    all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY"))
    and all(function in os.supports_dir_fd for function in (
        os.open, os.mkdir, os.stat, os.unlink, os.link, os.rename))
)

@unittest.skipUnless(
    SECURE_FILE_CHANGES, "requires secure descriptor-relative file changes")
class FileChangeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_symlinked_change_store_is_refused_without_touching_the_target(self):
        outside = self.root / "outside"
        outside.mkdir()
        pocketchat = self.root / ".pocketchat"
        pocketchat.mkdir()
        (pocketchat / "changes").symlink_to(outside, target_is_directory=True)

        with self.assertRaises(FileChangeStoreError):
            FileChangeStore(self.root)

        self.assertEqual([], list(outside.iterdir()))

    def test_writer_claim_is_exclusive_and_releases_only_its_own_token(self):
        store = FileChangeStore(self.root)
        claim = store.acquire_writer(
            "change_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            owner="session-1",
        )
        self.addCleanup(claim.release)

        with self.assertRaises(FileChangeStoreError):
            store.acquire_writer(
                "change_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                owner="session-2",
            )

        claim.release()
        replacement = store.acquire_writer(
            "change_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            owner="session-2",
        )
        replacement.release()

    def test_store_enforces_private_modes_duplicate_ids_and_quota(self):
        store = FileChangeStore(self.root, max_bytes=256)
        record = {
            "change_id": "change_12121212121212121212121212121212",
            "value": "small",
        }
        store.create_record(record)
        self.assertEqual(0, (store.root.stat().st_mode & 0o077))
        self.assertEqual(0, ((store.records /
                              "change_12121212121212121212121212121212.json").stat().st_mode
                             & 0o077))
        with self.assertRaises(FileChangeStoreError):
            store.create_record(record)
        with self.assertRaises(FileChangeStoreError):
            store.write_object(b"x" * 256)

    def test_duplicate_json_members_are_rejected_on_readback(self):
        store = FileChangeStore(self.root)
        change_id = "change_23232323232323232323232323232323"
        record_path = store.records / (change_id + ".json")
        record_path.write_text(
            '{"change_id":"%s","value":1,"value":2}' % change_id,
            encoding="utf-8",
        )
        record_path.chmod(0o600)

        with self.assertRaises(FileChangeStoreError):
            store.load_record(change_id)


@unittest.skipIf(
    SECURE_FILE_CHANGES, "runtime already supports secure file changes")
class FileChangeUnsupportedRuntimeTests(unittest.TestCase):
    def test_store_refuses_before_creating_private_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(FileChangeUnsupportedRuntime):
                FileChangeStore(root)
            self.assertFalse((root / ".pocketchat").exists())


if __name__ == "__main__":
    unittest.main()
