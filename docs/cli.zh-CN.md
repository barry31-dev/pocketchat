# 命令手册

各命令参数与行为的参照：对比与 manifest、请求快照、Replay、会话与分支、工作区
关系图、查询命令、派生索引、show 与 export、trace。文件格式与兼容规则见
[formats.zh-CN.md](formats.zh-CN.md)；凭据与证据的安全规则见
[security.zh-CN.md](security.zh-CN.md)。README 保留概览与快速上手。

## 对比

```bash
pocketchat compare "what is 2+2?" --profiles hosted,local
```

下面的输出来自测试使用的本地 stub。它证明命令与产物的形状，不证明任何 provider：

```
hosted       completed     0.0s     11      5  0.000021  stub reply for hosted-model
local        completed     0.0s     11      5  unknown  stub reply for local-model
manifest /private/tmp/pcrun/comparisons/cmp_20261018t232254_53de0c2d.json
```

2026-10-20 已用同一条命令对本机免费聚合路由完成一次真实对比：
当次配置的两个 profile 都 `completed`（`cmp_20261020t021019_4ce71581`）。
两份 trace 均通过 `trace-doctor validate`，请求快照 hash 复算一致，凭据在产物与
stdout 中零命中。这只验证这两条当次路由输入，不外推到其他模型或 provider。

```bash
export POCKETCHAT_HOSTED_KEY=...
export POCKETCHAT_API_KEY=...
pocketchat compare "what is 2+2?" --profiles hosted,local
```

一个 prompt、每个 profile 一次运行，顺序按参数里写的来。每次运行都是一次普通运行：
自己的 run id、trace 与请求快照，以及唯一一个终止事件。它们共享的只有起始上下文，
所以回复之间的差异是 profile 之间的差异。

成本列由该 profile 自己配置的价格算出，单位就是配置文件里那个「每百万 token」。
没配价格的 profile 显示 `unknown` —— 没人设过的价格不是零价：在那里印一个
`0.000000`，等于报告一个从没测过的花费。耗时是该运行所有模型请求的墙上时间之和，
从这次运行自己记录的事件里读回来，所以表里这个数字是 trace 撑得起来的。

token 两列只放 provider 报告过的计数；程序不会估算它们。provider 没有返回
`usage` 时，这两列保持为零。这个零表示「没有报告 usage」，不是「一定没有消耗
token」的证明。成本只用现有的计数计算：`unknown` 表示缺少价格；数字则只是对表里
这些计数做算术，不是 provider 的账单。

`comparisons/<comparison-id>.json` 是这些运行的索引。它记下每一次运行的 trace 与请求
快照位置，以及状态、耗时、usage、摘要与错误；里面的路径都相对工作区，所以这份 manifest
无论在哪里被读都能解析。它不把 trace 内容抄进来 —— 一个请求只有一个家。文件只创建一次、
绝不重写，与旁边的快照同一规矩。

里面的 `base_context` 记下这次对比从什么开始：`--session` 指定了哪份会话文件，以及那份
上下文的消息条数字符数。会话路径与文件里其他路径一样相对工作区，而那份文件只被读 ——
对比绝不写进它测量的对话。没有 `--session` 时该字段为 `null`：没有基准会话和有一个没
署名的基准会话，是两件不同的事。

`--json` 改为打印 comparison id、manifest 路径与每个 run 一条，而不是那张表。`--quiet`
去掉表格、只留 manifest 路径 —— 没人在看的对比，证据也得找得到。`--session FILE` 用一份
已有会话给上下文做种子：只读，不写；对比自己不建会话文件。

退出码描述的是命令，不是每个 profile。某个 profile 失败是 manifest 记下的一条结果，
命令仍然 exit 0。exit 2 留给「这次对比根本没法做」：未知 profile 名、重复的 profile 名、
凭据没设置的 profile，或 `--session` 指定的文件不存在。这些都在第一个请求发出之前查完，
所以被拒绝的对比不会留下 trace。

`compare` 与 `run`、`chat` 共用同一组参数，所以它仍然能解析 `--profile` 与
`--trace` —— 但这两个在这里都不适用，它的 help 也不再列出它们。profile 由
`--profiles` 决定，每次运行的 trace 由它自己写。传其中任何一个都会以 exit 2
拒绝，而不是被忽略：一个解析器接受却没人读的参数，会让操作者以为某个选择生效了，
而对比实际跑的是 `--profiles` 点名的那几个。保留解析而不改成「未识别参数」，
是为了让拒绝信息说清冲突是什么，而不是给一句没说哪个命令拒绝的通用用法错误。

`select` 记录「最后留下了哪个回答」。它把记录写在 manifest 旁边——manifest 本身
从不改写——并且拒绝对同一次对比的第二次选择，因为第一个决定也是证据：

```bash
pocketchat select <comparison-id> <run-id> --note "the fast one"
pocketchat select <comparison-id> local --into kept
```

run 可以用 id 点名；profile 名在没有歧义时也可以。`--into` 把选中的 prompt 与
回复追加进一个会话（不存在就新建），之后照常继续对话。这个会话是一次自己的
run——有自己的 run id 与 trace——把选中的交换与那次对比连起来的，是 selection
文件本身。回复只有在 run 的 trace 记录了全文时才会写入：没加 `--verbose` 的对比
只留下摘要，摘要写进会话会被读成完整回答，所以会在写任何字节之前被拒绝。
`workspace` 在有 selection 时如实报告，没有时仍然说 `not recorded`。

## 请求快照

每个请求在打开 socket 之前就写进 `traces/requests/<run-id>.turn-N.json`，
与它所属的 trace 放在一起。trace 说的是「发出过一个请求」，快照说的是
「那个请求里有什么」：

```bash
cat traces/requests/<run-id>.turn-1.json
```

```json
{
  "schema_version": "1",
  "run_id": "run_20261018t000000_abcdef12",
  "profile": "local",
  "created": "2026-10-18T00:00:00.000Z",
  "request": {"model": "example-model", "messages": [...], "stream": false},
  "request_sha256": "64 位小写十六进制"
}
```

快照就是发给 provider 的那个 payload，由请求本身使用的同一个构造函数产出，所以它
不可能描述一个从未发出的 body。它不带 header，也不带凭据。`request_sha256` 是这些
字节的摘要，`model.request.started` 同时带上路径与摘要，所以读的人可以从文件重算
哈希，而不是选择相信 trace。记录下来的路径相对于 trace，所以无论 `--trace` 把文件
放在哪里，它都指向写下来的那一份。

一次 run 每回合一份快照，因为每回合发出去的上下文不同。快照只创建一次、从不重写：
被续接的会话沿用它的 trace 已经在用的编号，而不是覆盖掉那份记录了真实请求的文件。

若快照的 body 里出现当前配置的凭据，就拒绝写入。body 里装的是操作者自己打的字，
引用的 key（粘贴、`dotenv` 一行、工单内容）本可以借道快照落成唯一会被长期保留
并回放的凭据副本。这种情况下请求照发、run 照常完成，但不写快照，
`model.request.started` 也不带 `request_snapshot` 引用，改为记一条
`credential_in_request` 的 `error.recorded` 事件说明原因。别人的 key 不归本包管，
只是提到某个 key 的提示词不受影响。

provider 拒绝凭据时，常常把它拒绝的内容回抄在错误 body 里。无论回抄成什么形状——
冒号后面、无关字段里、带了后缀、或者重复两遍——这个值在进入 trace、comparison
manifest 或 stdout 之前都会被替换掉，而 provider 自己的措辞保留下来，错误依然说得清
出了什么事。

## Replay

```bash
pocketchat replay <run-id> --dry-run
pocketchat replay traces/requests/<run-id>.turn-1.json --dry-run --json
```

dry-run replay 读取已保存的请求快照，并用写入时的同一个 canonical serializer 重算
SHA-256。传 run id 会核对默认 `traces/requests` 布局里它记录的每一回合；若
`--trace` 改变了快照文件名，则改为传明确的快照路径。这个命令不需要配置或凭据，
也不会打开 socket。

哈希不一致时 exit 2，同时打印期望值与实际值。快照缺失或格式损坏同样属于用法错误，
因为没有一份可信的请求可以 replay。

## 会话

`--session notes` 把一段对话存在 `sessions/notes.jsonl`，旁边是它在 `traces/` 下的
trace。会话文件保存每次往来的完整文本；trace 保存发生了什么。凭据两者都不在。

```bash
pocketchat run "what did we decide last time?" --session notes
pocketchat run "and about the retry policy?" --session notes
```

第二条命令接续第一条：同一个 run id、同一个 trace 文件，此前的往来仍在上下文里。
一个会话是一次 run，不是每次调用一次 run，所以 trace 始终是一个校验器读得懂的 run。
续接时如果显式传了 `--trace`，它必须指向会话记录的那个 trace；不同路径会在 run
开始前被拒绝。

会话文件带有自己的身份。header 记录它所属的 conversation 与 branch，每条消息行带一个
由它在文件中的位置推导出的 `turn_id`。因此一段对话在追加它的多次 run 之间保持同一个
身份，读同一份字节的两个读者指的是同一次往来。这两个字段出现之前写下的文件照原样读取、
显示 `not recorded`，不会为了补上没有的键而被改写。

`branch` 从某一次已完成的往来划出第二条路径。它会写出一个新的会话文件：沿用父文件的
conversation id，复制到指定 turn 为止的往来，并记下 `parent_branch_id` 与
`parent_turn_id`，让子文件能说出自己的出处。父文件不被改动：

```bash
pocketchat branch notes --from turn_000002 --name fork
```

从未被回复过的那个 turn 会被拒绝：分支延续的是模型已经答过的问题，不是父文件问过却从未
发出的问题。只有 v0.3 会话文件可以分支；没有 conversation id 的文件会被拒绝而不是补一个，
因为编造出来的链接读不回去。

`workspace` 把整个目录读成一张关系图：哪些文件是同一段对话的两条路径、每个分支从哪个 turn
岔出、每个文件记录的是哪一次 run、哪一次对比测量了哪些 run。它不写任何文件、不读任何凭据，
所以这张图画的是事后的事实，不会因为画它的进程而改变：

```bash
pocketchat workspace
pocketchat workspace --json
```

所有路径都相对于工作区，JSON 里既没有绝对路径也没有时钟，因此把同一批文件交给两个进程，
得到的是同一张图。末尾四个列表报告的是文件**没有**说的话，而不是替它补上：没有任何文件
认领的 trace 与请求快照、指向不存在之物的链接、争用同一次 run 或同一个 trace 的两个文件，
以及根本读不出来的文件。对比 manifest 没有「选了哪一轨」这个字段，所以这里印的是
`not recorded`，而不是替读者挑一个赢家。

`history`、`search` 和 `diff` 对同一批文件提问。三个命令都是只读的——不读配置、不读凭据、
不联网、不写一个字节——打印的每个路径都相对于工作区：

```bash
pocketchat history                    # 每段对话、每个分支、每一轮
pocketchat history notes              # 只看一个会话，按名字或按 id
pocketchat search "retry policy"      # 字面匹配，区分大小写
pocketchat search "retry policy" --session notes --json
pocketchat diff notes fork            # 先公共前缀，再各自新增的轮次
```

`history` 把各轮按对话与分支分组：一段分了岔的对话读起来就是同一段对话的两条路径，
每一轮用它所在位置推出的 id 命名；target 接受会话名、conversation id 或 branch id。
v0.1/v0.2 的旧文件照常列出它的各轮，不发明身份。`search` 按原文字面匹配——大小写和空白
都算数——每个命中都写明出自哪个文件、哪段对话、哪个分支、哪一轮；零命中是一个结果
（exit 0），空文本则被拒绝。`diff` 逐轮比较两份会话的内容，先报公共前缀，再报两侧各自
新增的轮次；当一侧记录了另一侧是自己的父分支时，它声称的岔出点会与两份文件实际共有的
前缀核对，对不上就照实印出来，而不是悄悄选一个。新写的会话文件还会携带
`schema_version`（描述文件格式本身，区别于写入方版本），读到不认识的版本就
拒绝并报出版本号。本包写出的每一种文件——会话、trace、请求快照、对比
manifest、选择记录与派生索引——各自的版本键与兼容规则见
[`formats.zh-CN.md`](formats.zh-CN.md)。

`index` 构建 `index.sqlite`——一份对同一批证据的派生索引：图里报告的每个对话分支、
每次对比、每条对比 run、每个问题各占一行，旁边是每个源文件的 sha256。索引本身
不含任何独立事实，也没有任何命令读它——删掉它，所有命令的回答不变。构建永远是
全量的（没有增量更新），替换已有索引必须显式 `--rebuild`：

```bash
pocketchat index --rebuild
pocketchat index          # fresh，或 stale 并列出变化的文件
```

不带 `--rebuild` 的 `index` 把索引与工作区核对：`fresh`，或 `stale`（exit 1），
逐个点名两边不一致的文件。构建中途失败会把写了一半的文件删掉，而不是留下一个
看起来像答案的索引。

`show` 先打印对话，再打印记录了它的事件，并且接受这对文件中的任意一个作为参数：

```bash
pocketchat show notes
pocketchat show traces/<run-id>.jsonl
```

`export` 写出会话归档或独立的 event-only trace。源文件不变时，两次导出的字节相同：

```bash
pocketchat export notes                     # sessions/notes.export.jsonl
pocketchat export notes --format md        # sessions/notes.export.md
pocketchat export notes --format trace     # sessions/notes.export.trace.jsonl
trace-doctor validate sessions/notes.export.trace.jsonl
```

`jsonl` 是混合归档：依次写入会话头、消息、trace 事件，每条都有 `kind` 字段。
`md` 是对话文本。两者都不是给 Trace Doctor 的 event-only trace；从混合归档删掉
`kind`，不会把会话头和消息变成事件。

`--format trace` 原样复制 trace 字节，不含会话头、消息或新增的 `kind` 字段。
事件 ID、时间戳、顺序和 payload 均不重造。它接受会话名或 trace 路径，也接受未使用
`--session` 的运行生成的 trace。默认后缀为 `.export.trace.jsonl`；`--out` 可指定
另一文件。三种格式都拒绝覆盖已有输出。

trace 导出先读完每一行再创建输出。trace 缺失或为空、JSON 或 UTF-8 损坏、混入非事件
记录时，返回退出码 2 且不创建导出文件。它不修复事件、不补造终止事件，也不替代 Trace
Doctor 的 schema 与跨事件校验。原 `jsonl` 归档保留既有行为，包括跳过 trace 中损坏的
JSON 行。终止事件完整性用 `trace-doctor fix-suggest FILE` 的 `incomplete_run` 信号核对；
`trace-doctor validate` 本身不检查这一项。

Ctrl-C 以 `run.cancelled` 结束对话 —— 无论它落在请求在途时，还是落在等待输入时。
两种情况 trace 都会拿到一个终止事件，因为中断永远不会被读循环吞掉。

`chat` 也能从管道读，所以脚本化的对话同样成立：

```bash
printf 'first\nsecond\n/exit\n' | pocketchat chat --quiet
```

## trace

事件是 Agent Trace Doctor `trace.schema.json` v1 的形状。这份 schema 随本包一起
发布，它是已发布的那一份加上 `model.request.started` 的两个 payload 字段说明
——`request_snapshot` 与 `request_sha256` —— 所以它与已发布文件并非逐字节一致。
没有变的是事件集合、必填字段与事件类型，所以现有的校验器不需要任何新工具就能读它：

```bash
trace-doctor validate traces/<run-id>.jsonl
trace-doctor summary traces/<run-id>.jsonl
```

一次 run、一个 run id、一个终止事件。`run.started` 在第一回合之前写入；run 结束时，
`run.finished`、`run.failed`、`run.cancelled` 三者恰好写入一个，写自每个入口都会经过的
那一处。有开头没有结尾的 trace 是唯一一种会被读成「不知道发生了什么」的形状，
所以这个程序不生产那种形状。

事件 id 按会话分配一次，永不重新生成，而每个事件带自己的时间戳。一个 append 到既有
trace 上的 run 会续上该 trace 的编号，而不是从头开始，所以文件仍然是一个 run，
没有 id 被用两次。因此同一会话的两次导出可以逐字节比较，这正是「两次 run 之间的 diff
有意义」所依赖的性质。

响应正文默认记成摘要（字符数加一小段前缀）。`--verbose` 记全文。选哪个由你；
trace 会说明用了哪一个。
