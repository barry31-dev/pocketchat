"""Pure bounded JSON decoding over caller-supplied bytes."""

from __future__ import annotations

import copy
import os
import unittest
from unittest import mock

from pocketchat.json_input import JSONInputError, decode_json_bytes


class JSONInputTests(unittest.TestCase):
    def assert_refused(self, data, *, error_type, category,
                       max_bytes=None, max_depth=8, max_nodes=64):
        if max_bytes is None:
            max_bytes = len(data) if isinstance(data, bytes) else 64
        with self.assertRaises(JSONInputError) as caught:
            decode_json_bytes(
                data,
                max_bytes=max_bytes,
                max_depth=max_depth,
                max_nodes=max_nodes,
            )
        error = caught.exception
        self.assertEqual(error_type, error.error_type)
        self.assertEqual(category, error.category)
        return error

    def test_valid_values_preserve_python_values_and_input_bytes(self):
        data = (
            '{"message":"你好😀","items":[true,null,3,1.25],'
            '"details":{"enabled":false}}'
        ).encode("utf-8")
        before = copy.deepcopy(data)

        decoded = decode_json_bytes(
            data,
            max_bytes=len(data),
            max_depth=2,
            max_nodes=9,
        )

        self.assertEqual({
            "message": "你好😀",
            "items": [True, None, 3, 1.25],
            "details": {"enabled": False},
        }, decoded)
        self.assertEqual(before, data)

    def test_scalar_roots_have_depth_zero_and_count_as_one_node(self):
        values = ((b"null", None), (b"true", True), (b"0", 0),
                  ('"文本😀"'.encode("utf-8"), "文本😀"))
        for data, expected in values:
            with self.subTest(data=data):
                self.assertEqual(
                    expected,
                    decode_json_bytes(
                        data,
                        max_bytes=len(data),
                        max_depth=1,
                        max_nodes=1,
                    ),
                )

    def test_data_and_limits_must_be_exact_bytes_and_positive_integers(self):
        cases = [
            (bytearray(b"{}"), 2, 1, 1),
            ("{}", 2, 1, 1),
            (b"{}", True, 1, 1),
            (b"{}", 2, False, 1),
            (b"{}", 2, 1, True),
            (b"{}", 0, 1, 1),
            (b"{}", 2, -1, 1),
            (b"{}", 2, 65, 1),
            (b"{}", 2, 1, 1.5),
        ]
        for data, max_bytes, max_depth, max_nodes in cases:
            with self.subTest(data_type=type(data), limits=(
                    max_bytes, max_depth, max_nodes)):
                error = self.assert_refused(
                    data,
                    error_type="invalid_limits",
                    category="limit",
                    max_bytes=max_bytes,
                    max_depth=max_depth,
                    max_nodes=max_nodes,
                )
                self.assertIsNone(error.offset)
                self.assertIsNone(error.path)

    def test_byte_limit_is_inclusive_and_checked_before_decoding(self):
        data = b'"\xffprivate"'
        error = self.assert_refused(
            data,
            error_type="byte_limit_exceeded",
            category="limit",
            max_bytes=len(data) - 1,
        )
        self.assertIsNone(error.offset)
        self.assertIsNone(error.path)

        valid = '"你好😀"'.encode("utf-8")
        self.assertEqual(
            "你好😀",
            decode_json_bytes(
                valid,
                max_bytes=len(valid),
                max_depth=1,
                max_nodes=1,
            ),
        )

    def test_invalid_utf8_reports_only_the_byte_offset(self):
        data = b'"\xffprivate-value"'
        error = self.assert_refused(
            data,
            error_type="invalid_utf8",
            category="encoding",
        )
        self.assertEqual(1, error.offset)
        self.assertIsNone(error.path)
        self.assertNotIn("private", str(error))

    def test_duplicate_keys_are_refused_at_any_depth_without_echo(self):
        canary = "secret-member-name"
        data = ('{"outer":{"%s":1,"%s":2}}' % (canary, canary)).encode()
        error = self.assert_refused(
            data,
            error_type="duplicate_key",
            category="structure",
        )
        self.assertIsNone(error.offset)
        self.assertIsNone(error.path)
        self.assertNotIn(canary, str(error))
        self.assertNotIn(canary, repr(error.__dict__))

    def test_nonfinite_numbers_are_refused(self):
        for data in (b"NaN", b"Infinity", b"-Infinity", b"1e400"):
            with self.subTest(data=data):
                error = self.assert_refused(
                    data,
                    error_type="nonfinite_number",
                    category="number",
                )
                self.assertNotIn(data.decode("ascii"), str(error))

    def test_trailing_value_and_invalid_json_have_distinct_metadata(self):
        trailing = self.assert_refused(
            b"{} []",
            error_type="trailing_value",
            category="syntax",
        )
        self.assertEqual(3, trailing.offset)
        self.assertIsNone(trailing.path)

        malformed = self.assert_refused(
            b'{"private":}',
            error_type="invalid_json",
            category="syntax",
        )
        self.assertIsInstance(malformed.offset, int)
        self.assertIsNone(malformed.path)
        self.assertNotIn("private", str(malformed))

    def test_container_depth_limit_is_inclusive(self):
        data = b'{"outer":[{"inner":1}]}'
        self.assertEqual(
            {"outer": [{"inner": 1}]},
            decode_json_bytes(
                data,
                max_bytes=len(data),
                max_depth=3,
                max_nodes=4,
            ),
        )

        error = self.assert_refused(
            data,
            error_type="depth_limit_exceeded",
            category="limit",
            max_depth=2,
            max_nodes=4,
        )
        self.assertEqual((('object', 0), ('array', 0)), error.path)
        self.assertIsNone(error.offset)

    def test_maximum_supported_depth_is_inclusive(self):
        data = b"[" * 64 + b"0" + b"]" * 64
        decoded = decode_json_bytes(
            data,
            max_bytes=len(data),
            max_depth=64,
            max_nodes=65,
        )
        for _ in range(64):
            self.assertIsInstance(decoded, list)
            decoded = decoded[0]
        self.assertEqual(0, decoded)

        error = self.assert_refused(
            data,
            error_type="depth_limit_exceeded",
            category="limit",
            max_depth=63,
            max_nodes=65,
        )
        self.assertEqual(63, len(error.path))

    def test_node_limit_counts_root_values_and_not_object_keys(self):
        data = b'{"outer":[{"inner":1}]}'
        self.assertEqual(
            {"outer": [{"inner": 1}]},
            decode_json_bytes(
                data,
                max_bytes=len(data),
                max_depth=3,
                max_nodes=4,
            ),
        )
        error = self.assert_refused(
            data,
            error_type="node_limit_exceeded",
            category="limit",
            max_depth=3,
            max_nodes=3,
        )
        self.assertEqual(
            (('object', 0), ('array', 0), ('object', 0)),
            error.path,
        )

        containers = b'{"first":{},"second":[]}'
        self.assertEqual(
            {"first": {}, "second": []},
            decode_json_bytes(
                containers,
                max_bytes=len(containers),
                max_depth=2,
                max_nodes=3,
            ),
        )

    def test_decoder_performs_no_external_io_or_provider_call(self):
        data = b'{"value":[1,2,3]}'
        with mock.patch("builtins.open", side_effect=AssertionError("open called")), \
                mock.patch.object(os.environ, "get",
                                  side_effect=AssertionError("environment read")), \
                mock.patch("os.getenv",
                           side_effect=AssertionError("environment read")), \
                mock.patch("socket.socket",
                           side_effect=AssertionError("socket called")), \
                mock.patch("subprocess.run",
                           side_effect=AssertionError("command called")), \
                mock.patch("pocketchat.provider.complete",
                           side_effect=AssertionError("provider called")):
            decoded = decode_json_bytes(
                data,
                max_bytes=len(data),
                max_depth=2,
                max_nodes=5,
            )
        self.assertEqual({"value": [1, 2, 3]}, decoded)


if __name__ == "__main__":
    unittest.main()
