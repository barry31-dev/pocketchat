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

## 显式观察 CLI

```bash
pocketchat evidence verify observations.json --workspace ./checkout --json
pocketchat evidence verify observations.json --workspace ./checkout
```

`observations.json` 必须是规范的**工作区相对路径**。`--workspace` 指定只读根，默认当前目录。
命令只读这一个观察文档，不打开其中声明的原工件，不执行任务、重放请求、修复证据、读取 profile
或调用模型。输入对象闭合为 `schema_version: 1`、`artifacts`、`relations` 三字段；工件与关系结构
沿用本文纯核契约。完整示例见 [英文对应页](evidence-links.md#explicit-observation-cli)，示例摘要
只是说明性声明，不是实际文件测量。

本入口复用 `verify_evidence_links()`，`complete` 只表示**传入观察内部一致**，不证明原文件仍存在
或被本进程重新核验。因此每份结果都带 `scope: "supplied_observations"`、`files_rechecked: false`；
`source_sha256` 只绑定真正读取的观察文档字节，不绑定其所指原工件的新鲜度。

ID 匹配 `[A-Za-z][A-Za-z0-9_-]{0,63}`，路径沿用现有安全相对路径规则；identity 名称沿用纯核的
闭合集合。`run_ids` 最多 256 个唯一 ID，`turn` 为正的 32 位整数；工件/关系未知字段、非法 metadata
均拒绝。`required` 与 `present` 是可选布尔字段。输入上限 1 MiB（含边界），有限 UTF-8 JSON 严格
解码，重复键、非有限数与超过 64 层嵌套拒绝。沿用 descriptor-relative/no-follow reader，拒绝
symlink、非普通文件、受保护路径、读取中变化与能力缺失，不退回普通路径跟随。显式根本身不能是
symlink；既有 reader 不声称能检测根外所有祖先替换，descriptor 观察不是文件系统沙箱。

输出为包含尾换行在内最多 64 KiB 的单 JSON，`ok` 只在 complete 为 true；finding 用输入的
**零基索引**定位，歧义/全局项用 null，仅保留固定 kind/status/reason。路径、ID、identity 值、诊断
detail、正文和凭据不复制到输出。同输入字节的 JSON 输出稳定；超预算明确拒绝，不悄悄截断。

完整 complete 返回 0；partial 返回 1 且 ok=false，包括不完整 coverage、可选证据省略或没有显式
relation；refused/坏输入返回 2 且 ok=false，包括空工件集、摘要/身份矛盾、不安全读取或超限。
人类输出与 JSON 共用结果/退出码。可处理错误有 `error: "evidence"` 与固定原因，JSON 的 stderr
不含正文/traceback。命令不生成 session、trace、request、验收 receipt 或输出文件；真实工件采集与
新鲜度核验另需独立批准，不把本入口宣称为那个能力。
