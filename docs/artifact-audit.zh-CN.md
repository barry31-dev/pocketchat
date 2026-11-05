# Artifact secret audit

`pocketchat.artifact_audit` 是对调用者已经授权检查的字节做纯离线审计的模块。它将显式提供的秘密字节值与这些字节做精确匹配，并返回有界报告。它不发现文件、不打开路径、不跟随符号链接、不读取环境变量、不调用 provider，也不写入 artifact。

## API

```python
audit_artifacts(
    artifacts,
    secret_values,
    *,
    max_artifacts=256,
    max_artifact_bytes=8 * 1024 * 1024,
    max_total_bytes=32 * 1024 * 1024,
    max_matches=256,
    max_report_bytes=64 * 1024,
)
```

`artifacts` 是只含以下字段的闭合记录列表：

| 字段 | 契约 |
|---|---|
| `artifact_id` | 稳定、非空、使用保守标识符字符集的 ID |
| `kind` | `request`、`trace`、`session`、`comparison`、`execution_report` 或 `checkpoint` |
| `path` | 规范化的工作区相对路径；只校验，不返回 |
| `entry_type` | `regular_file` |
| `content` | 调用者提供的 bytes |

`secret_values` 是非空 bytes 或 UTF-8 字符串。所有值按 bytes 比较，因此 CJK、emoji 以及跨越内部扫描块边界的结果是一致的。非法调用限额抛出 `ArtifactAuditError`；非法输入或超出包含边界的限额返回 `refused`，不会通过截断变成 `clean`。

## 报告

报告 schema 为 `pocketchat-secret-audit.v1`，版本为 `1`：

```json
{
  "schema": "pocketchat-secret-audit.v1",
  "schema_version": 1,
  "status": "clean | found | refused",
  "artifact_count": 1,
  "secret_count": 1,
  "match_count": 1,
  "matches": [
    {
      "artifact_id": "artifact-1",
      "location_category": "session",
      "artifact_sha256": "..."
    }
  ],
  "omissions": [],
  "limits": {},
  "report_sha256": "..."
}
```

`matches` 按 artifact、位置类别和 artifact 整体 SHA-256 排序并去重。报告不会包含秘密值、匹配片段、字节偏移、行号、路径、请求/消息正文或凭据。`report_sha256` 是去掉该字段后报告正文的 canonical JSON SHA-256。

`found` 只表示某个显式值出现在调用者给定的字节中；`clean` 只表示在这些字节中没有找到显式值。它不证明 artifact 的真实性、完整性、没有其他秘密、磁盘安全或检查后未发生变化。

## 范围

本切片只交付纯核心。它不新增 CLI、文件 reader、脱敏、隔离、修复、evidence-pack 集成、provider 调用、发布或 Release 动作。后续 reader 必须另外证明有界/no-follow 文件访问，并保持同样的不回显报告契约。

## 验证

聚焦测试覆盖干净与命中、ASCII 与 UTF-8 秘密、去重、闭合的 artifact 类型与路径、输入限额、报告限额、稳定顺序、输入不可变和零 I/O。实现分支的审查记录写明实际命令与未验证范围。
