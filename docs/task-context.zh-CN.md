# 有界任务上下文树

`pocketchat task context scan` 是只读、只看元数据的仓库树清单。它只是上下文阶段的第一块：
后续可以基于结果做相关文件选择或内容快照，但本命令两者都不做。

```bash
pocketchat task context scan . --include src --include tests --json
pocketchat task context scan . --max-files 256 --max-depth 8
```

结果里的工作区固定写成 `.`，每个路径都是 POSIX 风格的工作区相对路径。include 只能是明确的
文件或目录；`.` 表示工作区根。重复或重叠 include 只产生一条文件记录。常规文件只记录
`path` 与 `bytes`，不读取正文、时间戳或内容 hash。

## 边界与预算

workspace 参数只能是 `.` 或严格的当前目录相对路径；绝对路径、反斜杠、空段、`.` 段、`..`、
不存在路径及任一符号链接组件都拒绝。include 使用相同的段规则，也不能穿过符号链接。遍历中发现的符号链接只登记
`omitted: symlink`，绝不跟随；其他非常规文件类型也登记为省略。

以下目录名按精确名称剪枝，并登记 `excluded_directory`：`.git`、`.hg`、`.svn`、
`.pocketchat`、`.venv`、`venv`、`__pycache__`、`node_modules`、`dist`、`build`。

默认上限为 512 个文件、深度 12、已访问路径字符合计 65,536。调用方可在实现上限内调整。
任一预算超过上限就拒绝整次请求，不返回不完整清单。文件与省略项都按相对路径排序；相同元数据和
参数得到相同顺序。

## 输出与限制

JSON schema 是 `pocketchat-task-context-tree.v1`，包含规范化 include、limits、usage、排除目录名、
文件行与省略行；人读输出来自同一结果。拒绝 exit 2。成功或拒绝都不创建 task/session/trace/request，
也不调用 provider。

它不是内容快照、ignore 文件解析器、相关文件选择器、TaskSpec 权限扩张、操作系统沙箱，也不保证
扫描期间文件系统没有并发变化。文件名本身也可能敏感；应收窄 include，并把私人清单继续当私人证据。
