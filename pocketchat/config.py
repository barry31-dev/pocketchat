"""Provider configuration: one file, read once, validated before anything runs.

The file names the environment variable that holds the credential; it never
holds the credential itself. That split is what lets a config be committed,
shared and diffed without carrying a secret.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_SCHEMA_VERSION = "2"
LEGACY_SCHEMA_VERSION = "1"
DEFAULT_TIMEOUT_SECONDS = 60
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 600
DEFAULT_MAX_CONTEXT_CHARS = 24000
DEFAULT_SUMMARY_PREFIX_CHARS = 160

_ENV_VAR_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_URL_PATTERN = re.compile(r"^https?://\S+$")


class ConfigError(Exception):
    """Configuration is unusable. Raised before any network call is made."""


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
    }
    unknown = sorted(set(provider) - allowed)
    if unknown:
        raise ConfigError("%s: unknown profile field(s): %s" % (where, unknown))
    base_url = _require_string(provider, "base_url", where)
    if not _URL_PATTERN.match(base_url):
        raise ConfigError("%s: base_url must start with http:// or https://" % where)
    api_key_env = _require_string(provider, "api_key_env", where)
    if not _ENV_VAR_PATTERN.match(api_key_env):
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
    }


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
        "system_prompt": system_prompt or "",
    }


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
    merged["profile"] = selected
    merged["system_prompt"] = config.get("system_prompt") or ""
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
            },
        },
    },
    indent=2,
    ensure_ascii=False,
) + "\n"
