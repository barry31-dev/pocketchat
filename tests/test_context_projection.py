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

    def test_orphan_tool_results_are_refused(self):
        payload = self._payload()
        payload["messages"] = [
            {"role": "user", "content": "current"},
            {"role": "tool", "tool_call_id": "missing", "content": "result"},
        ]
        with self.assertRaises(ContextProjectionError) as caught:
            project_context(payload, max_chars=1000)
        self.assertEqual("incomplete_tool_exchange", caught.exception.error_type)

    def test_source_digest_changes_when_a_file_evidence_value_changes(self):
        first = project_context(self._payload(), max_chars=1000)
        payload = self._payload()
        payload["evidence"][0]["content"] = "different fact"
        second = project_context(payload, max_chars=1000)
        self.assertNotEqual(first["manifest"]["source_sha256"],
                            second["manifest"]["source_sha256"])
        self.assertNotEqual(first["manifest"]["projection_sha256"],
                            second["manifest"]["projection_sha256"])

    def test_tool_results_cannot_precede_their_calls(self):
        payload = self._payload()
        payload["messages"] = [
            {"role": "user", "content": "current"},
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
        ]
        with self.assertRaises(ContextProjectionError):
            project_context(payload, max_chars=1000)

    def test_projection_manifest_binds_exact_source_and_selected_messages(self):
        payload = self._payload()
        projection = project_context(payload, max_chars=1000)
        manifest = projection["manifest"]
        self.assertEqual("layered_v1", manifest["strategy"])
        self.assertEqual(64, len(manifest["source_sha256"]))
        self.assertEqual(64, len(manifest["projection_sha256"]))
        import hashlib
        import json
        source = {
            "instructions": payload["instructions"],
            "task_state": payload["task_state"],
            "messages": payload["messages"],
            "evidence": payload["evidence"],
            "pending_messages": [],
        }
        encoded = json.dumps(
            source, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")
        self.assertEqual(hashlib.sha256(encoded).hexdigest(),
                         manifest["source_sha256"])
        body = {"messages": projection["messages"], "manifest": manifest}
        self.assertEqual(projection, project_context(payload, max_chars=1000))
        self.assertEqual(body, {"messages": projection["messages"],
                                "manifest": manifest})

    def test_projection_does_not_share_mutable_messages_with_its_source(self):
        payload = self._payload()
        projection = project_context(payload, max_chars=1000)
        payload["messages"][-1]["content"] = "changed after projection"
        payload["evidence"][0]["content"] = "changed after projection"
        self.assertIn("current question", str(projection["messages"]))
        self.assertEqual("fact", projection["manifest"]["evidence"][0]["content"])

    def test_recent_turns_are_kept_as_complete_exchanges(self):
        payload = self._payload()
        payload["messages"] = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "new"},
            {"role": "assistant", "content": "new answer"},
        ]
        projection = project_context(
            payload, max_chars=1000, keep_recent_turns=1)
        self.assertEqual(
            ["system", "system", "user", "assistant"],
            [item["role"] for item in projection["messages"]])
        self.assertEqual("new", projection["messages"][2]["content"])
        self.assertEqual("new answer",
                         projection["messages"][3]["content"])

    def test_projection_is_deterministic(self):
        payload = self._payload()
        first = project_context(payload, max_chars=1000)
        second = project_context(payload, max_chars=1000)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
