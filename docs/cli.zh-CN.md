# 命令手册

各命令参数与行为的参照：对比与 manifest、请求快照、Replay、会话与分支、工作区
关系图、查询命令、派生索引、show 与 export、trace。文件格式与兼容规则见
[formats.zh-CN.md](formats.zh-CN.md)；凭据与证据的安全规则见
[security.zh-CN.md](security.zh-CN.md)。README 保留概览与快速上手。

## Automation JSON

`init`、`run`、`chat`、`branch`、`checkpoint`、`rewind`、`show`、`context`、`export`
都接受 `--json`。成功时
stdout 只有一个 JSON 对象：执行命令报告 `ok`、`schema_version`、`status`、
`run_id`、`turns`、`reply` 与工作区相对 `trace`；`branch` 报告关系 id、相对
`source` / `session` / `trace` 与 `messages_written`，绝不输出消息正文。准备、
执行或派生被拒绝时，stdout 只输出一个 `{"ok":false,...}` 对象，stderr 为空。
准备/拒绝 exit 2；已经开始但失败的 provider run 保持既有 exit 1。不带 `--json`
时，原有人类输出不变。

## Agent

`pocketchat agent` 是支持会话级切换的交互式聊天入口。每个普通输入是一个 run；
`--session` 指定的会话文件负责把上下文带到下一个 run。每个 run 恰好有一个终止
trace 事件。

```bash
pocketchat agent --session notes
```

斜杠命令：

| 命令 | 作用 |
|---|---|
| `/model NAME` | 为下一条消息选择已配置的 profile |
| `/session NAME` | 为下一条消息选择会话文件 |
| `/reasoning LEVEL` | 设置 `minimal`、`low`、`medium` 或 `high` |
| `/context N` | 设置下一条消息的字符预算 |
| `/status` | 显示当前 profile、model、session、思考强度和预算 |
| `/files` | 显示当前精确授权与 pending change id |
| `/files read|write PATH...` | 展示 provider 告知与精确路径；只有显示的 `/confirm` 后才激活 |
| `/files revoke PATH...` | 移除精确授权并使已展示确认失效 |
| `/diff` / `/apply [CHANGE_ID]` | 展示完整 pending diff，不立即写入，并生成绑定 digest 的确认 |
| `/changes` | 列出同 workspace、conversation 与 branch 所有的持久变更元数据 |
| `/undo CHANGE_ID` | 要求 fresh exact write grant，再展示持久补偿 diff 与确认 |
| `/confirm DIGEST` | 只执行当前精确展示的 grant、apply 或 undo 动作 |
| `/exit` / `/quit` / `/bye` | 退出 agent |

文件控制命令要求 stdin/stdout 都是真实 TTY。pipe 输入可以看到 line shell banner，但所有
文件命令都会在创建 Session、trace 或 file-change store 前拒绝。模型只获得已授权的
`read_file` 与 `propose_file_change`，不能授权、确认、apply、undo 或调用立即 `write_file`。
apply 成功后会消费目标 write grant，因此 undo 需要 fresh disclosed write grant。profile/session/
grant 变化、过期 SessionFile view、在飞 reply 或其他 writer 都拒绝确认。`/files cancel`
撤销授权和未确认 review，不撤销已写入文件；取消后仍未退出的请求保留 worker/writer。
[离线黄金 fixture](file-work-validation.zh-CN.md) 可在已安装开发 wheel 上运行；真实 provider、
人工文件终端验收和正式发布各自记录。
交易与证据契约见 [file-work.zh-CN.md](file-work.zh-CN.md)。

每次 run 都会把思考强度记录到 `run.started`。只有所选 profile 含
`reasoning_effort_supported: true` 时，才会把该字段放进请求 body；否则它只是本地
证据，不能假设端点接受该扩展。

## Doctor

```bash
pocketchat doctor
pocketchat doctor --profile hosted --json
```

`doctor` 是只读的启动检查。它加载并校验与运行相同的配置，解析选中的 profile，
并报告该 profile 的凭据环境变量是否已设置。它不打印凭据值，不打开 socket，不创建
trace 或会话，也不写配置文件。

`doctor --runtime --json` 额外增加一个有界的 `runtime` 字段（schema 版本
`1`）：解释器与 OS 身份、`dir_fd_open` / `nofollow_open_flag` /
`directory_open_flag` 三个显式布尔值，以及固定的
`not_checked` 名称 `provider_reachability`、`workspace_permissions` 与
`concurrency_atomicity`。缺 no-follow 支持只是能力限制，不表示 memory
或工作区操作安全。默认报告、现有三项检查、`ok` 与退出码不变。


报告就绪时 exit 0。配置缺失或无效、profile 不存在或凭据缺失时 exit 2，并给出下一
步动作。`--json` 返回一个对象，包含 `ok`、`schema_version`、选中的 `profile`、
`model`、`api_key_env`、稳定的 `checks` 列表、由检查名到状态派生出的
`check_status` 映射，以及按检查顺序收集失败项 `fix` 的 `next_actions` 数组。
报告就绪时 `next_actions` 为空数组。报告就绪不表示 provider 端点一定可达。

## Profile

```bash
pocketchat profile list --json
pocketchat profile set local \
  --base-url http://127.0.0.1:8123/v1 \
  --model example-model \
  --api-key-env POCKETCHAT_API_KEY
pocketchat profile use local
pocketchat profile remove staging
```

`profile set NAME` 创建或整体替换一个 profile。`--base-url`、`--model` 与
`--api-key-env` 必填；超时、上下文与摘要边界以及两个可选价格由对应选项提供。
省略的可选字段在加载配置时解析为默认值，所以修改已有 profile 的一个字段时也要
把完整 profile 再传一遍。命令只接受持有凭据的环境变量名，从不接受凭据值。

`--max-turn-tokens` 与 `--max-session-tokens` 分别设置单 turn、单 run 的 token
预算。它们是 token 数量，不是货币上限。请求前用本地估算检查，完成后用 provider
实报 usage 复核；超预算的 turn 不进入 session，配置 session 预算但 usage 未知时
fail-closed。

`--tool-read` 向模型提供唯一的自动 `read_file` 工具，`--no-tool-read` 明确关闭它。
每个路径必须用重复的
`--tool-read-allow` 显式列出；`--tool-call-max-count` 限制每 turn 调用数
（默认 2），`--tool-result-max-bytes` 限制单次结果（默认 16384）。工具调用与
`tool read` 共用 workspace 和敏感路径守卫。禁用/未知工具、缺参数和 guard 拒绝
会返回有界失败结果，让模型在调用预算内纠错；I/O 失败、结果超限和预算耗尽仍结束
run。不会提供 shell、执行或网络工具。

`--tool-write` 增加 create-only 的自动 `write_file` 工具。每个路径必须用重复的
`--tool-write-allow` 显式列出；模型只能提供 `path` 与 `content`，没有 overwrite
参数。已有文件、路径逃逸、敏感/二进制路径和超限内容一律拒绝。

`--tool-memory-search` 提供只读自动 `memory_search` 工具。
`--memory-recall` 启用 opt-in v2 recall projection，必须同时给出
`--memory-recall-store` 和 `--memory-recall-workspace-id`；它与旧工具相互独立。
`--tool-memory-search-max-results` 限制返回记录数（默认 20），并继续受工具结果
字节上限约束。模型不能新增、修改或删除记忆；`--no-tool-memory-search` 可关闭。

`profile use NAME` 修改 `default_profile`。`profile remove NAME` 删除非默认
profile，并拒绝删除当前默认项。三条命令都只写 schema `"2"` 配置；schema `"1"`
文件继续只读，不会作为副作用迁移。候选会先整体校验，再原子替换并保留文件权限；
被拒绝的修改让文件逐字节不变。

写入成功 exit 0；配置、schema、名字或候选不可用时 exit 2。参数解析完成后，
`--json` 成功时报告 `ok`、`action`、`profile` 与 `default_profile`；配置与写入
失败时增加稳定的 `error`、`message` 与 `fix` 字段。

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
token」的证明。trace 新增的 `usage_reported` 字段显式区分缺报与真实 0，既有 0 值
计数器不变。成本只用现有的计数计算：`unknown` 表示缺少价格；数字则只是对表里
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

哈希不一致时 exit 2，同时打印期望值与实际值。若相邻 projection sidecar 存在，
replay 会校验其 request 绑定与 recall 证据计数，不读取当前 memory store。
快照缺失或格式损坏同样属于用法错误，因为没有一份可信的请求可以 replay。

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
显示 `not recorded`，不会为了补上没有的键而被改写。会话文件里的 UTF-8 损坏同样是
`session_file` 拒绝：`show --json` 以 2 退出、stdout 只输出一个稳定错误对象，
输入字节保持不变，也不会漏出 traceback。

`branch` 从某一次已完成的往来划出第二条路径。它会写出一个新的会话文件：沿用父文件的
conversation id，复制到指定 turn 为止的往来，并记下 `parent_branch_id` 与
`parent_turn_id`，让子文件能说出自己的出处。父文件不被改动：

```bash
pocketchat branch notes --from turn_000002 --name fork
```

从未被回复过的那个 turn 会被拒绝：分支延续的是模型已经答过的问题，不是父文件问过却从未
发出的问题。只有 v0.3 会话文件可以分支；没有 conversation id 的文件会被拒绝而不是补一个，
因为编造出来的链接读不回去。

## Checkpoint 与 rewind

`checkpoint` 把某个已完成 turn 记成恢复边界，并在
`.pocketchat/recovery/checkpoints/` 写一份小型 manifest：包含 session/trace
引用、turn id，以及精确消息前缀的 SHA-256。trace 证据缺失、turn 尚未回复或
边界已变化时都会拒绝。

```bash
pocketchat checkpoint notes --at turn_000002 --id before-retry --json
pocketchat rewind before-retry --name notes-rewound --json
```

`rewind` 创建只包含 checkpoint 消息的新 session 分支；源 session 与 trace
永不回写。目标创建前先写 prepared 事务，目标通过边界摘要校验后才写 completed；
进程崩溃后重试可以完成事务或替换半成品目标。它不会自动重放既有证据，也不会
发送 provider 请求。

## 有界任务执行

`task` 暴露 v1.1 有界任务闭环，但不成为第二套执行内核：

```bash
pocketchat task run "compare the inputs"   --policy examples/task-policy.json --session task --json
pocketchat task status <task-id> --json
pocketchat task resume <task-id> --checkpoint <checkpoint-id>   --policy examples/task-policy.json --name resumed --json
pocketchat task report <task-id> --policy examples/task-policy.json --json
```

`task run` 必须显式给 policy 与 session。它创建
`.pocketchat/tasks/<task-id>.json`，模型/工具仍走既有 request snapshot、trace、
session 与 tool 事件。不传 `--acceptance` 时完成 exit 0；failed/cancelled exit 1；准备失败或契约拒绝
exit 2。

`task status` 在不执行任何模型工作的前提下区分 active、interrupted、failed 与
终态。`task resume` 在第一个新字节前拒绝 legacy、不完整或冲突的 task checkpoint，
包括不属于命令行所给 task id 的 checkpoint；随后把有序 tool context 恢复进续跑
分支，并报告追加的 recovery step。它不发送 provider 请求。

`task report` 列出已记录的 step 对象、工具调用、checkpoint 绑定、run id、trace
文件与已完成的 `read_file` / `write_file` artifacts。它只按解析后事件自身的
`run_id` 建立关联，不搜索原始 JSON 文本。损坏、不可读或缺失的 trace 会以
`refused` / `unknown` 状态进入 additive `evidence_issues`，不会从报告里静默消失。
没有匹配的 `--policy` 时，context/turn/session token 预算为 `unknown`；record 自带
的 step/tool 预算仍会显示。若 `TaskPlan` 经过 `execute_plan()`，同一份报告还列出
`.pocketchat/task-reports/` 中的 `execution_steps`、经核验的 `commands` 与文件
`changes`；这条路径只读既有证据，不重跑命令、不重新生成 provider 请求。缺失、
损坏、不属于该 task 或被篡改的命令 archive 进入 `evidence_issues`；命令成功仍
绝不等于验收通过。JSON 路径相对工作区；错误时 stdout 只有一个 JSON 对象，
stderr 为空。

确定性的 stub demo 覆盖成功、checkpoint resume 与权限拒绝：

```bash
/usr/bin/python3 examples/task-demo/demo.py
```

### 未发布：有界 TaskPlan 生成

`task plan generate` 把一份 TaskSpec 生成已验证但不可执行的计划：

```bash
pocketchat task plan generate --spec spec.json --run-id plan-review \
  --config pocketchat.json --profile local --json
```

默认写入普通计划 `.pocketchat/task-plans/<run-id>.json`（也可用工作区相对的
`--out` 指定新路径），并在 `traces/requests/` 写独占 plan snapshot。JSON 结果
包含 `plan` 路径、精确 `plan_sha256`、`spec_sha256`、request snapshots 与
attempt 数。生成不执行 step。provider 失败退出 1；非法输入或输出/快照已存在
退出 2。详见 [task-plan.zh-CN.md](task-plan.zh-CN.md)。

### 未发布：已授权 TaskPlan 执行

`task exec` 是产品 CLI 的完整执行入口：它接收操作者已审阅的 TaskSpec 与
TaskPlan，重新解析两份文档，要求调用者给出精确 plan digest，并在读取 provider
配置前拒绝不匹配或扩大权限的计划：

```bash
pocketchat task exec --spec spec.json --plan plan.json \
  --plan-sha256 <digest> --config pocketchat.json --profile local --json
```

各 step 串行复用同一套有界 task runtime；命令 step 只运行 TaskSpec 声明的 argv
模板。输出包含 spec/plan digest、step 状态、task id 与 execution report 路径。
step 失败返回 1 且保留报告。`ok` 只表示尝试的 step 完成，不表示验收通过，
也不把命令 exit 0 写成任务语义通过；命令证据仍是
`task_acceptance: not_evaluated`。

### 未发布：确定性验收

```bash
pocketchat task run "Write the report" --policy policy.json --session audit --acceptance acceptance.json --json
pocketchat task verify TASK_ID --acceptance acceptance.json --json
pocketchat task report TASK_ID --json
```

`--acceptance` 可选。执行 `status: completed` 与验收 `acceptance.state: passed`
分开；传入时 `ok`/exit 0 必须两者同时成立。产物缺失/不符、权限拒绝或未知观察
返回 1；非法输入在模型调用前以 2 拒绝。不传时旧 `task run` 输出与退出码不变。
`task verify` 不调模型，只追加验收；`task report` 只读历史，不重验当前产物，
exit 0 仍表示报告已读取。plan/check 类型、0/1/2 JSON、证据验证、上限与平台边界见
[task-acceptance.zh-CN.md](task-acceptance.zh-CN.md)。


## 上下文预览

`context` 读取一个 session，按现有预算策略干跑压缩，不发送请求、不写文件：

```bash
pocketchat context notes
pocketchat context notes --budget 24000 --json
```

报告包含消息索引、角色、字符数、压缩前后估算 token，以及现有
drops-oldest-turns 策略会保留/丢弃哪些消息。`--keep-recent` 固定最近边界
（默认 2）。未传 `--budget` 时读取 session trace 中的 `max_context_chars`；
预算连 pinned prompt 都放不下时 exit 2。输出不复制消息正文。已持久化的
`tool_context` 消息按续跑请求的实际顺序显示，字符数包含 tool call/result 元数据。

启用 `context_projection_enabled` 后，`context` 使用与 Session 相同的分层投影：
固定指令、完整近期 exchange、选入证据与省略原因会写入元数据；结果中的
`preview_messages` 是实际可发送的历史选择，pending prompt 由调用方最后追加。
`context_projection_max_bytes` 单独限制完整投影 JSON，不能把旧的文本
`max_context_chars` 当作对象字节上限。该模式默认关闭。

## 本地只读工具

`tool read` 是本地、显式 allowlist 的只读操作：

```bash
pocketchat tool read notes.txt --allow notes.txt --json
```

路径必须由 `--allow` 显式列出；必须解析到当前工作区内，不含敏感路径组件、
不是二进制后缀且为有效 UTF-8。输出受 `--max-bytes` 限制（默认 65536），截断会
报告。命令不写文件、不执行命令、不打开网络 socket。

`tool write` 执行显式 allowlist 的 UTF-8 写入：

```bash
pocketchat tool write notes.txt "new text" --allow notes.txt --overwrite --json
```

已有文件必须显式传 `--overwrite` 才能替换；写入使用原子替换，上限 16 KiB。
敏感、二进制和路径逃逸一律拒绝。工具不删除文件、不执行命令。


## 本地记忆

`memory` 管理 `.pocketchat/memory.jsonl` 中的显式记录：

```bash
pocketchat memory add "Prefer concise answers" --tag style --json
pocketchat memory list --json
pocketchat memory search concise --json
pocketchat memory remove <memory-id> --json
```

记录包含 `memory_id`、时间、内容和标签。add 上限为 16 KiB / 16 个标签；
search 为字面、只读匹配；损坏或 schema 不匹配的 store 一律拒绝。记忆不会自动
注入模型请求。

v2 记忆操作必须显式给出 `--store`，不会把 `.pocketchat/memory.v2.jsonl` 变成默认 store。
`memory recall` 只读，`--dry-run` 预览确定性 v2 检索、排除项和有界 context message，
不写 store 也不注入任何内容：

```bash
pocketchat memory migrate --workspace-id workspace-1 --store .pocketchat/memory.v2.jsonl --json
pocketchat memory accept <memory-id> --by operator --verification not_required --store .pocketchat/memory.v2.jsonl --json
pocketchat memory retire <memory-id> --store .pocketchat/memory.v2.jsonl --json
pocketchat memory forget <memory-id> --store .pocketchat/memory.v2.jsonl --json
pocketchat memory recall "query" --workspace-id workspace-1 --store .pocketchat/memory.v2.jsonl --dry-run --json
```

`migrate` 创建新的有界 v2 文件，v1 源文件保持不变；legacy 记录仍是未验证候选。
`accept` 必须给出明确批准者，只允许把 `legacy_explicit` 或 `user_explicit` 偏好标为
`not_required`，不会补造来源。`retire` 记录生命周期时间；`--superseded-by` 必须指向同一
store 中的另一条记录。`forget` 从活动 v2 store 物理删除所选记录，不保留隐藏正文；它不会
删除历史 trace、session 或 request snapshot 中可能已有的内容，也不承诺从备份或存储介质
安全擦除。

## 委派

`delegate run` 执行一个有限、串行的子任务批次：

```bash
pocketchat delegate run "check A" "check B" --profile local --max-children 4 --json
```

每个子任务都是独立 run，有自己的 trace；批次有子任务数量和截止时间上限，并写
`.pocketchat/delegations/<id>.json`。某个子任务失败不会中止其余子任务。这是
有界委派，不是递归多 Agent 编排：子任务不能继续委派，也不共享父 session。

## 恢复策略

schema `"2"` profile 可声明 `retry_max_attempts`（1-5，默认 1）、
`retry_backoff_ms`（0-60000，默认 0）、`fallback_max_attempts`（1-5，默认 1）
和只能一跳的 `fallback_profile`。只有 `retryable` provider 失败消耗重试预算；
每次重试写 `retry.attempt`，记录物理请求序号与 backoff。主 profile 预算耗尽后
写 `fallback.activated`，标明源/目标 profile；fallback 有独立预算，不允许链式
fallback。流式响应若未以 `[DONE]` 或终止 `finish_reason` 结束，会以可重试的
`incomplete_stream` 失败，半截内容不会被记录成完成回合。非流式正文不足或
chunked 传输损坏以可重试的 `incomplete_response` 失败；响应头前的 HTTP 协议
失败以可重试的 `connection_error` 失败。保留请求快照、失败响应和 run trace，
不把部分回复写成完成。只有已配置的恢复策略才重试；可重试不证明服务端未执行。
backoff 或请求中取消会
结束 run，不伪造恢复成功。

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

如果工作区图中有不可读文件、冲突或未解析链接，这些命令拒绝给出部分答案，并点名受影响
路径。合法追加会立即出现在下一次查询；已有 index 在重建前报告 `stale`。`index` 还会
把自身行与全新重建对账，源摘要没变但行不一致时报告 `inconsistent`。

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

`show --json` 输出一个机器可读对象，包含 `ok`、`schema_version`、会话 header
与 turns、trace 行摘要和 `trace_problem`。工件路径相对当前工作区。会话或 trace
缺失时，stdout 只输出一个稳定错误对象并以 2 退出，stderr 为空；不带 `--json`
时，原有渲染与诊断不变。

`export` 写出会话归档或独立的 event-only trace。源文件不变时，两次导出的字节相同：

```bash
pocketchat export notes                     # sessions/notes.export.jsonl
pocketchat export notes --format md        # sessions/notes.export.md
pocketchat export notes --format trace     # sessions/notes.export.trace.jsonl
trace-doctor validate sessions/notes.export.trace.jsonl
```

`export --json` 输出一个成功对象，含 `format`、`source`、`session`、`out`、
`messages` 与 `trace_events`。trace-only 导出把 trace 记为 `source`，`session` 与
`messages` 为 null。拒绝时 stdout 只输出一个稳定错误对象并以 2 退出；已有输出
永不覆盖。

`jsonl` 是混合归档：依次写入会话头、消息、trace 事件，每条都有 `kind` 字段。
`md` 是对话文本。两者都按续跑请求的顺序包含恢复出的 tool request/result 消息。
它们都不是给 Trace Doctor 的 event-only trace；从混合归档删掉 `kind`，不会把
会话头和消息变成事件。

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

`model.request.started`、`model.response.completed` 与
`model.response.failed` 带 `attempt_index`（当前 turn 内从 1 起算）和
`attempt_scope: "turn"`，用来把一次物理请求与它的结果配对。下一 turn 重新从 1
开始。它不是 retry ordinal：`v1.0.x` 每个请求仍只发送一次，所以将来若出现大于 1
的值，只表示该 turn 内发生了多次物理请求，不表示本版本执行过自动重试。

## `evidence verify`

```bash
pocketchat evidence verify observations.json --workspace ./checkout --json
```

有界读取一个显式工作区相对观察文档，复用既有 evidence-link 纯核；不加 `--json` 输出人类摘要，
不加 `--workspace` 使用当前目录。它不打开原工件、不读凭据、不调 provider、不执行/重放任务、
不写文件，两模式均说明只核对传入观察、原文件未重读。complete 返回 0，partial 返回 1，refused
或坏输入返回 2；partial 不能是 ok=true。JSON 为单个有界安全对象，只有输入索引/原因码，不回显
路径、身份或正文。输入与限额见 [证据契约](evidence-links.zh-CN.md#显式观察-cli)。

## `evidence release-preflight`

```bash
pocketchat evidence release-preflight release-acceptance.json --workspace . --json
```

这是 `evidence` 的子命令，既有顶层稳定命令清单不变。只读一份显式发布验收观察文件，
报告 1.2 尚缺的产物、provider、人工、授权与发布门禁。
不调用 provider、不读 wheel/源码树、不发布、不写文件。complete 返回 0；
pending/unknown/blocked 返回 1；拒绝输入返回 2。观察契约见
[发布验收预检](release-preflight.zh-CN.md)。
