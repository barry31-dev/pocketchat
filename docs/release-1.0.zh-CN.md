# PocketChat v1.0.0 发布检查表

> **版本专属候选记录，不是通用流程。** 当前开发、验证与发布规则见
> [开发与发布流程](development-and-release.zh-CN.md)。本页记录这一版实际检查过什么；
> 它不授权打 tag、建 Release、上传包索引或执行任何其他外部动作。

## 本地判定为候选完成前必须满足

- [ ] CPython 3.9 全量 CLI 测试通过（2026-10-29 本机无解释器，未运行）。
- [x] CPython 3.13 全量 CLI 测试通过（2026-10-29，576 tests）。
- [ ] `ruff check pocketchat tests` 通过（全仓仍有存量问题；本次改动文件 E9/F 通过）。
- [x] v1.0.0 wheel 构建成功，且无 server/web 资产。
- [x] wheel 安装到干净 venv 后报告版本 `1.0.0`。
- [x] 安装后的 CLI 含 trace schema，不含 `web` 包。
- [x] 安装后的 CLI 对 `serve` 返回 exit 2。
- [x] 兼容与 provider 适配器契约已成文。
- [x] CLI 命令集合与工件版本由测试锁定。
- [x] `agent` 支持多轮上下文及 `/model`、`/session`、`/reasoning`、`/context`、
  `/status`，并有本地 stub 覆盖。
- [x] 跨平台 CI workflow 存在；本机 macOS CPython 3.13/3.14 验证通过。CAND-7 仍是已跳过的托管 Provider 格；Linux/Windows/3.9 runner 结果未取得、不外推。
- [ ] 获得明确授权后创建公开 tag 与 Release。
- [ ] 获得明确授权后发布到包索引。

## 本候选的本机证据

候选记录（2026-10-29）：

```text
版本：1.0.0
候选 SHA：84ee31ae83dd63608fe40fb4ba11c36e3816cf98（RELEASE-1 提交前）
wheel：pocketchat-1.0.0-py3-none-any.whl
sha256：593159a096cc581a0128c58401fca976d364f587e223314ef5f6f8734423acaf
构建：本机 setuptools，--no-build-isolation
安装：干净 CPython 3.13 venv；version 1.0.0；schema 在；serve exit 2
已测：macOS CPython 3.13 全量；macOS 3.14 全量
未验证：CPython 3.9；Linux/Windows runner；隔离构建；全仓 ruff
发布：未授权、未执行
```

| 检查 | 证据 |
|---|---|
| Python 3.9 | 未运行（解释器不可用） |
| Python 3.13 | 576 tests OK（2026-10-29） |
| Python 3.14 | 全量套件 OK（2026-10-29） |
| Lint | 改动文件 `ruff --select E9,F` 通过；全仓 lint 未解决 |
| Wheel | `pocketchat-1.0.0-py3-none-any.whl`，35 个成员，sha256 `593159a096cc581a0128c58401fca976d364f587e223314ef5f6f8734423acaf` |
| Wheel 构建方式 | 本机 setuptools、`--no-build-isolation`；隔离构建未运行 |
| 安装后的 wheel | 版本 `1.0.0`；schema 在；web 不在；serve exit 2 |
| 隔离构建 | PyPI 访问超时；wheel 使用本机 setuptools 构建 |

隔离构建那一行是环境限制，不是产品通过。下一次公开发布必须在能解析
`build-system.requires` 的环境重跑并记录结果。

## 公开发布顺序

1. 在准确的候选 SHA 上跑本机与 CI 全矩阵。
2. 跑仓库发布门与身份门。
3. 取得用户对 tag/Release/push 的明确授权。
4. 通过既有发布路径创建 annotated tag 与 Release；不得绕门手工推送。
5. 回读远端 tag、Release、提交身份与发布树。
