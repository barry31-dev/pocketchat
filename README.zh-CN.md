# pocketchat

v1.2 源码候选准备与精确产物验收见[发布候选说明](docs/release-1.2.zh-CN.md)。
发布事实只看[发布台账](docs/release-ledger.md)；源码和离线 fixture 成功不认证真实
provider 或人工文件终端验收。

`pocketchat` 是一个很小的单用户命令行对话 agent。它**不是**通用 agent：没有代码执行、
shell 或无边界工具面；可选的本地工具都显式 allowlist 并限制在 workspace。它与你配置的
模型保持多轮对话，并写下这段对话的 trace，供别的工具事后核查。

trace 才是重点。其余一切都只是为了让「模型到底看到了什么、到底答了什么」这个问题有一个
文件可以打开，而不是靠记忆去信。

## 它不是什么

- 没有通用工具调用。可选本地工具只包括 allowlist 读取、create-only 写入和只读记忆搜索。
- 没有 Web UI、本地服务产品或打包桌面 App；同样没有云服务、账号体系或同步。
- 没有向量检索或自动长期记忆。本地记忆必须显式写入，也不会静默注入 prompt。
- 没有无界重试。可选 retry/fallback 有上限且全程记录。
- 不带任何 provider SDK。它用 `urllib` 讲 OpenAI 兼容的 HTTP API，指向你配置的
  `base_url`。

## 版本边界

`v1.1.0` 是当前 CLI-only 源码版本。它沿用
[兼容与弃用策略](docs/compatibility.zh-CN.md) 与
[provider 适配器契约](docs/provider-adapters.zh-CN.md) 以及
[开发与发布流程](docs/development-and-release.zh-CN.md) 中记录的命令、退出码、
JSON envelope、文件格式与适配器边界。v1.1 的版本专属记录见
[v1.1 发布记录](docs/release-1.1.zh-CN.md)；v1.0.0 候选记录保留在
[发布检查表](docs/release-1.0.zh-CN.md)。协作者规则见
[AGENTS.md](AGENTS.md)；当前已批准切片见
[切片队列](docs/slice-queue.md)，发布状态见
[发布台账](docs/release-ledger.md)。未来 Agent 基础能力的证据门槛见
[开发规划总则](docs/agent-development-master-plan.zh-CN.md)。候选下一步切片见
[pc 下一步计划](docs/pc-next-steps.zh-CN.md)，入队后才开工；已实现证据字段与缺口见
[证据矩阵](docs/pocketchat-evidence-matrix.md)。运行目标是 Linux、macOS、Windows 上的 CPython 3.9 与 3.13；当前本机验证只在
macOS 上完成。包内保留跨平台 CI workflow；某个候选 SHA 是否实际跑绿，按该次
Release 记录读取，不从运行目标或 workflow 存在本身外推。
不包含 Web UI、本地 API、桌面端、远程服务或通用工具调用。

`v0.1.0` 是单 profile 的基础版：可安装的 CLI、一个配置好的 provider、
`run`、`chat`、`show`、`export`、带终止事件的 trace，以及会话续接。2026-10-20
已用本机免费聚合路由完成一次真实 `run`（`run_20261020t015755_8e636ff8`，
trace SHA-256 `9102fab4a549ff398265d0094c38bf74cd81a33241db7bf77fbcd64621453e45`）。
这只验证该次调用的请求、trace 与凭据零命中，不外推到其他 provider。

`v0.2.0` 增加多 profile 配置与选择、串行对比、请求快照、对比 manifest、成本与
延迟，以及 replay dry-run。它不增加工具调用、自动重试、分支树、流式输出、
Web UI、本地服务产品或第二个 provider SDK。

## 未发布源码：可验证任务交付

Task Run 后可选确定性产物验收：执行 `completed` 与验收 `passed` 分开，
模型回答不能代替声明的文件/文本/JSON 条件。使用 `task run --acceptance`、
`task verify` 和 `task report` 的验收节。**公开 v1.1.0 制品不含这些新增能力**。
退出码 0/1/2、历史记录与平台拒绝边界见
[task-acceptance.zh-CN.md](docs/task-acceptance.zh-CN.md)。

```bash
python3 examples/task-acceptance/demo.py
```

## 未发布源码：本地报告与修订

此已复审源码候选让原有授权 task CLI 将完成读取快照交给报告步骤，并把 Session 结果绑定到固定读取 trace、把 task 工作区绑定到授权 policy；随后可创建独立修订版，并在接续时不重放已完成读写。运行 `python3 examples/local-report-demo.py` 可走离线真实文件 CLI fixture。权限、来源追溯、拒绝路径与真实 provider 未验证边界见 [本地报告说明](docs/local-report.zh-CN.md)；准确候选 CI 与产品 main 回读仍待完成，新增源码不代表正式发布。

## 环境要求

Python 3.9 或更高。运行时没有第三方依赖。

## 安装

```bash
python3 -m pip install .
pocketchat --help
```

### 使用本机 newnewrouter 模型目录

如果 newnewrouter 正在 `127.0.0.1:8137` 运行，可为目录里的每个模型生成一个
PocketChat profile：

```bash
python3 scripts/sync_newnewrouter_models.py
python3 -m pocketchat doctor --config ~/.pocketchat/newnewrouter.json
python3 -m pocketchat agent --config ~/.pocketchat/newnewrouter.json
```

生成的 profile 使用 `requires_api_key: false`：上游凭据由 newnewrouter 管理，
PocketChat 不保存、不打印它们。目录里有稳定别名 `pgt` 时，生成器优先
把它设为默认；没有时回退到目录第一项。newnewrouter 的
`/v1/models` 目录变化后，重跑同步脚本即可。

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

```bash
pocketchat profile set staging \
  --base-url https://example.invalid/v1 \
  --model example-model \
  --api-key-env POCKETCHAT_STAGING_KEY
pocketchat profile use staging
pocketchat profile remove staging
```

`profile set` 创建或整体替换一个 schema `"2"` profile，且从不接受凭据值。
`profile use` 修改默认 profile，`profile remove` 拒绝删除当前默认项。每次被拒绝
的写入都保持文件不变；成功写入会整体校验候选、保留文件权限并原子替换。`--json`
在参数解析完成后提供稳定的成功与配置/写入错误对象。完整选项见[命令手册](docs/cli.zh-CN.md)。

schema `"2"` profile 还可声明 `retry_max_attempts`、`retry_backoff_ms`、
`fallback_max_attempts` 与只能一跳的 `fallback_profile`。默认值保持现有行为：
不自动重试、不 fallback。
可选 `max_turn_tokens` 与 `max_session_tokens` 提供 fail-closed token 预算，
它们不是货币上限。

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

## Agent 对话

`pocketchat agent` 是全屏交互入口。它把对话保存在 session 中，并允许
下一条消息切换 profile、思考强度和上下文预算。斜杠命令是控制输入，不会变成用户
消息，也不会产生 trace 事件。

```bash
pocketchat
```

直接输入 `pocketchat` 会进入交互式 agent。标准库提供 curses 的终端会打开全屏界面：首屏显示紧凑品牌与配置摘要，对话采用紧凑 gutter 与
轮次分隔，底部是带上下规则的输入框和单行状态规则，支持历史记录与斜杠命令选择菜单。`Enter` 发送，`Ctrl+J` 换行，`↑/↓` 取历史，`Ctrl+T` 展开/收起思考，
鼠标滚轮与 `PgUp/PgDn` 翻阅对话历史，`Ctrl+C` 中断正在生成的回复，`Ctrl+D` 退出。输入 `/` 后用 Up/Down 选择命令、Tab 应用；Enter 对半截命令先补全，完整命令才执行。
标准 Windows Python 等不提供 curses 的平台会回落到同一条行式交互入口，不引入第三方运行时依赖。
输入不带参数的 `/model` 会打开 profile 选择器。请求运行时会持续显示
`thinking...`、已等待秒数，并实时显示思考内容与正文增量；收到完整回复后写入 session 与
trace。状态规则随后从 trace 读取已报告 token（`tok`）、按配置价格计算的估算成本（`cost~`）、
最近 TTFT、物理请求序号（`req`）和压缩事实（`cmp`）。缺 usage、价格或 TTFT 时显示 `?`；
`req` 不是 retry 次数。管道输入仍走原来的逐行 REPL，供脚本和测试使用。

若当前目录有 `pocketchat.json` 就使用它，否则使用
`~/.pocketchat/newnewrouter.json`；显式 `--config` 优先。`pocketchat run` 等具名子命令
继续可用。

```bash
pocketchat --profile hosted --reasoning high --session notes
```

```text
/model hosted
/session another-conversation
/reasoning high
/context 16000
/status
/exit
```

每条普通消息都是独立 run，并有自己的终止 trace 事件；session 文件负责在两轮之间
携带对话。思考强度始终记录在 run 配置中；只有 profile 显式声明
`reasoning_effort_supported: true` 时，才会把 `reasoning_effort` 发给
OpenAI-compatible 端点，因为兼容端点对该扩展并不统一；本检查点不改变其它已配置 agent 能力。
当前可信终端检查点新增 opt-in 精确路径文件控制：`/files` 显示当前授权与 pending change id，
`/files read|write PATH...` 展示 provider 告知与待确认的精确授权，`/diff` 或 `/apply` 展示
完整 pending diff，`/confirm` 执行精确展示的动作，`/changes` 列出
该 conversation 的持久变更元数据，`/undo CHANGE_ID` 预览一条持久补偿。模型只能读取已授权
路径并提出修改，不能授权、确认、apply 或 undo。文件控制要求 stdin/stdout 都是真实 TTY；
pipe 输入在创建 session、trace 或 file-change store 前拒绝。apply 成功后会消费该路径的 write
grant，因此 undo 要重新展示并确认 fresh write grant。`/files cancel` 撤销授权与未确认 review，
不撤销已写入效果；运行中断同样失效待确认状态，未退出请求保留写者。
[离线黄金 fixture](docs/file-work-validation.zh-CN.md) 覆盖共享控制器，可在源码外已安装开发 wheel
上运行。真实 provider、人工文件终端验收和正式发布各自记录，不将开发验证当发布。

## 对比

`compare` 用同一段上下文把一个 prompt 发给多个 profile，并把回复、延迟、token、
成本与失败原因写成 manifest：

```bash
pocketchat compare "summarize this" --profiles local,hosted
```

完整手册——对比的参数与 manifest、请求快照、Replay、会话与分支、checkpoint/rewind、工作区关系图、
查询命令、派生索引、show 与 export、trace——在
[docs/cli.zh-CN.md](docs/cli.zh-CN.md)。

脚本可对 `init`、`run`、`chat`、`branch`、`checkpoint`、`rewind`、`task`、
`show`、`context`、`export` 使用 `--json`。
执行对象包含状态、run id、turn 数、回复与工作区相对 trace；branch 对象不输出
消息正文；读取/导出对象报告相对工件路径。失败使用稳定错误对象，默认输出不变。

`checkpoint` 记录一个已完成的 session 边界，`rewind` 从它创建新分支。源 session
与 trace 保持不变；恢复 manifest 让边界和被中断的 rewind 事务可以回查。

`task run --policy FILE --session NAME` 通过同一 provider、工具、session 与 trace
路径执行一个有界任务。`task status` 报告封闭任务状态，
`task resume TASK --checkpoint ID --policy FILE` 恢复 task checkpoint 并报告 recovery
step，`task report TASK` 只聚合已存在的 steps、tools、budgets、checkpoints、精确的
run 到 trace 关联和已完成的文件 artifacts。损坏或缺失的 trace 证据会进入
`evidence_issues`，不会静默省略。四个命令都支持稳定 `--json`；report 未收到 policy
或摘要不匹配时，token 预算保持 `unknown`。确定性的本地 smoke demo 是
`examples/task-demo/demo.py`。

`context` 预览 session 的消息预算，并按当前 drop-oldest-turns 策略干跑压缩，
不发送请求、不写文件。

`tool read PATH --allow PATH` 读取工作区内一个显式 allowlist 的 UTF-8 文件；
只读、有大小上限，拒绝敏感或二进制路径；`tool read` 本身不写文件。

`tool write PATH CONTENT --allow PATH` 原子写入一个 allowlist UTF-8 文件；
已有文件必须显式 `--overwrite`。不提供删除、shell、执行或网络工具。

profile 可通过 `tool_read_enabled`、`tool_read_allow_paths`、
`tool_call_max_count`、`tool_result_max_bytes` 启用模型自动调用只读
`read_file`；默认关闭，且只提供这一个工具。

profile 可另外通过 `tool_write_enabled`、`tool_write_allow_paths` 启用
create-only 的自动 `write_file`；模型不能覆盖或删除文件，`--no-tool-write`
可关闭该工具。

profile 也可通过 `tool_memory_search_enabled`、
`tool_memory_search_max_results` 启用只读自动 `memory_search`；模型不能新增、
修改或删除记忆。

自动 tool loop 中，禁用/未知工具、缺参数和 workspace guard 拒绝会返回有界的
`status: failed` 工具结果，让模型在既有调用预算内纠错。底层 I/O 或记忆存储错误、
结果超限、预算耗尽、Provider 错误和中断仍结束 run。权限与工具 trace 事件都带
tool call id。已完成的 tool call 与结果会作为 additive session `tool_context`
保留，因此下一条 prompt、进程续跑和派生分支都能在最终回复前看到同一段工具交互。

`memory add/list/search/remove` 管理 `.pocketchat/memory.jsonl` 中的显式本地记忆；
记忆不会自动注入 prompt。

`delegate run PROMPT...` 串行执行有界的独立子 run 批次并写 delegation manifest；
它不是递归多 Agent 编排，也不共享父 session。

## 范围

PocketChat 只做 CLI。不规划、不支持、也不接受针对 Web UI、本地 API、桌面端、
移动端、远程服务或通用工具调用的新开发。上面的显式本地工具边界就是完整工具面。


本包对凭据与证据所强制执行的安全规则收拢在
[`docs/security.zh-CN.md`](docs/security.zh-CN.md)；文件格式与兼容规则见
[`docs/formats.zh-CN.md`](docs/formats.zh-CN.md)。

## 状态

稳定 CLI 候选。对话内核、REPL 与会话持久化已实现。PocketChat 是面向 AI 构建者与
技术团队的本地优先、trace-native、多模型对话 **CLI**。CLI 是唯一产品面；
Web UI、桌面端与通用工具调用均不在范围内；显式本地工具面仍按上文提供。

2026-10-28 的 CLI-only 回归在 CPython 3.9.6 与 3.13 上各通过 **371 tests**。
另用 CPython 3.9 从源码建立干净 venv：模块从 `site-packages` 导入，包含 trace
schema，不包含 `web` 包，`pocketchat serve` 以 exit 2 拒绝。安装后的 CLI 还完成了
init/doctor JSON、失败 run 的 exit 1 JSON 与终止 trace，以及字节一致的 trace 导出。

CLI 现在可以在一份配置里放多个 profile，并用 `--profile` 跑你选的那一个；
不选时用配置里的 `default_profile`。每个请求都会在 trace 旁边存成一份快照，
所以发出去的内容可以读回来并对账。`compare` 还可以把一个 prompt 同时发给多个
profile，并为这一批运行写一份 manifest。replay dry-run 可以核对一份已保存的请求，
不需要连接 provider、不需要读取凭据，也不会打开 socket。

这里列出的每个测试都用本地 stub HTTP server，所以测试证明的是请求的形状、trace
的形状，以及每一条失败路径，不是任何 provider 的行为。另有几次经本机 cc-switch
免费聚合路由的真实调用，各自只覆盖当次的路由输入（2026-10-20 一次单轮 run 与
一次双 profile 对比；2026-10-24 全链路一次：真实双轮对话、verbose 对比、选择入
会话、续写、派生分支并续写、导出、五条 trace 全部过校验、快照 dry-run 对账、
凭据零命中），案例见命令手册。它们不改变测试的 stub 性质，也不外推到其他
provider。

## License

Apache-2.0。见 `LICENSE`。

## 仓库

源代码维护在 `barry31-dev/pocketchat`。

## 发布状态

`v1.1.0` 的源码版本已公开，并有 GitHub tag 与 Release；它尚未发布到包索引。
可从该 Release 下载 wheel，或按上面的安装命令直接从仓库源码安装。此前的
`v0.3.0` 源码版本仍保持公开。

本文的中文版与英文版逐命令一致，英文版见 [`README.md`](README.md)。
