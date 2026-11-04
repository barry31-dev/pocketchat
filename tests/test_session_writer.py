"""Explicit session writes need one independent-process owner per path."""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import tempfile
import time
import unittest
from pathlib import Path

from pocketchat.session import Session
from pocketchat.sessions import SessionFile, SessionFileError
from pocketchat.session_writer import (SessionWriterBusy,
                                       acquire_session_writer)


ROOT = Path(__file__).resolve().parents[1]


class BrokenAdapter:
    name = "session-writer-broken"

    def build_payload(self, messages, config):
        return {"messages": messages}

    def complete(self, messages, config, api_key, on_delta=None,
                 prepared_payload=None):
        raise RuntimeError("fixture failure")


class CountingAdapter:
    name = "session-writer-counter"

    def __init__(self):
        self.requests = 0

    def build_payload(self, messages, config):
        return {"messages": messages}

    def complete(self, messages, config, api_key, on_delta=None,
                 prepared_payload=None):
        self.requests += 1
        return {
            "content": "reply",
            "finish_reason": "stop",
            "model": "m",
            "usage": {"input_tokens": 1, "output_tokens": 1,
                      "cache_read_tokens": 0, "cache_write_tokens": 0,
                      "reasoning_tokens": 0},
            "usage_reported": True,
        }


def _write_header(path: str) -> None:
    Path(path).write_text(
        '{"session":"run_fixture","trace":"../traces/main.jsonl"}\n',
        encoding="utf-8",
    )


def _enter_writer(path: str, start: multiprocessing.Event,
                  entered: multiprocessing.Event,
                  release: multiprocessing.Event) -> None:
    claim = acquire_session_writer(Path(path), owner="fixture")
    start.wait(timeout=10)
    store = SessionFile.open(Path(path), "run_fixture",
                            Path("../traces/main.jsonl"), "1.1.0")
    store.append("user", "hello", writer=claim)
    entered.set()
    release.wait(timeout=10)
    claim.release()


class SessionWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.path = self.base / "sessions" / "main.jsonl"
        self.path.parent.mkdir()
        _write_header(str(self.path))

    def test_second_owner_for_the_same_path_is_refused(self):
        first = acquire_session_writer(self.path)
        with self.assertRaises(SessionWriterBusy):
            acquire_session_writer(self.path)
        first.release()

    def test_different_paths_have_independent_owners(self):
        first = acquire_session_writer(self.path)
        other = self.base / "sessions" / "other.jsonl"
        _write_header(str(other))
        second = acquire_session_writer(other)
        first.release()
        second.release()

    def test_path_replacement_is_refused_by_the_owner_handle(self):
        first = acquire_session_writer(self.path)
        replacement = self.base / "sessions" / "replacement.jsonl"
        replacement.write_text(self.path.read_text(encoding="utf-8"),
                               encoding="utf-8")
        os.replace(str(replacement), str(self.path))
        with self.assertRaises(SessionWriterBusy):
            first.require_owned(self.path)
        with self.assertRaises(SessionFileError):
            first.bind_session_file(self.path)
        first.release()


    def test_session_symlink_is_refused_without_reading_the_target(self):
        target = self.base / "outside.jsonl"
        target.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        link = self.base / "sessions" / "linked.jsonl"
        link.symlink_to(target)
        before = target.read_bytes()
        with self.assertRaises(SessionWriterBusy):
            acquire_session_writer(link)
        self.assertEqual(before, target.read_bytes())
        self.assertFalse(link.with_name("linked.jsonl.writer").exists())

    def test_empty_session_keeps_identity_between_header_and_messages(self):
        writer = acquire_session_writer(self.path)
        self.path.unlink()
        store = SessionFile.open(self.path, "run_new",
                                Path("../traces/new.jsonl"), "1.1.0")
        header = self.path.read_text(encoding="utf-8")
        self.path.unlink()
        replacement = self.base / "sessions" / "replacement.jsonl"
        replacement.write_text(header, encoding="utf-8")
        os.replace(str(replacement), str(self.path))
        with self.assertRaises(SessionFileError):
            store.append("user", "hello")
        writer.release()

    def test_release_allows_a_later_owner(self):
        first = acquire_session_writer(self.path)
        first.release()
        second = acquire_session_writer(self.path)
        second.release()

    def test_foreign_owner_is_not_auto_stolen(self):
        first = acquire_session_writer(self.path)
        with self.assertRaises(SessionWriterBusy):
            acquire_session_writer(self.path, owner="foreign")
        first.release()

    def test_session_file_append_rejects_a_stale_writer_claim(self):
        first = acquire_session_writer(self.path)
        store = SessionFile.open(self.path, "run_fixture",
                                Path("../traces/main.jsonl"), "1.1.0")
        first.release()
        second = acquire_session_writer(self.path)
        with self.assertRaises(SessionWriterBusy):
            store.append("user", "hello", writer=first)
        second.release()

    def test_owned_session_file_append_still_works(self):
        writer = acquire_session_writer(self.path)
        store = SessionFile.open(self.path, "run_fixture",
                                Path("../traces/main.jsonl"), "1.1.0")
        store.append("user", "hello", writer=writer)
        writer.release()

    def test_independent_processes_are_serial_for_one_path(self):
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        entered = context.Event()
        release = context.Event()
        process = context.Process(target=_enter_writer, args=(
            str(self.path), start, entered, release))
        process.start()
        self.addCleanup(process.join, 10)
        deadline = time.time() + 5
        while not self.path.with_name("main.jsonl.writer").exists():
            self.assertLess(time.time(), deadline)
            time.sleep(0.01)
        start.set()
        self.assertTrue(entered.wait(timeout=10))
        with self.assertRaises(SessionWriterBusy):
            second = acquire_session_writer(self.path)
            second.release()
        release.set()
        process.join(10)
        self.assertEqual(process.exitcode, 0)
        after = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertNotEqual("", after)


    def test_two_stale_views_with_one_claim_cannot_duplicate_turns(self):
        writer = acquire_session_writer(self.path)
        first = SessionFile.open(self.path, "run_fixture",
                                Path("../traces/main.jsonl"), "1.1.0")
        stale = SessionFile.open(self.path, "run_fixture",
                                Path("../traces/main.jsonl"), "1.1.0")
        first.append("user", "hello", writer=writer)
        with self.assertRaises(SessionFileError):
            stale.append("user", "hello", writer=writer)
        writer.release()

    def test_hard_link_alias_is_refused(self):
        alias = self.base / "sessions" / "alias.jsonl"
        os.link(self.path, alias)
        with self.assertRaises(SessionWriterBusy):
            acquire_session_writer(alias)

    def test_initialization_failure_releases_only_its_claim(self):
        invalid = self.base / "sessions" / "invalid.jsonl"
        invalid.write_text("not a session\n", encoding="utf-8")
        config = {
            "model": "m", "base_url": "http://example.invalid/v1",
            "api_key_env": "UNUSED", "timeout_seconds": 5,
            "max_context_chars": 1000, "requires_api_key": False,
            "provider_adapter": CountingAdapter(),
        }
        with self.assertRaises(Exception):
            Session(config, self.base / "traces" / "invalid.jsonl",
                    "run_invalid", session_path=invalid)
        self.assertFalse(invalid.with_name("invalid.jsonl.writer").exists())

    def test_cancelled_and_failed_runs_release_their_claim(self):
        config = {
            "model": "m", "base_url": "http://example.invalid/v1",
            "api_key_env": "UNUSED", "timeout_seconds": 5,
            "max_context_chars": 1000, "summary_prefix_chars": 40,
            "requires_api_key": False,
            "provider_adapter": CountingAdapter(),
        }
        broken_config = dict(config, provider_adapter=BrokenAdapter())
        failed = Session(
            broken_config, self.base / "traces" / "failed.jsonl",
            "run_failed", session_path=self.path)
        self.assertEqual("failed", failed.run(["hello"]))
        self.assertFalse(self.path.with_name("main.jsonl.writer").exists())
        cancelled = Session(
            config, self.base / "traces" / "cancel.jsonl",
            "run_cancel", session_path=self.path)
        def interrupted():
            raise KeyboardInterrupt()
            yield "never"
        self.assertEqual("cancelled", cancelled.run(interrupted()))
        self.assertFalse(self.path.with_name("main.jsonl.writer").exists())

    def test_refused_writer_sends_zero_provider_requests(self):
        adapter = CountingAdapter()
        config = {
            "model": "m", "base_url": "http://example.invalid/v1",
            "api_key_env": "UNUSED", "timeout_seconds": 5,
            "max_context_chars": 1000,
            "summary_prefix_chars": 40, "requires_api_key": False,
            "provider_adapter": adapter,
        }
        first = Session(
            config, self.base / "traces" / "main.jsonl", "run_writer",
            session_path=self.path)
        with self.assertRaises(Exception) as refused:
            Session(
                config, self.base / "traces" / "other.jsonl", "run_other",
                session_path=self.path)
        self.assertIn("writer claim", str(refused.exception))
        self.assertEqual(0, adapter.requests)
        self.assertTrue(self.path.with_name("main.jsonl.writer").exists())
        self.assertEqual("completed", first.run(["hello"]))
        self.assertEqual(1, adapter.requests)

    def test_claim_file_must_not_be_a_replaced_directory(self):
        writer = acquire_session_writer(self.path)
        claim_path = self.path.with_name("main.jsonl.writer")
        claim_path.unlink()
        claim_path.mkdir()
        try:
            with self.assertRaises(SessionWriterBusy):
                acquire_session_writer(self.path)
        finally:
            claim_path.rmdir()
            writer.release()


if __name__ == "__main__":
    unittest.main()
