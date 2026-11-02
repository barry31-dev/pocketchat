"""Provider configuration: one file, read once, validated before anything runs.

The file names the environment variable that holds the credential; it never
holds the credential itself. That split is what lets a config be committed,
shared and diffed without carrying a secret.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG_SCHEMA_VERSION = "2"
LEGACY_SCHEMA_VERSION = "1"
DEFAULT_TIMEOUT_SECONDS = 60
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 600
DEFAULT_MAX_CONTEXT_CHARS = 24000
DEFAULT_SUMMARY_PREFIX_CHARS = 160
DEFAULT_RETRY_MAX_ATTEMPTS = 1
DEFAULT_RETRY_BACKOFF_MS = 0
DEFAULT_FALLBACK_MAX_ATTEMPTS = 1
DEFAULT_MAX_TURN_TOKENS: Optional[int] = None
DEFAULT_MAX_SESSION_TOKENS: Optional[int] = None
DEFAULT_TOOL_CALL_MAX_COUNT = 2
DEFAULT_TOOL_RESULT_MAX_BYTES = 16 * 1024
MAX_ATTEMPTS = 5
MAX_RETRY_BACKOFF_MS = 60000

_ENV_VAR_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_URL_PATTERN = re.compile(r"^https?://\S+$")


class ConfigError(Exception):
    """Configuration is unusable. Raised before any network call is made."""


class ConfigWriteError(Exception):
    """A requested configuration write was refused before the file changed."""

    def __init__(self, code: str, message: str, fix: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix


def _require_string(record: Dict[str, Any], key: str, where: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("%s: %r must be a non-empty string" % (where, key))
    return value.strip()


def _optional_int(record: Dict[str, Any], key: str, where: str,
                 low: int, high: int, default: int) -> int:
    value = record.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("%s: %r must be an integer" % (where, key))
    if not low <= value <= high:
        raise ConfigError("%s: %r must be between %d and %d" % (where, key, low, high))
    return value


def _optional_bool(record: Dict[str, Any], key: str, where: str,
                  default: bool) -> bool:
    value = record.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError("%s: %r must be a boolean" % (where, key))
    return value


def _optional_price(record: Dict[str, Any], key: str, where: str) -> Optional[float]:
    value = record.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("%s: %r must be a number" % (where, key))
    price = float(value)
    if price < 0:
        raise ConfigError("%s: %r must not be negative" % (where, key))
    return price


def _optional_string_list(record: Dict[str, Any], key: str,
                          where: str) -> List[str]:
    value = record.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError("%s: %r must be a list" % (where, key))
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError("%s: %r must contain non-empty strings" % (where, key))
        result.append(item.strip())
    return result


def _optional_name(record: Dict[str, Any], key: str, where: str) -> Optional[str]:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("%s: %r must be a non-empty string" % (where, key))
    value = value.strip()
    if not PROFILE_NAME_PATTERN.match(value):
        raise ConfigError("%s: %r is not a profile name" % (where, key))
    return value


def _profile_record(provider: Dict[str, Any], where: str) -> Dict[str, Any]:
    """Validate one profile, whatever schema version it was written in.

    A profile is the only unit this package acts on: a base URL, a model, the
    name of the credential's environment variable and the numbers that bound a
    run. The optional per-million prices are carried as ``None`` when they are
    absent rather than defaulted to zero, because "no price configured" and
    "this model is free" are different facts and only one of them is true.
    """
    allowed = {
        "base_url", "model", "api_key_env", "timeout_seconds",
        "max_context_chars", "summary_prefix_chars",
        "input_price_per_million", "output_price_per_million",
        "reasoning_effort_supported", "requires_api_key",
        "retry_max_attempts", "retry_backoff_ms", "fallback_max_attempts",
        "fallback_profile",
        "max_turn_tokens", "max_session_tokens",
        "tool_read_enabled", "tool_read_allow_paths",
        "tool_write_enabled", "tool_write_allow_paths",
        "tool_memory_search_enabled", "tool_memory_search_max_results",
        "tool_call_max_count", "tool_result_max_bytes", "max_payload_bytes",
    }
    unknown = sorted(set(provider) - allowed)
    if unknown:
        raise ConfigError("%s: unknown profile field(s): %s" % (where, unknown))
    base_url = _require_string(provider, "base_url", where)
    if not _URL_PATTERN.match(base_url):
        raise ConfigError("%s: base_url must start with http:// or https://" % where)
    requires_api_key = _optional_bool(provider, "requires_api_key", where, True)
    if requires_api_key:
        api_key_env = _require_string(provider, "api_key_env", where)
        if not _ENV_VAR_PATTERN.match(api_key_env):
            raise ConfigError("%s: api_key_env must be an environment variable name" % where)
    else:
        api_key_env = str(provider.get("api_key_env") or "").strip()
        if api_key_env and not _ENV_VAR_PATTERN.match(api_key_env):
            raise ConfigError("%s: api_key_env must be an environment variable name" % where)
    return {
        "base_url": base_url.rstrip("/"),
        "model": _require_string(provider, "model", where),
        "api_key_env": api_key_env,
        "timeout_seconds": _optional_int(
            provider, "timeout_seconds", where,
            MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS),
        "max_context_chars": _optional_int(
            provider, "max_context_chars", where,
            1, 10 ** 9, DEFAULT_MAX_CONTEXT_CHARS),
        "summary_prefix_chars": _optional_int(
            provider, "summary_prefix_chars", where,
            0, 4096, DEFAULT_SUMMARY_PREFIX_CHARS),
        "input_price_per_million": _optional_price(
            provider, "input_price_per_million", where),
        "output_price_per_million": _optional_price(
            provider, "output_price_per_million", where),
        "reasoning_effort_supported": _optional_bool(
            provider, "reasoning_effort_supported", where, False),
        "requires_api_key": requires_api_key,
        "retry_max_attempts": _optional_int(
            provider, "retry_max_attempts", where,
            1, MAX_ATTEMPTS, DEFAULT_RETRY_MAX_ATTEMPTS),
        "retry_backoff_ms": _optional_int(
            provider, "retry_backoff_ms", where,
            0, MAX_RETRY_BACKOFF_MS, DEFAULT_RETRY_BACKOFF_MS),
        "fallback_max_attempts": _optional_int(
            provider, "fallback_max_attempts", where,
            1, MAX_ATTEMPTS, DEFAULT_FALLBACK_MAX_ATTEMPTS),
        "fallback_profile": _optional_name(
            provider, "fallback_profile", where),
        "max_turn_tokens": _optional_int(
            provider, "max_turn_tokens", where,
            1, 10 ** 9, DEFAULT_MAX_TURN_TOKENS)
        if provider.get("max_turn_tokens") is not None
        else None,
        "max_session_tokens": _optional_int(
            provider, "max_session_tokens", where,
            1, 10 ** 9, DEFAULT_MAX_SESSION_TOKENS)
        if provider.get("max_session_tokens") is not None
        else None,
        "tool_read_enabled": _optional_bool(
            provider, "tool_read_enabled", where, False),
        "tool_read_allow_paths": _optional_string_list(
            provider, "tool_read_allow_paths", where),
        "tool_write_enabled": _optional_bool(
            provider, "tool_write_enabled", where, False),
        "tool_write_allow_paths": _optional_string_list(
            provider, "tool_write_allow_paths", where),
        "tool_memory_search_enabled": _optional_bool(
            provider, "tool_memory_search_enabled", where, False),
        "tool_memory_search_max_results": _optional_int(
            provider, "tool_memory_search_max_results", where,
            1, 100, 20),
        "tool_call_max_count": _optional_int(
            provider, "tool_call_max_count", where,
            1, 8, DEFAULT_TOOL_CALL_MAX_COUNT),
        "tool_result_max_bytes": _optional_int(
            provider, "tool_result_max_bytes", where,
            1, 1024 * 1024, DEFAULT_TOOL_RESULT_MAX_BYTES),
        "max_payload_bytes": _optional_int(
            provider, "max_payload_bytes", where,
            1, 16 * 1024 * 1024, 1024 * 1024),
    }


PROFILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_PROFILE_FIELDS = {
    "base_url", "model", "api_key_env", "timeout_seconds",
    "max_context_chars", "summary_prefix_chars",
    "input_price_per_million", "output_price_per_million",
    "reasoning_effort_supported", "requires_api_key",
    "retry_max_attempts", "retry_backoff_ms", "fallback_max_attempts",
    "fallback_profile",
    "max_turn_tokens", "max_session_tokens",
    "tool_read_enabled", "tool_read_allow_paths",
    "tool_write_enabled", "tool_write_allow_paths",
    "tool_memory_search_enabled", "tool_memory_search_max_results",
    "tool_call_max_count", "tool_result_max_bytes",
}


def _validate_fallbacks(profiles: Dict[str, Any]) -> None:
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        fallback = profile.get("fallback_profile")
        if fallback is None:
            continue
        if not isinstance(fallback, str) or not fallback.strip():
            raise ConfigError(
                "profile %r has invalid fallback_profile %r" % (name, fallback))
        if fallback not in profiles:
            raise ConfigError(
                "profile %r names unknown fallback_profile %r" % (name, fallback))
        if fallback == name:
            raise ConfigError(
                "profile %r cannot fall back to itself" % name)
        target = profiles[fallback]
        if isinstance(target, dict) and target.get("fallback_profile"):
            raise ConfigError(
                "fallback_profile chains are not supported: %s -> %s -> %s"
                % (name, fallback, target.get("fallback_profile")))


def _config_error_detail(exc: ConfigError) -> str:
    """Keep validation detail but omit the candidate's filesystem path."""
    return str(exc).split(": ", 1)[-1]


def read_config_document(path: Path) -> Dict[str, Any]:
    """Read the raw schema-2 document used by profile writes.

    Reads stay fail-closed and writes require the schema they know how to edit.
    A v1 file keeps its read-only compatibility, but it is never migrated as a
    side effect of managing a profile.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigWriteError(
            "config unavailable", "config file not found",
            "run pocketchat init to create one")
    except OSError:
        raise ConfigWriteError(
            "config unavailable", "config cannot be read",
            "repair the path or its permissions")
    except UnicodeError:
        raise ConfigWriteError(
            "config unavailable", "config is not UTF-8",
            "repair or recreate the configuration file")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raise ConfigWriteError(
            "config unavailable", "config is invalid JSON",
            "repair or recreate the configuration file")
    if not isinstance(raw, dict):
        raise ConfigWriteError(
            "config unavailable", "config must be an object",
            "repair or recreate the configuration file")
    if raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ConfigWriteError(
            "schema 2 required",
            "profile writes require configuration schema 2",
            "create a schema 2 configuration before writing profiles")
    try:
        load_config(path)
    except ConfigError as exc:
        raise ConfigWriteError(
            "invalid config", _config_error_detail(exc),
            "repair the configuration before writing profiles")
    return raw


def write_config_document(path: Path, payload: Dict[str, Any]) -> None:
    """Validate the whole candidate, then atomically replace the file."""
    mode = stat.S_IMODE(path.stat().st_mode)
    body = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, temp_name = tempfile.mkstemp(
        prefix=".%s." % path.name, dir=str(path.parent), text=True)
    candidate = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        try:
            load_config(candidate)
        except ConfigError as exc:
            raise ConfigWriteError(
                "invalid profile", _config_error_detail(exc),
                "correct the profile fields and try again")
        os.replace(temp_name, str(path))
    finally:
        if candidate.exists():
            candidate.unlink()


def _profile_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    unknown = sorted(set(payload) - _PROFILE_FIELDS)
    if unknown:
        raise ConfigWriteError(
            "invalid profile", "unknown profile field(s): %s" % unknown,
            "pass only non-secret profile fields")
    return {key: payload[key] for key in payload}


def _validated_profile_name(name: str) -> str:
    value = name.strip()
    if not PROFILE_NAME_PATTERN.match(value):
        raise ConfigWriteError(
            "invalid profile name",
            "profile name must start with an alphanumeric character and use "
            "at most 64 letters, numbers, dots, colons, underscores or hyphens",
            "choose a simpler profile name")
    return value


def upsert_profile(path: Path, name: str,
                   payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create or replace one profile without changing the default."""
    name = _validated_profile_name(name)
    raw = read_config_document(path)
    raw["profiles"][name] = _profile_payload(payload)
    write_config_document(path, raw)
    return load_config(path)


def set_default_profile(path: Path, name: str) -> Dict[str, Any]:
    """Select an existing profile as the configuration default."""
    name = _validated_profile_name(name)
    raw = read_config_document(path)
    if name not in raw["profiles"]:
        available = ", ".join(sorted(raw["profiles"]))
        raise ConfigWriteError(
            "unknown profile",
            "unknown profile: %s (available: %s)" % (name, available),
            "run pocketchat profile list to see available profiles")
    raw["default_profile"] = name
    write_config_document(path, raw)
    return load_config(path)


def remove_profile(path: Path, name: str) -> Dict[str, Any]:
    """Remove one non-default profile."""
    name = _validated_profile_name(name)
    raw = read_config_document(path)
    if name not in raw["profiles"]:
        available = ", ".join(sorted(raw["profiles"]))
        raise ConfigWriteError(
            "unknown profile",
            "unknown profile: %s (available: %s)" % (name, available),
            "run pocketchat profile list to see available profiles")
    if raw.get("default_profile") == name:
        raise ConfigWriteError(
            "default profile",
            "cannot remove the default profile: %s" % name,
            "select another profile with pocketchat profile use first")
    del raw["profiles"][name]
    write_config_document(path, raw)
    return load_config(path)


def load_config(path: Path) -> Dict[str, Any]:
    """Read and validate a configuration file. Unknown fields are rejected.

    Rejection is deliberate: a typo in a field name would otherwise look like a
    working provider choice while silently leaving the default in place.

    A v1 file names one ``provider``; a v2 file names a ``profiles`` map plus a
    ``default_profile``. Both are loaded into the same shape, because every
    consumer of this function already speaks that shape: the flat per-run keys
    alongside the ``profiles`` map it can select from. A v1 file therefore
    keeps working unchanged, gaining one profile derived from its own ``name``.
    """
    if not path.exists():
        raise ConfigError("config file not found: %s" % path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError("%s: invalid JSON: %s" % (path, exc))
    if not isinstance(raw, dict):
        raise ConfigError("%s: expected a JSON object" % path)

    where = str(path)
    version = raw.get("schema_version")
    if version not in (LEGACY_SCHEMA_VERSION, CONFIG_SCHEMA_VERSION):
        raise ConfigError(
            "%s: schema_version must be %r or %r, got %r"
            % (where, LEGACY_SCHEMA_VERSION, CONFIG_SCHEMA_VERSION, version)
        )

    allowed = {"schema_version", "name", "provider", "system_prompt",
               "default_profile", "profiles"}
    # Which of the two provider-shaped fields is legal depends on the version.
    # Accepting both in either file would let a config that declares
    # `profiles` while also carrying a stale `provider` load the stale one and
    # ignore the map the operator edited -- the silent-wrong-value failure
    # this module rejects everywhere else.
    if version == CONFIG_SCHEMA_VERSION:
        allowed -= {"provider"}
    else:
        allowed -= {"default_profile", "profiles"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError("%s: unknown field(s): %s" % (where, unknown))

    system_prompt = raw.get("system_prompt")
    if system_prompt is not None and not isinstance(system_prompt, str):
        raise ConfigError("%s: 'system_prompt' must be a string when present" % where)

    if version == CONFIG_SCHEMA_VERSION:
        profiles = raw.get("profiles")
        if not isinstance(profiles, dict) or not profiles:
            raise ConfigError("%s: 'profiles' must be a non-empty object" % where)
    else:
        provider = raw.get("provider")
        if not isinstance(provider, dict):
            raise ConfigError("%s: 'provider' must be an object" % where)
        # The v1 name becomes the profile name, so a migrated file keeps the
        # label the operator already recognises. A file with no name falls back
        # to the file's own stem, which is the same default `init` writes.
        profiles = {str(raw.get("name") or path.stem): provider}

    records: Dict[str, Dict[str, Any]] = {}
    for name in sorted(profiles):
        profile = profiles[name]
        if not isinstance(profile, dict):
            raise ConfigError("%s: profile %r must be an object" % (where, name))
        records[name] = _profile_record(profile, "%s: profile %r" % (where, name))

    _validate_fallbacks(records)

    default_profile = raw.get("default_profile")
    if version == CONFIG_SCHEMA_VERSION:
        if not isinstance(default_profile, str) or not default_profile.strip():
            raise ConfigError("%s: 'default_profile' must be a non-empty string" % where)
        default_profile = default_profile.strip()
        if default_profile not in records:
            raise ConfigError("%s: unknown default_profile: %s" % (where, default_profile))
    else:
        default_profile = next(iter(records))

    selected = records[default_profile]
    return {
        "name": str(raw.get("name") or path.stem),
        "schema_version": CONFIG_SCHEMA_VERSION,
        "default_profile": default_profile,
        "profiles": records,
        "base_url": selected["base_url"],
        "model": selected["model"],
        "api_key_env": selected["api_key_env"],
        "timeout_seconds": selected["timeout_seconds"],
        "max_context_chars": selected["max_context_chars"],
        "summary_prefix_chars": selected["summary_prefix_chars"],
        "retry_max_attempts": selected["retry_max_attempts"],
        "retry_backoff_ms": selected["retry_backoff_ms"],
        "fallback_max_attempts": selected["fallback_max_attempts"],
        "fallback_profile": selected["fallback_profile"],
        "max_turn_tokens": selected["max_turn_tokens"],
        "max_session_tokens": selected["max_session_tokens"],
        "tool_read_enabled": selected["tool_read_enabled"],
        "tool_read_allow_paths": selected["tool_read_allow_paths"],
        "tool_write_enabled": selected["tool_write_enabled"],
        "tool_write_allow_paths": selected["tool_write_allow_paths"],
        "tool_call_max_count": selected["tool_call_max_count"],
        "tool_result_max_bytes": selected["tool_result_max_bytes"],
        "max_payload_bytes": selected["max_payload_bytes"],
        "system_prompt": system_prompt or "",
    }


def requires_api_key(config: Dict[str, Any]) -> bool:
    """Whether a profile must resolve a credential before a run."""
    return config.get("requires_api_key", True) is not False


def load_api_key(config: Dict[str, Any]) -> Optional[str]:
    """Return the credential for a run, or None when it is not set.

    The caller decides what a missing credential means. The value is never
    written to a trace, a session file or stdout.
    """
    return os.environ.get(config["api_key_env"], "").strip() or None


def profile_config(config: Dict[str, Any],
                   name: Optional[str] = None) -> Dict[str, Any]:
    """Resolve one profile out of a loaded config into the shape a run uses.

    ``Session``, the provider and the CLI all read the flat per-run keys, so
    this returns exactly those keys -- the selected profile's own record plus
    the ``profile`` name and the config's ``system_prompt``. Selecting the
    default profile therefore produces the same dict the config already
    carried, which is what makes ``--profile`` a selection rather than a
    second configuration format.

    ``name`` is the profile to use, or ``None`` for the config's
    ``default_profile``. An unknown name is refused with the names that do
    exist, because "which profile did you mean" is the question the operator
    is about to ask and the config is the only place the answer lives.
    """
    profiles = config.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ConfigError("config carries no profiles to select from")

    selected = name if name is not None else config.get("default_profile")
    if not isinstance(selected, str) or not selected.strip():
        raise ConfigError("no profile named, and the config has no default_profile")
    selected = selected.strip()
    if selected not in profiles:
        raise ConfigError(
            "unknown profile: %s (available: %s)"
            % (selected, ", ".join(sorted(profiles)))
        )

    record = profiles[selected]
    if not isinstance(record, dict):
        raise ConfigError("profile %r must be an object" % selected)
    # Re-running the same validation keeps this function usable on a dict that
    # never went through ``load_config`` -- a hand-built config, or a caller
    # that assembled one -- instead of trusting keys nobody checked. It is
    # idempotent on an already-valid record, so a loaded config is unchanged.
    merged = _profile_record(record, "profile %r" % selected)
    _validate_fallbacks(profiles)
    merged["profile"] = selected
    merged["system_prompt"] = config.get("system_prompt") or ""
    fallback_name = merged.get("fallback_profile")
    if fallback_name:
        fallback = _profile_record(
            profiles[fallback_name], "profile %r fallback" % selected)
        fallback["profile"] = fallback_name
        fallback["system_prompt"] = config.get("system_prompt") or ""
        merged["fallback_config"] = fallback
    return merged


CONFIG_TEMPLATE = json.dumps(
    {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "name": "local-provider",
        "default_profile": "local",
        "system_prompt": "You are a concise assistant.",
        "profiles": {
            "local": {
                "base_url": "http://127.0.0.1:8123/v1",
                "model": "example-model",
                "api_key_env": "POCKETCHAT_API_KEY",
                "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                "max_context_chars": DEFAULT_MAX_CONTEXT_CHARS,
                "summary_prefix_chars": DEFAULT_SUMMARY_PREFIX_CHARS,
                "input_price_per_million": None,
                "output_price_per_million": None,
                "retry_max_attempts": DEFAULT_RETRY_MAX_ATTEMPTS,
                "retry_backoff_ms": DEFAULT_RETRY_BACKOFF_MS,
                "fallback_max_attempts": DEFAULT_FALLBACK_MAX_ATTEMPTS,
            },
            "hosted": {
                "base_url": "https://example.invalid/v1",
                "model": "example-model",
                "api_key_env": "POCKETCHAT_HOSTED_KEY",
                "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                "max_context_chars": DEFAULT_MAX_CONTEXT_CHARS,
                "summary_prefix_chars": DEFAULT_SUMMARY_PREFIX_CHARS,
                "input_price_per_million": None,
                "output_price_per_million": None,
                "retry_max_attempts": DEFAULT_RETRY_MAX_ATTEMPTS,
                "retry_backoff_ms": DEFAULT_RETRY_BACKOFF_MS,
                "fallback_max_attempts": DEFAULT_FALLBACK_MAX_ATTEMPTS,
            },
        },
    },
    indent=2,
    ensure_ascii=False,
) + "\n"
