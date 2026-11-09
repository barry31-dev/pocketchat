"""Guards for the long-lived PocketChat development/release process docs."""
from pathlib import Path
import unittest

DOCS = Path(__file__).resolve().parents[1] / "docs"
EN = DOCS / "development-and-release.md"
ZH = DOCS / "development-and-release.zh-CN.md"
QUEUE = DOCS / "slice-queue.md"
RELEASE_EN = DOCS / "release-1.0.md"
RELEASE_ZH = DOCS / "release-1.0.zh-CN.md"

# Built by concatenation for the same reason the sibling independence guard
# does it: this file ships in the public package, and a file that carries the
# literal private paths is itself the leak it is looking for. The public
# clean-room scan reads the shipped tree, so the markers must not appear here.
PRIVATE_PLANNING_REPO = "\u5185" + "\u90e8\u89c4\u5212\u4ed3"
PRIVATE_SLICE_QUEUE = "\u5185" + "\u90e8\u5207\u7247\u961f\u5217"
CONTROL_SYSTEM = "\u7cfb" + "\u7edf/"
CONTROL_ARCHIVE = "\u8d44" + "\u6599\u5e93/"


class ReleaseProcessDocsTests(unittest.TestCase):
    def test_section_numbers_are_unique_within_each_process_doc(self):
        import re
        for path in (EN, ZH):
            headings = re.findall(r"^### (\d+\.\d+)", path.read_text(encoding="utf-8"), re.M)
            self.assertEqual(len(headings), len(set(headings)), (path, headings))

    def test_both_process_docs_exist_and_share_section_owner(self):
        self.assertTrue(EN.is_file())
        self.assertTrue(ZH.is_file())
        en = EN.read_text(encoding="utf-8")
        zh = ZH.read_text(encoding="utf-8")
        for marker in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.",
                       "## 6.", "## 7.", "## 8.", "## 9.", "## 10."):
            self.assertIn(marker, en)
            self.assertIn(marker, zh)

    def test_completion_and_checkpoint_record_the_working_directory(self):
        en = EN.read_text(encoding="utf-8")
        zh = ZH.read_text(encoding="utf-8")
        for text in (en, zh):
            self.assertIn("Working directory" if text is en else "工作目录", text)
            self.assertIn("cwd", text)

    def test_process_distinguishes_uncovered_path_from_missing_layer(self):
        en = EN.read_text(encoding="utf-8")
        zh = ZH.read_text(encoding="utf-8")
        self.assertIn("uncovered path", en.lower())
        self.assertIn("缺失的路径", zh)

    def test_process_names_the_package_root_for_test_discovery(self):
        self.assertIn("from the package root", EN.read_text(encoding="utf-8").lower())
        self.assertIn("包根", ZH.read_text(encoding="utf-8"))

    def test_process_requires_an_observed_red_before_implementation(self):
        en = EN.read_text(encoding="utf-8")
        zh = ZH.read_text(encoding="utf-8")
        self.assertIn("before", en.lower())
        self.assertIn("do not prove", en.lower())
        self.assertIn("先单独运行测试看它红", zh)
        self.assertIn("不能证明测试真的能失败", zh)

    def test_process_uses_a_risk_scaled_review_ladder(self):
        en = " ".join(EN.read_text(encoding="utf-8").split())
        zh = " ".join(ZH.read_text(encoding="utf-8").split())
        for text in (en, zh):
            for phrase in ("Review Ladder", "low", "medium", "high",
                           "quick", "standard", "deep", "fresh-context"):
                self.assertIn(phrase, text)
        self.assertIn("Risk is set by the change", en)
        self.assertIn("风险由改动决定", zh)

    def test_review_record_blocks_findings_and_caps_rounds(self):
        en = " ".join(EN.read_text(encoding="utf-8").split())
        zh = " ".join(ZH.read_text(encoding="utf-8").split())
        self.assertIn("An open `blocker` or `major` finding blocks landing", en)
        self.assertIn("after at most three rounds", en)
        self.assertIn("fingerprint", en)
        self.assertIn("`blocker` 或 `major` 的 open finding 阻止合入", zh)
        self.assertIn("最多 3 轮", zh)
        self.assertIn("fingerprint", zh)
        self.assertIn("The merge entry validates the whole record", en)
        self.assertIn("Inspecting only the findings list is not a review gate", en)
        self.assertIn("recorded paths must exactly match the task diff", en)
        self.assertIn("合并入口必须校验**整份记录**", zh)
        self.assertIn("record paths 必须与 task diff", zh)
        self.assertIn("只检查 findings 列表不算审查门", zh)

    def test_review_separates_machine_evidence_from_human_approval(self):
        en = " ".join(EN.read_text(encoding="utf-8").split())
        zh = " ".join(ZH.read_text(encoding="utf-8").split())
        self.assertIn("A bot finding or approval never replaces a required human approval", en)
        self.assertIn("机器人 finding 或 approval 不能顶替要求的人工批准", zh)
        self.assertIn("Every change receives an automated review", en)
        self.assertIn("每个改动都要经过自动审查", zh)
        self.assertIn("commit / candidate SHA", en)
        self.assertIn("commit / candidate SHA", zh)
        self.assertIn("no open blocking finding", en)
        self.assertIn("没有 open blocking finding", zh)

    def test_product_changes_require_a_slice_queue_change(self):
        """The queue rule must survive being moved out of the private repo.

        It used to name a queue file under the control repository's draft
        directory, a path only the private planning repository has. The rule
        is still required;
        what changed is where the current slice lives. The assertions below
        check the rule, not the old path, so this guard cannot pass by
        re-pointing at a file a public contributor cannot read.
        """
        en = " ".join(EN.read_text(encoding="utf-8").split())
        zh = " ".join(ZH.read_text(encoding="utf-8").split())
        self.assertIn("A readable slice contract is required before implementation", en)
        self.assertIn("does not require a separate planning PR", en)
        self.assertIn("does not edit the shared queue merely to claim or complete work", en)
        self.assertIn("disjoint `write_scope`", en)
        self.assertIn("docs/slice-queue.md", en)
        self.assertIn("machine confirmation that the file or issue changed", en)
        self.assertIn("is a precondition, not approval", en)
        self.assertIn("delivery history", en)
        self.assertIn("开始实现前必须有可读的短任务契约", zh)
        self.assertIn("不要求先单独合入规划 PR", zh)
        self.assertIn("不会只为领取或完成任务而修改共享队列", zh)
        self.assertIn("`write_scope` 不相交", zh)
        self.assertIn("docs/slice-queue.md", zh)
        self.assertIn("不能把“有变更”写成“已批准”", zh)
        self.assertIn("已交付历史", zh)

    def test_slice_queue_declares_parallel_branch_and_write_scope_contract(self):
        text = QUEUE.read_text(encoding="utf-8")
        for field in ("`branch`", "`write_scope`", "`depends_on`", "`approved_by`"):
            self.assertIn(field, text)
        self.assertIn("before a development branch is created", text)
        self.assertIn("does not edit this file", text)
        self.assertIn("does not mirror live claim or lease state", text)
        self.assertIn("same path or an ancestor/descendant path", text)

    def test_the_process_never_names_a_private_repository_path(self):
        """A process a public reader cannot follow is not a process.

        Scope is the two process pages. This guard's own source is excluded:
        it necessarily contains the markers it searches for, and a self-match
        would make it vacuous.
        """
        for path in (EN, ZH):
            text = path.read_text(encoding="utf-8")
            for marker in (CONTROL_SYSTEM, CONTROL_ARCHIVE,
                           PRIVATE_PLANNING_REPO, PRIVATE_SLICE_QUEUE):
                self.assertNotIn(marker, text, (path.name, marker))

    def test_process_covers_harness_release_and_rollback(self):
        text = EN.read_text(encoding="utf-8")
        for phrase in ("Harness practices", "Release candidate", "Publication",
                       "Rollback and maintenance", "minimal completion card"):
            self.assertIn(phrase.lower(), text.lower())

    def test_version_checklists_point_to_the_general_process(self):
        for path in (RELEASE_EN, RELEASE_ZH):
            text = path.read_text(encoding="utf-8")
            self.assertIn("development-and-release", text)
            self.assertIn("not the general process" if path == RELEASE_EN else "不是通用流程", text)

    def test_public_docs_do_not_link_to_internal_paths(self):
        internal_roots = ("../../" + "系" + "统/", "../../" + "资" + "料库/")
        for path in DOCS.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            for marker in internal_roots:
                self.assertNotIn(marker, text, str(path))
            self.assertNotIn("/" + "Users/", text, str(path))

    def test_no_current_running_policy_regression_in_new_docs(self):
        for path in (EN, ZH):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("static review only", text)
            self.assertNotIn("不执行任何测试", text)
            self.assertNotIn("本轮不运行", text)


if __name__ == "__main__":
    unittest.main()
