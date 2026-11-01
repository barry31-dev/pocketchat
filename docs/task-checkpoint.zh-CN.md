# 计划/命令 checkpoint

`task checkpoint preview` 只校验一份 `pocketchat-task-checkpoint.v1` 记录，
不执行任何步骤。checkpoint 由封闭的
`pocketchat-task-execution-report.v1` 报告派生，指明哪些计划步骤已确认完成、
哪些尚未执行。它是后续恢复的证据，不是新的执行授权。

```bash
pocketchat task checkpoint preview ckpt.json --json
pocketchat task checkpoint preview ckpt.json \
  --spec-sha256 <TaskSpec digest> --plan-sha256 <TaskPlan digest>
```

## 绑定什么

记录包含 spec 与 plan digest、已完成/待执行步骤划分、等于已完成数量的
`boundary_index`、步骤所属 task id、有界文件副作用摘要（路径、新 SHA-256、
字节数、基线）与命令副作用摘要（step id、模板 id、退出码、超时标记、
档案 SHA-256），以及它派生自的报告的 SHA-256。

`create_checkpoint()` 会读取报告文件，并在以下情况拒绝：传入对象与磁盘字节
不一致、声明的报告 digest 不匹配、报告带未决证据问题、已完成步骤出现在未执行
步骤之后。失败步骤永远不会进入已完成前缀；缺少档案 digest 的命令副作用也会
被拒绝。

`parse_checkpoint()` 可把记录绑定到将要恢复的执行：传入
`expected_spec_sha256` / `expected_plan_sha256` 后，一份结构合法但指向另一次
授权执行的记录会被拒绝；不传则只校验结构。

## 从 checkpoint 恢复

`resume_from_checkpoint(root, path)` 把 checkpoint 解析成确切的报告与 digest，
调用方无需猜测当前是哪一份报告。返回的 `report_path` 是工作区相对路径，且必须
直接位于 `.pocketchat/task-reports/` 下——checkpoint 不能把任意文件指为自己的
证据。报告字节会被哈希，且必须等于记录的 `report_sha256`；可选的 expected
spec/plan digest 也必须匹配。

```bash
pocketchat task checkpoint resume ckpt.json \
  --spec-sha256 <TaskSpec digest> --plan-sha256 <TaskPlan digest> --json
```

该预览是只读的。把解析出的报告与 digest 交给现有的
`execute_plan(..., resume_report=..., expected_report_sha256=...)`
即执行恢复；已完成步骤会被重新校验，而不是重放。

## 边界

checkpoint 文件与其报告文件都必须是常规非符号链接文件，且分别不超过 1 MiB 与
报告读取方自身的上限。重复 JSON 成员和 `ok:false` 的 CLI envelope 会被拒绝。
`task checkpoint preview` 是只读的：不创建 task、session、trace、request、
`.pocketchat` 状态，也不调用 provider；它不执行步骤、不重跑命令、不重读文件正文。

从 checkpoint 恢复，不证明工作区仍与记录的文件 hash 一致（除现有恢复检查
重新校验的部分），不能从任意崩溃恢复，不防护并发写者，也不授权发布。

checkpoint 只证明报告声称哪些步骤已完成以及各自的 hash。它不证明工作区仍与这些
hash 一致，不重放步骤，不防护并发写者或跨进程替换，本身不恢复任务，也不授权发布。
