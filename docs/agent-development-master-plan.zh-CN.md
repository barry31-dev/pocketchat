# PocketChat 开发规划总则

> **文档性质**：开发总则与证据门槛，不是某个功能的实现计划。
> **维护范围**：`开源/pocketchat/`（下称 **pc**）的 CLI 能力规划。
> **核心原则**：不凭产品名字、UI 截图或个人想象补功能。没有证据，先逆向；没有逆向结论，不进入实现。
> **维护日期**：2026-10-28

## 1. 目标与边界

> **2026-10-28 用户裁决（现行口径优先）：PocketChat 只做 CLI。**
> 同日明确：**终极目标是参考顶级 Agent 的核心功能做出 PocketChat 的核心能力；现在先做基础。**
> 当前只交付显式 allowlist 的本地 read/write/memory search、显式记忆和有界串行委派；
> 不做通用工具、代码执行、递归多 Agent 编排、自动规划、Web UI、本地 API、桌面端或网络服务；
> 后续核心轴必须逐项立项，不因终极目标自动实现。产品方向与里程碑的唯一来源是
> 产品路线与当前格以本仓库的 issue 或 `docs/slice-queue.md` 为准；本文件只保留能力边界与证据规则。
> 下一格以外部批准记录为准，不在本文件自行开工。
> 本文是**证据规则与规划方法**，不是产品范围扩展的授权。

PocketChat 当前的可信基础是：

- 可配置的 OpenAI-compatible provider；
- 多轮 session、branch、request snapshot；
- 完整 trace，能回答“模型看到了什么、发出了什么请求、最后发生了什么”；
- TTY 交互界面、流式正文与 reasoning 显示；
- 显式 allowlist 的本地 read/write/memory search 工具、显式记忆、有界委派与恢复状态。

这使 PocketChat 已经是一个**可审计聊天内核**。当前阶段继续加固基础；终极目标是参考顶级
Agent 的核心机制 clean-room 实现自己的 Agent 能力，但不成为通用助手或通用 Agent 平台：

- 不增加 Web UI、桌面产品、账号系统或云同步；
- 不把显式本地工具扩成通用工具、文件系统、代码执行、递归多 Agent 编排或自动规划；
- 主循环、上下文、工具/权限、恢复、委派、扩展等核心轴逐项走证据与切片门；
- 不把计划、重试、上下文压缩、成本核算写成“已经支持”；
- 不把某一家 Agent 的类名、路径、代码或品牌素材复制到 PocketChat。

v1.0.x 的命令、退出码、JSON、文件格式边界仍以 [`compatibility.md`](compatibility.md) 为准。
通用工具调用、显式本地工具之外的文件访问、命令执行、递归多 Agent 编排等仍是
**后续顶级 Agent 核心轴候选，不是当前能力**；每一轴必须先改路线图与切片队列，
再改本文，不得由某次实现反向打开范围。

## 2. 证据规则

### 2.1 只有五类证据可以支持设计

| 证据类型 | 允许的用途 | 记录要求 |
|---|---|---|
| 官方规范（D） | 确认公开契约和产品边界 | URL、打开日期、原文要点 |
| 本机源码/逆向结论（R） | 确认实现机制、状态机、字段和失败分支 | 文件/篇目、章节、静态深度 |
| 本机实测（V） | 确认某条路径真实发生 | 命令、解释器/版本、输出、退出码 |
| PocketChat 当前代码（C） | 确认已有能力、契约与兼容边界 | 文件、行号、测试、trace/schema |
| 热门开源库源码/文档（O） | 提供机制模式、字段语义与失败处理参考 | 仓库 URL、版本/commit、文件/章节、许可证需另行核对 |

每个结论分开记录：

- **来源类型**：官方来源 / 项目解释 / 本机实测 / 假设；
- **验证状态**：已核验 / 待核验 / 未验证；
- **适用范围**：只覆盖本次读到的版本、端点、设备或路径。

“官方文档说支持”不等于 PocketChat 已实现；“本机逆向看到符号”不等于运行已验证；“计划做”永远不能写成“已支持”。

热门开源库只能帮助回答“这类机制通常怎样建模”，不能证明某个商业 Agent 的真实实现，也不能替代 PocketChat 的兼容边界。库中的 API、类名、目录、许可证文本和示例代码不直接复制；需要复用的只是可独立重写的状态机、字段语义和失败分类。

### 2.2 查不到就逆向

如果设计需要的机制在官方文档中查不到，按以下顺序补证据：

1. 读 PocketChat 现有代码、测试、schema、trace；
2. 读本机《顶级 Agent 解构》已有的契约和代码级结论；
3. 读热门开源库的最小相关模块，比较多种实现的共同状态和失败边界；
4. 只读取所需片段，优先源码/明文/已解包树；
5. 闭源、混淆、二进制才投入逆向成本；
6. 逆向产出必须回答来源、机制、状态、失败分支与适用范围，并标注静态深度和运行验证等级；内部题单不在公开包内提供。

逆向任务不能只输出关键词或字符串命中。至少要回答：

- 主循环是什么；
- 上下文如何装配、何时压缩；
- 工具如何注册、分派、返回；
- 权限如何判定、谁批准、超时/取消如何终态；
- 会话、checkpoint、usage、trace 如何持久化；
- 失败如何分类、重试、fallback、恢复；
- 哪些状态是事实，哪些只是 UI 或日志。

### 2.3 参考对象

当前逆向参考集以内部路线图批准的对象为准：

`ChatGPT / Claude / 第三方代理 / Cursor / WorkBuddy / Trae / Qoder / Doubao / DoubaoWork`

其他对象只能作为补充样本，不能改变主线优先级。逆向方法和优先级以 `92` §0/§4.1 为准；UI 展示、工具全枚举、外围插件不优先于主循环、工具、权限、持久化和恢复。

## 3. 参考证据索引

### 3.1 本地逆向资料

| 编号 | 位置 | 对本规划的作用 |
|---|---|---|
| R-01 | 内部原理题单 | 十问、静态/运行深度、不许跳级 |
| R-02 | 内部逆向路线 | 主链优先级和任务顺序 |
| R-03 | 内部横向对比 | 架构取向、权限、扩展、会话/压缩 |
| R-04 | 内部权限终态研究 | guardrail、审批、timeout/cancelled/deny 分离 |
| R-05 | 内部持久化与恢复研究 | session、usage、checkpoint、迁移和可回查字段 |
| R-06 | 内部压缩触发研究 | 工具轮阶段、落库先于执行、压缩触发 |
| R-07 | 内部错误恢复研究 | 错误分类、重试、fallback、overflow 恢复 |
| R-08 | 内部安全研究 | 数据/指令分离、默认拒绝、精确审批绑定、零副作用验证 |

### 3.2 热门开源库参考

以下仓库于 2026-10-28 通过 GitHub API/官方仓库页核读，作为**机制参考源**，不是产品功能清单或实现授权。选取依据是社区采用度与机制覆盖面，不是质量排名；star 数会变化，不作为架构正确性证据：

| 编号 | 仓库 | 可参考机制 | CLI-only 下的可用范围 |
|---|---|---|---|
| O-01 | [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) | tools、guardrails、handoffs、sandbox agent | 只看其**错误分类与 run 终态**；tools/sandbox 不做 |
| O-02 | [LangGraph](https://github.com/langchain-ai/langgraph) | stateful graph、节点/边、checkpoint、持久化状态 | 只参考状态与 checkpoint 的字段语义；不引入图执行 |
| O-03 | [smolagents](https://github.com/huggingface/smolagents) | 最小工具循环、code agent、工具结果回填 | 只参考其**失败与结果记录形状**；不引入工具执行 |
| O-04 | [AutoGen](https://github.com/microsoft/autogen) | 多 Agent 会话、角色协作、消息传递 | 只作范围对照，标记「当前不做」 |
| O-05 | [CrewAI](https://github.com/crewAIInc/crewAI) | task/agent 编排、依赖、结果汇总 | 只作范围对照，标记「当前不做」 |
| O-06 | [Pydantic AI](https://github.com/pydantic/pydantic-ai) | 结构化输出、deferred tool approval、run persistence/resume/fork、guardrails、planning | 只参考 run 持久化/恢复的字段语义；approval 与 planning 不做 |

使用规则：

- 先比较多个库的共同机制，再决定 PocketChat 的最小契约；
- 只借鉴“字段/状态/失败边界”的思想，不复制 API 形状或代码；
- 每个采用的机制仍需绑定官方规范或顶级 Agent 逆向证据；
- 落在通用工具、显式本地工具之外的文件访问、代码执行、递归多 agent 编排内的机制
  一律标记「当前不做」；
- 如果开源库没有回答真实产品如何处理上下文、恢复或持久化，按 §2.2 逆向顶级 Agent；
- 许可证只决定能否复用代码，不决定 PocketChat 是否应该拥有该功能；本总则不授权复制任何第三方代码。

### 3.3 网络官方资料

以下页面均于 2026-10-28 直接打开核读；它们支持机制分类，不代表 PocketChat 已实现。
涉及通用 tools、memory、skills、MCP、审批、沙箱的条目只作范围对照；只有切片队列
批准并交付的显式本地边界可写成当前能力。

| 编号 | 来源 | 可支持的事实 |
|---|---|---|
| W-01 | [Claude Code Overview](https://docs.anthropic.com/en/docs/claude-code/overview) | 读代码、改文件、执行命令、工具/权限、memory、skills、hooks |
| W-02 | [Claude Code Costs](https://docs.anthropic.com/en/docs/claude-code/costs) | `/compact`、checkpoint/rewind、plan mode、token/成本管理 |
| W-03 | [第三方代理 Docs](https://third-party documentation) | tools、memory、skills、MCP、审批、隔离、沙箱 |
| W-04 | [第三方代理 Quickstart](https://third-party documentationgetting-started/quickstart) | 先稳定基础对话，再启用工具、memory、skills、MCP |
| W-05 | [MCP Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) | 工具发现/调用、schema、结果、超时、审计 |
| W-06 | [MCP Sampling](https://modelcontextprotocol.io/specification/2025-06-18/client/sampling) | 用户批准控制、输入校验、限流、敏感数据边界 |

OpenAI 编码代理 官方页面本轮直读返回 HTTP 403，因此不把未读到的 编码代理 网络内容当作本总则依据；ChatGPT/编码代理 相关结论只引用本机逆向资料，并保留其证据等级。

## 4. 基础能力模型（CLI-only 口径）

| 能力层 | PocketChat 当前状态 | 缺口 | 证据来源 |
|---|---|---|---|
| Provider / session / trace | 已有；trace 含 TTFT 与 usage_reported（2026-10-28） | 真实 provider 覆盖仍有限；直连托管端点未运行 | `session.py`、`trace.py`、schema、切片队列 §1 |
| 多 profile 对比 | 已有 | 失败隔离与 manifest 可回查已有；真实双 profile 证据有限 | `compare.py`、`cost.py`、切片队列 M2 |
| 请求快照 / replay | 已有 | dry-run 已支持；未覆盖更多失败输入形状 | `request.py`、`replay.py`、CLI 手册 |
| 上下文管理 | 已有字符预算、`context.compacted` 与 `show` 可读摘要（2026-10-28） | 无（后续仅按真实需求扩展） | `context.py`、`session.py`、`service.py`、schema |
| 失败分类与恢复 | 部分 | trace 已记录 error_type 与 retryable（2026-10-28）；缺 attempt/fallback 记录 | `provider.py`、`session.py`、逆向 R-07 |
| 持久化 / 续跑 | 已有 | 工作区重建与查询已有；缺 CLI 侧 checkpoint/rewind | `sessions.py`、逆向 R-05 |
| TUI 可观测性 | 已有基础 | 状态栏缺 token、成本、TTFT、工具耗时、重试次数 | `tui.py`、`curses_ui.py` |
| 工具 / 文件 / 执行 / 多 agent | **有限工具层已开始**：TOOLS-1 仅提供显式 allowlist 的只读 `tool read`；写入、shell/执行、自动 tool calling、多 agent 仍未实现 | 自动工具协议、写/执行权限、委派与跨平台仍未实现 | 用户 2026-10-29 要求继续完成后续开发；TOOLS-1 契约 |

## 5. 开发顺序与门

### M0：证据与契约门

产出：

- 十问覆盖表只针对 PocketChat 的 CLI/对比/证据能力；
- `context`、`recovery`、`usage`、`checkpoint` 的字段草案；
- 每个字段绑定 D/R/V/C/O 证据编号；
- 明确 v1.0.x 兼容影响和版本轨道。

门：没有证据编号的设计不进入实现；没有失败终态的字段不进入 schema。

### M1：只读证据增强

范围（按顺序）：

1. ~~先修 `context.compacted` schema/实现字段不一致~~（已交付，切片队列 CLI-3）；
2. ~~再补 `model.response.completed` 的 TTFT（只对真正发生首包的路径记录）~~（已交付，切片队列 CLI-6）；
3. ~~补 `model.response.failed` 的 `retryable` 与稳定 `error_type`~~（已交付，切片队列 CLI-4）；
4. ~~补 `context.compacted` 的 CLI 可读摘要~~（已交付，切片队列 CLI-5）。

usage 的“缺报为 0”是 v1.0 兼容契约；把 0 改为 `null`/`not_reported` 必须先走新主版本或 additive 字段评审。

门：缺失 usage 必须能区分「未报告」与真实 0；当前 API 用 0 表示未报告，故必须先新增 additive 字段或进入新主版本，不能直接改 0 的语义。TTFT 缺失写 `unknown`，不估算为事实。

### M2：真实 provider 与成本核对

范围：

- 不经本机路由的托管 provider 直连；
- 成本计算与真实账单或官方价格表核对；
- README 10 分钟 quickstart 与一条完整 CLI 案例。

门：未授权不调用真实凭据；成本不伪造；案例必须可回放。

### M3：CLI 稳定性与本机验证

范围：

- 3.9/3.13 全量、ruff、wheel、干净安装；
- 本机 CPython 3.9/3.13 全量回归；跨平台 CI workflow 存在，但候选 SHA 的远端结果未取得；
- JSON 消费者与退出码回归。

门：只把本机实测写成「已验证」；跨平台不写「已验证」；tag / Release 仍需用户授权。

### M4：体验与可观测性（仅 CLI 范围内）

范围：

- TUI 状态栏显示 token、成本、TTFT、重试与压缩事实；
- `history` / `search` / `diff` / `index` 的一致性回归；
- 命令手册、README 中英与 CHANGELOG 同步。

门：任何显示必须来自 trace / manifest 事实，不新增第二次计算路径。

## 6. 逆向任务模板

任何“查不到”的机制，先创建逆向任务，不直接写代码。任务必须包含：

1. **问题**：只问一个主链机制，例如“上下文压缩前后各丢了多少条消息、由谁记录”；
2. **对象和版本**：九个参考 Agent 中的哪一个、哪个本机版本；
3. **证据路径**：D 官方规范 → C PocketChat 代码 → R 顶级 Agent 逆向结论 → O 热门开源库 → 需要时再逆向二进制；
4. **十问范围**：只标 Q2/Q3/Q4/Q5/Q7/Q9/Q10 中实际涉及的项；
5. **静态深度**：L0–L4，禁止把字符串命中写成 L3；
6. **运行验证**：V0–V2，未运行就写未运行；
7. **工程结论**：我们应采用的机制、不应复制的部分、PocketChat 落点；
8. **证据缺口**：哪些字段仍不能确定；
9. **范围核对**：结论是否落在 CLI-only 范围内；若指向显式本地边界之外的工具或
   agent 执行能力，标记为「当前不做」。

## 7. 实现约束

- **先契约后代码**：先写 schema、字段定义、失败枚举和测试，再写执行路径。
- **TDD**：每个新能力先写失败测试；测试必须覆盖成功、失败、超时、取消、溢出和恢复。
- **单一运行入口**：模型调用、trace、session、checkpoint 不得各自造一套状态。
- **最小动作面**：当前不引入新的外部动作通道；已批准的 CLI 动作保持显式与可回放。
- **事实优先**：token/cost/usage/TTFT 以 provider 实报为准；缺失写 `not reported` 或 `unknown`，不估算为事实。
- **clean-room**：只借鉴机制和工程取舍，不复制第三方源码、类名、路径、业务模型或品牌素材；热门开源库也不例外。
- **版本诚实**：v1.0.x 的范围不扩大；新能力经过兼容评审后进入明确的新版本轨道。

## 8. 交付判据

每个里程碑交付必须同时有：

1. 设计证据表；
2. schema/状态机变更；
3. 红-绿测试和失败路径测试；
4. trace、session、snapshot 的可回查证据；
5. README/CHANGELOG/兼容文档；
6. 实测记录和未验证范围；
7. 若机制来自逆向，附 L/V 等级和“没有运行验证”的边界。

## 9. 明确不做

以下越界能力都不是 PocketChat 的**当前能力**，也不因某次实现自动变成能力；其中主循环、
上下文、工具/权限、恢复、委派、扩展只能沿已批准的显式边界逐轴立项：

- 通用工具调用 / function calling、显式本地工具之外的文件访问、代码执行、沙箱；
- 递归多 agent 编排、共享上下文委派、自动规划、无界重试、自动选优、自动评分；
- 自动 memory、向量检索、skills、MCP、cron、后台任务、跨平台 gateway；
- Web UI、本地 API、桌面端、账号与云同步；
- 只有总结没有 `context.compacted` 记录的“上下文管理”；
- 只有日志没有恢复状态的“断点续跑”；
- 只有某个开源库 API 名称、没有 PocketChat 状态机和证据链的“参考实现”。

## 10. 下一步

当前先做基础：继续清理契约缺口、稳定 CLI/trace/比较/回放并补真实验证。基础稳定后，从顶级
Agent 核心轴中一次选一轴建立证据表、状态机、失败出口与切片；没有绑定 D/R/V/C/O 的字段保持
`REVERSE_REQUIRED`，不写实现。任何新切片必须先进入本仓库的当前格记录（issue 或 `docs/slice-queue.md`）。
