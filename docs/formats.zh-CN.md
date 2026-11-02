# 文件格式

本文描述 `pocketchat` 写出的每一种文件、各自携带的版本键，以及版本之间的兼容
规则。内容与本版代码一致；每节注明拥有该格式的模块。

统领所有格式的规则：**已经写出的文件绝不改写。** 会话文件、trace、请求快照与
对比 manifest 都是独占创建、只追加；迁移读取旧文件、写出新文件，原件原地保留。

## 会话文件 — `sessions/<name>.jsonl`

归属：`pocketchat.sessions`。格式版本：**1**（header 键 `schema_version`，自
v0.3 身份层起写入；更早写的文件没有这个键，属旧格式）。

会话文件是 JSONL：一行 header，之后每条消息一行。

Header（格式版本 1）：

| 键 | 含义 |
|---|---|
| `session` | 拥有这段对话的 run id |
| `trace` | trace 路径，相对本文件 |
| `created` | 会话开始时间 |
| `pocketchat_version` | 写入方的程序版本 |
| `schema_version` | 文件格式版本（`"1"`） |
| `conversation_id` | 本文件所属的对话 |
| `branch_id` | 该对话中的路径 |
| `parent_branch_id` | 仅分支文件携带 |
| `parent_turn_id` | 本文件从父分支的哪个 turn 继续 |

消息行：`role`（`user` 或 `assistant`）、`content`、`turn_id`
（`turn_NNNNNN`，由行位置推导）。已完成的 assistant 行可带 additive
`tool_context`：该 turn tool loop 中按实际顺序发送的 provider messages，
包括 assistant `tool_calls` 与一一匹配的 `role: tool` 结果，位于最终
assistant `content` 之前。新 reader 重建模型上下文时校验并展开它；只读
`role`/`content` 的 reader 仍看到同一组 user/最终 assistant 交换。

兼容规则：

- 没有 `schema_version` 键的是 v0.1/v0.2 旧文件。照常读取，一字不改；
  conversation / branch id 读作 `not recorded`，turn id 由行位置推导，
  不会为了补键而改写。
- 带未知 `schema_version` 的文件出自更新的 pocketchat，拒绝读取
  （错误信息带版本号，不会被笼统的「no such session」掩盖）。
- 旧文件不能原地升级（append-only）。要在身份模型下继续旧对话，把它复制进
  新会话并保留原件；`branch` 拒绝没有 conversation id 的文件，出于同一理由。
- `tool_context` 在 schema 1 内是可选 additive 字段；没有该字段的文件绝不补写。
  形状错误或 tool call/result 未配对的上下文一律拒绝，不会残缺地发给 provider。

## Trace — `traces/<run-id>.jsonl`

归属：`pocketchat.trace`。格式：Agent Trace Doctor `trace.schema.json` v1
（随包携带于 `pocketchat/schemas/`）。event-only 导出（`export --format
trace`）逐字节复制；schema 与跨事件校验归 `trace-doctor validate`。

流式响应中，`time_to_first_token_ms` 仍表示首个非空 reasoning 或 content
delta。additive 的 `first_delta_kind`、`time_to_first_reasoning_ms`、
`time_to_first_content_ms` 分别说明两类 delta 的时点。它们描述 PocketChat
recorder，不代表 provider 官方 TTFT 定义；对应 delta 未到达时字段不写。

`retry.attempt` 与 `fallback.activated` 是 additive trace 事件。前者记录计划中的
物理请求重试与 backoff，后者记录主 profile 预算耗尽后的一跳 fallback 激活；
两者都不表示恢复成功，最终结果仍只看终止 `run.*` 事件。

## 请求快照 — `traces/requests/<run-id>.turn-N.json`

归属：`pocketchat.request`。格式版本：**1**。

| 键 | 含义 |
|---|---|
| `schema_version` | `"1"` |
| `run_id` | 发出该请求的 run |
| `profile` | 所用 profile（可为空） |
| `created` | 发送时间 |
| `request` | 实际发出的 body，不含任何 header |
| `request_sha256` | 规范化 body 字节的 SHA-256 |

摘要只覆盖 body（`canonical_bytes`：键排序、固定分隔符、结尾换行）。body
含所配置凭据的快照永远不会写出——写入方 fail-closed。快照独占创建、绝不改写；
`replay --dry-run` 重算摘要，不一致就报告，不信任存量值。

## 对比 manifest — `comparisons/<comparison-id>.json`

归属：`pocketchat.compare`。格式版本：**1**。

键：`schema_version`、`comparison_id`、`created`、`prompt`、`base_context`
（`session` 引用、`messages` 计数、`context_chars`）、`runs`——每个 profile
一条，含 `profile`、`run_id`、`trace`、`request`、`status`、`duration_ms`、
`usage`、`cost_usd`、`output_summary`、`error`。manifest 是索引：只指路，
不复制证据。独占创建、绝不改写，所以它记录的测量不可被修订。

## 恢复记录 — `.pocketchat/recovery/`

归属：`pocketchat.recovery`。checkpoint manifest 的 schema version 是
`pocketchat-checkpoint.v1`，rewind 事务是 `pocketchat-rewind.v1`。
checkpoint 记录源 session/trace、已完成 `turn_id`、消息数，以及精确消息前缀的
SHA-256（存在 additive `tool_context` 时一并覆盖）；rewind 事务记录 checkpoint、生成目标、branch 身份和
`prepared`/`completed` 状态。

checkpoint manifest 只创建一次、不回写。rewind 事务在生成目标通过校验后有意从
`prepared` 更新为 `completed`，这是崩溃恢复边界。两类记录都不改写源 session 或
trace；未知 schema version 一律拒绝。

Task Run checkpoint 在同一 manifest 上增加 `task_id`、`step_id` 与
`policy_sha256`。三个字段要么都有、要么都没有：legacy checkpoint 保持原
checkpoint/rewind 语义，`task resume` 则拒绝 legacy 或不完整绑定。task checkpoint
只能写在最新 task step 以 `completed` 或 `refused` 结束之后，checkpoint step 自身也
计入 `max_steps`。resume 在第一个新字节写入前校验 policy 摘要、task record 状态与顺序、
checkpoint 身份和 tool context；已 prepared 的 rewind 事务会被恢复，而不是重复创建。

## 任务 policy — 操作者提供的 JSON

归属：`pocketchat.task_policy`。格式版本：**`pocketchat-task-policy.v1`**
（`schemas/task.schema.json` 的 `$defs.policy`）。

task policy 是授权一次有界任务执行的那份文件，因此读取是严格的：任何一层的未知
字段都拒绝，不忽略。一份会默默接受未声明字段的 policy，读起来会比操作者实际写下的
授权更宽 —— 这正是要挡住的失败。

| 键 | 含义 |
|---|---|
| `schema_version` | `pocketchat-task-policy.v1` |
| `objective` | 这个任务要成立的那一句目标 |
| `workspace_root` | 任务工作的目录；`.` 表示 workspace 本身 |
| `max_steps` | 已记录步数的硬上限（1-64）；到上限即停 |
| `max_tool_calls` | 整个任务的工具调用次数（1-64），失败调用也计入 |
| `max_context_chars` | 交给既有压缩逻辑的字符预算 |
| `max_turn_tokens` | 单 turn token 预算，由既有护栏执行 |
| `max_session_tokens` | 可选的整任务预算；不得小于 `max_turn_tokens` |
| `tools` | `read_file`、`write_file`（create-only）与 `memory_search` 的授权 |
| `delegate` | 可选的有限串行委派，`enabled` 默认为 false |

allowlist 路径是 workspace 相对路径。绝对路径、空段、`.` 段与任何 `..` 一律拒绝，
而不是规范化后放过：授权是与工具层解析出的结果比对的，一条只有被改写之后才安全的路径，
等于一条没人读过的路径。`write_file` 不能被放宽为可覆盖。只有这三个工具存在；policy
写了包里没实现的工具，是拒绝，不是空操作。

## 任务记录 — `.pocketchat/tasks/<task-id>.json`

归属：`pocketchat.tasking`。格式版本：**`pocketchat-task.v1`**
（`schemas/task.schema.json` 的 `$defs.task_record`）。

一个任务一条记录，承载没有其他工件拥有的事实：任务走了多少步、当前处于什么状态、
为什么停下。记录独占创建，之后通过原子替换追加，所以崩溃只会留下旧记录或新记录，
不会留下半份。读取方在报告前校验封闭的记录与 step 形状：文件里的 `task_id` 必须与
文件名一致，step 序号必须连续且不超过声明的预算；终态必须带非空的 `terminal_reason`，
活动态不得带终态原因。

| 键 | 含义 |
|---|---|
| `schema_version` | `pocketchat-task.v1` |
| `task_id` | `task_<hex>`；同时是记录文件名主干 |
| `created` | RFC 3339 UTC |
| `objective` | 从 policy 复制 |
| `policy_sha256` | policy 规范化文档的摘要 |
| `state` | 见下方状态 |
| `max_steps` / `max_tool_calls` | policy 声明的上限 |
| `workspace_root` | 任务工作的根目录 |
| `terminal_reason` | 只在记录写了原因时出现；绝不推断 |
| `steps` | 追加列表，含 `index`、`kind`、`state`，可选 `tool`、`detail`、`run_id`、`checkpoint_id`、`recorded` |

状态与封闭转换表：

```text
created      --start-->      running
running      --checkpoint--> checkpointed --resume--> running
running      --interrupt-->  interrupted  --resume--> running
running      --complete-->   completed
running      --fail------>   failed
running|checkpointed|interrupted --cancel--> cancelled
```

`created`、`running`、`checkpointed`、`interrupted` 是活动状态；`completed`、
`failed`、`cancelled` 是终态，不能重启。转换表没写的事件被拒绝而不是被忽略；
进入终态必须带已记录的原因。

记录是索引，不是副本：一个 step 指出 `run_id`（其 trace 持有模型与工具证据）与
`checkpoint_id`（其 manifest 持有恢复边界），不复制任何 trace 事实，因此不可能与
trace 说法不一致。`policy_sha256` 让 resume 诚实 —— 在摘要不同的 policy 下 resume，
是另一个任务，会被拒绝。
checkpoint 的 `task_id` 同样要在写任何恢复目标之前，与 resume 命令点名的 task
核对一致。

## 选择记录 — `comparisons/<comparison-id>.selection.json`

归属：`pocketchat.selection`。格式版本：**1**。

键：`schema_version`、`comparison_id`、`run_id`、`profile`、`selected_at`，
可选 `note`。一次对比一个决定，独占创建：manifest 不改写，第二次 `select`
被拒绝。`--into` 把选中的交换写进会话文件（见上）；把该会话与对比连起来的
正是 selection 文件。

## 索引 — `index.sqlite`

归属：`pocketchat.indexing`。格式版本：**1**（在 `meta` 表）。

对证据文件的派生索引：每个对话分支、对比、对比 run、已报告的问题各一行，
外加每个源文件的 sha256。索引不含任何独立事实，没有命令读它——删掉它所有
回答不变。重建永远是全量（无增量）；记录的摘要与工作区失配时报告 `stale`
并以非零退出。本版实测（的 SQLite）上同字节两次构建文件字节相同（索引不含
时钟）；跨 SQLite 版本只承诺逻辑等价——行集一致。

## CLI 输出记录

`workspace`、`history`、`search`、`diff` 与 `index --json` 打印的 JSON 记录，
携带版本键的地方 `schema_version` 均为 `1`（工作区图与查询记录携带；index
报告不携带）。其中路径都相对工作区；除文件本身记录的时刻外不含时钟。

## 兼容策略

v1.0 的稳定兼容承诺、运行矩阵与弃用规则见
[`compatibility.zh-CN.md`](compatibility.zh-CN.md)。本页列出的格式版本在 v1.0.x
内稳定；新增字段属于向后兼容，改变既有字段含义需要新大版本。

## 迁移规则

- 旧文件照常读取，绝不回填。
- 迁移到新格式 = 在旧文件旁边写新文件，原件不动。
- 读到不认识的版本就拒绝并报出版本号，而不是对没见过的字段瞎猜。

## 未发布：验收计划与历史记录

`pocketchat-acceptance-plan.v1` 是独立封闭操作者输入，不扩展
`pocketchat-task-policy.v1`。`pocketchat-acceptance.v1` 记录独占追加在
`.pocketchat/acceptance/<task-id>/<acceptance-id>.json`，绑定 canonical
task-record/policy/plan 身份、时间、执行状态及逐项 bytes/SHA-256。
原 task/step/checkpoint/trace v1 格式不变；`schemas/acceptance.schema.json`
声明形状，运行时还校验唯一 id、hash、状态/原因匹配和聚合结果。
落盘记录内含操作者 plan；公开 JSON 不复制 plan 或产物正文。
字段语义、预算、历史报告与平台边界只见
[task-acceptance.zh-CN.md](task-acceptance.zh-CN.md)。
