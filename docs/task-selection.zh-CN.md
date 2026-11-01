# 确定性任务上下文选择

`pocketchat task context select` 只对冻结的 `pocketchat-task-context-tree.v1` 清单做路径排序；
不打开清单中的任何文件，也不调用模型。

```bash
pocketchat task context scan . --include src --include tests --json > tree.json
pocketchat task context select tree.json --query task --query context --max-files 8 --json
```

## 冻结输入

选择器接受扫描器产出的封闭 tree schema，或 `ok: true` 的扫描 CLI JSON 外层，并拒绝重复 JSON 成员，再核对 schema、workspace 标记、include 覆盖、扫描器排除策略、
limits、usage、排序且唯一的文件/省略行、相对路径、字节数与两类路径不重叠。manifest 必须是常规、非符号链接、
不超过 8 MiB 的文件。每个选择结果都带 manifest 规范化 JSON 的 SHA-256；格式和对象键顺序不改变 digest。

omission 行永不进入候选；任何落在声明 include 之外的路径直接拒绝，不静默参与选择。

## 评分

query 先 case-fold，再拆成 Unicode 词 token；下划线是分隔符。token 去重并排序。空值、NUL、非字符串、单项超过 128 字符、
超过 32 个输入项或规范化 token 都拒绝。

每个文件的规则只有三条：

- basename 中精确 token 得 4 分；
- 否则，相对路径其他位置的同一精确 token 得 2 分；
- 子串、前缀、文件正文与 omission 行得 0 分。

多个 query token 的分数相加。正分候选按分数降序、路径升序排列。`max_files` 只能是 1–64。
结果同时记录总命中、是否因输出上限截断、每个选择行的 matched terms，以及没有命中任何候选的 query terms。

## 边界

JSON 与人读输出来自同一结果。manifest 或请求非法时 exit 2，不创建 task/session/trace/provider 证据；
合法但零命中是成功且明确的空选择。

路径 token 命中不等于语义相关，也不证明正文已读、安全或有用。本命令不解析 ignore、不扩张 TaskSpec 权限、
不做内容快照、不保证跨进程并发替换下的 manifest 原子性、不提供操作系统沙箱，也不授权发布。
