# PocketChat Evidence Matrix

> **文档性质**：pc CLI-only 能力的证据登记与实现缺口表，不是产品范围授权。
> **维护范围**：`开源/pocketchat/` 中 trace、usage、TTFT、失败分类、上下文压缩等证据字段。
> **维护日期**：2026-11-01

## 1. 证据编号

| 编号 | 含义 |
|---|---|
| D | 官方规范 |
| C | PocketChat 当前代码/测试/schema |
| R | 本机顶级 Agent 逆向结论 |
| V | 本机运行实测 |
| O | 热门开源库机制参考 |

每一行同时记录**来源类型**、**验证状态**与**适用范围**。`REVERSE_REQUIRED` 表示该字段
尚无足够证据进入实现；`已交付` 只表示 PocketChat 当前树里存在，不代表外部 provider 已验证。

## 2. 已实现字段

| 字段/契约 | 落点 | 证据 | 状态 | 适用范围与缺口 |
|---|---|---|---|---|
| `model.response.completed.usage` | `provider.py` `_parse_response`/`_stream_response`；`session.py` `_turn`；`schemas/trace.schema.json` | C provider/session/schema；V `tests/test_kernel.py` | 已交付 | v1.0 契约：provider 缺报时计数为 0，不估算。只覆盖当前 OpenAI-compatible 归一化层。 |
| `usage_reported` | 同上 | C provider/session/schema；V `test_usage_reported_flag_distinguishes_missing_from_real_zero`、`test_usage_reported_is_declared_by_the_schema`；D `docs/provider-adapters.md`/`docs/cli.md` | 已交付 | additive 字段，只区分 provider 是否返回 usage 对象；未改旧 0 语义。未验证真实托管 provider。 |
| `time_to_first_token_ms` | `provider.py` `_stream_response`；`session.py` `_turn`；trace schema | C provider/session/schema；V `test_stream_records_time_to_first_token`、`test_streamed_trace_has_ttft_and_nonstreamed_trace_does_not` | 已交付 | 兼容字段：只对首个非空 content/reasoning delta 记录；非流式与无 delta 不写。 |
| `first_delta_kind` / `time_to_first_reasoning_ms` / `time_to_first_content_ms` | `provider.py` `_stream_response`；`session.py` `_turn`；trace schema | C provider/session/schema；V `test_stream_separates_first_reasoning_and_content_deltas`、`test_ttft_delta_semantics_are_declared_by_the_schema` | 已交付（CAND-8） | PocketChat recorder 分别记录首个非空 reasoning/content delta 的时点；不声称托管 Provider 的官方首包语义。 |
| `model.response.failed.retryable` | `provider.py` `_http_error`；`session.py` `_complete_attempt`/`_turn`；trace schema | C provider/session/schema；V `test_failed_response_records_whether_the_error_is_retryable`；R R-07 | 已交付 | 408/425/429/5xx=true，其余 4xx/畸形/中断=false；字段是 retry/fallback 状态机输入，不是恢复成功。 |
| `retry.attempt` / `fallback.activated` | `session.py` `_turn`；trace schema | C session/schema；V `tests/test_recovery_policy.py` | 已交付（RECOVERY-2） | 默认 `retry_max_attempts=1` 不重试；只有 retryable 失败消耗预算；fallback 一跳、独立预算，事件不代表成功。 |
| `context.compacted` payload | `context.py` `apply_budget`；`session.py` `_context_with_budget`；trace schema | C context/session/schema；V `test_every_context_compacted_field_is_declared_by_the_schema` 与压缩用例；R R-06/R-118 | 已交付 | `estimated_tokens_*` 是本地估算，不是 provider 实报；不含 trigger/phase/attempt 二分。 |
| session `tool_context` | `sessions.py` additive assistant field / `read_context_messages`；`session.py` resume；`branching.py` copy；`service.py` context preview；`tests/test_session.py` | C sessions/session/branching/service；V `tests/test_session.py` | 已交付（CONTEXT-2） | schema 1 additive；保存已完成 tool loop 的 assistant `tool_calls` 与配对 tool results；同进程、进程续跑、branch/rewind 派生和 context preview 使用同一重建顺序；旧文件不补写，未配对/畸形上下文拒绝。 |
| `attempt_index` / `attempt_scope` | `session.py` request/completed/failed payloads；`schemas/trace.schema.json` | C session/schema；R `119` §4/§7（四种计数不可互推）；V `test_attempt_index_*`；本机 newnewrouter trace `trace-doctor validate` | 已交付（PC-BASE-1） | 只表示一个 turn 内的物理请求序号，从 1 起算；不表示 retry_count，也不证明执行过重试或 fallback。旧 trace 缺字段仍可读。 |
| layered context projection / sidecar | `context_projection.py` `project_context`；`session.py` `_context_with_budget`/`_write_projection_sidecar`；`service.py` `context_preview`；trace schema | C projection/session/service/schema；V `tests/test_context_projection.py`、`tests/test_context.py` | 已交付（PC-CTXPROJ-1，source/offline） | Profile opt-in；固定指令/任务状态、完整近期 exchange、证据元数据与省略原因均有确定性 manifest；sidecar 只存元数据并绑定 request digest；preview 与请求共用历史选择。真实 provider、跨平台和真实收益未验证。 |
| memory v2 parser / pure migration | `memory_migration.py` `parse_v2_record`/`migrate_v1_to_v2`；`tests/test_memory_migration.py`；`docs/formats.md` | C parser/migration；V `tests/test_memory_migration.py` | 已交付（PC-META-1，source/offline） | v2 记录封闭校验、source/verification/admission 分离；v1 只读迁移到新文件，legacy 固定为 unverified candidate；未知 v1 字段、重复 source ref、自引用生命周期、工作区外或 symlink 目标均拒绝。v2 尚未接入 CLI、召回或 prompt；真实 provider、跨平台和真实数据迁移未验证。 |
| `show` 压缩摘要 | `service.py` `session_overview`/`show_record`；`cli.py` `_cmd_show` | C service/cli；V `test_show_surfaces_a_context_compaction_summary`、`test_show_json_carries_the_context_compaction_payload` | 已交付 | 人类与 JSON 均只投影压缩事件 payload；其他事件不投影正文。 |
| TUI 状态规则事实 | `tui.py` `trace_status_facts`/`_status_bar` | C trace/tui；V `test_trace_status_facts_*`、`test_tui_status_bar_shows_trace_facts_and_unknown_markers` | 已交付（TUI-1） | 只聚合 trace 中 usage/TTFT/attempt/compaction；`cost~` 是配置价格估算，不是账单；缺 usage、价格或 TTFT 显示 `?`，`req` 不是 retry count。 |
| checkpoint / rewind 边界 | `recovery.py`、`cli.py` checkpoint/rewind；`.pocketchat/recovery/` manifests | C recovery/cli/formats；V `tests/test_recovery.py`、真实子进程 round-trip | 已交付（RECOVERY-1） | checkpoint 记录消息前缀摘要；rewind 只新建分支，不改源 session/trace；prepared/completed 事务支持崩溃恢复，未验证跨平台文件系统语义。 |

## 3. 待证据/未实现

| 字段/契约 | 缺什么 | 下一步证据 |
|---|---|---|
| `usage` 的 provider 实报 token 与账单核对 | CAND-7 用户 2026-10-29 明确跳过；只有本地 stub/newnewrouter 证据 | 不再等待托管 profile、凭据或官方价格/账单；不把本机路由证据外推为托管 Provider 证据 |
| TTFT 的 provider 首包定义 | PocketChat 自有 delta 语义已交付；托管 Provider 官方定义未核 | CAND-7 真实凭据与官方流式语义到位后逐 provider 核对，不外推 |
| retry/fallback 的真实 provider 行为 | 本地 stub 覆盖状态机，真实 provider 预算/延迟/取消未核 | CAND-7 真实凭据与端点验证后再登记 |
| newnewrouter 当前模型真实 run | `local verification artifact (not shipped)`；`run_20261029t060056_2227ec2b` | V `run_20261029t060056_2227ec2b` | 已验证（CAND-6R，本机路由范围） | `configured-profile-a` completed；usage input 18/output 2，非流式无 TTFT，attempt 1，snapshot replay 匹配、凭据值零命中。回复为 `OK.` 不满足 exactly `OK`；配置价格 0.0 无官方依据，成本未核。不是托管 Provider 直连。 |
| checkpoint/rewind 跨平台语义 | 本地状态机与崩溃恢复 fixture 已交付 | 真实 Windows/APFS 文件系统中断与原子替换需发布矩阵另验 |
| 查询/索引一致性 | `queries.py` `_require_consistent_graph`；`indexing.py` `index_status`；`tests/test_queries.py`、`tests/test_index.py` | C queries/indexing；V `tests/test_queries.py`、`tests/test_index.py` | 已交付（CLI-10） | 损坏/冲突/未解析 workspace fail-closed 并点名路径；并发追加立即被查询看到并使 index stale；索引摘要相同但行不同报告 `inconsistent`。 |
| 上下文预览/干跑压缩 | `context.py` `preview_budget`；`service.py` `context_preview`；`tests/test_context.py` | 已交付（CONTEXT-1） | 只读输出索引/角色/字符、估算 token 和 kept/dropped；按完整 exchange 裁剪，保留最近边界与 pinned prompt；不复制正文、不写文件、不发请求。 |
| token 预算护栏 | `config.py` profile 预算；`session.py` 请求前/响应后检查；`tests/test_cost_budget.py` | 已交付（COST-1） | `max_turn_tokens` / `max_session_tokens` 是 token 预算；超预算不进入上下文，session 预算遇 usage 未知 fail-closed；不是货币上限或真实账单。 |
| 只读文件工具/权限 | `tools.py`；CLI `tool read`；trace `permission.*`/`tool.call.*`；`tests/test_tools.py` | 已交付（TOOLS-1） | 仅显式 allowlist、工作区内 UTF-8 文件；拒绝敏感/二进制/逃逸；有界输出；不写、不执行、不联网；未实现自动 tool calling。 |
| 本地显式记忆 | `memory.py`；CLI `memory`；`.pocketchat/memory.jsonl`；`tests/test_memory.py` | 已交付（MEMORY-1） | 显式 add/list/search/remove；每 workspace 独立；16 KiB/16 tags；损坏 store fail-closed；不自动注入 prompt。 |
| 有界串行委派 | `delegate.py`；CLI `delegate run`；`.pocketchat/delegations/`；`tests/test_delegate.py` | 已交付（DELEGATE-1） | 子任务数/deadline 有上限；每个 child 独立 Session/trace；失败隔离但 KeyboardInterrupt 中止批次；不递归、不共享父 session、不并行多 Agent。 |
| 显式受控写文件 | `tools.py` `execute_write_file`；CLI `tool write`；`tests/test_tools.py` | 已交付（TOOLS-2） | 显式 allowlist、workspace 内 UTF-8、原子写、16 KiB；已有文件需 `--overwrite`；拒绝敏感/二进制/逃逸；无删除/shell/执行/网络。 |
| 模型自动只读工具 | `provider.py` tool schema/parse；`session.py` tool loop；`tools.py` 权限层；`tests/test_tool_calls.py` | 已交付（TOOLS-3） | 默认关闭；只向模型提供显式启用的 `read_file`；显式路径 allowlist；每 turn 调用数与结果字节上限；trace 记录 tool/permission 事件、call id 与 schema hash。 |
| 模型自动创建文件 | `provider.py` write schema；`session.py` tool dispatch；`tools.py` write guard；`tests/test_tool_calls.py` | 已交付（TOOLS-4） | 默认关闭；只允许 create-only `write_file`；显式路径 allowlist；无 overwrite/delete/shell/network。 |
| 模型自动记忆查询 | `provider.py` memory schema；`session.py` tool dispatch；`memory.py` search；`tests/test_memory_tools.py` | 已交付（TOOLS-5） | 默认关闭；只读 `memory_search`；结果条数/字节有界；不自动 add/edit/delete 或注入 prompt。 |
| 模型工具拒绝结果回传 | `session.py` recoverable tool dispatch；`tools.py` stable error types；`tests/test_tool_calls.py` | 已交付（TOOLS-6） | 禁用/未知工具、缺参数、guard 拒绝和非 UTF-8 文本回传有界 `status: failed` 结果；同一 batch 其余调用继续；失败计入 tool call 预算；I/O、结果/预算超限和 Provider/中断仍终止。 |
| 跨平台基础验证 | `.github/workflows/ci.yml`；`docs/compatibility.md`；`tests/test_tools.py` 路径测试 | 已验证（本机范围，CROSS-PLATFORM-1） | 本机 macOS CPython 3.13 全量 576 tests、3.14 全量、干净 venv 安装通过；Linux/Windows/3.9 仍只由 workflow 定义，未取得 runner 结果。 |
| v1.0.0 发布候选 | `docs/release-1.0.md`；wheel sha256 `593159a096cc581a0128c58401fca976d364f587e223314ef5f6f8734423acaf` | 候选已准备（RELEASE-1） | 35 成员 wheel、干净 3.13 venv 安装、版本/schema/serve 行为通过；3.9/Linux/Windows/隔离构建/全仓 ruff 未验证；tag/Release/上传未授权。 |
| 有界串行委派 | `delegate.py`；CLI `delegate run`；`.pocketchat/delegations/`；`tests/test_delegate.py` | 已交付（DELEGATE-1） | 子任务数/deadline 有上限；每个 child 独立 Session/trace；失败隔离但 KeyboardInterrupt 中止批次；不递归、不共享父 session、不并行多 Agent。 |
| 跨平台 CI | workflow 在 `.github/workflows/ci.yml`；本机只验证 macOS，未取得候选 SHA 的远端结果 | 目标平台不等于已验证；不把 workflow 存在写成跑绿 |

| 有界 task policy / record | `task_policy.py`、`tasking.py`、`schemas/task.schema.json`；`tests/test_task_policy.py`、`test_tasking.py` | C schema/实现；V 契约与负向测试 | 已交付（TASK-RUN-1） | policy 字段封闭、预算/路径 fail-closed；record 状态机封闭、原子持久化。未验证真实长任务并发写者。 |
| 有界 task 执行 | `agent.run_task`、`session.py` task observer；`tests/test_task_run.py` | C 主循环/Session；V 8 条 stub 端到端 | 已交付（TASK-RUN-2） | 模型/工具复用既有 request、trace、session；step/tool/token 预算与 fallback 收窄。stub 不是真实 provider。 |
| task checkpoint / resume | `recovery.py`、`tasking.py`；`tests/test_task_recovery.py` | C manifest/事务；V 8 条恢复路径 | 已交付（TASK-RUN-3） | 三字段 all-or-none；resume 写前校验并恢复 prepared 事务/有序 tool context。取消仍为终态；resume 后不自动调 provider。 |
| task CLI / report | `cli.py task`；`tests/test_cli_exec_json.py`、`test_demo.py` | C CLI/JSON；V 8 条 CLI + demo test | 已交付（TASK-RUN-4） | run/status/resume/report、稳定 JSON、workspace-relative 路径；report 不推断。自定义嵌套 trace 发现未验证。 |
| v1.1.0 发布 | `docs/release-1.1.md`；wheel sha256 `a9f873f037112bd87474e21cc98d5e42c31d3567eb0563b2adb7cfa25e19834d` | V 3.9/3.13 各 691 tests、干净 3.9 安装、3 traces validate、replay hash、GitHub Release 回读 | 已发布（TASK-RUN-5） | GitHub tag/Release 与 wheel 已发布；38 成员 wheel、task schema 在、web/server 0。真实 provider、跨平台 runner、隔离构建、包索引仍未验证或未完成。 |

## 3.5 阶段 3 真实任务证据（2026-11-01）

以下五行是**本机真实 provider 运行**结果，不是 stub，也不是静态推演。
运行环境：`origin/main` `c7c9bb8c`、CPython 3.9.6、一个本机 router profile
（`requires_api_key: false`；profile 名不随文档发布，与本文 §2 的
`configured-profile-a` 同一口径）。
**这些结果只覆盖本机这一个 profile 与这些具体输入，不外推为托管 provider 证据，
也不宣称普遍稳定。**

| 任务 | 实际输入 | 实际输出 | 验收状态 | 未验证范围 |
|---|---|---|---|---|
| 读+写报告 | `input.md` 三行中文；policy 只授 `read_file:[input.md]` 与 create-only `write_file:[report.md]` | 真实读取后写入 `report.md`（256 B，`## Findings` 开头）；exit 0 | 2/2 `passed`（`file_exists` + `text_contains`） | 内容质量——`text_contains` 只查子串，不是质量评分 |
| 命令进证据 | 本地 `tests/` 两个用例；TaskSpec 声明 argv 模板 `run_tests` | `unittest discover` 真实执行 exit 0、2 tests OK；模型按授权写 `results.md`；`task_acceptance: not_evaluated` | 2/2 `passed`（`file_exists` + `command_succeeded`，**未重跑命令**） | 真实测试套件规模、超时与输出上限边界 |
| 失败后修正 | 故意错误的 committed fixture `counter.py`（只 +1），命令要求 count≥3 | 第一次 exit 1 → operator callback 修正（+3/-3 行，hash 变）→ 同一模板同一 argv exit 0 | 执行状态 `failed` → `completed` | **修正者是 operator callback，不是模型自主诊断**；要证后者需另设设计 |
| 恢复 | 两步 plan，`stop_after=1` | `stage1.md` hash **与 mtime 均未变**；`stage2.md` 由恢复生成；新报告经 `recovery` 链接前一份及其 digest | 恢复链完整 | 任意崩溃恢复、并发写者、跨进程原子替换 |
| 正确拒绝 | spec allow_paths 越界 / delegate 计划 digest 不匹配 / delegate 子任务越权路径 | 三例均 exit 2 且零 task/trace/session/delegation 产物 | 拒绝发生在调用前 | 见下方「被推翻的假证据」 |

### 被推翻的假证据（必须保留）

最初用「让模型读 `secrets/api.key`」构造拒绝用例，返回 `ok:true`、exit 0。
逐层查证后确认：**模型从未发起那次读取**，只是回复了文本，拒绝路径从未被触发。
因此该用例不构成拒绝证据，已改用 `task spec preview` 与 `task delegate preview`
这两条真正走校验的入口。结论：拒绝机制有效，但「模型主动越权」在当前实现里
不构成拒绝用例。

### checkpoint 接入续跑（同日追加）

`resume_from_checkpoint()` 把 checkpoint 解析成工作区相对报告路径与该报告 digest，
报告必须位于 `.pocketchat/task-reports/` 下且字节 hash 等于记录值。
真实集成：`stop_after=1` 后创建 checkpoint，解析后续跑，`stage1.md` hash 与 mtime
未变、`stage2.md` 由恢复生成。未验证：自动触发恢复、任意崩溃恢复、并发写者。

### 使用门槛（2026-11-01 记录，2026-11-12 更新入口）

`task run --policy` 只接受 `pocketchat-task-policy.v1`，**不接受 TaskSpec**。
带 command templates 的 TaskSpec/TaskPlan 通过产品 CLI 入口执行：
`task exec --spec SPEC --plan PLAN --plan-sha256 DIGEST`。该入口在读取 provider 配置前
重新校验 spec、plan 与显式摘要；命令只使用 TaskSpec 声明的 argv 模板，不是自由 shell。
此前的“必须走 Python 路径”记录已过期，现由 `TA-CLI-EXEC-1` 交付的 CLI 入口取代；
`task exec` 仍不生成计划、不扩大授权，命令成功也不等于任务验收通过。

## 4. 入口规则

- 任何新 trace/JSON 字段，先在本表登记证据与适用范围，再写实现。
- 没有 D/C/R/V/O 之一的字段保持 `REVERSE_REQUIRED`，不写代码。
- 已交付字段的兼容变更先走 additive 字段或新主版本；不静默改变 0、`unknown`、错误分类或事件语义。
- 通用工具调用、代码执行、shell/network 和递归多 agent 编排不在本表范围内；
  上表登记的显式本地工具是当前完整工具面。
