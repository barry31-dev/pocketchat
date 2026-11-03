# TaskSpec v1

TaskSpec v1 是操作者编写的只读授权文档。它被严格解析并转换为现有
`pocketchat-task-policy.v1`，不新增第二套执行器或权限解释器。已发布的
v1.1.0 制品不含这项未发布源码能力。

## 形状

```json
{
  "schema_version": "pocketchat-task-spec.v1",
  "objective": "write one bounded report",
  "workspace_root": ".",
  "inputs": ["input.md"],
  "budgets": {
    "max_steps": 8,
    "max_tool_calls": 4,
    "max_context_chars": 8000,
    "max_turn_tokens": 2000,
    "max_session_tokens": 6000,
    "max_task_tokens": 12000
  },
  "tools": {"read_file": {"allow_paths": ["input.md"]}},
  "commands": [],
  "acceptance": {"path": "acceptance.json"}
}
```

所有根字段必填且形状封闭。`inputs` 只列出任务可显式读取的工作区相对文件；
`workspace_root` 与 acceptance 路径也必须是工作区相对路径。绝对路径、反斜杠、
空段、`.` 段和 `..` 一律拒绝，不做“规范化后再放行”。

预算沿用现有 policy 的上限与类型规则。`max_session_tokens` 仍是既有的
单步骤 Session 限额；`max_task_tokens` 是可选的整项任务限额：`task exec`
为授权计划建立一份账本，在每次模型请求前预约输入估算与已配置的输出预留；
provider 自报的输入/输出 usage 替代该预约，按任务总余额结算，不能因响应大于
输入估算就误拒绝。真实 usage 超出任务总限额时也先记录消耗，再停止后续工作。
缺失或损坏 usage 保持 unknown，在工具、下一请求或修复 edit 之前失败关闭；
传输失败同样阻断预算模式下的重试，因为消耗未知。估算不等于 provider 硬输出上限，
只有 profile 明确声明支持才发送硬输出参数。带总预算的接续会恢复所选执行报告
持久化的历史账本；账本缺失、损坏或不匹配时明确拒绝（见
[task-exec.zh-CN.md](task-exec.zh-CN.md)）。工具仅支持 `read_file`、create-only
`write_file` 与有界 `memory_search`；读 allowlist 必须属于已声明 inputs。
`write_file.create_only` 只能为 `true`，删除、自由 shell 字符串与未知工具均
拒绝。`commands` 是封闭的 argv 模板列表，每个模板经校验后只能由 TaskPlan
按 id 引用（见 [task-command.zh-CN.md](task-command.zh-CN.md)）。可选
`overwrite_paths` 列出
独立受控编辑器可替换的既有文件；每项必须同时在 `inputs` 与
`tools.write_file.allow_paths` 中（见 [task-edit.zh-CN.md](task-edit.zh-CN.md)）。

## 预览

```bash
pocketchat task spec preview spec.json --json
```

预览校验文档，经 `to_policy()` 调用现有 policy 解析器，并输出等价 policy。
它不创建 task record、trace、session 或 request snapshot，也不调用 provider。
非法输入 exit 2，输出稳定 JSON 错误对象；exit 0 不能外推为任务已执行。

TaskPlan 与受控覆盖是独立 opt-in 模块。命令模板同样是独立 opt-in 执行切片；
预览仍不写文件、不调用 provider。后续切片各自需要独立证据。
