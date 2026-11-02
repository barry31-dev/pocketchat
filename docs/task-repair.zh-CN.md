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

传入 root 时，有界结果只持久化一次到 `.pocketchat/task-repair/<step>.json`；
同 step 第二次写入被拒绝。所有结果都带 `task_acceptance_pass: false`：命令成功
绝不等于任务验收通过。

## 边界

本切片不改文件、不重跑验收、不调用 provider、不新增通用重试框架、不安装依赖，
也没有 OS 级沙箱。当前只验证本机 macOS + CPython 3.13.9；Python 3.9、Windows、
Linux、wheel 与干净安装仍未验证。
