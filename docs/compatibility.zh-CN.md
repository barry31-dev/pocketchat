# 兼容与弃用策略（v1.0）

PocketChat 只做 CLI。本页是 `v1.0.x` 产品线的兼容承诺，不承诺 CLI 之外的任何界面。

## 支持矩阵

| 维度 | `v1.0.x` 支持 | 验证 |
|---|---|---|
| Python | CPython 3.9 与 3.13 | 2026-10-29 本机 3.13 全量通过，3.14 全量也通过；3.9 仍待 runner 结果 |
| 操作系统 | Linux、macOS、Windows（运行目标） | 当前本机验证 macOS 3.13/3.14；CI workflow 定义了其他矩阵格，但没有远端绿灯证据 |
| Shell | POSIX shell 与 PowerShell/`cmd.exe` 的普通命令 | CLI 不依赖特定 shell 解析；只验证本机实际跑过的路径 |
| Provider 协议 | HTTP(S) 上的 OpenAI-compatible `/chat/completions` | 本地 stub HTTP 测试；真实路由证据单独记录，不外推 |

未列出的 Python 版本与操作系统不在 `v1.0.x` 承诺内。新增运行环境必须先通过同一份全量测试。

## 稳定 CLI 面

`v1.0.x` 内以下内容稳定：

- 命令名：`init`、`doctor`、`run`、`chat`、`agent`、`show`、`context`、`tool`、`memory`、`delegate`、`export`、
  `branch`、`checkpoint`、`rewind`、`profile`、`compare`、`replay`、`workspace`、
  `history`、`search`、`diff`、`select`、`index`。
- 退出码类别：`0` 成功；`1` run 已启动但失败或被取消；`2` run 之前的
  用法、配置或拒绝错误。
- 带 `--json` 的命令：stdout 只有一个对象，含 `ok` 与 `schema_version`；
  已文档化的 JSON 错误路径不向 stderr 混诊断文本。
- JSON 中的工件引用除非另有文档例外，否则相对工作区。

新增命令属于向后兼容的小版本变化。删除或重命名命令、改变退出码类别、改变已文档化
JSON 字段的含义，都是破坏性变更，必须使用新的大版本。

## 文件格式

`v1.0.x` 保证下列格式可读：

| 工件 | 版本键 | 规则 |
|---|---|---|
| Session | `schema_version: "1"` | 旧文件仍可读；已完成 assistant 行可带 additive `tool_context`；未知新版本拒绝 |
| Trace | Agent Trace Doctor schema `"1"` | 不静默改写事件形状 |
| 请求快照 | `schema_version: "1"` | 精确 body、无 header，replay 校验 hash |
| Context projection sidecar | `schema_version: "pocketchat-context-projection.v1"` | profile 显式启用；只含元数据、独占写在 request snapshot 旁、上限 64 KiB；存在时 replay 校验绑定 |
| Task checkpoint context binding | optional `context_bindings` + `task_budget_sha256` | 旧 checkpoint 仍可读；新模式对缺失或变化的绑定产物拒绝 |
| Comparison manifest | `schema_version: "1"` | 独占创建的证据索引 |
| Selection 记录 | `schema_version: "1"` | 每次对比只记一个选择 |
| 派生索引 | `schema_version: "1"` | 可删除；过期时要求重建 |
| 本地记忆 | `schema_version: "pocketchat-memory.v1"` | 显式记录；损坏/未知 store 拒绝；永不自动注入 |
| 委派 manifest | `schema_version: "pocketchat-delegation.v1"` | 串行有界子任务；独占生成；不支持递归委派 |
| 工具写入 | `write_file` 事件 | 显式 allowlist、UTF-8、原子写、overwrite 可选；无删除/shell/网络 |
| 自动 tool call | `read_file` / `write_file` / `memory_search` | 默认关闭；显式 allowlist；限制调用/结果；guard/参数失败回传有界结果，I/O、大小和预算失败结束 run |
| 自动写工具 | `write_file` create-only | 默认关闭；显式路径 allowlist；无 overwrite/delete |
| 自动记忆工具 | `memory_search` 只读 | 默认关闭；结果有界；不修改记忆 |
| Checkpoint manifest | `schema_version: "pocketchat-checkpoint.v1"` | rewind 前必须匹配边界摘要；未知版本拒绝 |
| Rewind 事务 | `schema_version: "pocketchat-rewind.v1"` | prepared/completed 记录崩溃恢复；源证据永不回写 |

迁移意味着在旧文件旁写新文件。既有证据永不原地改写。读到未知格式版本时拒绝并点名
版本，绝不猜测。

## v1.1 新增（Task Run）

下列工件是 v1.1 新增的，不属于上面 `v1.0.x` 的承诺。它们都是 additive：v1.0 的
workspace 里这些都没有，v1.1 读这样的 workspace 时一字不改。

| 工件 | 版本键 | 规则 |
|---|---|---|
| 任务 policy | `schema_version: "pocketchat-task-policy.v1"` | 操作者提供；每一层字段封闭；未知字段或版本拒绝 |
| 任务记录 | `schema_version: "pocketchat-task.v1"` | 独占创建、经原子替换只追加；状态机封闭；终态不能重启 |
| 任务 checkpoint 绑定 | `pocketchat-checkpoint.v1` + `task_id` / `step_id` / `policy_sha256` | 三个 additive 字段要么都有要么都没有；task resume 拒绝 legacy、不完整或冲突绑定 |

任务记录是证据的索引而不是副本，所以它引用的 `run_id` 与 `checkpoint_id` 仍受上文
trace 与 checkpoint 规则约束。

## 弃用

弃用会先写进 CHANGELOG 与命令手册。`v1.0.x` 内，被弃用的命令或字段至少继续可用
一个小版本。删除它需要新大版本；只有为堵住凭据泄漏、路径逃逸或证据损坏缺陷时才可
例外，并在安全页与 CHANGELOG 记录。

## 不在范围

Web UI、本地 API、桌面打包、远程访问、账号体系、telemetry、通用工具调用、显式本地
工具之外的文件访问、代码执行、递归多 agent 编排与 provider SDK 捆绑，都不属于
`v1.0.x` 兼容承诺。

## 未发布：additive 任务验收

新增 `task verify`、可选 `task run --acceptance` 和 `task report.acceptance`。
不传 flag 的旧 run 保留字段/退出码；显式传入时，执行完成**并且**验收通过才是
`ok`/exit 0。原 task 执行状态与 policy/record/checkpoint/trace v1 形状均不变。
验收 plan/record 使用独立版本键，未知版本拒绝；旧 workspace 没有验收记录就是
`not_recorded`，不是 `passed`。新验收器需要 POSIX descriptor-relative no-follow API；
Windows 当前显式 `unsupported_platform`，不继承普通 CLI 的目标平台承诺。
这是未发布源码新增能力，不追溯改变 v1.1.0 制品保证。完整边界见
[task-acceptance.zh-CN.md](task-acceptance.zh-CN.md)。

## 未发布：additive 记忆检索核心

`pocketchat-memory-retrieval.v1` 是新增的离线检索表面。旧 `memory` 命令和默认
`memory_search` 子串行为不变。`memory recall --dry-run` 只读；只有同时显式开启
layered projection 与 `memory_recall_enabled`，并给出 v2 store 和 workspace id，
召回内容才会进入请求。固定分词、准入、排序、双预算 excerpt、遗漏计数与 stale-cursor
拒绝规则见 [memory-retrieval.zh-CN.md](memory-retrieval.zh-CN.md)。
