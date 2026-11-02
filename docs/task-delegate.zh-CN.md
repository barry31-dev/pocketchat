# 父计划授权的委派

`task delegate preview` 用一份 `pocketchat-task-delegation.v1` 请求对照它声称
派生的 TaskPlan 做校验。它只授权子任务，不执行子任务。

```bash
pocketchat task delegate preview deleg.json --plan plan.json --spec spec.json --json
```

## 授权检查什么

请求是封闭对象，包含 `delegation_id`、父计划 `parent_plan_sha256`、它挂接的
`parent_step_id`，以及 1–32 个子任务。每个子任务带有唯一且有界 id、目标、一个
动作和 1–32 个声明输入。

授权前会检查：

- 父计划 digest 必须等于已授权的 `ExecutionPlan` digest；
- 指定的父步骤必须存在于该计划中；
- 子任务动作必须是 TaskSpec 已授予的（`read_file`、`write_file`、`memory_search`）；
- 每个子任务输入必须工作区相对，且落在该动作的 allowlist 内；
  `write_file` 目标还必须是已授权的覆盖路径；
- `memory_search` 子任务只带一个字面查询。

结果把子任务行绑定到其父步骤，并记录父 spec digest。它不放宽任何权限：要求未声明
动作、父授权之外路径、越界段或绝对路径的子任务，会在调用 provider 前被拒绝。

## 边界

请求文件必须是常规非符号链接文件且不超过 1 MiB；重复 JSON 成员会被拒绝。预览是
只读的：不创建 task、session、trace、request、`.pocketchat` 状态，不调用 provider，
也不运行任何子任务。

授权不等于执行。它不启动子运行，不约束子任务预算，不证明子任务成功，不防护并发
写者，也不授权发布。子任务结果必须由调用方用自己的证据记录；被拒绝或失败的子任务
不得写回成已完成的父步骤。
