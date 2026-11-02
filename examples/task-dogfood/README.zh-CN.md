# 任务型 Agent 离线闭环示例

本示例走实际 PocketChat task/tool 路径，但模型是进程内 **stub**。命令真实在本机执行；
不调用真实 provider、不读取付费凭据、不联网、不安装依赖、不发布。
该示例尚未发布，不属于 v1.1.0 成品。

```bash
python3 examples/task-dogfood/demo.py
python3 examples/task-dogfood/demo.py --output /tmp/task-dogfood-evidence
```

`--output` 只能是不存在或空的目录；已有内容直接拒绝且不修改。不传时临时证据会在退出时删除，
要回查请传该参数。repair 需要 Git，只在样例目录创建通用 fixture 身份、无 remote 的独立仓库，
不改调用方检出。

## 四条路径及其真实边界

1. **success**：stub 请求通过 `run_task` → create-only `write_file` 真正创建 `report.md`，
   不由脚本提前写好。声明命令检查文件并把计数器加到 `1`；独立文件/文本与
   `command_succeeded` 验收都通过，有任务工具记录、执行报告和命令档案。
2. **repair**：先提交一份故意错误的 fixture；固定 argv 检查失败。确定性的
   **operator callback** 调用既有 `snapshot_overwrite` / `apply_overwrite` 安全覆盖，
   同一模板再跑一次成功，计数器为 `2`。前后 hash/行数、修正收据、两次命令档案都保留；
   另一个 read task 做 **file-only** 独立验收。不是模型自行诊断修复，也不把独立 repair
   命令伪装成 TaskPlan 已执行步骤或命令验收通过。
3. **recovery**：写文件与命令执行后 `stop_after=2`，留下从未开始的 finish。
   以原 report/SHA 真正续跑并创建 `done.md`；旧 task/trace/session/report/文件/命令
   字节和 mtime 不变，命令计数仍为 `1`。新累计报告有前序链接，文件/命令/finish 独立验收通过。
4. **refusal**：无效 TaskSpec 在任何 provider、命令、任务或 session 前被拒绝。
   单独保存 `pocketchat-dogfood-refusal.v1` 收据，trace/task/spec/plan 为 `null`；
   不造空 trace，也不套用另一份合法计划的 hash。

## 回查证据

每行 stdout 是 JSON，包含 case/outcome、stub 标记、有效时的 Spec/Plan digest、task id、
工作区相对路径和报告字节 SHA-256。非 refusal 路径保存 `spec.json`、`plan.json`、
`acceptance.json`、真实执行记录与独立验收收据。

repair 行包括两次命令的相同模板 hash/argv、archive/trace 路径、编辑与修正收据。
recovery 行包括旧产物前后 hash/mtime。测试还回读磁盘上的命令计数，不仅相信返回的调用次数。
从具体 case 工作区使用含本未发布改动的安装执行 `pocketchat task report TASK_ID --json`，
只读历史，不再次运行命令。

## 未验证范围

只有本机固定输入的离线证据，不代表真实模型质量、任意任务成功率、Windows/Linux 支持或公开发布。
repair 为操作者编写回调，恢复为单写者已确认步骤边界，不是任意崩溃/并发 exactly-once 事务；
无 OS 级文件系统与网络沙箱。替换 fixture 为私人数据后，输出需按私人证据保管。

> 本 demo 的 repair 仍是 operator callback，不是模型自主修正；独立的
> `task exec --repair-model` 路径不在本离线 demo 的执行范围内。
