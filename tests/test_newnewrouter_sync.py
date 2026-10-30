"""The generated newnewrouter config has a stable preferred default."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _sync_module():
    path = ROOT / "scripts" / "sync_newnewrouter_models.py"
    spec = importlib.util.spec_from_file_location("sync_newnewrouter_models", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load sync_newnewrouter_models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DefaultProfileTests(unittest.TestCase):
    def test_prefers_the_stable_router_alias_when_the_catalog_has_it(self):
        module = _sync_module()
        self.assertEqual(
            module.choose_default_profile([
                "another-model",
                "pgt",
                "wb-cn:model-a",
            ]),
            "pgt",
        )

    def test_falls_back_to_the_first_catalog_model(self):
        module = _sync_module()
        self.assertEqual(module.choose_default_profile(["other", "first"]), "other")


if __name__ == "__main__":
    unittest.main()
