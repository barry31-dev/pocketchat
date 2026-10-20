# pocketchat

`pocketchat` 是一个很小的单用户命令行对话 agent。它**不是**通用 agent：没有工具、没有
文件访问、没有代码执行，也没有任何多 agent 相关的东西。它只做一件事 —— 与你配置的模型
保持一段多轮对话，并写下这段对话的 trace，供别的工具事后核查。

trace 才是重点。其余一切都只是为了让「模型到底看到了什么、到底答了什么」这个问题有一个
文件可以打开，而不是靠记忆去信。

## 它不是什么

- 没有工具调用，也没有 function calling。首版只有对话。
- 没有 GUI、没有 Web UI、没有云服务、没有账号、没有同步。
- 没有检索、没有记忆库，除了当前会话之外没有长期记忆。
- 没有自动重试。一次请求失败就结束这一回合。
- 不带任何 provider SDK。它用 `urllib` 讲 OpenAI 兼容的 HTTP API，指向你配置的
  `base_url`。

## 版本边界

`v0.1.0` 是单 profile 的基础版：可安装的 CLI、一个配置好的 provider、
`run`、`chat`、`show`、`export`、带终止事件的 trace，以及会话续接。2026-10-20
已用本机免费聚合路由完成一次真实 `run`（`run_20261020t015755_8e636ff8`，
trace SHA-256 `9102fab4a549ff398265d0094c38bf74cd81a33241db7bf77fbcd64621453e45`）。
这只验证该次调用的请求、trace 与凭据零命中，不外推到其他 provider。

`v0.2.0` 增加多 profile 配置与选择、串行对比、请求快照、对比 manifest、成本与
延迟，以及 replay dry-run。它不增加工具调用、自动重试、分支树、流式输出、
Web UI 或第二个 provider SDK。

## 环境要求

Python 3.9 或更高。运行时没有第三方依赖。

## 安装

```bash
python3 -m pip install .
pocketchat --help
```

## 配置

`pocketchat init` 会写入一份模板：

```bash
pocketchat init
```

```json
{
  "schema_version": "2",
  "name": "local-provider",
  "default_profile": "local",
  "system_prompt": "You are a concise assistant.",
  "profiles": {
    "local": {
      "base_url": "http://127.0.0.1:8123/v1",
      "model": "example-model",
      "api_key_env": "POCKETCHAT_API_KEY",
      "timeout_seconds": 60,
      "max_context_chars": 24000,
      "summary_prefix_chars": 160,
      "input_price_per_million": null,
      "output_price_per_million": null
    },
    "hosted": {
      "base_url": "https://example.invalid/v1",
      "model": "example-model",
      "api_key_env": "POCKETCHAT_HOSTED_KEY",
      "timeout_seconds": 60,
      "max_context_chars": 24000,
      "summary_prefix_chars": 160,
      "input_price_per_million": null,
      "output_price_per_million": null
    }
  }
}
```

配置文件只写持有凭据的那个环境变量的名字，从不写凭据本身。**凭据只从环境变量读取，
并且从不写入 trace、会话文件或 stdout。**

`base_url` 必须指向一个 OpenAI 兼容的端点。环境里没有凭据时，`run` 会拒绝启动，
而不是去找一个默认值。

`schema_version` 为 `"1"` 的配置仍然可以加载，行为不变：它只写一个 `provider`，
加载时会变成唯一一个 profile，名字取配置的 `name`（没有则取文件名）。
这个迁移只发生在内存里，磁盘上的 v1 文件不会被改写。`run` 与 `chat` 使用
`default_profile`，这等价于用 `--profile` 显式指定它。

`pocketchat profile list` 显示一个配置提供了什么：每个 profile 一行，带模型名、
base URL、持有凭据的那个环境变量名，以及这个变量在当前 shell 里是否已设置。凭据
的值永远不会被打印。`--json` 用同一个 JSON 对象打印同样的事实。

```bash
pocketchat profile list
pocketchat profile list --json
```

`run` 与 `chat` 都接受 `--profile NAME`。名字不存在时，在创建任何 trace 文件之前
就会拒绝，错误信息里列出确实存在的 profile。某个 profile 的凭据没有设置时同样
失败 —— 不会退回去用一个有凭据的 profile。运行自己的 trace 会记录它用的是哪个
profile，因为两个 profile 可以指向同一个模型。

## 运行

```bash
export POCKETCHAT_API_KEY=...
pocketchat run "what did we decide last time?"
```

每次运行写一份 JSONL trace。`pocketchat show` 打印它：

```bash
pocketchat show traces/<run-id>.jsonl
```

## 对话

```bash
pocketchat chat
```

读一行、发一行、打印回复，如此重复，直到 EOF、Ctrl-C 或 `/exit`、`/quit`、`/bye`
之一。空行不算 prompt。`--session FILE` 命名或续接会话，与 `run` 用的是同一个参数。

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

## 状态

早期开发。对话内核、REPL 与会话持久化已实现。PocketChat 现在按多版本产品推进：
面向 AI 构建者与技术团队的本地优先、trace-native 多模型对话工作台。
CLI 是第一个产品界面，桌面端属于后续里程碑。

CLI 现在可以在一份配置里放多个 profile，并用 `--profile` 跑你选的那一个；
不选时用配置里的 `default_profile`。每个请求都会在 trace 旁边存成一份快照，
所以发出去的内容可以读回来并对账。`compare` 还可以把一个 prompt 同时发给多个
profile，并为这一批运行写一份 manifest。replay dry-run 可以核对一份已保存的请求，
不需要连接 provider、不需要读取凭据，也不会打开 socket。

这里列出的每个测试都用本地 stub HTTP server，所以测试证明的是请求的形状、trace
的形状，以及每一条失败路径，不是任何 provider 的行为。2026-10-20 另有一次本机
免费聚合路由的真实调用与对比；其证据边界见「版本边界」和「对比」两节，不随测试套件
自动复现。

## License

Apache-2.0。见 `LICENSE`。

## 仓库

源代码维护在 `barry31-dev/pocketchat`。

## 发布状态

`v0.2.0` 的源码版本已公开。它尚未发布到包索引；请按上面的安装命令直接从
仓库源码安装。

本文的中文版与英文版逐命令一致，英文版见 [`README.md`](README.md)。
