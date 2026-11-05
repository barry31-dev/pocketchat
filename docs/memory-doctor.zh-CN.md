# Memory Doctor 纯核契约

> 范围：对调用者已读取并传入的 v2 memory 记录与显式 source observation，生成
> `pocketchat-memory-doctor.v1` 完整性报告。它不是 store reader、CLI、修复、迁移，
> 也不证明来源当前仍然存在或观察值绝对真实。

## API

```python
audit_memory_records(
    records,
    *,
    workspace_id,
    source_observations,
    max_records=4096,
    max_sources=4096,
    max_findings=256,
    max_report_bytes=65536,
)
```

`records` 是调用者已经读出的记录；`source_observations` 是调用者显式提供的来源观察。
函数不打开文件、不读环境、不解析路径、不拿锁、不联网、不执行命令、不读时钟，也不调用
provider。它不重新读取来源来证明观察值真实，也不声称排除了来源读取过程中的竞态。

限额必须为正整数，布尔值不算整数。record、source 的项数恰好等于上限时接受，超一即由
`MemoryDoctorError.error_type` 拒绝。输入 iterable 最多消费“上限 + 1”项，不会先整体物化。
`max_findings` 只限制报告保留的 finding 数，不限制处理；超出的 finding 计入
`omitted_findings`，既不抛异常也不静默丢失。最终 canonical report 必须恰好适配
`max_report_bytes`，超一即拒绝。

## 记录检查

每条记录复用既有 `parse_v2_record` 契约校验。坏记录产生 `invalid_record` finding，报告为
`refused`，不做规范化或修复。重复 `memory_id` 与重复 `(memory_id, revision)` 都被拒绝。

属于其他 workspace 的记录以显式 `foreign_workspace` omission 计数，不当作当前 workspace
的损坏。合法且没有 source ref 的记录不需要 source observation。

报告包含 accepted/candidate/retired/conflicted 的 admission 计数、`sensitive` 计数、来源计数、
保留/省略 finding 计数与 omission 计数；不包含 memory 正文、memory ID、source ref、source digest、
凭据或自由文本。

## Source observation

每条 observation 只允许以下封闭字段：

```json
{
  "source_kind": "file",
  "ref": "notes.md",
  "scope": "whole-file",
  "sha256": "<lowercase sha256>",
  "coverage": "complete"
}
```

`source_kind` 为 `file` / `event` / `url`；`coverage` 为 `complete` / `partial` / `missing`。
identity 是 `(source_kind, ref, scope)`。

- 记录 source ref 与 `complete` observation 的 digest 完全一致：`sources_verified` 加一。
- 缺 observation，或 observation 为 `missing` / `partial`：`sources_unknown` 加一，生成固定
  finding；**不写成已验证**。
- `complete` observation 的 digest 与记录 ref 不同：`source_digest_mismatch` 矛盾，报告
  `refused`。
- 重复 observation 被拒绝：同 digest 同 coverage 为 `duplicate_source_observation`；不同
  digest 为 `source_observation_conflict`；不同 coverage 为
  `source_observation_coverage_conflict`。

多条记录可以引用同一个 source identity。observation 可共享，但每条 ref 仍计入来源统计。

## 状态与边界

- `refused`：坏输入、重复 identity、source observation 冲突或已知 digest 不一致。
- `partial`：合法 workspace 记录中存在 missing/partial source，或有 foreign-workspace omission。
- `complete`：所有记录合法、所有 source ref 都有 complete 且 exact 的 observation，且无 omission。

报告是 canonical JSON-compatible data，不随输入顺序改变。finding 按稳定顺序保留有界前缀，其余
数量显式写入 `omitted_findings`。报告超过字节上限时拒绝而不是截断。输入 mapping/list 不被修改。

## 证据边界

本纯核只证明“传入记录与传入 observation 之间的一致性”。它不证明文件字节、store 尾部、来源历史
或 observation 的采集过程正确。后续只读 CLI 接线必须复用现有有界安全 v2 store reader，且不属于
本切片。本切片不授权 accept、retire、forget、merge、repair 或发布。
