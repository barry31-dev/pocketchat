# 任务预检

`pocketchat.task_preflight.check_task_preflight(spec, plan, snapshot, observation)`
是对调用方提供的观察做纯校验。它不运行 Git、不打开工作区文件、不启动命令、
不联系 provider，也不授权执行。报告为 `allow` 只说明传入观察完整且内部一致；
真实观察仍由调用方负责采集。

## 输入

`spec` 与 `plan` 必须是已经解析好的 `TaskSpec` / `TaskPlan`，校验复用现有的
`spec_sha256()` / `plan_sha256()`；本模块不重新解析或执行文档。`snapshot` 提供
现有任务快照及其 `snapshot_sha256`。`observation` 是封闭映射，包含
`workspace_id`、`workspace_roots`、`base_commit`、`head_commit`、`is_git`、`tracked_paths`、
`untracked_paths`、`dirty_paths`、可选的 `write_paths`、`dirty_coverage`、
`source_coverage`、`spec_sha256`、`plan_sha256`、`snapshot_sha256`。

所有路径都是工作区相对路径，报告前稳定排序。`workspace_roots` 是本次观察覆盖的
工作区身份集合，`workspace_id` 必须正好是其中之一；缺失身份或混入 foreign 工作区
直接拒绝，不能合并成当前工作区。调用方必须显式提供 `dirty_coverage` 与
`source_coverage`；**空 `dirty_paths` + `partial` 覆盖是
`unknown`**，空列表不等于一次完整扫描。非 Git 工作区也不能被当成干净工作区。

`write_paths` 可省略；省略时从已解析 plan 的 `write_file` 输入推导。显式提供时
必须与 plan 一致，TaskSpec 的读写 allowlist 仍是最终授权边界。

## 结果

报告含 `schema_version: 1`、稳定排序的 refusals / unknowns / omissions、路径计数
和显式限额。状态含义：

| 状态 | 含义 |
|---|---|
| `allow` | 必需观察完整一致，HEAD 未漂移，计划写入都在声明范围内。 |
| `refused` | 身份、摘要或路径与 plan/TaskSpec 矛盾，或超过限额。 |
| `unknown` | 必需观察缺失、明确不完整或不是 Git 观察；缺失绝不当作干净。 |

快照摘要与观察不一致为 `refused`，缺失摘要为 `unknown`；缺
`plan_sha256`、spec 摘要或 HEAD 也是 `unknown`。范围检查拒绝祖先/后代重叠和
大小写冲突，dirty / untracked 路径越出声明写范围时给出具名拒绝原因。

本函数不修改输入，不把自由文本 objective、request/message/credential 正文复制到
报告；超过 4096 个路径观察或 64 KiB canonical 报告时拒绝而不是截断。

## 不做

这不是实时 Git 预检、执行授权 token、文件读取器、命令执行器、provider adapter
或 CLI。真实工作区观察与执行接线属于另一个有界切片；报告只证明传入观察的内部一致性。
