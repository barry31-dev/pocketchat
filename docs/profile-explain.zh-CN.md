# Profile 解释

`pocketchat.profile_explain.explain_profile` 是一个纯解释层，只读取调用方已经
提供的配置映射、已选 profile 和封闭的 credential 观测。它不读取配置文件、不
枚举环境、不调用 provider，也不写任何产物。

```python
from pocketchat.profile_explain import explain_profile

report = explain_profile(
    raw_config,
    selected_profile,
    explicit_profile,
    {"presence": "present", "source": "environment"},
)
```

## 输入与选择

- `raw_config` 是既有配置层已经接受的 schema 1 或 schema 2 文档，提供 profile
  本体和默认选择。
- `resolved_config` 是 `profile_config` 返回的单个 profile，必须与原始记录选择
  同一个名称，并具有一致的 model、endpoint、credential 变量名和是否需要
  credential 的结论。
- `explicit_profile` 是 CLI 显式名称或 `None`。显式名称记录
  `selection: cli`；`None` 记录配置声明的默认项 `selection: default`。
- `credential_presence` 只允许 `presence` 与 `source` 两个键。`presence` 为
  `present`、`missing` 或 `unknown`，`source` 为 `environment` 或
  `not_required`。环境读取由调用方完成，本模块只接收这个封闭观测，永远不接收
  credential 值。

显式名称不存在，或 raw/resolved 选择不一致时，返回封闭的 refused 报告，不猜
另一个 profile。格式错误、重复键、endpoint 的 user-info 或未知字段，会在报告
可被信任前抛出带稳定类型的 `ProfileExplainError`。

## 结果

完整报告使用 `pocketchat-profile-explain.v1`，包含 `profile`、`selection`、
`model`、脱敏后的 `endpoint`、封闭的 `credential` 对象，以及按字段排序的
`provenance`。endpoint 只保留 scheme、host 和可选 port；path、query、
fragment 与 user-info 都不复制。provenance 区分原始 profile 中显式出现的字段
与配置层补上的默认值。

拒绝报告为 `status: refused` 并带封闭的 `findings`，不包含 model、endpoint 或
credential 对象。完整与拒绝报告都不会包含 profile 本体、system prompt、完整环境
或 credential 值。输入不会被修改，相同观测会产生相同报告。

## 限额与非目标

规范化 UTF-8 报告封套上限为 64 KiB、128 个结构字段；超限时拒绝，不截断。报告
用 `report_bytes` 与 `report_sha256` 描述自己的有界封套。

本模块不检查网络健康、不测试 credential、不读取或修改配置、不新增 CLI、不产生
trace/session 证据、不调用 provider，也不授权执行。`present` 只表示调用方观察
到命名环境变量存在，不证明 provider 接受它。
