"""Pure context projection explanation: audit a bound projection, never redo it."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import unittest
from unittest import mock

from pocketchat.context_explain import explain_context_projection
from pocketchat.context_projection import project_context

LAYERS = ("fixed", "recent", "task", "memory", "tool")


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _source_payload():
    return {
        "instructions": [
            {"role": "system", "content": "Be brief."},
            {"role": "system", "content": "Use evidence."},
        ],
        "task_state": {"step_id": "next", "workspace_id": "ws_1"},
        "messages": [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "current question"},
            {"role": "assistant", "content": "current answer"},
        ],
        "evidence": [
            {"source": "memory/one", "content": "fact", "sensitive": False},
            {"source": "tool/read", "content": "tool result", "sensitive": False},
        ],
        "pending_messages": [
            {"role": "user", "content": "pending question"},
        ],
    }


def _descriptors_for(payload):
    descriptors = []
    for index in range(len(payload["instructions"])):
        descriptors.append({
            "source_id": f"fixed-{index}",
            "layer": "fixed",
            "locator": {"kind": "instruction", "index": index},
        })
    if payload["task_state"]:
        descriptors.append({
            "source_id": "task-state",
            "layer": "task",
            "locator": {"kind": "task_state", "index": 0},
        })
    for index in range(len(payload["messages"])):
        descriptors.append({
            "source_id": f"message-{index}",
            "layer": "recent",
            "locator": {"kind": "message", "index": index},
        })
    for index, item in enumerate(payload["evidence"]):
        descriptors.append({
            "source_id": f"evidence-{index}",
            "layer": "memory" if index == 0 else "tool",
            "locator": {"kind": "evidence", "index": index},
        })
    for index in range(len(payload["pending_messages"])):
        descriptors.append({
            "source_id": f"pending-{index}",
            "layer": "recent",
            "locator": {"kind": "pending_message", "index": index},
        })
    return descriptors


def _projection_input(payload=None, *, keep_recent_turns=0, max_chars=4000):
    source = payload if payload is not None else _source_payload()
    result = project_context(
        source,
        max_chars=max_chars,
        keep_recent_turns=keep_recent_turns,
        pending_messages=source["pending_messages"],
    )
    return {
        "schema_version": "pocketchat-context-explain-input.v1",
        "source": copy.deepcopy(source),
        "selection": {
            "max_chars": max_chars,
            "keep_recent_turns": keep_recent_turns,
        },
        "result": copy.deepcopy(result),
        "sources": _descriptors_for(source),
    }


def _finding(report, code):
    return [item for item in report["findings"] if item["code"] == code]


def _omission(report, code):
    return [item for item in report["omissions"] if item["code"] == code]


class ContextExplainTests(unittest.TestCase):
    def test_five_layers_are_complete_ordered_and_deterministic(self):
        projection = _projection_input()
        first = explain_context_projection(projection)
        second = explain_context_projection(copy.deepcopy(projection))

        self.assertEqual(first, second)
        self.assertEqual("complete", first["status"])
        self.assertEqual(tuple(first["layers"]), LAYERS)
        self.assertEqual([], first["findings"])
        self.assertEqual([], first["omissions"])
        self.assertEqual(
            ["fixed-0", "fixed-1", "message-0", "message-1", "message-2",
             "message-3", "pending-0", "task-state", "evidence-0", "evidence-1"],
            [item["source_id"] for item in first["observations"]],
        )
        self.assertTrue(all("content" not in item for item in first["observations"]))

    def test_source_and_projection_digest_drift_are_distinct_refusals(self):
        projection = _projection_input()
        projection["source"]["instructions"][0]["content"] = "changed"
        report = explain_context_projection(projection)
        self.assertEqual("refused", report["status"])
        self.assertEqual("source_digest_mismatch", report["findings"][0]["code"])

        projection = _projection_input()
        projection["result"]["messages"][0]["content"] = "changed"
        report = explain_context_projection(projection)
        self.assertEqual("refused", report["status"])
        self.assertEqual("projection_digest_mismatch", report["findings"][0]["code"])

    def test_duplicate_missing_and_unexpected_source_ids_refuse(self):
        projection = _projection_input()
        projection["sources"][1]["source_id"] = projection["sources"][0]["source_id"]
        report = explain_context_projection(projection)
        self.assertEqual("refused", report["status"])
        self.assertTrue(_finding(report, "duplicate_source_id"))

        projection = _projection_input()
        projection["sources"].pop()
        report = explain_context_projection(projection)
        self.assertEqual("refused", report["status"])
        self.assertTrue(_finding(report, "missing_source_descriptor"))

        projection = _projection_input()
        projection["sources"].append({
            "source_id": "extra",
            "layer": "fixed",
            "locator": {"kind": "instruction", "index": 99},
        })
        report = explain_context_projection(projection)
        self.assertEqual("refused", report["status"])
        self.assertTrue(_finding(report, "unexpected_source_descriptor"))

    def test_sensitive_memory_and_tool_findings_redact_all_bodies(self):
        payload = _source_payload()
        payload["evidence"][0]["sensitive"] = True
        payload["evidence"][1]["sensitive"] = True
        projection = _projection_input(payload)
        report = explain_context_projection(projection)

        self.assertEqual("partial", report["status"])
        self.assertEqual(2, len(_finding(report, "sensitive_source")))
        self.assertNotIn("fact", json.dumps(report, ensure_ascii=False))
        self.assertNotIn("tool result", json.dumps(report, ensure_ascii=False))
        self.assertNotIn("question", json.dumps(report, ensure_ascii=False))

    def test_finding_and_omission_limits_refuse_instead_of_truncating(self):
        payload = _source_payload()
        payload["evidence"][0]["sensitive"] = True
        payload["evidence"][1]["sensitive"] = True
        projection = _projection_input(payload)
        report = explain_context_projection(projection, max_findings=1)
        self.assertEqual("refused", report["status"])
        self.assertTrue(_finding(report, "finding_limit_exceeded"))

        projection = _projection_input(keep_recent_turns=1)
        report = explain_context_projection(projection, max_omissions=1)
        self.assertEqual("refused", report["status"])
        self.assertTrue(_finding(report, "omission_limit_exceeded"))

    def test_report_limit_returns_bounded_refusal(self):
        projection = _projection_input()
        report = explain_context_projection(projection, max_bytes=512)
        self.assertEqual("refused", report["status"])
        self.assertTrue(_finding(report, "report_limit_exceeded"))

    def test_closed_input_schema_and_unknown_versions_refuse(self):
        projection = _projection_input()
        projection["extra"] = True
        report = explain_context_projection(projection)
        self.assertEqual("refused", report["status"])
        self.assertTrue(_finding(report, "invalid_input_shape"))

        projection = _projection_input()
        projection["schema_version"] = "unknown.v9"
        report = explain_context_projection(projection)
        self.assertEqual("refused", report["status"])
        self.assertTrue(_finding(report, "unknown_input_schema"))

        projection = _projection_input()
        projection["result"]["manifest"]["schema_version"] = "unknown.v9"
        projection["result"]["manifest"]["projection_sha256"] = _digest({
            "messages": projection["result"]["messages"],
            "manifest": {
                key: value for key, value in projection["result"]["manifest"].items()
                if key != "projection_sha256"
            },
        })
        report = explain_context_projection(projection)
        self.assertEqual("refused", report["status"])
        self.assertTrue(_finding(report, "unknown_projection_schema"))

    def test_unmatched_sidecar_evidence_refuses_without_echoing_body(self):
        projection = _projection_input()
        manifest = projection["result"]["manifest"]
        manifest["evidence"].append({
            "source": "unexpected",
            "content": "private body",
        })
        manifest["projection_sha256"] = _digest({
            "messages": projection["result"]["messages"],
            "manifest": {
                key: value for key, value in manifest.items()
                if key != "projection_sha256"
            },
        })
        report = explain_context_projection(projection)

        self.assertEqual("refused", report["status"])
        self.assertTrue(_finding(report, "projection_shape_mismatch"))
        self.assertNotIn(
            "private body", json.dumps(report, ensure_ascii=False)
        )

    def test_dropped_reason_codes_cannot_echo_bodies(self):
        projection = _projection_input()
        manifest = projection["result"]["manifest"]
        manifest["evidence"] = manifest["evidence"][:1]
        manifest["dropped"] = [{
            "source": projection["source"]["evidence"][1]["source"],
            "reason": "SECRET_BODY_AS_REASON",
        }]
        manifest["projection_sha256"] = _digest({
            "messages": projection["result"]["messages"],
            "manifest": {
                key: value for key, value in manifest.items()
                if key != "projection_sha256"
            },
        })
        report = explain_context_projection(projection)

        self.assertEqual("refused", report["status"])
        self.assertTrue(_finding(report, "invalid_projection_shape"))
        self.assertNotIn(
            "SECRET_BODY_AS_REASON", json.dumps(report, ensure_ascii=False)
        )

    def test_non_string_dropped_reasons_refuse_without_raising(self):
        canary = "PRIVATE_REASON_BODY"
        reasons = ([], {}, [canary], {"body": canary}, None, True, 17)
        for reason in reasons:
            with self.subTest(reason_type=type(reason).__name__, reason=reason):
                projection = _projection_input()
                manifest = projection["result"]["manifest"]
                manifest["dropped"] = [{
                    "source": projection["source"]["evidence"][1]["source"],
                    "reason": reason,
                }]
                manifest["projection_sha256"] = _digest({
                    "messages": projection["result"]["messages"],
                    "manifest": {
                        key: value for key, value in manifest.items()
                        if key != "projection_sha256"
                    },
                })
                original = copy.deepcopy(projection)
                try:
                    report = explain_context_projection(projection)
                except TypeError as error:
                    self.fail(f"malformed reason escaped refusal: {error}")

                self.assertEqual("refused", report["status"])
                self.assertTrue(_finding(report, "invalid_projection_shape"))
                self.assertNotIn(canary, json.dumps(report, ensure_ascii=False))
                self.assertEqual(original, projection)

    def test_empty_valid_projection_is_complete_with_all_layer_summaries(self):
        payload = {
            "instructions": [],
            "task_state": {},
            "messages": [],
            "evidence": [],
            "pending_messages": [],
        }
        projection = _projection_input(payload)
        report = explain_context_projection(projection)

        self.assertEqual("complete", report["status"])
        self.assertEqual(tuple(report["layers"]), LAYERS)
        self.assertEqual([], report["observations"])

    def test_missing_sidecar_metadata_is_partial_and_named(self):
        projection = _projection_input()
        manifest = projection["result"]["manifest"]
        manifest.pop("kept_turns")
        manifest.pop("dropped")
        manifest["projection_sha256"] = _digest({
            "messages": projection["result"]["messages"],
            "manifest": {
                key: value for key, value in manifest.items()
                if key != "projection_sha256"
            },
        })
        report = explain_context_projection(projection)

        self.assertEqual("partial", report["status"])
        self.assertTrue(_omission(report, "sidecar_metadata_unavailable"))

    def test_inputs_are_unchanged_and_no_io_command_or_socket_is_used(self):
        projection = _projection_input()
        original = copy.deepcopy(projection)
        with mock.patch("builtins.open") as opened, \
                mock.patch.object(subprocess, "run") as run, \
                mock.patch("socket.socket") as socket_factory:
            report = explain_context_projection(projection)

        self.assertEqual("complete", report["status"])
        self.assertEqual(original, projection)
        opened.assert_not_called()
        run.assert_not_called()
        socket_factory.assert_not_called()

    def test_invalid_limits_refuse_without_partial_envelopes(self):
        projection = _projection_input()
        for kwargs in (
            {"max_findings": -1},
            {"max_omissions": True},
            {"max_bytes": -1},
            {"max_findings": 1.5},
        ):
            report = explain_context_projection(projection, **kwargs)
            self.assertEqual("refused", report["status"])
            self.assertTrue(_finding(report, "invalid_limit"))


if __name__ == "__main__":
    unittest.main()
