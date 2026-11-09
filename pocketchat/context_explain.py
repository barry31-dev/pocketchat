"""Bounded, read-only explanations for an existing context projection."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

INPUT_SCHEMA = "pocketchat-context-explain-input.v1"
REPORT_SCHEMA = "pocketchat-context-explain.v1"
PROJECTION_SCHEMA = "pocketchat-context-projection.v1"
LAYERS = ("fixed", "recent", "task", "memory", "tool")

_INPUT_KEYS = {"schema_version", "source", "selection", "result", "sources"}
_SOURCE_KEYS = {
    "instructions", "task_state", "messages", "evidence", "pending_messages"
}
_SELECTION_KEYS = {"max_chars", "keep_recent_turns"}
_RESULT_KEYS = {"messages", "manifest"}
_DESCRIPTOR_KEYS = {"source_id", "layer", "locator"}
_LOCATOR_KEYS = {"kind", "index"}
_MANIFEST_REQUIRED = {
    "schema_version", "strategy", "max_chars", "kept_chars", "pending_messages",
    "pending_sha256", "source_chars", "source_sha256", "evidence",
    "projection_sha256",
}
_MANIFEST_OPTIONAL = {"kept_turns", "dropped"}
_KIND_ORDER = {
    "instruction": 0,
    "message": 1,
    "pending_message": 2,
    "task_state": 3,
    "evidence": 4,
}
_KIND_LAYER = {
    "instruction": "fixed",
    "message": "recent",
    "pending_message": "recent",
    "task_state": "task",
}
_DROPPED_REASONS = {"duplicate_source", "over_budget"}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _empty_layers() -> dict[str, dict[str, int]]:
    return {
        layer: {"selected": 0, "omitted": 0, "selected_bytes": 0}
        for layer in LAYERS
    }


def _budget(limits: dict[str, int], findings: int, omissions: int,
            payload_bytes: int) -> dict[str, int]:
    return {
        "max_findings": limits["max_findings"],
        "max_omissions": limits["max_omissions"],
        "max_bytes": limits["max_bytes"],
        "findings": findings,
        "omissions": omissions,
        "payload_bytes": payload_bytes,
    }


def _refusal(code: str, limits: dict[str, int], *, source_id: str = "",
             layer: str = "") -> dict[str, Any]:
    finding = {"code": code}
    if source_id:
        finding["source_id"] = source_id
    if layer:
        finding["layer"] = layer
    findings = [finding]
    layers = _empty_layers()
    payload_bytes = len(_canonical({
        "layers": layers,
        "observations": [],
        "findings": findings,
        "omissions": [],
    }))
    return {
        "schema_version": REPORT_SCHEMA,
        "status": "refused",
        "layers": layers,
        "observations": [],
        "findings": findings,
        "omissions": [],
        "budget": _budget(limits, 1, 0, payload_bytes),
    }


def _valid_limits(max_findings: Any, max_omissions: Any,
                  max_bytes: Any) -> tuple[bool, dict[str, int]]:
    values = {
        "max_findings": max_findings,
        "max_omissions": max_omissions,
        "max_bytes": max_bytes,
    }
    valid = (
        isinstance(max_findings, int) and not isinstance(max_findings, bool)
        and max_findings >= 0
        and isinstance(max_omissions, int) and not isinstance(max_omissions, bool)
        and max_omissions >= 0
        and isinstance(max_bytes, int) and not isinstance(max_bytes, bool)
        and max_bytes > 0
    )
    return valid, values


def _turns(messages: list[dict[str, Any]]) -> list[list[tuple[int, dict[str, Any]]]]:
    turns: list[list[tuple[int, dict[str, Any]]]] = []
    current: list[tuple[int, dict[str, Any]]] = []
    for index, message in enumerate(messages):
        if message.get("role") == "user" and current:
            turns.append(current)
            current = [(index, message)]
        else:
            current.append((index, message))
    if current:
        turns.append(current)
    return turns


def _same(left: Any, right: Any) -> bool:
    return _canonical(left) == _canonical(right)


def _shape_error(projection: Any) -> bool:
    if not isinstance(projection, dict) or set(projection) != _INPUT_KEYS:
        return True
    source = projection.get("source")
    selection = projection.get("selection")
    result = projection.get("result")
    descriptors = projection.get("sources")
    if not isinstance(source, dict) or set(source) != _SOURCE_KEYS:
        return True
    if not isinstance(selection, dict) or set(selection) != _SELECTION_KEYS:
        return True
    if not isinstance(result, dict) or set(result) != _RESULT_KEYS:
        return True
    if not isinstance(descriptors, list):
        return True
    if not isinstance(source.get("instructions"), list) \
            or not isinstance(source.get("task_state"), dict) \
            or not isinstance(source.get("messages"), list) \
            or not isinstance(source.get("evidence"), list) \
            or not isinstance(source.get("pending_messages"), list):
        return True
    if any(not isinstance(item, dict) for item in source["instructions"]) \
            or any(not isinstance(item, dict) for item in source["messages"]) \
            or any(not isinstance(item, dict) for item in source["evidence"]) \
            or any(not isinstance(item, dict) for item in source["pending_messages"]):
        return True
    max_chars = selection.get("max_chars")
    keep_recent_turns = selection.get("keep_recent_turns")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
        return True
    if not isinstance(keep_recent_turns, int) \
            or isinstance(keep_recent_turns, bool) or keep_recent_turns < 0:
        return True
    return not isinstance(result.get("messages"), list) \
        or not isinstance(result.get("manifest"), dict)


def _manifest_error(manifest: dict[str, Any]) -> str:
    if manifest.get("schema_version") != PROJECTION_SCHEMA:
        return "unknown_projection_schema"
    keys = set(manifest)
    if not _MANIFEST_REQUIRED.issubset(keys) \
            or not keys.issubset(_MANIFEST_REQUIRED | _MANIFEST_OPTIONAL):
        return "invalid_projection_shape"
    if "dropped" in manifest:
        dropped = manifest["dropped"]
        if not isinstance(dropped, list):
            return "invalid_projection_shape"
        for item in dropped:
            if not isinstance(item, dict) or set(item) != {"source", "reason"} \
                    or not isinstance(item["source"], str) \
                    or not isinstance(item["reason"], str) \
                    or item["reason"] not in _DROPPED_REASONS:
                return "invalid_projection_shape"
    return ""


def _expected_locators(source: dict[str, Any]) -> dict[tuple[str, int], tuple[str, ...]]:
    expected: dict[tuple[str, int], tuple[str, ...]] = {}
    for index in range(len(source["instructions"])):
        expected[("instruction", index)] = ("fixed",)
    if source["task_state"]:
        expected[("task_state", 0)] = ("task",)
    for index in range(len(source["messages"])):
        expected[("message", index)] = ("recent",)
    for index in range(len(source["evidence"])):
        expected[("evidence", index)] = ("memory", "tool")
    for index in range(len(source["pending_messages"])):
        expected[("pending_message", index)] = ("recent",)
    return expected


def _descriptor_error(source: dict[str, Any], descriptors: list[Any]
                      ) -> tuple[dict[tuple[str, int], dict[str, Any]], str]:
    expected = _expected_locators(source)
    by_locator: dict[tuple[str, int], dict[str, Any]] = {}
    seen_ids = set()
    for descriptor in descriptors:
        if not isinstance(descriptor, dict) or set(descriptor) != _DESCRIPTOR_KEYS:
            return {}, "invalid_input_shape"
        source_id = descriptor.get("source_id")
        layer = descriptor.get("layer")
        locator = descriptor.get("locator")
        if not isinstance(source_id, str) or not source_id:
            return {}, "invalid_input_shape"
        if source_id in seen_ids:
            return {}, "duplicate_source_id"
        seen_ids.add(source_id)
        if not isinstance(locator, dict) or set(locator) != _LOCATOR_KEYS:
            return {}, "invalid_input_shape"
        kind = locator.get("kind")
        index = locator.get("index")
        if kind not in _KIND_ORDER or not isinstance(index, int) \
                or isinstance(index, bool) or index < 0:
            return {}, "invalid_input_shape"
        key = (kind, index)
        if key not in expected:
            return {}, "unexpected_source_descriptor"
        if layer not in expected[key]:
            return {}, "layer_mismatch"
        if key in by_locator:
            return {}, "duplicate_source_descriptor"
        by_locator[key] = descriptor
    for key in expected:
        if key not in by_locator:
            return {}, "missing_source_descriptor"
    return by_locator, ""


def _observation(descriptor: dict[str, Any], selected: bool,
                 reason_code: str, selected_bytes: int = 0) -> dict[str, Any]:
    locator = descriptor["locator"]
    return {
        "source_id": descriptor["source_id"],
        "layer": descriptor["layer"],
        "record_kind": locator["kind"],
        "record_index": locator["index"],
        "selected": selected,
        "reason_code": reason_code,
        "selected_bytes": selected_bytes,
    }


def explain_context_projection(
    projection: Any,
    *,
    max_findings: int = 64,
    max_omissions: int = 64,
    max_bytes: int = 65536,
) -> dict[str, Any]:
    """Explain one digest-bound projection without rereading or reselecting it."""
    valid, limits = _valid_limits(max_findings, max_omissions, max_bytes)
    if not valid:
        return _refusal("invalid_limit", limits)
    if _shape_error(projection):
        return _refusal("invalid_input_shape", limits)
    if projection.get("schema_version") != INPUT_SCHEMA:
        return _refusal("unknown_input_schema", limits)

    source = projection["source"]
    selection = projection["selection"]
    result = projection["result"]
    manifest = result["manifest"]
    schema_error = _manifest_error(manifest)
    if schema_error:
        return _refusal(schema_error, limits)
    if manifest.get("max_chars") != selection["max_chars"]:
        return _refusal("selection_mismatch", limits)
    if manifest.get("source_sha256") != _digest(source):
        return _refusal("source_digest_mismatch", limits)
    manifest_without_projection_hash = copy.deepcopy(manifest)
    manifest_without_projection_hash.pop("projection_sha256")
    expected_projection_digest = _digest({
        "messages": result["messages"],
        "manifest": manifest_without_projection_hash,
    })
    if manifest.get("projection_sha256") != expected_projection_digest:
        return _refusal("projection_digest_mismatch", limits)

    descriptors, descriptor_error = _descriptor_error(
        source, projection["sources"]
    )
    if descriptor_error:
        return _refusal(descriptor_error, limits)

    fixed_expected = copy.deepcopy(source["instructions"])
    if source["task_state"]:
        fixed_expected.append({
            "role": "system",
            "content": json.dumps(
                source["task_state"], ensure_ascii=False, sort_keys=True
            ),
        })
    pending_expected = copy.deepcopy(source["pending_messages"])
    messages = result["messages"]
    if len(messages) < len(fixed_expected) + len(pending_expected):
        return _refusal("projection_shape_mismatch", limits)
    if messages[:len(fixed_expected)] != fixed_expected:
        return _refusal("projection_shape_mismatch", limits)
    if pending_expected and messages[-len(pending_expected):] != pending_expected:
        return _refusal("projection_shape_mismatch", limits)
    projected_turn_messages = messages[
        len(fixed_expected):len(messages) - len(pending_expected)
    ]

    observations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    metadata_available = True

    def add_observation(descriptor, selected, reason, selected_bytes=0):
        observations.append(_observation(
            descriptor, selected, reason, selected_bytes
        ))

    for index in range(len(source["instructions"])):
        item = source["instructions"][index]
        add_observation(
            descriptors[("instruction", index)], True,
            "selected", len(_canonical(item)),
        )
    if source["task_state"]:
        task_descriptor = descriptors[("task_state", 0)]
        add_observation(
            task_descriptor, True, "selected",
            len(_canonical(source["task_state"])),
        )

    source_turns = _turns(source["messages"])
    if selection["keep_recent_turns"]:
        candidate_turns = set(range(
            max(0, len(source_turns) - selection["keep_recent_turns"]),
            len(source_turns),
        ))
    else:
        candidate_turns = set(range(len(source_turns)))
    cursor = 0
    for turn_index, turn in enumerate(source_turns):
        turn_messages = [message for _, message in turn]
        turn_selected = (
            turn_index in candidate_turns
            and projected_turn_messages[
                cursor:cursor + len(turn_messages)
            ] == turn_messages
        )
        if turn_selected:
            cursor += len(turn_messages)
        for index, message in turn:
            reason = "selected" if turn_selected else (
                "outside_recent_window"
                if turn_index not in candidate_turns
                else "unrecorded_omission"
            )
            add_observation(
                descriptors[("message", index)],
                turn_selected,
                reason,
                len(_canonical(message)) if turn_selected else 0,
            )
    if cursor != len(projected_turn_messages):
        return _refusal("projection_shape_mismatch", limits)

    for index, message in enumerate(source["pending_messages"]):
        add_observation(
            descriptors[("pending_message", index)], True,
            "selected", len(_canonical(message)),
        )

    kept_evidence = manifest.get("evidence")
    dropped_evidence = manifest.get("dropped")
    if not isinstance(kept_evidence, list) or not isinstance(dropped_evidence, list):
        metadata_available = False
        kept_evidence = []
        dropped_evidence = []
        omissions.append({
            "code": "sidecar_metadata_unavailable",
            "detail": "kept_turns_or_dropped_reasons",
        })
    kept_pool = list(kept_evidence)
    dropped_pool = list(dropped_evidence)
    for index, item in enumerate(source["evidence"]):
        matching_descriptor = descriptors[("evidence", index)]
        kept_match = next((
            candidate for candidate in kept_pool
            if _same(candidate, item)
        ), None)
        if kept_match is not None:
            kept_pool.remove(kept_match)
            add_observation(
                matching_descriptor, True, "selected", len(_canonical(item))
            )
            if item.get("sensitive") is True:
                findings.append({
                    "code": "sensitive_source",
                    "source_id": matching_descriptor["source_id"],
                    "layer": matching_descriptor["layer"],
                })
            continue
        dropped_match = next((
            candidate for candidate in dropped_pool
            if candidate.get("source") == item.get("source")
        ), None)
        if dropped_match is not None:
            dropped_pool.remove(dropped_match)
            reason = dropped_match.get("reason", "unrecorded_omission")
        else:
            reason = "unrecorded_omission"
        add_observation(matching_descriptor, False, reason)
        omissions.append({
            "code": "source_omitted",
            "source_id": matching_descriptor["source_id"],
            "layer": matching_descriptor["layer"],
            "reason_code": reason,
        })

    if kept_pool or dropped_pool:
        return _refusal("projection_shape_mismatch", limits)

    if ("kept_turns" not in manifest or "dropped" not in manifest) \
            and not any(
                item["code"] == "sidecar_metadata_unavailable"
                for item in omissions
            ):
        metadata_available = False
        omissions.append({
            "code": "sidecar_metadata_unavailable",
            "detail": "kept_turns_or_dropped_reasons",
        })

    for observation in observations:
        if observation["selected"]:
            continue
        if any(
            omission.get("source_id") == observation["source_id"]
            for omission in omissions
        ):
            continue
        omissions.append({
            "code": "source_omitted",
            "source_id": observation["source_id"],
            "layer": observation["layer"],
            "reason_code": observation["reason_code"],
        })

    if len(findings) > max_findings:
        return _refusal("finding_limit_exceeded", limits)
    if len(omissions) > max_omissions:
        return _refusal("omission_limit_exceeded", limits)

    kind_order = {
        "instruction": 0,
        "message": 1,
        "pending_message": 2,
        "task_state": 3,
        "evidence": 4,
    }
    observations.sort(key=lambda item: (
        kind_order[item["record_kind"]], item["record_index"], item["source_id"]
    ))
    layers = _empty_layers()
    for observation in observations:
        summary = layers[observation["layer"]]
        if observation["selected"]:
            summary["selected"] += 1
            summary["selected_bytes"] += observation["selected_bytes"]
        else:
            summary["omitted"] += 1
    payload = {
        "layers": layers,
        "observations": observations,
        "findings": findings,
        "omissions": omissions,
    }
    payload_bytes = len(_canonical(payload))
    if payload_bytes > max_bytes:
        return _refusal("report_limit_exceeded", limits)
    status = "partial" if findings or omissions or not metadata_available else "complete"
    return {
        "schema_version": REPORT_SCHEMA,
        "status": status,
        **payload,
        "budget": _budget(
            limits, len(findings), len(omissions), payload_bytes
        ),
    }
