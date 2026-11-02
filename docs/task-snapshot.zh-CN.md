# 显式选中文件的内容快照

`pocketchat task context snapshot` 消费冻结的
`pocketchat-task-context-tree.v1` 清单与之前生成的
`pocketchat-task-context-selection.v1` 结果。它是路径选择之后的内容阶段，
不负责语义相关性判断。

```bash
pocketchat task context scan . --include src --include tests --json > tree.json
pocketchat task context select tree.json --query task --query context --json > selection.json
pocketchat task context snapshot tree.json selection.json --json
```

## 来源绑定

命令先通过选择器验证 tree，再用 selection 记录的查询词和 `max_files`
重新生成选择结果。selection 解析后必须与新结果逐字段一致；被改动的
路径、分数、字节数、digest、计数、截断标记或额外字段一律拒绝。输出绑定
tree 与 selection 的 canonical SHA-256。

两个 JSON 输入都必须是常规、非符号链接文件，且不超过 8 MiB。重复 JSON
成员和 `ok:false` 的 CLI envelope 会被拒绝。

## 只读显式选中文件

快照只打开已校验 selection 的最终 `matches`。它不重新扫描工作区，也不读取
omitted、未命中或被截断的候选。每个选中路径必须保持工作区相对、只经过常规
目录，并以常规非符号链接文件结束。敏感路径分量、二进制后缀、非法 UTF-8 和
NUL 字节会被拒绝。

每个文件记录 `selection_bytes`、当前 `bytes`、SHA-256 与有界 `text`。
`selection_size_changed` 及其计数只表示当前大小与冻结 selection 元数据的差异；
同字节数正文变化仍会改变单文件 hash 与整体 snapshot digest。零命中选择是
显式成功的空快照。

## 预算与摘要

默认总预算与单文件上限各 1 MiB；调用方可把总预算提高到 16 MiB。总预算是
包含边界，因此已用满预算后仍可快照一个零字节选中文件。超限、预算不足、
格式错误或来源不可信时 exit 2，且不创建 task、session、trace、request、
`.pocketchat` 或 provider 证据。

`snapshot_sha256` 是去除该字段后结果的 canonical JSON 摘要。格式与对象键序
不影响它。它只证明本次调用读取了哪些有界字节，不代表语义相关、支持 ignore、
跨进程原子文件系统快照、TaskSpec 扩权、任务验收、checkpoint/resume、OS 沙箱
或发布授权。
