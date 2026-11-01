# 任务恢复证据与步骤边界续跑

本功能尚未发布，分为历史盘点与严格的执行恢复两条入口。盘点不是执行授权；
已发布的 v1.1.0 不包含本计划续跑接口。

## 历史盘点

`completed_side_effects(root, task_id, planned=[...])` 只读关联该 task 的执行报告，
列出完成的步骤 id，并按调用方的顺序返回 `remaining_steps`。缺失或损坏证据记为
unknown/refused。该兼容 helper **不绑定** Spec、Plan、任务记录或当前文件内容，
不能直接拿其返回的 id 授权跳过步骤。

## 显式执行边界

使用已授权的 `ExecutionPlan` 与现有 `execute_plan`：

1. 首次调用可传 `stop_after=2`，完成两个新尝试的计划步骤后停止；它不是 provider turn 数。
2. 明确选择本次生成的 `.pocketchat/task-reports/` 下那一份报告并计算原始字节 SHA-256，
   不根据文件名排序猜“最新”。
3. 用同一 execution、workspace、trace/session 路径与 runner 再次调用，传入
   `resume_report=report` 和 `expected_report_sha256=report_sha256`。
4. 续跑也可传 `stop_after`；该值只计本次新步骤，必须为不大于计划长度的正整数。
   完整 Python 调用示例见 [英文对照](task-recovery.md)。

报告文件名/id、精确 hash、Spec/Plan digest 和全部步骤顺序必须一致。只允许“完成的前缀 +
`status=not_executed`、`reason=step_boundary` 的从未开始后缀”作为恢复边界。

任何新 provider 请求、命令、session 或报告写入之前，逐项核对：

- 原执行边界记录的 trace、任务记录/session 或命令档案字节 hash 仍一致；
  trace 结构、唯一完成终态及 run/task 归属一致，session 包含成对消息并指向同一 trace；
- 任务记录属于同一步的收窄 policy，且有且只有一次成功的已授权工具动作；模型只说完成不算；
- 写入路径有唯一内容 hash/字节数证据，当前文件仍一致；外部改动、缺失、符号链接拒绝；
- 命令档案的模板 id/run id 与步骤一致，报告摘要与档案一致，exit 0 且未超时；
- task id 归属准确，没有未决 evidence issue 或 runner 异常。

证据复用既有有界 no-follow 读取器，每份最多 1 MiB；非常规文件、符号链接路径、读取中变化均拒绝。
未开始步骤的 trace/session/命令档案及 create-only 业务文件必须不存在，输出父目录不能是符号链接；不静默追加旧产物。命令档案与 trace 同目录，支持非默认 trace 目录。

## 报告与不重复执行

已完成结果保留原 task id、trace/session 路径与 hash。执行器仅执行从未开始的后缀，权限仍来自
同一 one-action policy 和声明 runner。新报告累计保留结果及文件改动，并用 `recovery` 字段
记录前序报告的相对路径与精确 hash；不重写旧报告、trace 或 session。
对全部完成报告续跑是已验证的 no-op：直接返回原结果，不创建新的累计报告。

`task report` 只在前序字节、Spec/Plan、完成结果和改动记录均被保留时，按累计关系替代前序视图，
不重复计算同一命令。缺失/冲突链接、多个后继进入 evidence issues；读报告不重跑命令或验收。
命令验收复用同一累计视图，不让旧的未开始边界盖过后续成功命令。

## 拒绝与未验证边界

- 不自动重试 failed/cancelled 或未知进行中副作用；需要单独授权的修正或人工核对。
- 旧报告无完整 artifact hash 时仍可查看，不能授权恢复；不在续跑时补写 hash。
- 仅保证本地单写者、显式步骤边界；不是任意崩溃、跨进程/跨机器的 exactly-once 事务。
  无 OS 沙箱，不防校验后的并发写者；hash 不是抵御全量证据及授权替换的签名。
- 执行完成与验收通过仍分开；不新增 CLI 命令、自动修正、provider 选择或发布。
  真实 provider、Windows/Linux、wheel 与干净安装不在本次验证范围。
