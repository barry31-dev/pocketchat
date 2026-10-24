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

`compare` 用同一段上下文把一个 prompt 发给多个 profile，并把回复、延迟、token、
成本与失败原因写成 manifest：

```bash
pocketchat compare "summarize this" --profiles local,hosted
```

完整手册——对比的参数与 manifest、请求快照、Replay、会话与分支、工作区关系图、
查询命令、派生索引、show 与 export、trace——在
[docs/cli.zh-CN.md](docs/cli.zh-CN.md)。


本包对凭据与证据所强制执行的安全规则收拢在
[`docs/security.zh-CN.md`](docs/security.zh-CN.md)；文件格式与兼容规则见
[`docs/formats.zh-CN.md`](docs/formats.zh-CN.md)。

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

`v0.3.0` 的源码版本已公开。它尚未发布到包索引；请按上面的安装命令直接从
仓库源码安装。

本文的中文版与英文版逐命令一致，英文版见 [`README.md`](README.md)。
