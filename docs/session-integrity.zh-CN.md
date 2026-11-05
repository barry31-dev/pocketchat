# 会话完整性审计

`pocketchat.session_integrity` 只审计**调用方已经读出的** session records。
它不打开 session 文件、不修复记录、不执行命令、不读取环境，也不调用
provider。`complete` 只表示传入对象通过了下列检查，不证明原文件仍然存在，
也不证明其中的运行真实发生过。

## 接口

```python
from pocketchat.session_integrity import audit_session_records

report = audit_session_records(
    records,
    max_findings=64,
    max_omissions=64,
    max_bytes=65536,
)
```

`records` 是 session 文件按顺序读出的 JSON 对象列表，包含 header。
输入及嵌套 tool context 不会被修改。报告使用 `schema_version=1` 与
`pocketchat-session-integrity.v1` schema 名。

| 状态 | 含义 |
|---|---|
| `complete` | 可用记录已完整检查，所有 finding 都在报告中。 |
| `partial` | 审计已执行，但受上限约束的 finding 或 omission 没有全部展示，并已显式计数。 |
| `refused` | 容器/header 不可审计、限额非法，或 canonical 报告无法装入 `max_bytes`。 |

默认报告上限为 64 KiB；超限直接 `refused`，不截断后冒充完整结果。
`max_findings` 与 `max_omissions` 限制为 1..4096，`max_bytes` 限制为
512 字节..1 MiB。

## 检查范围

第一条必须是 session header。没有 `schema_version` 的 legacy header 仍可读；
schema `1` 必须有非空 `conversation_id` 与 `branch_id`。run/session 与可选
workspace 绑定会被比较，但标识符本身不会回显。parent branch 与 parent turn
必须成对出现。

message role 只能是 `user` 与 `assistant`。现代 `turn_id` 使用
`turn_NNNNNN`，必须唯一且单调递增；没有 turn ID 的 legacy message 仍可接受。
tool context 沿用现有 provider 形状：assistant call 指向一个 function，tool
result 指向尚未结束的 call，每个有效 call 必须恰好收到一个 result。result
就是该 call 的显式终止证据；缺失、重复、孤立和未结束配对都会生成具名 finding。

finding 只含固定错误码和位置元数据：record index、message index、field 与
tool-context index。它不包含 message、request、tool 参数/结果、session 或
凭据正文。finding 顺序稳定并由输入位置决定。空输入是具名 refusal，不与非法
容器或损坏记录混为一类。

## 不做

本切片不增加 CLI、文件读取、repair/copy、writer 修改、迁移、provider 调用
或发布动作，也不会从“报告干净”推断缺失历史。真实文件读取、reader 集成和
退出码契约属于其他独立切片。
