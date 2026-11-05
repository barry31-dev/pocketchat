# 已选上下文漂移核对

`check_context_drift()` 用一个 `pocketchat-task-context-snapshot.v1` 快照中已选文件的证据，
核对调用方提供的当前观察。它是纯核：不扫描工作区、不刷新快照、不读取已选文件正文、不调用
provider，也不执行命令。

```python
from pocketchat.context_drift import check_context_drift

report = check_context_drift(
    frozen_snapshot,
    {
        "coverage": "complete",
        "files": [
            {
                "path": "README.md",
                "entry_type": "regular_file",
                "bytes": 17,
                "sha256": "<64 个小写十六进制字符>",
            }
        ],
    },
)
```

观察列表可以不含未选路径。路径不在冻结选择里的行会被忽略并计入
`ignored_observations`，不会把未变化的已选内容误判为拒绝。

## 状态

| 状态 | 含义 |
|---|---|
| `allow` | 观察完整，所有已选路径仍是普通文件，字节数与 SHA-256 均相同 |
| `refused` | 已知有变化、完整观察缺已选路径、输入格式错误或明确超限 |
| `unknown` | 没有已知矛盾，但覆盖不完整或某个已选路径未被观察 |

已知变化优先于部分覆盖，始终返回 `refused`。部分观察即使碰巧包含全部已选文件，也不能升级为
`allow`，报告仍会记录 `coverage_partial`。对应的 `next_action` 分别是 `continue`、`replan`
或 `reobserve`。

变化原因为闭合集合：

- `content_changed`：字节数或 SHA-256 不同；
- `path_missing`：已选路径缺失；
- `entry_type_changed`：路径变成符号链接、目录或其他非普通文件。

## 冻结快照校验

核对前会验证快照的 schema、tree schema、查询项限额、文件行、UTF-8 文本与字节数一致性、
SHA-256、计数器以及 canonical `snapshot_sha256`。伪造或不一致的快照会抛出
`ContextDriftError`，不会被当成空快照或“未变化”。

快照文件行的列表顺序不作额外要求，但重复路径会被拒绝。

## 限额与摘要

默认最多 256 条观察，报告正文最多 64 KiB。超过任一限制都会返回 `refused`，不会静默截断后
给出 `allow`。报告正文是不含 `report_sha256` 的 canonical UTF-8 JSON；`report_sha256`
覆盖包含实际限额在内的正文。

## 边界

这份报告只证明调用方提供的观察与快照相同或不同。它不证明观察采集过程没有竞态、不证明未选
路径未变化、不证明调用方不能伪造观察，也不证明后续计划安全。真实采集接线、快照刷新、自动
重新规划以及 provider/命令集成都必须另开已批准切片。
