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
（`turn_NNNNNN`，由行位置推导）。

兼容规则：

- 没有 `schema_version` 键的是 v0.1/v0.2 旧文件。照常读取，一字不改；
  conversation / branch id 读作 `not recorded`，turn id 由行位置推导，
  不会为了补键而改写。
- 带未知 `schema_version` 的文件出自更新的 pocketchat，拒绝读取
  （错误信息带版本号，不会被笼统的「no such session」掩盖）。
- 旧文件不能原地升级（append-only）。要在身份模型下继续旧对话，把它复制进
  新会话并保留原件；`branch` 拒绝没有 conversation id 的文件，出于同一理由。

## Trace — `traces/<run-id>.jsonl`

归属：`pocketchat.trace`。格式：Agent Trace Doctor `trace.schema.json` v1
（随包携带于 `pocketchat/schemas/`）。event-only 导出（`export --format
trace`）逐字节复制；schema 与跨事件校验归 `trace-doctor validate`。

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

## 迁移规则

- 旧文件照常读取，绝不回填。
- 迁移到新格式 = 在旧文件旁边写新文件，原件不动。
- 读到不认识的版本就拒绝并报出版本号，而不是对没见过的字段瞎猜。
