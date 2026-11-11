"""The evidence matrix names the current authorized task execution entry."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "pocketchat-evidence-matrix.md"


def usage_gate():
    text = MATRIX.read_text(encoding="utf-8")
    start = text.index("### 使用门槛")
    end = text.index("\n## 4. 入口规则", start)
    return " ".join(text[start:end].split())


class EvidenceMatrixExecutionEntryTests(unittest.TestCase):
    def test_usage_gate_names_task_exec_for_command_templates(self):
        text = usage_gate()
        self.assertIn("`task exec --spec SPEC --plan PLAN --plan-sha256 DIGEST`", text)
        self.assertIn("command templates", text)
        self.assertIn("`task run --policy`", text)
        self.assertIn("不接受 TaskSpec", text)
        self.assertIn("不生成计划、不扩大授权", text)
        self.assertIn("命令成功也不等于任务验收通过", text)

    def test_usage_gate_does_not_require_a_python_only_execution_path(self):
        self.assertNotIn("带命令模板必须走 `task_exec.authorize` + `execute_plan` 的 Python 路径", usage_gate())


if __name__ == "__main__":
    unittest.main()
