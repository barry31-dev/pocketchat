# 有界命令修正 v1

本未发布切片为失败的命令 step 增加一次显式修正；不重试任意任务、不重新生成
计划、不改变任务验收。

## 规则

`run_bounded_repair()` 先运行声明的模板一次。失败时调用者只拿到有界命令摘要，
并返回明确的 `RepairDecision`。决定修正后只允许重跑同一模板 id 一次，trace 写
同级 `<step>.retry-1.jsonl`；不能注入 shell 字符串、另一模板或不同 cwd。

失败指纹是 exit status、超时与两条完整输出 hash 的 SHA-256，不含输出预览。
修正后若指纹仍相同，结果为 `same_failure` 并立即停止，不存在第三次尝试。其他
状态是 `repaired`、`retry_failed`、`not_repairable` 与 `succeeded`（无需修正）。
命令启动失败没有 exit code，两条流 hash 也为空；除非失败结果本身能被完整
指纹化，否则不进入模型修正。

传入 root 时，有界结果只持久化一次到 `.pocketchat/task-repair/<step>.json`；
同 step 第二次写入被拒绝。所有结果都带 `task_acceptance_pass: false`：命令成功
绝不等于任务验收通过。

## 边界

operator callback 路径本身不改文件、不调用 provider；下方显式开启的模型路径会
通过既有受控编辑器和 provider adapter 完成这两件事。两条路径都不重跑验收、不新增
通用重试框架、不安装依赖，也没有 OS 级沙箱。当前未发布切片只验证本机 macOS +
CPython 3.13.9；Python 3.9、Windows、Linux、wheel 与干净安装仍未验证。

## 模型修正（显式开启）

`task exec --repair-model` 为已取得 exit status（含超时）的失败 `run_command` step
开启独立的模型修正路径；默认关闭，不带该参数不会发出修正 provider 请求。模型只收到已授权的 spec/plan
digest、原命令模板 digest、稳定失败指纹、有界 stdout/stderr 摘要与 spec 的
`overwrite_paths`。

模型必须返回恰好包含 `action`、`path`、`content`、`reason` 的封闭 JSON。
`action` 只能是 `overwrite_file`；`path` 必须精确匹配 `overwrite_paths`。模型
不能提供 argv、shell、cwd、工具、provider 或额外路径。替换内容只经既有
`snapshot_overwrite()` / `apply_overwrite()` 安全覆盖，然后以同一模板 id、argv、
cwd 和声明的超时重跑一次，trace 写同级 retry 文件。动作格式错误、越权、快照
陈旧或包含配置凭据时，在任何文件修改与重试前拒绝；provider 构建、请求或请求
证据写入失败同样在修改前停止。

修正回执只写一次到 `.pocketchat/task-repair/<step>.json`；请求快照单独写到
`.pocketchat/task-repair/<step>.attempt-1.json`，并由回执中的 SHA-256 绑定。
回执包含 `repair_mode: model`、spec/plan/template digest、两次失败指纹、覆盖前后文件
hash 与行数、有界命令事实，以及 `task_acceptance_pass: false`；回执本身不保存替换正文
或命令输出预览；请求快照会保存实际发送给 provider 的上下文，因此包含有界失败摘要，
但不含 provider 回复与替换后的文件正文。重试成功仍只是命令证据，不是任务验收。
