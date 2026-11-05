"""Pure explanation of one supplied PocketChat profile.

The caller supplies both the raw configuration document and the resolved
profile produced by the existing configuration layer. This module does not
read the environment, open files, call a provider, or mutate either mapping.
It explains provenance and credential *presence* only; credential values never
enter the report.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from .config import _profile_record

SCHEMA_VERSION = 1
PROFILE_EXPLAIN_SCHEMA = "pocketchat-profile-explain.v1"
MAX_FIELDS = 128
MAX_REPORT_BYTES = 64 * 1024

_RAW_KEYS = frozenset({
    "schema_version", "name", "provider", "system_prompt",
    "default_profile", "profiles",
})
_PROFILE_KEYS = frozenset({
    "base_url", "model", "api_key_env", "timeout_seconds",
    "max_context_chars", "summary_prefix_chars",
    "input_price_per_million", "output_price_per_million",
    "reasoning_effort_supported", "requires_api_key",
    "retry_max_attempts", "retry_backoff_ms", "fallback_max_attempts",
    "fallback_profile", "max_turn_tokens", "max_session_tokens",
    "tool_read_enabled", "tool_read_allow_paths",
    "tool_write_enabled", "tool_write_allow_paths",
    "tool_memory_search_enabled", "tool_memory_search_max_results",
    "tool_call_max_count", "tool_result_max_bytes", "max_payload_bytes",
    "model_context_window_tokens", "output_reserve_tokens",
    "safety_margin_tokens", "max_output_tokens_supported",
    "context_projection_enabled", "context_projection_max_bytes",
    "memory_recall_enabled", "memory_recall_store",
    "memory_recall_workspace_id", "memory_recall_max_results",
    "memory_recall_max_excerpt_bytes", "memory_recall_max_recall_tokens",
})
_RESOLVED_KEYS = frozenset({
    "profile", "base_url", "model", "api_key_env", "requires_api_key",
    "timeout_seconds", "max_context_chars", "summary_prefix_chars",
    "input_price_per_million", "output_price_per_million",
    "reasoning_effort_supported", "retry_max_attempts",
    "retry_backoff_ms", "fallback_max_attempts", "fallback_profile",
    "max_turn_tokens", "max_session_tokens", "tool_read_enabled",
    "tool_read_allow_paths", "tool_write_enabled", "tool_write_allow_paths",
    "tool_memory_search_enabled", "tool_memory_search_max_results",
    "tool_call_max_count", "tool_result_max_bytes", "max_payload_bytes",
    "model_context_window_tokens", "output_reserve_tokens",
    "safety_margin_tokens", "max_output_tokens_supported",
    "context_projection_enabled", "context_projection_max_bytes",
    "memory_recall_enabled", "memory_recall_store",
    "memory_recall_workspace_id", "memory_recall_max_results",
    "memory_recall_max_excerpt_bytes", "memory_recall_max_recall_tokens",
    "system_prompt", "fallback_config",
})
_CREDENTIAL_KEYS = frozenset({"presence", "source"})
_CREDENTIAL_PRESENCE = frozenset({"present", "missing", "unknown"})
_CREDENTIAL_SOURCES = frozenset({"environment", "not_required"})
_DEFAULT_FIELDS = frozenset({
    "timeout_seconds", "max_context_chars", "summary_prefix_chars",
    "retry_max_attempts", "retry_backoff_ms", "fallback_max_attempts",
})


class ProfileExplainError(ValueError):
    """A supplied profile observation cannot produce a trustworthy report."""

    def __init__(self, error_type: str, category: str,
                 detail: str) -> None:
        super().__init__(detail)
        self.error_type = error_type
        self.category = category
        self.detail = detail


def _canonical_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8"))


def _field_count(value: Any) -> int:
    if isinstance(value, dict):
        return len(value) + sum(_field_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_field_count(item) for item in value)
    return 0


def _mapping(value: Any, label: str, *, error_type: str = "invalid_config",
             category: str = "structure") -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileExplainError(error_type, category,
                                  "%s must be an object" % label)
    try:
        items = value.items()
    except (AttributeError, TypeError):
        raise ProfileExplainError(error_type, category,
                                  "%s must be a mapping" % label)
    try:
        pairs = list(items)
    except (TypeError, ValueError):
        raise ProfileExplainError(error_type, category,
                                  "%s must be a stable mapping" % label)
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ProfileExplainError(error_type, category,
                                  "%s contains duplicate keys" % label)
    if any(not isinstance(key, str) for key in keys):
        raise ProfileExplainError(error_type, category,
                                  "%s keys must be strings" % label)
    return {key: value[key] for key in keys}


def _profile_name(value: Any) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 64
            and all(char.isalnum() or char in "._:-" for char in value)
            and value[0].isalnum())


def _endpoint(base_url: Any) -> str:
    if not isinstance(base_url, str):
        raise ProfileExplainError("invalid_config", "structure",
                                  "base_url must be a string")
    try:
        parsed = urlsplit(base_url)
    except ValueError:
        raise ProfileExplainError("unsafe_endpoint", "privacy",
                                  "base_url cannot be parsed")
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ProfileExplainError("unsafe_endpoint", "privacy",
                                  "base_url must be an HTTP endpoint without user information")
    host = parsed.hostname
    if ":" in host:
        host = "[%s]" % host
    endpoint = "%s://%s" % (parsed.scheme, host)
    if parsed.port is not None:
        endpoint += ":%d" % parsed.port
    return endpoint


def _raw_profile(raw: Dict[str, Any], profile: str) -> Tuple[Dict[str, Any], bool]:
    version = raw.get("schema_version")
    if version == "2":
        profiles = raw.get("profiles")
        if not isinstance(profiles, dict):
            raise ProfileExplainError("invalid_config", "structure",
                                      "profiles must be an object")
        if profile not in profiles:
            raise ProfileExplainError("invalid_config", "structure",
                                      "selected profile is absent from the raw configuration")
        record = _mapping(profiles[profile], "raw profile")
        return record, False
    if version == "1":
        provider = raw.get("provider")
        if not isinstance(provider, dict):
            raise ProfileExplainError("invalid_config", "structure",
                                      "provider must be an object")
        name = raw.get("name")
        if not isinstance(name, str) or name != profile:
            raise ProfileExplainError("invalid_config", "structure",
                                      "legacy profile name does not match the selection")
        return _mapping(provider, "legacy provider"), True
    raise ProfileExplainError("invalid_config", "structure",
                              "schema_version must be 1 or 2")


def _revalidated(record: Dict[str, Any], profile: str) -> Dict[str, Any]:
    try:
        merged = _profile_record(record, "profile %r" % profile)
    except Exception:
        raise ProfileExplainError("invalid_config", "structure",
                                  "raw profile does not satisfy the config contract")
    return merged


def _finding(identifier: str, reason: str, detail: str) -> Dict[str, str]:
    return {"id": identifier, "reason": reason, "detail": detail}


def _finish(payload: Dict[str, Any], refusals: List[Dict[str, str]]) -> Dict[str, Any]:
    report = {
        "schema": PROFILE_EXPLAIN_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": "refused" if refusals else "complete",
        "findings": sorted(refusals, key=lambda row: (row["id"], row["reason"], row["detail"])),
        "limits": {"fields": MAX_FIELDS, "report_bytes": MAX_REPORT_BYTES},
    }
    report.update(payload)
    report["report_bytes"] = _canonical_size(report)
    if _field_count(report) > MAX_FIELDS:
        raise ProfileExplainError("report_limit_exceeded", "limit",
                                  "profile explanation exceeds the field limit")
    if report["report_bytes"] > MAX_REPORT_BYTES:
        raise ProfileExplainError("report_limit_exceeded", "limit",
                                  "profile explanation exceeds the byte limit")
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if _canonical_size(report) > MAX_REPORT_BYTES:
        raise ProfileExplainError("report_limit_exceeded", "limit",
                                  "profile explanation envelope exceeds the byte limit")
    return report


def explain_profile(raw_config: Any, resolved_config: Any,
                    explicit_profile: Optional[str],
                    credential_presence: Any) -> Dict[str, Any]:
    """Explain one already-selected profile without performing I/O.

    ``explicit_profile`` is the caller's CLI selection, or ``None`` to use the
    raw document's declared default. The resolved mapping must come from the
    existing config layer. ``credential_presence`` is a closed observation
    supplied by the caller and never contains a credential value.
    """
    raw = _mapping(raw_config, "raw config")
    resolved = _mapping(resolved_config, "resolved config",
                        error_type="invalid_resolved_config")
    credential = _mapping(credential_presence, "credential observation",
                          error_type="invalid_credential_observation")
    if set(raw) - _RAW_KEYS:
        raise ProfileExplainError("invalid_config", "structure",
                                  "raw config contains unknown fields")
    if set(resolved) - _RESOLVED_KEYS:
        raise ProfileExplainError("invalid_resolved_config", "structure",
                                  "resolved config contains unknown fields")
    if set(credential) != _CREDENTIAL_KEYS:
        raise ProfileExplainError("invalid_credential_observation", "structure",
                                  "credential observation must be closed")
    presence = credential["presence"]
    source = credential["source"]
    if presence not in _CREDENTIAL_PRESENCE or source not in _CREDENTIAL_SOURCES:
        raise ProfileExplainError("invalid_credential_observation", "structure",
                                  "credential observation has an unknown state")
    if explicit_profile is not None:
        if not _profile_name(explicit_profile):
            raise ProfileExplainError("invalid_selection", "structure",
                                      "explicit profile name is invalid")
        profile = explicit_profile
        selection = "cli"
    else:
        profile = raw.get("default_profile")
        if raw.get("schema_version") == "1":
            profile = raw.get("name")
        if not _profile_name(profile):
            raise ProfileExplainError("invalid_config", "structure",
                                      "raw config has no valid default profile")
        selection = "default"
    try:
        raw_record, legacy = _raw_profile(raw, profile)
    except ProfileExplainError as exc:
        if exc.error_type == "invalid_config" and selection == "cli":
            return _finish({}, [_finding("profile", "unknown_profile",
                                         "selected profile is absent from the raw configuration")])
        raise
    resolved_profile = resolved.get("profile")
    if resolved_profile != profile:
        return _finish({}, [_finding("profile", "profile_selection_mismatch",
                                     "resolved profile differs from the selection")])
    if set(raw_record) - _PROFILE_KEYS:
        raise ProfileExplainError("invalid_config", "structure",
                                  "raw profile contains unknown fields")
    normalized = _revalidated(raw_record, profile)
    model = resolved.get("model")
    base_url = resolved.get("base_url")
    api_key_env = resolved.get("api_key_env")
    requires_api_key = resolved.get("requires_api_key")
    if model != normalized["model"]:
        return _finish({}, [_finding("profile", "profile_record_mismatch",
                                     "resolved model differs from the raw profile")])
    if base_url != normalized["base_url"]:
        return _finish({}, [_finding("profile", "profile_record_mismatch",
                                     "resolved endpoint differs from the raw profile")])
    if api_key_env != normalized["api_key_env"]:
        return _finish({}, [_finding("profile", "profile_record_mismatch",
                                     "resolved credential variable differs from the raw profile")])
    if requires_api_key is not normalized["requires_api_key"]:
        return _finish({}, [_finding("profile", "profile_record_mismatch",
                                     "resolved credential requirement differs from the raw profile")])
    if requires_api_key:
        if source != "environment" or presence == "unknown":
            return _finish({}, [_finding("credential", "credential_observation_mismatch",
                                         "required credential needs an environment presence observation")])
    elif presence != "unknown" or source != "not_required":
        return _finish({}, [_finding("credential", "credential_observation_mismatch",
                                     "credential-free profile cannot report an environment value")])
    endpoint = _endpoint(normalized["base_url"])
    provenance = []
    for field in sorted(_PROFILE_KEYS):
        if field not in normalized:
            continue
        source_name = "explicit" if field in raw_record else "default"
        provenance.append({"field": field, "source": source_name})
    payload = {
        "profile": profile,
        "selection": selection,
        "model": model,
        "endpoint": endpoint,
        "credential": {
            "required": requires_api_key,
            "api_key_env": api_key_env if requires_api_key else "",
            "presence": presence,
            "source": source,
        },
        "provenance": provenance,
    }
    return _finish(payload, [])
