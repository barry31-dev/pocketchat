"""Pure evidence-link verification: compare supplied observations, never read them."""

from __future__ import annotations

import copy
import unittest
from unittest import mock

from pocketchat.evidence_links import verify_evidence_links


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def artifact(
    artifact_id,
    kind,
    path,
    *,
    whole_file_sha256=DIGEST_A,
    semantic_sha256=DIGEST_B,
    semantic_scope="canonical-json",
    identity=None,
    coverage="complete",
    required=True,
):
    return {
        "id": artifact_id,
        "kind": kind,
        "path": path,
        "whole_file_sha256": whole_file_sha256,
        "observed_whole_file_sha256": whole_file_sha256,
        "semantic_sha256": semantic_sha256,
        "semantic_scope": semantic_scope,
        "identity": identity or {},
        "read_coverage": coverage,
        "required": required,
    }


def relation(relation_id, source, target, kind, fields):
    return {
        "id": relation_id,
        "source": source,
        "target": target,
        "kind": kind,
        "fields": fields,
        "required": True,
    }


def session_trace_request():
    artifacts = [
        artifact("session", "session", "sessions/main.jsonl",
                 identity={"run_id": "run_1", "conversation_id": "conv_1"}),
        artifact("trace", "trace", "traces/run_1.jsonl",
                 identity={"run_id": "run_1"}),
        artifact("request", "request", "traces/requests/run_1.turn-1.json",
                 semantic_scope="request-body-canonical",
                 identity={"run_id": "run_1", "turn": 1}),
    ]
    relations = [
        relation("session-trace", "session", "trace", "same-run",
                 ["run_id"]),
        relation("trace-request", "trace", "request", "same-run-turn",
                 ["run_id", "turn"]),
    ]
    return artifacts, relations


def comparison_run():
    artifacts = [
        artifact("comparison", "comparison", "comparisons/compare_1.json",
                 identity={"comparison_id": "compare_1", "run_ids": ["run_a"]}),
        artifact("run", "trace", "traces/run_a.jsonl",
                 identity={"run_id": "run_a"}),
    ]
    relations = [relation("comparison-run", "comparison", "run", "contains-run",
                          ["run_id"])]
    return artifacts, relations


def report_spec_plan_checkpoint():
    artifacts = [
        artifact("report", "execution_report", "reports/execution.json",
                 identity={"task_id": "task_1", "spec_sha256": DIGEST_A,
                           "plan_sha256": DIGEST_B}),
        artifact("spec", "request", "tasks/spec.json",
                 identity={"task_id": "task_1", "spec_sha256": DIGEST_A}),
        artifact("plan", "request", "tasks/plan.json",
                 identity={"task_id": "task_1", "plan_sha256": DIGEST_B}),
        artifact("checkpoint", "checkpoint", "checkpoints/task_1.json",
                 identity={"task_id": "task_1", "spec_sha256": DIGEST_A,
                           "plan_sha256": DIGEST_B}),
    ]
    relations = [
        relation("report-spec", "report", "spec", "binds-spec", ["spec_sha256"]),
        relation("report-plan", "report", "plan", "binds-plan", ["plan_sha256"]),
        relation("report-checkpoint", "report", "checkpoint", "binds-checkpoint",
                 ["spec_sha256", "plan_sha256"]),
    ]
    return artifacts, relations


class EvidenceLinksTests(unittest.TestCase):
    def test_three_fixed_observations_are_complete_and_deterministic(self):
        for artifacts, relations in (
            session_trace_request(),
            comparison_run(),
            report_spec_plan_checkpoint(),
        ):
            first = verify_evidence_links(artifacts, relations)
            second = verify_evidence_links(list(reversed(artifacts)),
                                           list(reversed(relations)))
            self.assertEqual(first, second)
            self.assertEqual(1, first["schema_version"])
            self.assertEqual("complete", first["status"])
            self.assertEqual([], first["unknowns"])
            self.assertEqual([], first["omissions"])

    def test_same_hash_different_run_identity_is_refused(self):
        artifacts, relations = session_trace_request()
        artifacts[1]["identity"]["run_id"] = "run_2"
        report = verify_evidence_links(artifacts, relations)
        self.assertEqual("refused", report["status"])
        self.assertEqual("identity_mismatch", report["edges"][0]["reason"])

    def test_whole_file_request_and_semantic_digests_are_not_interchangeable(self):
        artifacts, relations = session_trace_request()
        artifacts[2]["semantic_sha256"] = artifacts[2]["whole_file_sha256"]
        artifacts[2]["semantic_scope"] = "whole-file"
        report = verify_evidence_links(artifacts, relations)
        self.assertEqual("refused", report["status"])
        request = next(item for item in report["artifacts"] if item["id"] == "request")
        self.assertEqual("digest_scope_confusion", request["reason"])

    def test_duplicate_ids_orphans_unsafe_paths_and_observed_hash_drift_refuse(self):
        artifacts, relations = session_trace_request()
        artifacts.append(copy.deepcopy(artifacts[0]))
        report = verify_evidence_links(artifacts, relations)
        self.assertEqual("refused", report["status"])
        self.assertEqual("duplicate_id", report["unknowns"][0]["reason"])

        artifacts, relations = session_trace_request()
        relations.append(relation("orphan", "session", "missing", "same-run",
                                  ["run_id"]))
        report = verify_evidence_links(artifacts, relations)
        self.assertEqual("refused", report["status"])
        self.assertEqual("orphan_relation", report["unknowns"][0]["reason"])

        artifacts, relations = session_trace_request()
        artifacts[0]["path"] = "../outside.jsonl"
        report = verify_evidence_links(artifacts, relations)
        self.assertEqual("refused", report["status"])
        session = next(item for item in report["artifacts"] if item["id"] == "session")
        self.assertEqual("unsafe_path", session["reason"])

        artifacts, relations = session_trace_request()
        artifacts[0]["observed_whole_file_sha256"] = DIGEST_C
        report = verify_evidence_links(artifacts, relations)
        self.assertEqual("refused", report["status"])
        session = next(item for item in report["artifacts"] if item["id"] == "session")
        self.assertEqual("digest_mismatch", session["reason"])

    def test_missing_optional_artifact_and_incomplete_coverage_are_partial(self):
        artifacts, relations = session_trace_request()
        artifacts[2]["required"] = False
        artifacts[2]["read_coverage"] = "partial"
        relations[1]["required"] = False
        report = verify_evidence_links(artifacts, relations)
        self.assertEqual("partial", report["status"])
        self.assertEqual("incomplete_read_coverage", report["omissions"][0]["reason"])
        self.assertEqual("optional_relation_not_covered", report["omissions"][1]["reason"])

    def test_missing_optional_artifact_is_partial(self):
        artifacts, relations = session_trace_request()
        optional = artifacts.pop()
        relations = [item for item in relations
                     if optional["id"] not in (item["source"], item["target"])]
        optional["required"] = False
        optional["present"] = False
        report = verify_evidence_links(artifacts + [optional], relations)
        self.assertEqual("partial", report["status"])
        self.assertEqual("optional_artifact_not_observed", report["omissions"][0]["reason"])

    def test_missing_required_artifact_is_refused(self):
        artifacts, relations = session_trace_request()
        missing = artifacts.pop()
        relations = [item for item in relations
                     if missing["id"] not in (item["source"], item["target"])]
        report = verify_evidence_links(
            artifacts + [dict(missing, present=False)], relations)
        self.assertEqual("refused", report["status"])
        self.assertEqual("missing_required_artifact", report["unknowns"][0]["reason"])

    def test_inputs_are_not_mutated(self):
        artifacts, relations = session_trace_request()
        before = copy.deepcopy((artifacts, relations))
        verify_evidence_links(artifacts, relations)
        self.assertEqual(before, (artifacts, relations))

    def test_report_never_echoes_request_message_or_credential_text(self):
        artifacts, relations = session_trace_request()
        canary = "sk-secret-and-private-message"
        artifacts[2]["request"] = {"messages": [{"content": canary}]}
        artifacts[2]["credential"] = canary
        artifacts[0]["identity"]["message"] = canary
        rendered = repr(verify_evidence_links(artifacts, relations))
        self.assertNotIn(canary, rendered)
        self.assertIn("unexpected_identity_field", rendered)

    def test_no_file_network_or_provider_calls(self):
        artifacts, relations = session_trace_request()
        with mock.patch("builtins.open", side_effect=AssertionError("open called")), \
                mock.patch("socket.socket", side_effect=AssertionError("socket called")):
            report = verify_evidence_links(artifacts, relations)
        self.assertEqual("complete", report["status"])

    def test_limits_refuse_instead_of_truncating(self):
        artifacts, relations = session_trace_request()
        with mock.patch("pocketchat.evidence_links.MAX_ARTIFACTS", 2):
            self.assertEqual("refused", verify_evidence_links(artifacts, relations)["status"])
        with mock.patch("pocketchat.evidence_links.MAX_RELATIONS", 1):
            self.assertEqual("refused", verify_evidence_links(artifacts, relations)["status"])
        with mock.patch("pocketchat.evidence_links.MAX_REPORT_BYTES", 64):
            self.assertEqual("refused", verify_evidence_links(artifacts, relations)["status"])


if __name__ == "__main__":
    unittest.main()
