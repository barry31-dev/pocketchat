# PocketChat（pc）下一步计划

> **文档性质**：2026-10-28 候选计划的历史记录，不是当前队列。
> **依据**：[`开发规划总则`](agent-development-master-plan.zh-CN.md) 与外部批准的产品路线。
> **队列规则**：当前批准状态只看 `内部切片队列`；本文件不自动开工。
> **维护日期**：2026-10-29

## 0. 当前状态

- 2026-10-29 用户批准六项候选方向；当前状态、契约、交付与执行顺序统一见 内部规划的
  `内部切片队列`。本节不再复制队列状态。
- v1.0.0 是本地候选：3.9/3.13 全量、ruff、wheel 与干净安装已完成；跨平台 CI workflow 在仓库中，但该候选 SHA 的 CI 结果未取得，当前只在本机测试；tag/Release 未授权。
- **本文件 §2「明确不做」的口径已过期，现按下方两栏读**：显式 allowlist 只读文件工具、
  显式受控写文件（create-only）、只读 `memory_search`、有界串行委派、checkpoint/rewind
  与有界 retry/fallback 已于 2026-10-29 实现并登记在
  [`formats.md`](formats.md) 与 [`compatibility.md`](compatibility.md)。
  仍然不做的是：shell 与任意代码执行、网络工具、文件覆盖与删除、递归委派、自动选优/评分、
  Web UI、本地 API、桌面端与账号云同步。
- 下一阶段是 v1.1 Task Run：把上述已有能力组装成一个可审计、可恢复、可演示的有界任务闭环。
  契约已冻结在 [`formats.md`](formats.md)「Task policy」「Task records」两节与
  `schemas/task.schema.json`；范围、五个大格与验收判据见 内部规划的切片队列
  `TASK-RUN-1` … `TASK-RUN-5`。本节不复制那些契约。

## 1. 候选切片（按优先级）

### CAND-1 · Evidence Matrix（M0）— **已交付（2026-10-28，切片队列 CLI-8）**

**内容**：建立 pc 证据矩阵，把 M1–M4 每个字段绑定到 D/R/V/C/O 证据编号；无绑定的字段标 `REVERSE_REQUIRED`。
**为什么先做**：现在「缺什么」靠阅读判断，容易把假设写成设计；矩阵是后续每格的前置门。
**出口判据**：每个字段有编号、来源位置和适用范围；未绑定项单独列出且不进入实现。**已达成**：见 `pocketchat-evidence-matrix.md`。
**明确不做**：不改产品范围、不改 schema、不写实现。
**相称测试**：无代码改动；跑文档链接与 `git diff --check` 即可。

### CAND-2 · 压缩事件 schema 对齐（M1）— **已交付（2026-10-28，切片队列 CLI-3）**

**内容**：修正 `context.compacted` 的 schema/实现不一致。实现写入 `kept_messages`、`dropped_messages`、`kept_chars`、`estimated_tokens_before`、`estimated_tokens_after`；schema 当前要求的是 `before_messages`、`after_messages`、`before_tokens`、`after_tokens`。
**为什么先做**：这是已存在的事件类型与已发布 schema 之间的真实不一致，继续加字段会扩大问题。
**出口判据**：现有真实 trace 的 `context.compacted` 字段在 schema 中有明确类型与阅读语义；实现与文档字段名一致。**已达成**：schema 声明实现写入的全部 7 个字段；`trace-doctor 0.1.0` 自带旧字段名，作为已发布工具不回改，差异已记录。
**明确不做**：不改压缩策略，不引入模型总结，不重写历史 trace。
**相称测试**：真实超预算 trace 的每个 payload 字段都被 schema 声明；字段缺失/未知版本仍 fail-closed。

### CAND-3 · Usage 未报告语义与 TTFT（M1）— **已交付（2026-10-28，切片队列 CLI-6/CLI-7）**

**内容**：在 `model.response.completed` 记录 provider 实报 token 与 `time_to_first_token_ms`；对真正发生首包的流式路径记录 TTFT。
**依据**：`provider.py` 已归一并返回 usage（缺报时为 0）；`session.py` 已写 usage；流式回调已存在，但 TTFT 未记录。
**兼容门**：`docs/provider-adapters.md` 与 `docs/cli.md` 明确规定“缺 usage 为 0，表示未报告”。把 0 改成 `null`/`not_reported` 是 breaking change；必须先定新主版本或新增 additive 字段，不能静默改。
**出口判据**：同一 stub provider 下 trace 记录 usage 与 TTFT；既有 usage 字段契约不变；新增字段能区分缺报；无首包的失败路径不伪造 TTFT。**已达成**：流式 trace 有 TTFT，非流式无该键；`usage_reported` 以 additive 字段区分缺报与真实 0，既有 0 语义不变。
**明确不做**：不做自动重试、fallback、评分；不改 compare 的现有输出契约。
**相称测试**：stub provider 非流式与流式各一条；缺 usage 与缺 TTFT 各一条负向用例；兼容门未过时不得改 JSON 契约。

### CAND-4 · 失败分类分层（M1）— **已交付（2026-10-28，切片队列 CLI-4）**

**内容**：`model.response.failed` 增补 `retryable` 与稳定 `error_type`，便于复盘；不自动重试。
**依据**：`provider.py` 已有 `ProviderError.retryable`/`error_type`；`session.py` 写 trace 时仍未带上 retryable；逆向 R-07 给出分类依据。
**出口判据**：timeout、connection_error、rate_limited、unauthorized、malformed_response 各有稳定 error_type 与 retryable；trace 可回查。**已达成**：429/408/425/5xx 为 true，畸形响应为 false，字段被 schema 声明。
**明确不做**：不实现重试、退避、模型切换或熔断。
**相称测试**：每种错误一条 stub 用例，断言 trace 字段与退出码。

### CAND-5 · 压缩报告 CLI 可读化（M1）— **已交付（2026-10-28，切片队列 CLI-5）**

**内容**：`context.compacted` 现有 payload 已有 strategy/dropped_messages/kept_messages/kept_chars/token 估算；本格只补 CLI 可读摘要，schema 对齐归 CAND-2。
**依据**：`context.py` 已计算 trim；`session.py` 已写 `context.compacted`。
**出口判据**：现有 kept/dropped 数字与真实 trim 一致；CLI 摘要字段与 trace payload 同源；未压缩时不产生事件；`--json` 与人类输出同源。**已达成**：人类 show 打印摘要，show --json 携带同一 payload。
**明确不做**：不做语义摘要式压缩（不引入模型总结）。
**相称测试**：超预算一条、刚好容纳一条、系统提示独立一条。

### CAND-6 · newnewrouter 本机模型真实验证（M2）— **已交付（2026-10-28，按用户指示改为本机范围）**

**范围变更**：用户 2026-10-28 指示「用 newnewrouter 的本模型测试」「就在本机测试」，
因此本格不再要求绕过本机 router 的托管直连，改为通过本机配置目录中的
`newnewrouter.json` profile 发起真实请求。原托管直连/真实账单范围不冒充已验证。

**输入**：newnewrouter 当前目录中的 `configured-profile-a` 与
`configured-profile-b` 两个免客户端凭据 profile；短 prompt；同工作区先建 session 再比较。
**动作**：执行一次 `run --json --verbose` 与一次双 profile `compare --json --verbose`，
保存 session、trace、request snapshot 与 comparison manifest；随后用 `trace-doctor validate`
检查每条 trace，并扫描生成工件中的凭据值。
**出口判据**：run 成功；compare 至少有一个 profile 成功并允许另一个 profile 失败，两个 run
都有独立 trace/request 与终止事件；trace 校验通过；凭据值零落盘。**已达成**：run 返回
`LOCAL_OK`；compare 的 `configured-profile-a` 成功、`configured-profile-b`
被 newnewrouter 以 HTTP 503 `no healthy account` 拒绝，但两条 trace 均终止且通过校验。
**明确不做**：不自动重试、不切换 provider、不伪造成本；不把未取得的跨平台 CI 结果写成通过。
**相称测试**：`tests.test_cli_exec_json.CliExecJsonTests.test_compare_json_accepts_profiles_that_require_no_api_key`
先红后绿；相关 compare/profile 回归与本机真实运行记录。

## 2. 明确不做（任何候选格都不得越界）

> ⚠️ **2026-10-29 更新**：本节原清单把「工具调用 / 文件访问 / 委派 / memory」整体列为不做，
> 已与实际实现不符 —— 切片队列里的 `TOOLS-1`…`TOOLS-6`、`MEMORY-1`、`DELEGATE-1`、
> `RECOVERY-1`/`RECOVERY-2` 于同日交付（不是 §0 提到的那六项候选）。现按下面两栏读，
> 本文件其余各节的旧候选描述属历史记录，不再代表现行范围。

**已实现（有契约、有测试、有格式登记）**：

- 显式 allowlist 的只读文件工具（`read_file`）、create-only 写文件（`write_file`）、
  只读本地记忆检索（`memory_search`）；三者默认关闭，需 profile 显式开启；
- 有界串行委派（`delegate`），子任务不能再委派；
- 显式本地记忆（`memory`）、checkpoint/rewind 恢复、有界 retry 与一跳 fallback；
- 上下文预览与干跑压缩、token 预算护栏、`tool_context` 持久化与续跑。

**仍然不做**：

- shell、任意代码执行、网络工具、沙箱逃逸与审批闸绕过；
- 文件覆盖与删除（写只到 create-only）、越出 allowlist 的任何路径；
- 递归委派、多 agent 编排、自动规划、自动选优与自动评分；
- skills、MCP、cron、后台任务与跨平台 gateway；
- Web UI、本地 API、桌面端、账号与云同步。

## 3. 建议执行顺序

1. CAND-1（只读文档，先立门槛）
2. CAND-2（先修已存在的 schema 不一致）
3. CAND-3 + CAND-4 + CAND-5（同一批 M1 schema/CLI 增强，可一次评审；CAND-3 必须先过兼容门）
4. CAND-6（newnewrouter 本机模型验证，已交付）

## 4. 未验证

- 本文件本身不替代运行记录；本轮真实运行证据见 `internal handoff record`。
- CAND-3 的 TTFT 定义需与 provider 行为核对：流式首包时间与非流式整体时间不是同一件事。
- CAND-6 的已验证范围只覆盖 newnewrouter 本机 profile；托管直连、真实账单与其他 provider 仍未验证。
