"""Layered context projection tests, independent of provider I/O."""

from __future__ import annotations

import unittest

from pocketchat.context_projection import ContextProjectionError, project_context


class ContextProjectionTests(unittest.TestCase):
    def _payload(self):
        return {
            "instructions": [{"role": "system", "content": "Be brief."}],
            "task_state": {"step_id": "next", "evidence": []},
            "messages": [
                {"role": "user", "content": "old question"},
                {"role": "assistant", "content": "old answer"},
                {"role": "user", "content": "current question"},
            ],
            "evidence": [{"source": "facts.txt", "content": "fact"}],
        }

    def test_layers_are_ordered_and_complete_exchanges_survive(self):
        projection = project_context(
            self._payload(), max_chars=1000, keep_recent_turns=1)
        self.assertEqual("system", projection["messages"][0]["role"])
        self.assertIn("current question", str(projection["messages"]))
        self.assertEqual("facts.txt", projection["manifest"]["evidence"][0]["source"])

    def test_unrelated_evidence_and_duplicate_sources_are_dropped(self):
        payload = self._payload()
        payload["evidence"] = [
            {"source": "a.txt", "content": "a"},
            {"source": "a.txt", "content": "a"},
        ]
        projection = project_context(payload, max_chars=1000)
        self.assertEqual(1, len(projection["manifest"]["evidence"]))
        self.assertEqual("duplicate_source",
                         projection["manifest"]["dropped"][0]["reason"])

    def test_fixed_layers_that_do_not_fit_are_refused(self):
        payload = self._payload()
        payload["instructions"] = [{"role": "system", "content": "x" * 1000}]
        with self.assertRaises(ContextProjectionError) as caught:
            project_context(payload, max_chars=100)
        self.assertEqual("fixed_context_too_large", caught.exception.error_type)

    def test_tool_exchange_is_dropped_as_a_unit(self):
        payload = self._payload()
        payload["messages"] = [
            {"role": "user", "content": "read"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "call_1", "type": "function",
                              "function": {"name": "read_file",
                                           "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "name": "read_file",
             "content": "result"},
            {"role": "user", "content": "current"},
        ]
        projection = project_context(
            payload, max_chars=400, keep_recent_turns=1)
        roles = [item["role"] for item in projection["messages"]]
        self.assertNotIn("tool", roles)
        self.assertIn("current", str(projection["messages"]))

    def test_projection_is_deterministic(self):
        payload = self._payload()
        first = project_context(payload, max_chars=1000)
        second = project_context(payload, max_chars=1000)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
