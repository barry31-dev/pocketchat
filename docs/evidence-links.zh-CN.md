# 证据关联核对

`pocketchat.evidence_links` 是对**调用方已经采集的观察**做纯校验的核心。
它不打开文件、不解析真实路径、不调用 provider，也不修复 artifact。`complete`
只表示传入的描述符和关系在内部一致；**不表示本模块重新读取了原文件**，也不表示
被描述的所有运行真的发生过。

## 接口

```python
from pocketchat.evidence_links import verify_evidence_links

report = verify_evidence_links(artifacts, relations)
```

两个参数都是字典列表，校验过程不修改输入。报告含 `schema_version=1`，状态为：

| 状态 | 含义 |
|---|---|
| `complete` | 所有必需观察都存在、读取覆盖完整，且所有必需关系均被覆盖、无矛盾。 |
| `partial` | 已有证据一致，但显式可选的 artifact/关系缺失，或观察明确不完整。 |
| `refused` | 输入畸形或不安全、身份/摘要不一致、必需 artifact 缺失，或超过预算。 |

限额为 256 个 artifact、1024 个 relation、64 KiB canonical 报告。超过限额直接
`refused`，不截断后冒充 `complete`。

## Artifact 描述符

```json
{
  "id": "request",
  "kind": "request",
  "path": "traces/requests/run_1.turn-1.json",
  "whole_file_sha256": "...",
  "observed_whole_file_sha256": "...",
  "semantic_sha256": "...",
  "semantic_scope": "request-body-canonical",
  "identity": {"run_id": "run_1", "turn": 1},
  "read_coverage": "complete",
  "required": true,
  "present": true
}
```

`kind` 是封闭集合：`session`、`trace`、`request`、`comparison`、
`execution_report`、`checkpoint`。artifact ID 必须唯一。路径必须是规范化的
workspace 相对 POSIX 路径；绝对路径、`..`、反斜杠和 symlink 式逃逸均拒绝。
`identity` 也只允许 `run_id`、`run_ids`、`turn`、`conversation_id`、`task_id`、
`spec_sha256`、`plan_sha256`、`comparison_id`；其他键直接拒绝，不复制到报告。

声明的与观察的整文件 SHA-256 都必须是 64 位小写十六进制并彼此相等。
`semantic_sha256` 是独立摘要，其 `semantic_scope` 只能是 `canonical-json`、
`request-body-canonical` 或 `trace-events`。语义摘要与整文件摘要相同，或使用
`whole-file` 作为语义 scope，直接 `refused`，不能把两种口径混成一个名字。

`read_coverage` 只能是 `complete` 或 `partial`。`required` 默认 true，
`present` 默认 true，仅在记录明确缺失时设为 false。缺必需 artifact 为
`refused`，缺可选 artifact 为 `partial`。

## 关系

每个 relation 明确两端 artifact ID、封闭的关系 kind 与被比较身份字段：

```json
{
  "id": "trace-request",
  "source": "trace",
  "target": "request",
  "kind": "same-run-turn",
  "fields": ["run_id", "turn"],
  "required": true
}
```

关系 kind 为 `same-run`、`same-run-turn`、`contains-run`、`binds-spec`、
`binds-plan`、`binds-checkpoint`。**没有显式关系就没有关联结论**。
`same-run-turn` 校验 run 身份与 request turn；`contains-run` 接受 comparison 的
`run_ids` 列表；其他关系只比较显式字段。端点不存在、relation ID 重复、自关联
或比较字段未观察，均为 `refused`。

报告包含有界的 artifact/edge 结果及具名 `unknowns`、`omissions`，不复制
request、message 或 credential 正文。排序稳定，不受调用方输入顺序影响。

## 不做

本切片不加 CLI、不读取 artifact 字节、不证明摘要来自当前文件系统、不修复
不一致、不调用 provider，也不声称已发布。需要这些动作时，应由另一个有界
reader/CLI 切片实现，并在报告中保持证据范围明确。
