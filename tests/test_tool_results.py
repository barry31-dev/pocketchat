"""Bounded result projection tests for tool output sent back to a model."""

from __future__ import annotations

import json
import unittest

from pocketchat.tool_results import ResultProjectionError, project_result


class ResultProjectionTests(unittest.TestCase):
    def test_large_text_is_cut_to_fit_the_complete_json_envelope(self):
        raw = {"path": "facts.txt", "content": "x" * 4096,
               "bytes": 4096, "truncated": False}
        projection = project_result(
            "call_1", "read_file", raw, max_bytes=512)
        encoded = projection.encoded.encode("utf-8")
        decoded = json.loads(projection.encoded)
        self.assertLessEqual(len(encoded), 512)
        self.assertEqual(decoded["tool_call_id"], "call_1")
        self.assertEqual(decoded["status"], "completed")
        self.assertEqual(decoded["source"], "facts.txt")
        self.assertTrue(decoded["truncated"])
        self.assertEqual("read_file", decoded["kind"])
        self.assertGreater(projection.bytes_omitted, 0)

    def test_projection_does_not_mutate_the_tool_result(self):
        raw = {"path": "facts.txt", "content": "hello", "bytes": 5,
               "truncated": False}
        before = json.loads(json.dumps(raw))
        project_result("call_1", "read_file", raw, max_bytes=512)
        self.assertEqual(before, raw)

    def test_cjk_cut_is_valid_utf8(self):
        raw = {"path": "中文.txt", "content": "你" * 200,
               "bytes": 600, "truncated": False}
        projection = project_result(
            "call_1", "read_file", raw, max_bytes=240)
        self.assertLessEqual(len(projection.encoded.encode("utf-8")), 240)
        json.loads(projection.encoded)

    def test_minimum_metadata_that_cannot_fit_is_refused(self):
        raw = {"path": "facts.txt", "content": "hello"}
        with self.assertRaises(ResultProjectionError) as caught:
            project_result("call_" + "x" * 200, "read_file", raw,
                           max_bytes=32)
        self.assertEqual("result_projection_too_small", caught.exception.error_type)

    def test_write_confirmation_can_omit_the_content_echo(self):
        raw = {"path": "new.txt", "bytes": 4, "content": "data",
               "overwritten": False}
        projection = project_result(
            "call_1", "write_file", raw, max_bytes=512,
            omit_content_echo=True, effect_disposition="applied")
        decoded = json.loads(projection.encoded)
        self.assertNotIn("data", projection.encoded)
        self.assertEqual("applied", decoded["effect_disposition"])
        self.assertEqual("new.txt", decoded["source"])

    def test_exact_fit_keeps_a_small_result_complete(self):
        raw = {"path": "facts.txt", "content": "hello", "bytes": 5,
               "truncated": False}
        unbounded = project_result("call_1", "read_file", raw,
                                   max_bytes=4096)
        exact = project_result("call_1", "read_file", raw,
                               max_bytes=len(unbounded.encoded.encode("utf-8")))
        self.assertEqual(unbounded.encoded, exact.encoded)
        self.assertFalse(json.loads(exact.encoded)["truncated"])
        self.assertEqual(0, exact.bytes_omitted)

    def test_memory_whole_record_selection_includes_all_envelope_overhead(self):
        raw = {"items": [{"id": "a", "text": "x" * 220}]}
        self.assertLess(len(json.dumps(raw).encode("utf-8")), 300)
        projected = project_result("call_1", "memory_search", raw, max_bytes=300)
        result = json.loads(projected.encoded)
        self.assertLessEqual(len(projected.encoded.encode("utf-8")), 300)
        self.assertEqual([], result["items"])
        self.assertEqual(1, result["omitted_items"])
        self.assertEqual(result["bytes_omitted"], projected.bytes_omitted)

    def test_non_object_result_is_refused(self):
        with self.assertRaises(ResultProjectionError) as caught:
            project_result("call_1", "read_file", [], max_bytes=512)
        self.assertEqual("invalid_tool_result", caught.exception.error_type)


if __name__ == "__main__":
    unittest.main()
