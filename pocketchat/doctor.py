"""Read-only startup diagnostics.

Doctor answers one question: with this shell and this configuration, could a
run resolve a profile and find its credential? It deliberately stops there.
An environment variable being present is not a provider being reachable, so
this module never opens a socket or claims endpoint health.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import ConfigError, load_config, profile_config, requires_api_key


def _check(name: str, status: str, detail: str,
           fix: Optional[str] = None) -> Dict[str, Any]:
    record: Dict[str, Any] = {"name": name, "status": status, "detail": detail}
    if fix is not None:
        record["fix"] = fix
    return record


def diagnose(path: Path, profile: Optional[str] = None) -> Dict[str, Any]:
    """Report configuration, profile and credential readiness.

    Every failure becomes a check with a next action. ``ok`` is true only when
    all three checks pass. A missing or invalid config stops the later checks
    because there is no selected profile to inspect; those checks are reported
    as skipped rather than guessed.
    """
    checks = []
    try:
        config = load_config(path)
    except (ConfigError, OSError, UnicodeError) as exc:
        message = str(exc)
        if isinstance(exc, ConfigError) and message.startswith("config file not found:"):
            detail = "configuration file not found"
            fix = "run 'pocketchat init', then edit %s" % path
        else:
            detail = "configuration is invalid"
            fix = "correct %s; run 'pocketchat profile list' to see the error" % path
        checks.append(_check("config", "failed", detail, fix))
        checks.append(_check("profile", "skipped",
                             "profile cannot be selected until the config loads"))
        checks.append(_check("credential", "skipped",
                             "credential cannot be checked until a profile is selected"))
        return {"ok": False, "profile": None, "model": None,
                "api_key_env": None, "checks": checks}

    checks.append(_check("config", "ok", "configuration loaded and validated"))

    try:
        selected = profile_config(config, profile)
    except ConfigError as exc:
        checks.append(_check("profile", "failed", str(exc),
                             "name an existing profile with --profile, or fix default_profile"))
        checks.append(_check("credential", "skipped",
                             "credential cannot be checked until a profile is selected"))
        return {"ok": False, "profile": None, "model": None,
                "api_key_env": None, "checks": checks}

    name = selected["profile"]
    checks.append(_check("profile", "ok", "selected profile %s" % name))
    env_name = selected["api_key_env"]
    if not requires_api_key(selected):
        checks.append(_check("credential", "ok",
                             "profile declares no client credential"))
        ok = True
    elif os.environ.get(env_name, "").strip():
        checks.append(_check("credential", "ok",
                             "%s is set (value not read into the report)" % env_name))
        ok = True
    else:
        checks.append(_check(
            "credential", "failed", "%s is not set" % env_name,
            "export %s=... in this shell, then run doctor again" % env_name))
        ok = False
    return {"ok": ok, "profile": name, "model": selected["model"],
            "api_key_env": env_name, "checks": checks}


def render_lines(report: Dict[str, Any]) -> List[str]:
    """Render the same report as short human-readable lines."""
    lines = []
    for check in report["checks"]:
        line = "%s: %s  %s" % (check["name"], check["status"], check["detail"])
        lines.append(line)
        if check.get("fix"):
            lines.append("  next: %s" % check["fix"])
    lines.append("doctor: ready" if report["ok"] else "doctor: not ready")
    return lines
