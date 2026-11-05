"""Pure bounded JSON decoding over caller-supplied bytes."""

from __future__ import annotations

import copy
import os
import sys
import traceback
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

    def test_structural_limits_refuse_before_materializing_the_value(self):
        cases = (
            (b"[" * 65 + b"0" + b"]" * 65, 64, 66,
             "depth_limit_exceeded", (("array", 0),) * 64),
            (b'{"private-key":[0,1]}', 2, 3,
             "node_limit_exceeded", (("object", 0), ("array", 1))),
            (b'["one","two"]', 1, 2,
             "node_limit_exceeded", (("array", 1),)),
        )
        for data, depth, nodes, error_type, path in cases:
            with self.subTest(error_type=error_type, nodes=nodes):
                # A post-decode walk cannot enforce a parsing resource budget:
                # no whole-value materialization may happen for these inputs.
                with mock.patch(
                    "pocketchat.json_input.json.JSONDecoder.raw_decode",
                    side_effect=AssertionError("value decoded before admission"),
                ):
                    error = self.assert_refused(
                        data, error_type=error_type, category="limit",
                        max_depth=depth, max_nodes=nodes,
                    )
                self.assertEqual(path, error.path)
                self.assertNotIn("private-key", repr(error.__dict__))

    def test_admission_ignores_delimiters_in_escaped_keys_and_strings(self):
        data = br'{"a\"[,]":{"\\":["\"}{,:[]",0]}}'
        self.assertEqual(
            {'a"[,]': {'\\': ['"}{,:[]', 0]}},
            decode_json_bytes(data, len(data), 3, 5),
        )

    def test_admission_preserves_syntax_and_trailing_refusals(self):
        for data in (b'{"key" 1}', b'[1,]', b'{,}', b'"unterminated',
                     br'"invalid\q"', b'{"key":}', b'[0 1]'):
            with self.subTest(data=data):
                self.assert_refused(
                    data, error_type="invalid_json", category="syntax",
                )
        self.assert_refused(
            b'0 ' + b'[' * 65 + b'0' + b']' * 65,
            error_type="trailing_value", category="syntax",
            max_depth=1, max_nodes=1,
        )

    def test_refusals_do_not_retain_decoder_input_in_exception_context(self):
        for data, error_type, category in (
            (b'"private-source\xff"', "invalid_utf8", "encoding"),
            (br'"private-source\q"', "invalid_json", "syntax"),
            (b'{"private-source":1,"private-source":2}',
             "duplicate_key", "structure"),
        ):
            with self.subTest(error_type=error_type):
                error = self.assert_refused(
                    data, error_type=error_type, category=category,
                )
                self.assertIsNone(error.__context__)
                self.assertIsNone(error.__cause__)
                self.assertNotIn("private-source", repr(error.__dict__))

    def test_refusals_suppress_an_exception_already_handled_by_the_caller(self):
        cases = (
            (b"null", 4, 0, 1, "invalid_limits"),
            (b"null", 3, 1, 1, "byte_limit_exceeded"),
            (b'"\xff"', 3, 1, 1, "invalid_utf8"),
            (br'"\q"', 4, 1, 1, "invalid_json"),
            (b'0 1', 3, 1, 1, "trailing_value"),
            (b'NaN', 3, 1, 1, "nonfinite_number"),
            (b'1e400', 5, 1, 1, "nonfinite_number"),
            (b'{"key":0,"key":1}', 17, 1, 3, "duplicate_key"),
            (b'[[0]]', 5, 1, 3, "depth_limit_exceeded"),
            (b'[0]', 3, 1, 1, "node_limit_exceeded"),
        )
        canary = "outer-exception-private-canary"
        for data, byte_limit, depth, nodes, error_type in cases:
            with self.subTest(error_type=error_type, data=data):
                try:
                    raise ValueError(canary)
                except ValueError:
                    try:
                        decode_json_bytes(data, byte_limit, depth, nodes)
                    except JSONInputError as error:
                        self.assertEqual(error_type, error.error_type)
                        rendered = "".join(traceback.format_exception(
                            type(error), error, error.__traceback__))
                        self.assertNotIn(canary, rendered)
                        self.assertTrue(error.__suppress_context__)
                        self.assertIsNone(error.__cause__)
                    else:
                        self.fail("expected a bounded JSON refusal")

    def test_large_finite_integers_preserve_value_without_changing_global_guard(self):
        get_guard = getattr(sys, "get_int_max_str_digits", lambda: None)
        guard_before = get_guard()
        expected = 10 ** 4300
        for prefix, value in ((b"", expected), (b"-", -expected)):
            data = prefix + b"1" + b"0" * 4300
            with self.subTest(negative=bool(prefix)):
                decoded = decode_json_bytes(data, len(data), 1, 1)
                self.assertIs(type(decoded), int)
                self.assertTrue(decoded == value)
        self.assertEqual(guard_before, get_guard())

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
