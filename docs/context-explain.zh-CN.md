# Context explain

`pocketchat.context_explain` 是针对一次既有分层 context projection 的纯函数、
有界解释面。它只观察调用方给出的 source payload 与 `project_context` 结果，
不读取文件、不重算选择、不改写 projection，也不调用模型或 provider。

## API

```python
explain_context_projection(
    projection,
    *,
    max_findings=64,
    max_omissions=64,
    max_bytes=65536,
)
```

`projection` 是闭合的 `pocketchat-context-explain-input.v1` 对象：

| key | 含义 |
|---|---|
| `source` | `project_context` 的精确 source payload，包含 `instructions`、`task_state`、`messages`、`evidence`、`pending_messages`。 |
| `selection` | 生成 projection 时使用的精确 `max_chars` 与 `keep_recent_turns`。 |
| `result` | 既有 `{messages, manifest}` 结果，manifest 为 `pocketchat-context-projection.v1`。 |
| `sources` | 每个 source 条目一个闭合 descriptor：`{source_id, layer, locator}`。 |

locator 为 `{kind, index}`。`kind` 只能是 `instruction`、`task_state`、
`message`、`pending_message` 或 `evidence`；`layer` 只能是 `fixed`、`recent`、
`task`、`memory` 或 `tool`，其中 evidence 只能标为 `memory` 或 `tool`。
source id 必须唯一，且每个 source 条目必须恰好有一个 descriptor。

解释前，核心先用 canonical source payload 校验 `source_sha256`，再用精确的
projected messages 与 manifest 校验 `projection_sha256`。任一不一致都直接
`refused`，不提供尽力而为的解释。

## 报告

返回闭合的 `pocketchat-context-explain.v1`：

| key | 含义 |
|---|---|
| `status` | `complete`、`partial` 或 `refused`。 |
| `layers` | `fixed`、`recent`、`task`、`memory`、`tool` 五层摘要。 |
| `observations` | 有序的 source 选入/省略观察。 |
| `findings` | 类型化矛盾、敏感 source 标记或拒绝原因。 |
| `omissions` | 明确的元数据缺失或 source 省略记录。 |
| `budget` | 配置上限与实际 findings/omissions/payload bytes。 |

observation 只含 `source_id`、`layer`、`record_kind`、`record_index`、
`selected`、`reason_code`、`selected_bytes`，绝不复制 request、message、
session、credential、memory 或被省略内容的正文。敏感 source 只产生
`sensitive_source` finding 并使报告成为 `partial`，不复制内容。

稳定 reason code 包括 `selected`、`outside_recent_window`、
`unrecorded_omission`、`duplicate_source`、`over_budget`、
`sidecar_metadata_unavailable`、`source_digest_mismatch`、
`projection_digest_mismatch`、`invalid_input_shape`、`unknown_input_schema`、
`unknown_projection_schema`、`missing_source_descriptor`、
`unexpected_source_descriptor`、`layer_mismatch`、`finding_limit_exceeded`、
`omission_limit_exceeded`、`report_limit_exceeded`。

## 状态与上限

- `complete`：所有 source 都有观察，且没有 finding 或 omission。
- `partial`：projection 可用，但 source 敏感、被省略，或 sidecar 没有保留足够
  元数据来解释它。
- `refused`：输入、digest 绑定、schema 或限额契约不可信。拒绝也使用有界 envelope，
  不静默截断。
- `max_findings`、`max_omissions`、`max_bytes` 溢出时拒绝；不会为了装入报告而丢弃
  最旧的 finding 或 omission。

函数保持输入不变，不执行 filesystem、command、socket、provider 或 model 调用。
本切片不增加 CLI、reader 接线、repair、migration、projection 改写、tag、Release
或包上传。

## 验证

`tests/test_context_explain.py` 覆盖五层解释、digest 绑定、descriptor 闭包、敏感信息
脱敏、限额拒绝、sidecar 元数据缺失、空 projection、稳定输出、输入不可变与零 I/O。
