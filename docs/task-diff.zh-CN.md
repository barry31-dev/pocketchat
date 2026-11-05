# 任务差异证据

`pocketchat.task_diff.build_task_diff(baseline, final)` 是对两份已经生成的
`pocketchat-task-context-snapshot.v1` 记录做纯比较。它不打开文件、不运行 Git、
不刷新快照、不生成 patch，也不声称未选中的路径没有变化。

## 输入契约

两个参数都必须符合现有 context snapshot schema。函数会重新校验封闭记录形状、
tree / snapshot schema、SHA-256 字段、canonical `snapshot_sha256`、文件行、总字节、
大小变化计数和工作区相对路径，并执行 producer 的真实边界：最多 64 个选中文件、
32 个有界查询词、16 MiB snapshot 预算、当前单文件正文最多 1 MiB，且文本不得含 NUL。
格式错误或 producer 不可能产生的记录用 `TaskDiffError` 拒绝，绝不当成空快照。

`max_files` 与 `max_bytes` 必须显式给出正整数；布尔、字符串、浮点和非正值都以
`error_type="invalid_limits"` 拒绝。精确边界本身允许通过。

## 结果

报告使用 `pocketchat-task-diff.v1`，包含：

- `status`：`complete` 或 `partial`；
- `whole_workspace_compared`：始终为 `false`；
- 按路径稳定排序的 `entries`；
- 明确的 `omissions`、`omitted_files` 和 `omitted_paths`；
- 报告封套的 `report_bytes` 与 `report_sha256`。

只在 `final` 出现的路径是 `added`，只在 `baseline` 出现的是 `deleted`。两边都有但
digest 或字节数不同的是 `modified`；完全相同的是 `unchanged`，不进入 `entries`。
只有两边都有文本时才输出行数变化；源文本绝不复制进报告。

`text_compared` 只在 modified 行确实执行了有界行比较时为 true。为了避免重复文本触发
二次方复杂度，两边正文合计超过 256 KiB 或 2,048 行时省略行级明细，此时
`text_compared` 为 false、两个行数为 null，但 digest / 字节变化证据仍保留。
added / deleted 行也固定为 false，因为不存在双边文本比较。snapshot schema 没有 rename
标记，所以本函数不猜重命名，只分别报告 delete 与 add。

`compared_files` 是限额前 entries 代表的变化路径数，不是 baseline 与 final 文件行数之和。

## 覆盖与预算

选择被截断、两边选择输入不同、冻结 tree 不同、选择后文件大小变化或空快照，都会返回
`partial` 并带具名 omission。只有两份传入快照都被完整比较时才可能返回 `complete`；
这仍然只说明这两份记录被比较，不说明整个工作区都被检查。

`report_bytes` 是不含 `report_bytes` 与 `report_sha256` 的报告内容 canonical UTF-8 字节数；
完整序列化封套仍然必须放进 `max_bytes`。文件限额省略变化路径时，只要预算允许，
`omitted_paths` 会点名它们；如果字节压力要求删掉这项明细，状态仍为 `partial`，并保留
`max_bytes` omission。连最小公开封套都放不下的预算以 `byte_limit_exceeded` 拒绝；
只要报告能返回，就绝不超出字节预算。

错误含稳定 `error_type` 与 `category`。结构错误使用 `invalid_limits`、
`invalid_snapshot`、`duplicate_path`、`unsafe_path`、`snapshot_contradiction`；
最小封套放不下时使用 `byte_limit_exceeded`。本函数不修改任一输入。

## 不做

这不是实时 diff、快照生成器、Git 观察器、patch 引擎、合并解决器、语义审查器或 CLI。
它不证明两份记录以外某个被选中文件没有变化，也不授权任务执行、验收、发布或版本发布。
