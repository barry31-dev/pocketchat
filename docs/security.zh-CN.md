# 安全边界

本包对凭据做什么、不做什么，对它写出的证据做什么、不做什么。这里的每一条
都由代码强制、由测试钉住；各节注明拥有该规则的模块。本页把散落在 README 与
各模块 docstring 里的规则收拢到一处。

## 凭据永不落盘

- 配置文件只存 profile 要读的环境变量**名字**（`api_key_env`），不存值。
  没有任何可以填 key 的字段。（`pocketchat.config`）
- `pocketchat profile list` 只打印每个 profile 的模型、base URL、变量名与
  是否已设置。值从不出现——文本输出没有，`--json` 里也没有。（`cli._cmd_profile`）
- key 只来自环境变量。没有桌面端系统钥匙串，也没有其他凭据来源。

## 请求与快照

- 请求快照携带实际发出的 body，**不含任何 header**：Authorization header
  不会被记录在任何地方。（`pocketchat.request`）
- 快照写出之前，body 会与本进程持有的凭据做字面字节比对——无论 key 周围有
  什么引号或转义。body 含 key 时 fail-closed：不写快照，也不写「脱敏版」。
  （`CredentialInRequestError`）
- 对比的前置校验在第一个请求发出之前解析 profile 与凭据：缺 key 是用法错误
  （exit 2），不留下任何 trace、快照或 manifest。

## Trace 与回复

- trace 默认只记录每个回复的**摘要**（长度加短前缀，`summary_prefix_chars`）。
  全文是操作者主动选择的 `--verbose`，而这个选择本身也记录在 run 自己的证据里
  （`run.started` → `config_summary.verbose`）——这正是 `select --into` 能拒绝
  把摘要写进会话（在那里会被读成完整回答）的依据。
- provider 错误在进入 trace 或 manifest 之前按字面凭据脱敏：回抄 key 的
  响应体不会被存下来。（`pocketchat.provider`）

## Provider 适配器边界

每次模型调用都经过 provider 适配器。适配器负责构造精确 payload 并发起调用；适配器名称
记录在 run 的 config summary 中。详见
[`provider-adapters.zh-CN.md`](provider-adapters.zh-CN.md)。下面的安全规则适用于所有
适配器，包括未来新增的原生适配器。

## 写什么、写在哪

本包写出的证据都在命令所运行的工作区之内：`sessions/`、`traces/`
（含 `traces/requests/`）、`comparisons/`，以及——仅在被要求时——
`index.sqlite`。唯一有意的例外是 `export --out`：操作者指哪里就写哪里。
记录内的路径都相对工作区；JSON 记录（`workspace`、`history`、`search`、
`diff`）不携带绝对路径。没有 telemetry、没有默认上传、没有静默默认端点：
某个 provider 被使用，是因为某个 profile 点了它的名。

## 本地工具边界

`tool read` 是唯一的本地工具面：必须显式 allowlist、限制在工作区、拒绝敏感名称
与二进制数据；不写文件、不执行命令，并记录权限/工具事件而不保存完整参数。

`tool write` 是独立的显式 allowlist 写入面：只接受 UTF-8 文本，上限 16 KiB，
使用原子替换，替换已有文件必须显式 `--overwrite`；不提供删除、shell、执行或网络
操作。

模型自动 tool call 默认关闭。profile 只提供它显式启用的本地工具（`read_file`、
create-only `write_file` 或只读 `memory_search`），路径来自显式 allowlist，
调用次数和结果大小受限。禁用/未知工具、缺参数或未知参数和 guard 拒绝返回有界失败结果，
让模型纠错；参数在触达文件或记忆 I/O 前按封闭 contract 校验。provider schema、
trace 摘要、task policy 与宿主校验共用 `pocketchat.tool_contracts`。底层
I/O/存储错误、结果超限和预算耗尽仍结束 run。run trace 记录各 contract 的 schema
hash 与 call id；shell、执行和网络工具仍不可用。

启用 `tool_write_enabled` 时，`write_file` 只能 create-only。模型不能请求
overwrite 或 delete，目标必须显式 allowlist；已有文件在写入前 fail-closed。

启用 `memory_search` 时它是只读的：只搜索显式本地记录，返回有界元数据/内容；
模型不能新增、修改或删除记忆。记忆也不会被静默注入 prompt。

## 还没有的（因此也不承诺）

PocketChat 只做 CLI。没有计划中的单 profile chat 发送路由、远程访问模式、TLS、
账号体系、分析服务、telemetry、打包桌面端、Windows 桌面支持或桌面端钥匙串。

## 未发布验收边界

操作者 plan 只授权有限只读产物检查，不扩大模型工具。验收器拒绝敏感/内部路径、
符号链接、特殊文件和超大内容；缺少 no-follow API 不退回普通路径读。
逐项 JSON 只含 bytes/hash，不复制产物正文、needle 或 expected。
独立记录含操作者 plan，因此不要往输入放凭据或秘密比较值。
记录是本地可编辑观察，不是签名、对抗性防篡改、创作归属证据或安全沙箱。
准确上限和状态只见 [task-acceptance.zh-CN.md](task-acceptance.zh-CN.md)。
