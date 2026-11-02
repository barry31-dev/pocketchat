# 命令模板 v1

本未发布切片只执行 TaskSpec 中操作者声明的命令模板。它不接受 shell 字符串，
也不会启用 `shell=True`。公开 v1.1.0 制品不含该能力。

## TaskSpec

`commands` 是封闭数组。每一项必须且只能包含：

```json
{
  "id": "run_tests",
  "argv": ["python3", "-m", "unittest"],
  "cwd": ".",
  "timeout_seconds": 30,
  "max_output_bytes": 4096
}
```

`id` 唯一且稳定。`argv` 是非空字符串数组；`sh`、`bash`、`zsh`、`cmd`、
PowerShell、`pwsh` 等 shell 可执行文件即使写成路径也会拒绝。`cwd` 必须留在
工作区内。`timeout_seconds` 为 1–300；`max_output_bytes` 按每条流计算，上限
为 1 MiB。

## TaskPlan

`run_command` step 在 `inputs` 中只写一个已声明模板 id，不能携带 `argv`、
shell 字符串、环境变量或内联工作目录。没有 commands 的 TaskSpec 不能授予该
动作。

## 执行证据

`run_command_template()` 使用 argv 与 `shell=False` 启动进程，不继承任意环境
变量，只保留 `PATH`、locale、临时目录等基本进程变量；cwd 必须解析在工作区内。

结果记录模板 id、相对 cwd、exit code、超时标记、耗时、字节数、完整流 SHA-256
和有界 stdout/stderr 摘要。摘要被截断时以 `[truncated]` 结尾，但 hash 覆盖完整
流。trace 写入 `run.started`、`tool.call.started`、一个终止工具事件和
`run.finished` 或 `run.failed`，不写完整 argv。

`exit 0` 只表示命令成功，不表示任务验收通过：`task_acceptance` 始终为
`not_evaluated`。非零退出与超时是带证据的命令结果，不是验收回执。

## 边界

`task report` 现在只读有界执行报告，不重跑命令。本切片不重试命令、不做失败
修正、不安装依赖、不删除文件、不调用真实 provider，也不发布版本。执行器本身没有 OS 级网络或
文件系统沙箱；操作者声明的联网命令仍由操作者负责。当前只验证本机 macOS +
CPython 3.13.9；Python 3.9、Windows、Linux、wheel 与干净安装仍未验证。
有界结果同时写入 `traces/command-evidence/<trace-stem>.json`，schema 为
`pocketchat-command-evidence.v1`。若配置凭据出现在该归档中，runner 会 fail-closed
且不写入文件。
