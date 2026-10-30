"""Provider context estimation includes tool message metadata."""

from __future__ import annotations

import unittest

from pocketchat.provider import estimate_tokens


class EstimateToolContextTests(unittest.TestCase):
    def test_tool_call_metadata_counts_toward_estimated_tokens(self):
        plain = [{"role": "assistant", "content": ""}]
        with_tool_calls = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file",
                             "arguments": "{\"path\":\"facts.txt\"}"},
            }],
        }]
        self.assertGreater(estimate_tokens(with_tool_calls),
                           estimate_tokens(plain))


if __name__ == "__main__":
    unittest.main()
