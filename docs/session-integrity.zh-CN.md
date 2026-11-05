# 会话完整性审计

`pocketchat.session_integrity.audit_session_records(records, *, max_findings=64,
max_omissions=64, max_bytes=65536)` 审计**调用方已经读入**的记录。它不打开会话文件、
不修复或重写任何一行、不执行命令、不联系 provider，也不回显消息正文。

报告是封闭结构，schema 为 `pocketchat-session-integrity.v1`，`schema_version=1`。
状态含义：

| 状态 | 含义 |
|---|---|
| `complete` | 传入记录内部一致，且没有被有界审计截断。 |
| `partial` | 传入记录内部一致，但显式读取覆盖为部分，或有界审计省略了记录/证据。 |
| `refused` | header、schema、消息形状、turn 顺序或工具上下文格式错误、不满足硬契约或超出范围。 |

`complete` 只证明这组**内存中的记录**满足审计契约；不证明文件已被完整读取、
provider 真的执行过该对话，也不证明另一个会话有效。

## 输入

`records` 是有序列表。首条必须是 header，含非空 `session` 身份；出现
`trace`、`created`、`pocketchat_version` 时，它们必须是非空字符串。没有
`schema_version` 的 header 按 legacy 接受；一旦出现，必须是 `"1"`，显式 null
或其他值会被拒绝。可选的 conversation/branch/parent 身份若出现，也必须是非空字符串。
未知 header 键不会复制到报告。

后续每条记录必须是由 `user` 或 `assistant` 角色及字符串 `content` 构成的对象。
现代记录可带 `turn_id`；legacy 记录没有该字段时，沿用现有 reader 的
`turn_000001` 顺序编号。显式 turn id 必须为 `turn_` 加六位数字、唯一、严格递增并
符合顺序。工具上下文只允许出现在 assistant 消息上，采用现有 session reader 接受的
provider 形状：一条或多条带唯一 function-call id 的 assistant `tool_calls`，随后每条
调用恰有一个匹配的 `tool` 结果。后面的 `tool_calls: []` 不是终态；每个调用都必须
与结果配对。消息与工具正文绝不复制到 finding。

输入记录可以带 `_audit_read_coverage="partial"`，表示调用方只观察了该记录来源的
一部分；这是 `partial`，不是格式损坏。其他值会被拒绝。该字段仅是输入观察，不会
写回输入。

## 限额与报告

默认限额为 64 条 finding、64 条 omission 和 64 KiB canonical UTF-8 报告。
限额必须是正整数，布尔值会被拒绝。包含 header 在内最多检查 128 条记录；更多记录
会产生显式的整数 `record_limit` omission，状态为 `partial`。finding 预算耗尽时，
报告给出固定错误码、record/item 索引和整数 `evidence_omitted` 计数。如果连最小报告
都放不进 `max_bytes`，函数抛出 `ValueError`，而不是返回一个误导性的 `complete`。
本函数不修改输入。

## 不做

本切片不增加 CLI、真实 session 文件 reader、修复/复制、writer 变更、runtime 接线、
provider 调用、tag、Release 或发布。真实 reader 与 CLI 必须分别由新的有界切片实现。
