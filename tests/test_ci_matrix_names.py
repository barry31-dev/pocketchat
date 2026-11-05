"""Guards for distinct, stable GitHub Actions matrix identities."""
from __future__ import annotations

import itertools
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _inline_values(block: str, key: str) -> list[str]:
    match = re.search(
        rf"(?m)^\s*{re.escape(key)}:\s*\[([^]]+)\]\s*$",
        block,
    )
    if match is None:
        raise AssertionError(f"missing inline matrix key: {key}")
    return [item.strip().strip('"\'') for item in match.group(1).split(",")]


class CiMatrixNameTests(unittest.TestCase):
    def test_every_platform_python_cell_has_a_distinct_stable_name(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        test_start = workflow.index("  test:\n")
        release_start = workflow.index("\n  release-smoke:\n", test_start)
        test_block = workflow[test_start:release_start]
        release_block = workflow[release_start:]

        name_match = re.search(r"(?m)^    name:\s*(.+)$", test_block)
        self.assertIsNotNone(name_match)
        template = name_match.group(1)
        operating_systems = _inline_values(test_block, "os")
        python_versions = _inline_values(test_block, "python")

        self.assertEqual(
            operating_systems,
            ["ubuntu-24.04", "macos-latest", "windows-latest"],
        )
        self.assertEqual(python_versions, ["3.9", "3.13"])

        names = {
            template.replace("${{ matrix.os }}", operating_system).replace(
                "${{ matrix.python }}", python_version
            )
            for operating_system, python_version in itertools.product(
                operating_systems,
                python_versions,
            )
        }
        expected = {
            f"pocketchat-ci ({operating_system}, python {python_version})"
            for operating_system, python_version in itertools.product(
                operating_systems,
                python_versions,
            )
        }
        self.assertEqual(names, expected)
        self.assertEqual(len(names), 6)
        self.assertRegex(
            release_block,
            r"(?m)^    name:\s*pocketchat-release-smoke$",
        )


if __name__ == "__main__":
    unittest.main()
