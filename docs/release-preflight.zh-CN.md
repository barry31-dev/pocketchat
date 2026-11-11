# 发布验收预检

`release-preflight` 对一份显式观察文件做只读检查。它报告版本 1.2 尚缺的发布门禁中，
哪些已有输入事实支持、哪些仍 pending / unknown / blocked。它不是 provider 调用、
人工验收、tag、Release、上传，也不证明命令行重读了原始文件。

## 命令

```bash
pocketchat release-preflight release-acceptance.json --workspace . --json
```

参数只能是一份工作区相对、普通非软链接、最大 64 KiB 的 JSON 文件。不加 `--json`
输出人类摘要，不加 `--workspace` 使用当前目录。命令只读这份文件；不读 wheel、
源码树、trace、provider 配置、凭据、Git 元数据或网络，也不写任何文件。它要求与
`evidence verify` 相同的 descriptor-relative no-follow 文件原语；不支持平台返回
`refused`，原因 `unsupported_platform`。

退出码：

| 状态 | 退出码 |
|---|---:|
| `complete` | 0 |
| `pending` | 1 |
| `unknown` | 1 |
| `blocked` | 1 |
| `refused` | 2 |

`complete` 只表示所有必需门禁都由调用方显式提供且身份不矛盾；不代表输入证据被重跑、
provider 真的回答过、有人真的操作过终端，或发布已经发生。

## 观察形状

外层对象封闭：

```json
{
  "schema_version": "pocketchat-release-acceptance-observations.v1",
  "release": {
    "version": "1.2.0",
    "source_sha": "<40 位小写 hex>",
    "wheel_sha256": "<64 位小写 hex>"
  },
  "evidence": {},
  "blockers": []
}
```

`release` 是每个已提供门禁必须对齐的产物身份。七个有序门禁是：

1. `offline_fixture`：同版本源码与 wheel，加 fixture 证据摘要；
2. `exact_ci`：同源码、workflow run id、恰好七项检查与证据摘要；
3. `real_provider`：同源码/wheel、provider 与 model 范围、证据摘要；
4. `manual_tty`：同源码/wheel、操作者范围、证据摘要；
5. `publication_authority`：同源码/wheel、`action: "tag_release"`、授权摘要；
6. `publication`：同源码/wheel、tag `v<version>`、证据摘要；
7. `public_install_readback`：同版本/wheel、证据摘要。

未提供且无 blocker 的门禁是 `pending`。blocker 精确指向一个未提供的门禁，状态只能是
`unknown`、`not_run` 或 `failed`。同一门禁同时有 evidence 和 blocker 会拒绝；前序缺失或
阻塞时提供后序 evidence 会以顺序错误拒绝，避免 publication 看似补上跳过的前置。

身份不匹配、开放或错误形状、重复 blocker、非法摘要、错误版本/tag、CI 检查数不是七、
坏 JSON 都拒绝。provider/model/operator/tag/action 名称只做校验，不回显到报告；输出只含
发布摘要与固定门禁状态，并带 `scope: "supplied_observations"`、
`files_rechecked: false`、稳定顺序、输入字节的 `source_sha256`，以及覆盖其余所有
报告字段的 canonical `report_sha256`。读者只需移除 `report_sha256`，对剩余对象按
sorted compact UTF-8 JSON 重新编码即可核验摘要。

## 诚实的下一步

报告返回固定动作名，如 `run_authorized_real_provider_acceptance`、
`run_manual_trusted_terminal_acceptance`、`obtain_exact_tag_release_authorization`、
`publish_the_exact_authorized_artifact` 与 `install_and_read_back_the_public_artifact`。
这些只是工作流标签，不是授权。本包不因观察文件自行获得付费调用、凭据、发布或上传权限。

## 当前发布边界

当前源码候选是 1.2.0。源码准备、离线 fixture 和精确候选 CI 与仍缺的真实支持工具
provider 运行、可信人工终端验收、精确 tag/Release 授权、发布及公开产物安装回读分开。
预检只能在观察事实存在后把缺口写清楚，不能制造任何一项。
