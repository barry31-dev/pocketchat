#!/usr/bin/env python3
"""Write a PocketChat schema-2 config from newnewrouter's live model catalog.

The router is local and owns all upstream credentials. PocketChat profiles
therefore declare `requires_api_key: false` and never store an upstream key.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path


PREFERRED_DEFAULT = "pgt"


def choose_default_profile(model_ids):
    """Pick the default without silently changing it when the catalog moves.

    The stable router alias wins when present. Otherwise the first catalog entry
    is used, so an older or partial router still produces a usable config.
    """
    if PREFERRED_DEFAULT in model_ids:
        return PREFERRED_DEFAULT
    if "pgt" in model_ids:
        return "pgt"
    return model_ids[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8137/v1/models")
    parser.add_argument("--out", type=Path,
                        default=Path.home() / ".pocketchat" / "newnewrouter.json")
    parser.add_argument("--system-prompt",
                        default="You are a helpful, concise assistant.")
    args = parser.parse_args()

    with urllib.request.urlopen(args.url, timeout=10) as response:
        catalog = json.loads(response.read().decode("utf-8"))
    models = catalog.get("data")
    if not isinstance(models, list) or not models:
        raise SystemExit("newnewrouter returned no models")

    profiles = {}
    for item in models:
        model = item["id"]
        context = int(item.get("context_window") or 200000)
        profiles[model] = {
            "base_url": "http://127.0.0.1:8137/v1",
            "model": model,
            "api_key_env": "",
            "requires_api_key": False,
            "timeout_seconds": 600,
            "max_context_chars": context,
            "summary_prefix_chars": 160,
            "input_price_per_million": 0.0,
            "output_price_per_million": 0.0,
            "reasoning_effort_supported": bool(item.get("reasoning_levels")),
        }
    config = {
        "schema_version": "2",
        "name": "newnewrouter",
        "default_profile": choose_default_profile(list(profiles)),
        "system_prompt": args.system_prompt,
        "profiles": profiles,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print("wrote %s (%d profiles; default %s)"
          % (args.out, len(profiles), config["default_profile"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
