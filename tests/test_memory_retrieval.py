"""Deterministic memory-v2 retrieval tests."""

from __future__ import annotations

import datetime
import hashlib
import unittest

from pocketchat.memory_migration import parse_v2_record
from pocketchat.memory_retrieval import (
    MemoryRetrievalError,
    recall_context_message,
    recall_evidence_items,
    retrieve_memories,
)


def memory_record(memory_id, content, *, updated="2026-01-02T00:00:00.000Z",
                  workspace_id="workspace-1", admission_status="accepted",
                  accepted_by="user", tags=None, sensitive=False,
                  expires_at=None, retired_at=None):
    record = {
        "schema_version": "pocketchat-memory.v2",
        "memory_id": memory_id,
        "revision": 1,
        "workspace_id": workspace_id,
        "category": "user_preference",
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "created": "2026-01-01T00:00:00.000Z",
        "updated": updated,
        "source_kind": "user_explicit",
        "source_refs": [],
        "verification_status": "not_required",
        "admission_status": admission_status,
        "accepted_by": accepted_by,
        "supersedes": [],
        "sensitive": sensitive,
    }
    if tags is not None:
        record["tags"] = tags
    if expires_at is not None:
        record["expires_at"] = expires_at
    if retired_at is not None:
        record["retired_at"] = retired_at
    return parse_v2_record(record)


def retrieval(records, query="alpha", **kwargs):
    options = {
        "workspace_id": "workspace-1",
        "now": datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        "max_results": 10,
        "max_excerpt_bytes": 4096,
        "max_recall_tokens": 1024,
    }
    options.update(kwargs)
    return retrieve_memories(records, query, **options)


class MemoryRetrievalTests(unittest.TestCase):
    def test_admission_is_applied_before_top_k(self):
        admitted = memory_record("mem_old", "alpha", updated="2026-01-01T00:00:00Z")
        candidate = memory_record(
            "mem_new", "alpha", updated="2026-02-01T00:00:00Z",
            admission_status="candidate", accepted_by="")
        result = retrieval([candidate, admitted], max_results=1)
        self.assertEqual(["mem_old"], [item["memory_id"] for item in result["items"]])
        self.assertEqual(
            [{"memory_id": "mem_new", "reasons": ["not_accepted"]}],
            result["excluded"])

    def test_workspace_expiry_sensitive_and_lifecycle_are_excluded(self):
        records = [
            memory_record("mem_workspace", "alpha", workspace_id="other"),
            memory_record("mem_sensitive", "alpha", sensitive=True),
            memory_record("mem_expired", "alpha", expires_at="2026-05-01T00:00:00Z"),
            memory_record(
                "mem_retired", "alpha", admission_status="retired",
                accepted_by="", retired_at="2026-02-01T00:00:00Z"),
        ]
        result = retrieval(records)
        self.assertEqual([], result["items"])
        self.assertEqual(
            {
                "mem_workspace": ["workspace_mismatch"],
                "mem_sensitive": ["sensitive"],
                "mem_expired": ["expired"],
                "mem_retired": ["not_accepted"],
            },
            {item["memory_id"]: item["reasons"] for item in result["excluded"]})

    def test_english_casefold_and_chinese_bigrams_have_fixed_rules(self):
        records = [
            memory_record("mem_zh", "这个 MEMORY检索 很重要"),
            memory_record("mem_en", "A memory-only note"),
        ]
        result = retrieval(records, query="memory检索")
        self.assertEqual(
            ["mem_zh", "mem_en"],
            [item["memory_id"] for item in result["items"]])
        self.assertEqual(["memory", "检索"], result["query_terms"])
        self.assertEqual("lexical-zh-en.v1", result["tokenizer_version"])
        self.assertTrue(result["items"][0]["exact_phrase"])
        self.assertEqual(["memory", "检索"], result["items"][0]["matched_terms"])

        reversed_result = retrieval(list(reversed(records)), query="memory检索")
        self.assertEqual(result["items"], reversed_result["items"])

    def test_exact_phrase_then_coverage_then_tags_then_stable_identity(self):
        records = [
            memory_record("mem_tag", "unrelated", tags=["alpha"]),
            memory_record("mem_coverage", "beta middle alpha"),
            memory_record("mem_exact", "alpha beta",
                      updated="2026-01-01T00:00:00Z"),
            memory_record("mem_tie_b", "alpha beta", updated="2026-01-02T00:00:00Z"),
            memory_record("mem_tie_a", "alpha beta", updated="2026-01-02T00:00:00Z"),
        ]
        result = retrieval(records, query="alpha beta")
        self.assertEqual(
            ["mem_tie_a", "mem_tie_b", "mem_exact", "mem_coverage", "mem_tag"],
            [item["memory_id"] for item in result["items"]])
        self.assertEqual(
            ["alpha", "beta"], result["items"][0]["matched_terms"])
        self.assertEqual(["alpha"], result["items"][-1]["matched_tags"])

    def test_items_return_an_excerpt_without_the_full_content(self):
        content = "alpha " + "x" * 200
        result = retrieval([memory_record("mem_a", content)],
                           max_excerpt_bytes=8)
        self.assertEqual("alpha xx", result["items"][0]["excerpt"])
        self.assertNotIn("content", result["items"][0])
        self.assertNotIn(content, repr(result))

    def test_byte_and_token_budgets_bound_excerpts_independently(self):
        records = [
            memory_record("mem_cjk", "中文内容"),
            memory_record("mem_next", "中文下一"),
        ]
        byte_limited = retrieval(
            records, query="中文", max_results=10,
            max_excerpt_bytes=3, max_recall_tokens=100)
        self.assertEqual(["mem_cjk"], [item["memory_id"] for item in byte_limited["items"]])
        self.assertEqual("中", byte_limited["items"][0]["excerpt"])
        self.assertTrue(byte_limited["items"][0]["excerpt_truncated"])
        self.assertEqual(1, byte_limited["omissions"]["byte_budget"])

        token_limited = retrieval(
            records, query="中文", max_results=10,
            max_excerpt_bytes=4096, max_recall_tokens=1)
        self.assertEqual(["mem_cjk"], [item["memory_id"] for item in token_limited["items"]])
        self.assertEqual("中", token_limited["items"][0]["excerpt"])
        self.assertEqual(1, token_limited["omissions"]["token_budget"])

    def test_cursor_paginates_stably_and_refuses_a_changed_match_set(self):
        records = [
            memory_record("mem_a", "alpha first"),
            memory_record("mem_b", "alpha second"),
        ]
        first = retrieval(records, max_results=1)
        self.assertEqual(["mem_a"], [item["memory_id"] for item in first["items"]])
        self.assertTrue(first["next_cursor"])
        second = retrieval(records, max_results=1, cursor=first["next_cursor"])
        self.assertEqual(["mem_b"], [item["memory_id"] for item in second["items"]])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(1, second["omissions"]["before_cursor"])

        shuffled_first = retrieval(list(reversed(records)), max_results=1)
        self.assertEqual(first["items"], shuffled_first["items"])
        self.assertEqual(first["next_cursor"], shuffled_first["next_cursor"])

        changed = records + [memory_record("mem_z", "alpha newest",
                                           updated="2026-03-01T00:00:00Z")]
        with self.assertRaises(MemoryRetrievalError) as caught:
            retrieval(changed, max_results=1, cursor=first["next_cursor"])
        self.assertEqual("memory_retrieval_cursor_stale",
                         caught.exception.error_type)

    def test_recall_projection_is_bounded_and_source_bound(self):
        content = "alpha " + "x" * 200
        result = retrieval([memory_record("mem_a", content)],
                           max_excerpt_bytes=8)
        evidence = recall_evidence_items(result)
        message = recall_context_message(result)
        self.assertEqual(["memory:workspace-1:mem_a:1:" + result["items"][0]["content_sha256"]],
                         [item["source"] for item in evidence])
        self.assertEqual("alpha xx", evidence[0]["content"])
        self.assertNotIn(content, repr(evidence))
        self.assertNotIn(content, repr(message))
        self.assertEqual("system", message["role"])
        self.assertIn("memory_recall", message["content"])
        self.assertEqual(evidence, recall_evidence_items(result))

    def test_empty_results_and_unrepresentable_budget_are_distinct(self):
        empty = retrieval([memory_record("mem_a", "other")])
        self.assertEqual("complete", empty["status"])
        self.assertEqual([], empty["items"])
        self.assertEqual("no_matches", empty["empty_reason"])

        budget = retrieval(
            [memory_record("mem_a", "中文")], query="中文",
            max_results=1, max_excerpt_bytes=1, max_recall_tokens=1)
        self.assertEqual("budget_exhausted", budget["status"])
        self.assertEqual("memory_retrieval_budget_exhausted", budget["error_type"])
        self.assertEqual([], budget["items"])
        self.assertEqual(1, budget["matched_count"])
        self.assertEqual(1, budget["omissions"]["byte_budget"])


if __name__ == "__main__":
    unittest.main()
