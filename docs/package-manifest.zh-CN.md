# 包清单

`PC-PACKAGE-MANIFEST-1` 为现有 release smoke 真正构建、安装的包字节增加确定、
有界的说明。清单是包证据，不是发布、签名、来源真实性结论或公开授权。

## API

```python
build_package_manifest(
    source,
    *,
    source_kind,
    max_members=2048,
    max_member_bytes=8_388_608,
    max_total_bytes=67_108_864,
    max_report_bytes=262_144,
)
```

`source_kind` 只有两种：

- `wheel`：调用方明确选择的一份 wheel；
- `installed_tree`：调用方明确选择的一份 `site-packages` 根。只遍历其中精确的
  `pocketchat/` 与匹配的 `pocketchat-<version>.dist-info/`，不扫描其他已安装包。

调用会读取包字节，但不修改 wheel 或安装树；不负责构建、安装、import、provider、
凭据、网络或任意工作区搜索。

## 报告契约

成功结果是可 JSON 序列化的封闭对象：

```json
{
  "schema": "pocketchat-package-manifest.v1",
  "schema_version": 1,
  "source_kind": "wheel",
  "package": "pocketchat",
  "version": "1.1.0",
  "entry_point": {
    "name": "pocketchat",
    "target": "pocketchat.cli:main"
  },
  "groups": {
    "metadata": [],
    "modules": [],
    "package_data": [],
    "schemas": []
  },
  "members": []
}
```

`members` 按路径稳定排序，每行只有 `path`、`category`、`bytes` 与 `sha256`。
路径是包内 POSIX 相对路径；报告不含源绝对路径或文件正文。`groups` 只是这些行的
确定投影，不是第二份库存。

安装阶段生成的 `__pycache__`、`.pyc`、`.pyo`、`INSTALLER`、`REQUESTED` 与
`direct_url.json` 不属于可比 payload。`RECORD` 会登记，但安装器可为已安装文件改写
它，因此 wheel / 安装树比较时排除其摘要；其余模块、schema、包数据与 metadata
必须一致。

## 必需表面与拒绝

两种来源都必须只有一个 `pocketchat/` 与一个匹配的
`pocketchat-<version>.dist-info/`。现有发布关键模块、5 份 schema、`METADATA`、
`WHEEL`、`RECORD` 与 `entry_points.txt` 都必须存在；metadata 名称 / 版本与
`pocketchat = pocketchat.cli:main` 必须相符。

以下情况 fail closed：

- 重复、绝对、父级、反斜杠、大小写碰撞或 NFC 碰撞路径；
- 加密 wheel 成员、wheel link、安装树 symlink 或其他非普通文件；
- 未知来源类型、包 / 版本 / entry point 不符、第二个包身份、必需资源缺失或已移除的
  web/server 表面重新出现；
- 非正整数限额、成员过多、单成员过大、总字节超限或 canonical 报告超限。

超限会拒绝输入，不会截断后冒充完整。

## Release smoke 接线

`build_wheel()` 把 wheel manifest 绑定进 `WheelArtifact`；
`inspect_installed()` 定位干净 venv 的 `site-packages` 并生成安装树 manifest，随后保留
原有 installed `--version` 与完整 `task ... --help` smoke。`run_release_smoke()` 核对
包身份与全部可比成员摘要，并在不删除旧 JSON 字段的前提下新增 `package_manifests`。

在产品根运行：

```bash
python scripts/release_smoke.py --json
```

## 验证范围

2026-11-05，macOS + CPython 3.13.9 + pip 25.3 实际构建并安装了当前 wheel；
最终 smoke 登记 67 个纳入成员，wheel SHA-256 为
`edc233bf0f7cb554440bea4ea5094202311f7571a03a84bebe370012f569f980`，wheel / 安装树
payload 一致，11 条 task help 命令全部通过。这只证明该次本地运行。另两次连续构建的
wheel 容器摘要不同（`c563154f...` / `33da6dd4...`），但 canonical manifest 字节完全
一致，SHA-256 都是
`a4abfa4972e2e3cf80462df0eb8aeb3bb4ba50ec8fface137dc37591265a2b87`。因此本轮证明的是
清单确定性，不声称 ZIP 容器可复现。

Apple Command Line Tools 的 CPython 3.9.6 使用 pip 21.2.4：隔离构建生成了
`UNKNOWN-0.0.0`；显式非隔离回退又使用低于本仓 `setuptools>=61` 要求的
setuptools 58.0.4。两条失败都没有进入有效 PocketChat 包清单，不能外推成“所有
CPython 3.9 不支持”。Linux、Windows、精确候选 CI、tag、Release 与包索引上传仍需
以后真实证据；没有运行就继续写未验证。
