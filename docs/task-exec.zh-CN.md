# 任务执行转换 v1

本未发布切片把一份已解析 TaskPlan 转换为对现有 `run_task` 的有界调用，
不新增第二套执行器：provider、工具、session、task record 与 trace 仍由当前
任务运行时负责。公开 v1.1.0 制品不含该模块。

## 授权

`authorize(plan, spec, expected_plan_sha256=...)` 会对着同一份不可变 TaskSpec
重新解析计划。只有 plan 的 `spec_sha256` 仍匹配，且该 plan 的规范 digest 与调用者
显式给出的授权值一致时才允许转换。Spec 改变、plan 被改写或传入另一计划的
digest，都会在任何 provider/工具调用前拒绝。

## Step 转换

每个 plan step 变成单动作 task policy：

- `read_file` 与 `write_file` 只保留该 step 的 `inputs`，且必须是 TaskSpec
  allowlist 的子集；
- `write_file` 仍为 create-only；
- `memory_search` 使用 Spec 的结果上限与一个字面查询；
- 不引入重试、任意动作或自由 shell。

Prompt 只包含 step objective、action/inputs 与 expected evidence。执行前还会
检查计划的依赖顺序。

## CLI 执行

`task exec --spec SPEC --plan PLAN --plan-sha256 DIGEST` 是已经持有两份文档的
操作者使用的产品入口。它会重新解析 Spec、把 Plan 绑定到该 Spec，并在读取
provider 配置前校验 plan digest；随后复用下文同一个 `execute_plan()` 路径。
CLI 不生成计划，也不扩大权限。

JSON 结果包含精确的 spec/plan digest、task id、step 状态和本次调用创建的
execution report。step 失败返回 1，报告仍可回查。step 完成不等于验收通过；
命令证据仍保持 `task_acceptance: not_evaluated`。

## 模型修正入口

`task exec --repair-model` 是显式 opt-in。失败的命令 step 可以请求一次模型生成
受 `overwrite_paths` 约束的文件替换，经既有安全覆盖后只重跑同一模板一次；
不带该参数不会调用修正 provider。详见 [有界命令修正](task-repair.zh-CN.md)。

## 步骤边界与接续

`--stop-after N` 在本次新尝试 N 步后写出明确的 `step_boundary`。这是有意的暂停，
命令返回 0、`status: boundary`，但它不是任务完成，也不代表验收通过。

可以从精确报告接续：

```bash
pocketchat task exec --spec spec.json --plan plan.json \
  --plan-sha256 <digest> --resume-report REPORT \
  --report-sha256 <report-digest> --config pocketchat.json --json
```

也可以从 checkpoint 接续；CLI 会在读取 provider 配置前核对 checkpoint 绑定的
报告路径与 digest：

```bash
pocketchat task exec --spec spec.json --plan plan.json \
  --plan-sha256 <digest> --checkpoint checkpoint.json \
  --config pocketchat.json --json
```

已完成 step 会重新验证并跳过，只执行从未开始的后缀；完全完成的历史报告是已验证
no-op，不会生成新报告。失败/未知副作用、报告 digest 改变、Spec/Plan 不匹配或证据
缺失都会在新的模型/命令调用前拒绝。`--resume-report` 与 `--checkpoint` 互斥；直接
报告必须同时给出 `--report-sha256`；两种接续都不能与 `--stop-after` 组合。

## 顺序执行

`execute_plan()` 显式接收现有 `run_task` callable，测试可传 stub。各 step
按依赖顺序，在各自收窄后的 policy 下串行执行。某一步失败后停止后续执行，
后续 step 返回 `not_executed`，不静默跳过。每个尝试过的 step 都返回 task id、
trace 路径与 session 路径；只有状态为 `completed` 的 step 才允许其依赖继续运行。

本格不覆盖既有文件、不做自动修正、不调用真实 provider，也不提供超出既有
`run_task` 契约的验收能力。命令 step 是下方明确 opt-in 的例外。

## 命令 step

`run_command` step 不生成模型 policy。`execute_plan()` 必须显式收到
`command_runner`，并只传模板 id 与 trace 路径；缺少 runner 时在执行前拒绝。
命令成功仍然不等于任务验收通过。

## 执行报告

`execute_plan()` 在获准尝试的 step 结束后，把一份封闭的
`pocketchat-task-execution-report.v1` 写到 `.pocketchat/task-reports/`。它绑定
spec/plan digest、task id、各 step 状态与 trace、命令 archive/双流 hash 与计数
（绝不复制 stdout/stderr 摘要），以及已完成 write step 的文件 hash 与行数。
命令 archive 的精确字节 digest 会在证据接入前重检，字节变化即拒绝。runner 抛错时
报告仍先落盘再向外传播，失败或拒绝的 step 不会消失。

`task report` 只读这些报告，不执行命令、不生成 provider 请求、不改写 task
record。缺失、损坏、不属于该 task 或被篡改的命令 archive 进入 additive
`evidence_issues`，状态为 `refused` 或 `unknown`，绝不包装成
`task_acceptance: passed`。`commands` 列模板 id、cwd、exit code、超时标记、
输出 hash 与截断标记；`changes` 列路径、前后 hash、字节数与行数。

## 步骤边界接续

`stop_after=N` 在本次新尝试 N 步后，把后缀记为明确从未开始。
`resume_report` + `expected_report_sha256` 将续跑绑定到原报告、Spec/Plan 与已完成产物证据；
跳过验证过的前缀，保留旧证据。失败或未知副作用拒绝，不自动重试。
严格前置检查、累计报告链接和单写者/崩溃边界见 [任务恢复](task-recovery.zh-CN.md)。

## 边界

本切片不自动重试命令、不做失败修正、不安装依赖、不删除文件、不调用真实
provider，也不发布版本。执行器本身没有 OS 级网络或文件系统沙箱；本次步骤边界
目标用例在本机 macOS 的 CPython 3.13.9 与 3.9.6 均有运行证据，不等于完整 3.9 回归；
Windows、Linux、wheel 与干净安装仍未验证。
