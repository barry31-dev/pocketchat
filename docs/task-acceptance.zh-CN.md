# 确定性任务验收

执行 `completed` 只表示有界模型/工具运行正常结束，不表示产物满足任务目标。
验收是操作者显式声明的本地条件，用确定性代码判断，不问模型、不执行 shell。
这是未发布源码能力；公开 v1.1.0 制品不含这些新命令或格式。

## 运行与独立验收

```bash
pocketchat task run "Write report.md and summary.json" \
  --policy policy.json --session audit \
  --acceptance examples/task-acceptance/acceptance.json --json
pocketchat task verify TASK_ID --acceptance acceptance.json --json
pocketchat task report TASK_ID --json
```

`task run` 的 `--acceptance` 可选；不传时既有执行字段与退出码不变。
传入时先解析计划，再调模型；只有执行成功才做一次验收。`task verify` 只接受
`completed` 任务，只追加一次验收，不调用 provider、不修改任务和产物。
活动/失败/取消任务不能验收。执行失败返回 `acceptance.state: not_run`，不写验收记录。

| 结果 | 退出码 | `ok` |
|---|---:|---|
| 执行完成，全部条件 `passed` | 0 | true |
| 执行失败，或验收 `failed` / `refused` / `unknown` | 1 | false |
| 非法 plan/task、不安全记录目录或其他输入拒绝 | 2 | false |

`task run.status` 仍是执行状态；`acceptance.state` 单列验收状态。
执行后记录无法落盘时为 `unknown`、无 record 路径、exit 1，不伪装通过。
JSON stdout 只输出一个对象，已处理错误不写 stderr；缺少必需参数等 argparse
错误仍走普通 stderr/exit 2。人类输出同时打印执行与验收状态。

## 操作者计划

格式为 `pocketchat-acceptance-plan.v1`：

```json
{
  "schema_version": "pocketchat-acceptance-plan.v1",
  "max_file_bytes": 65536,
  "checks": [
    {"id": "report", "kind": "file_exists", "path": "report.md"},
    {"id": "heading", "kind": "text_contains", "path": "report.md", "text": "## Findings"},
    {"id": "ready", "kind": "json_equals", "path": "summary.json", "pointer": "/status", "expected": "ready"}
  ]
}
```

- 1–32 条检查；id 为不超过 64 字符的唯一 ASCII 标识。字段/类型/版本封闭；
  plan 最多 65536 bytes，重复 JSON 键、非有限数和超过 64 层的嵌套拒绝。
- `max_file_bytes` 默认 65536，可显式设 1–1048576 的整数。一次验收同一路径
  只读一次；`file_exists` 也有界读取普通文件，以记录完整 bytes/SHA-256。
- `text_contains` 为 1–4096 字符、严格 UTF-8、大小写敏感精确子串，不是正则或质量评分。
- `json_equals` 读取有限、无重复键的 JSON，按 RFC 6901 JSON Pointer 取值、递归比较。
  `true` 不等于 `1`，`1.0` 可等于 `1`；空 pointer 取整个文档；`~0`/`~1`
  还原 `~`/`/`；数组索引必须规范非负整数，不接受 `01` 或 `-`。

路径相对 task record 的 `workspace_root`。验收计划只授权读取逐条声明的产物，
不扩大模型工具或 read/write allowlist；条件、路径、比较值由操作者提供，不来自模型回复。

## 安全与状态

绝对/越界/Windows drive 路径、反斜杠、歧义路径段和控制字符在输入阶段拒绝。
敏感名字、`.env*`、`pocketchat.json`、`.pocketchat`、`traces`、`requests`、
`sessions` 在检查阶段受保护。符号链接（含父目录）、目录和特殊文件拒绝；
超大文件不生成 hash，不把截断结果当通过。
支持的 POSIX 平台用 descriptor-relative no-follow 读取固定目标目录。
缺少该 API 时显式 `unsupported_platform` / `refused`，不静默退回不安全读取；
新增验收能力的 Windows 运行**未验证且当前拒绝**。

缺文件、UTF-8/JSON 非法、pointer 缺失、值不符是 `failed`；权限/大小/平台拒绝是
`refused`；不可读或观察到并发变化是 `unknown`。聚合优先级
`refused > unknown > failed > passed`，成功不能掩盖拒绝。
CLI 不复制产物正文、文本 needle 或 expected；落盘记录含操作者 plan，因此不要
把凭据或期望保持秘密的比较值放进 plan。

## 历史记录与报告

记录格式 `pocketchat-acceptance.v1`，独占追加到：

```text
.pocketchat/acceptance/<task-id>/<acceptance-id>.json
```

记录 task/policy 身份、canonical task-record/plan SHA-256、执行状态、根目录、
时间、plan 快照、逐项结果以及成功读取的完整 bytes/SHA-256。
不改 task state、session、checkpoint、request、旧记录或 trace。同一计划再验收
也生成新的 `acceptance_id`。

`task report` **不重新检查**产物。它只读记录，验证封闭形状、task 关联、plan hash
及聚合一致性；坏/不可用证据进入 `acceptance.evidence_issues`。
没有记录是 `not_recorded`，不是 `passed`。按规范化为 UTC 的实际时刻选最近结果，不按字符串排序；最新同刻出现
冲突状态为 `unknown`（`ambiguous_record_order`），不猜先后。
最多读取 256 条记录，超限显式 `history_limit_exceeded`，不是部分通过。
task-record digest 改变会使旧验收失效；产物变化不回写历史观察，要观察当前文件
请再次 `task verify`。若安全验收读取不可用，原执行报告仍返回，验收节为
`unknown` / `acceptance_unavailable`，不把可选验收器失败变成旧报告的输入错误。
`task report` 的 exit 0 仍仅表示报告已读取，不表示验收通过。

记录**不是签名**。可编辑文件和可重算 hash 不证明对抗性防篡改、创作归属、
任务语义全量成功或文件系统沙箱。预先存在的文件也能满足条件；通过只证明本次
有界本地观察上的声明条件成立。

## 离线演示

```bash
python3 examples/task-acceptance/demo.py
python3 examples/task-acceptance/demo.py --output /tmp/pocketchat-acceptance-demo
```

四条确定性案例：成功、缺产物、JSON 不符、敏感路径拒绝。模型为进程内 stub，
不是实际 provider 核验。`--output` 使用全新或空目录，保留
任务/session/trace/request/验收记录供回查。

本功能不做 shell、代码执行、网络工具、自动评分、修改失败产物、自动重试、UI 或公开发布。

## 引用已有命令证据

验收计划可增加 `command_succeeded`：

```json
{"id":"tests","kind":"command_succeeded","step_id":"run_tests"}
```

它只读取 `.pocketchat/task-reports/` 中已存在的 command step，并校验命令已完成、未超时、archive 可回查；不会重新执行命令。命令非零退出为 `failed`，缺失/不可读/不属于该 task 为 `unknown` 或 `refused`，绝不包装成 `passed`。
