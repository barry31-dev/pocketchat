"""Pure, read-only runtime capability facts.

This module reports only what the running Python process exposes. It does not
probe the network, credentials, workspace permissions or concurrency safety.
"""

from __future__ import annotations

import os
import platform
import sys
from typing import Dict

SCHEMA_VERSION = "1"


def _capability_source() -> Dict[str, bool]:
    """Read the process-level primitive declarations used by secure I/O.

    ``os.supports_dir_fd`` is a capability set, not a promise that every call
    has been exercised on this host. Missing attributes become explicit false.
    """
    return {
        "dir_fd_open": os.open in os.supports_dir_fd,
        "nofollow_open_flag": hasattr(os, "O_NOFOLLOW"),
        "directory_open_flag": hasattr(os, "O_DIRECTORY"),
    }


def collect_runtime_capabilities() -> Dict[str, object]:
    """Return one bounded, JSON-serializable capability report.

    The report deliberately contains no "safe" or "verified" conclusion. The
    fixed ``not_checked`` list names the checks this pure collector cannot do.
    """
    primitives = _capability_source()
    return {
        "schema_version": SCHEMA_VERSION,
        "interpreter": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "os": {
            "name": os.name,
            "platform": platform.system(),
            "release": platform.release(),
        },
        "primitives": {
            name: bool(primitives.get(name, False))
            for name in (
                "dir_fd_open",
                "nofollow_open_flag",
                "directory_open_flag",
            )
        },
        "not_checked": [
            "provider_reachability",
            "workspace_permissions",
            "concurrency_atomicity",
        ],
    }
