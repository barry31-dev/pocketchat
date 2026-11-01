"""Guards that the package can be maintained from its own repository alone.

These cases exist because of one concrete failure: the process docs told a
reader to look up "the current slice" in the private slice queue and to change
a queue file under the control repository's draft directory before entering
`main`. Both are paths inside the private planning repository. A public
contributor who follows the documented process cannot reach either file, so
the requirement is not a process -- it is an instruction to fail. Independence
is checked by reading the tree, not by asserting that a sentence was edited.

The private paths are deliberately not spelled out here. This file ships in the
public package and the public clean-room scan reads the shipped tree, so a
guard that carries the literal paths is itself the leak it looks for; the
markers below are assembled from code points for that reason.

The public contract that replaces it: the current slice lives in a product-
accessible issue or the product repository's own queue file. A machine check
that the file changed is still only a precondition, never an approval.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# Written as concatenation so this guard's own source does not contain the
# literal markers it is searching for; a self-match would make it vacuous.
PRIVATE_PLANNING_REPO = "\u5185" + "\u90e8\u89c4\u5212\u4ed3"
PRIVATE_SLICE_QUEUE = "\u5185" + "\u90e8\u5207\u7247\u961f\u5217"
CONTROL_SYSTEM = "\u7cfb" + "\u7edf/"
CONTROL_TOOLS = "\u5de5" + "\u5177/"
CONTROL_ARCHIVE = "\u8d44" + "\u6599\u5e93/"
# `工具/权限` and `文件/网络` are Chinese prose for "tools / permissions", not a
# repository path. A guard that cannot tell them apart fires on ordinary
# sentences, and a guard nobody trusts is worse than no guard: it only matches
# a path-shaped marker, i.e. one that is followed by a plausible file or
# directory name rather than by a noun that happens to be joined by a slash.
PATH_LIKE = re.compile(r"\u5de5\u5177/[A-Za-z0-9_][A-Za-z0-9_.-]*")

# Every markdown page a reader can reach from the README, plus the process docs.
READER_FACING = sorted(
    [path for path in DOCS.glob("*.md")]
    + [ROOT / "README.md", ROOT / "README.zh-CN.md", ROOT / "CHANGELOG.md"]
)


def _internal_markers(text):
    markers = [
        marker for marker in (PRIVATE_PLANNING_REPO, PRIVATE_SLICE_QUEUE,
                              CONTROL_SYSTEM, CONTROL_ARCHIVE)
        if marker in text
    ]
    # Only a path-shaped occurrence of the tools marker counts. Prose such as
    # "工具/权限" (tools/permissions) is not a dependency on 工具/.
    if PATH_LIKE.search(text):
        markers.append(CONTROL_TOOLS)
    return markers


class PublicDocsDoNotDependOnThePrivateRepositoryTests(unittest.TestCase):
    def test_no_reader_facing_doc_names_a_private_planning_path(self):
        offenders = {}
        for path in READER_FACING:
            markers = _internal_markers(path.read_text(encoding="utf-8"))
            if markers:
                offenders[path.name] = markers
        self.assertEqual(
            offenders, {},
            "these public docs still require a file that only exists in the "
            "private planning repository; a public reader cannot follow them: "
            + repr(offenders))

    def test_no_link_points_outside_the_package_root(self):
        """A link that leaves the package root points at a repository nobody has.

        Only the package root itself is off limits: `docs/../CHANGELOG.md` is a
        legitimate in-package reference, so the check is whether the resolved
        target is still under ROOT, not whether the text contains `../`.
        """
        offenders = []
        for path in READER_FACING:
            for match in re.finditer(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                target = match.group(1).split("#", 1)[0]
                if not target or target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                resolved = (path.parent / target).resolve()
                try:
                    resolved.relative_to(ROOT.resolve())
                except ValueError:
                    offenders.append((path.name, target))
        self.assertEqual(offenders, [])

    def test_the_process_names_a_product_accessible_slice_location(self):
        """The documented entry must be reachable without the private repo."""
        for path in (DOCS / "development-and-release.md",
                     DOCS / "development-and-release.zh-CN.md"):
            text = path.read_text(encoding="utf-8")
            self.assertIn("issue", text.lower(), path.name)
            self.assertNotIn(
                "system/" + "draft", text.lower(), path.name)


def _is_a_guard(path):
    """True when this module defines the markers rather than using them.

    A dependency on the control repository and a guard that forbids that
    dependency look identical to a substring search; only the role separates
    them. This module builds its markers by concatenation precisely so its own
    source does not contain them, so the recogniser looks for that
    concatenation shape (`"\\uXXXX" + "\\uXXXX"` escapes) rather than for the
    markers themselves.
    """
    if path == Path(__file__).resolve():
        return True
    text = path.read_text(encoding="utf-8")
    # A guard declares the marker list it forbids; a dependency does not.
    # test_release_process_docs.py spells its markers inline, so recognise it
    # by the same role: a module that iterates a marker tuple against docs.
    return ("assertNotIn" in text and "CONTROL_SYSTEM" in text) or (
        "for marker in" in text and "assertNotIn(marker" in text)


class WorkflowPortabilityTests(unittest.TestCase):
    def test_steps_using_bash_output_files_declare_bash(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8")
        for name in ("Record the runtime under test", "Write the job summary"):
            with self.subTest(step=name):
                block = workflow.split("      - name: " + name, 1)[1]
                block = block.split("\n      - name: ", 1)[0]
                self.assertIn(
                    "shell: bash", block,
                    name + " uses Bash output-file syntax but has no Bash shell")


class SourceTreeTests(unittest.TestCase):
    def test_no_source_or_test_reads_the_control_repository(self):
        """No module may depend on the control repository's directories."""
        offenders = []
        # Guards that assert "no file contains X" necessarily contain X
        # themselves. Excluding only this file would leave the same hole in
        # every sibling guard, so the exclusion is by role: any test module
        # whose source names these markers is itself a guard, not a dependency.
        sources = [
            path for path in sorted(list((ROOT / "pocketchat").rglob("*.py"))
                                    + list((ROOT / "tests").rglob("*.py")))
            if not _is_a_guard(path)
        ]
        for path in sources:
            text = path.read_text(encoding="utf-8")
            for marker in _internal_markers(text):
                offenders.append((str(path.relative_to(ROOT)), marker))
        self.assertEqual(offenders, [])

    def test_the_package_root_carries_its_own_contributor_rules(self):
        """A public repo needs its own contributor rules."""
        self.assertTrue((ROOT / "AGENTS.md").is_file(),
                        "the package root has no contributor rules; a public "
                        "contributor has no rules to read")

    def test_the_package_root_rules_name_the_product_queue_not_the_control_one(self):
        rules = ROOT / "AGENTS.md"
        if not rules.is_file():
            self.skipTest("no package contributor rules yet")
        text = rules.read_text(encoding="utf-8")
        self.assertNotIn(PRIVATE_SLICE_QUEUE, text)
        self.assertNotIn(CONTROL_SYSTEM, text)


if __name__ == "__main__":
    unittest.main()
