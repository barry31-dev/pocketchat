# 安全边界

本包对凭据做什么、不做什么，对它写出的证据做什么、不做什么。这里的每一条
都由代码强制、由测试钉住；各节注明拥有该规则的模块。本页把散落在 README 与
各模块 docstring 里的规则收拢到一处。

## 凭据永不落盘

- 配置文件只存 profile 要读的环境变量**名字**（`api_key_env`），不存值。
  没有任何可以填 key 的字段。（`pocketchat.config`）
- `pocketchat profile list` 只打印每个 profile 的模型、base URL、变量名与
  是否已设置。值从不出现——文本输出没有，`--json` 里也没有。（`cli._cmd_profile`）
- key 只来自环境变量。桌面端系统钥匙串是计划中的第二个来源（M5）；没有第三个。

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

## 写什么、写在哪

本包写出的证据都在命令所运行的工作区之内：`sessions/`、`traces/`
（含 `traces/requests/`）、`comparisons/`，以及——仅在被要求时——
`index.sqlite`。唯一有意的例外是 `export --out`：操作者指哪里就写哪里。
记录内的路径都相对工作区；JSON 记录（`workspace`、`history`、`search`、
`diff`）不携带绝对路径。没有 telemetry、没有默认上传、没有静默默认端点：
某个 provider 被使用，是因为某个 profile 点了它的名。

## 还没有的（因此也不承诺）

本地 UI（M4）尚不存在。它落地时的规格是只监听 loopback 并要求短期 token；
在这一里程碑之前，除操作者自行配置的 provider 请求外，不存在需要设防的
网络面。Windows 支持与桌面端钥匙串按计划在 M5。
