# PocketChat v1.1.0 发布候选

> **版本专属候选记录，不是发布授权。** 通用流程见
> [开发与发布流程](development-and-release.zh-CN.md)。本页只记录一个 v1.1.0
> 候选的本机证据。用户已于 2026-10-30 授权该版本的 tag、GitHub Release 与
> 包索引发布；本预发布记录本身不执行这些动作，发布后事实由外部台账回读。

## 候选记录

```text
版本：1.1.0
候选代码 SHA：0f4a137427ad1a601a10c03c16b3345752b3b3f2
全量测试：CPython 3.9.6，691 tests OK，55.554s，exit 0
全量测试：CPython 3.13.9，691 tests OK，36.776s，exit 0
wheel：pocketchat-1.1.0-py3-none-any.whl
wheel 字节数：150895
wheel 成员数：38
wheel sha256：a9f873f037112bd87474e21cc98d5e42c31d3567eb0563b2adb7cfa25e19834d
构建：CPython 3.13.9 / setuptools 80.9.0，--no-deps --no-build-isolation
安装：干净 CPython 3.9.6 venv，--no-index
安装版本：1.1.0
安装工件：task schema 存在；web/server 成员 0
发布授权：2026-10-30 已收到（v1.1.0）
```

3.9 工具链自带 setuptools 58.0.4 不实现 PEP 621 metadata，在
`--no-build-isolation` 下生成 `UNKNOWN-0.0.0`，该产物已拒绝。有效 wheel 使用
本机 setuptools 80.9.0 构建，再通过干净 3.9 venv 验证。隔离 `uv build` 已尝试，
但在解析 `setuptools>=61` 时连续三次网络重试失败，尚未进入项目构建；这是环境/网络
缺口，不算隔离构建通过。

## 必须检查

- [x] CPython 3.9 全量 PocketChat 测试通过。
- [x] CPython 3.13 全量 PocketChat 测试通过。
- [x] `pocketchat-1.1.0-py3-none-any.whl` 构建成功。
- [x] wheel 装入干净 venv 后报告 `1.1.0`。
- [x] wheel 含 `pocketchat/schemas/task.schema.json`。
- [x] wheel 不含 `web/` 包或 `server.py`。
- [x] 安装后的 CLI 含 `task run/status/resume/report`。
- [x] 确定性 demo 写出 success、checkpoint-resume、permission-refusal 证据。
- [x] 三条 demo trace 均通过 `trace-doctor validate`。
- [x] `replay --dry-run` 重算选定 request snapshot 的摘要。
- [x] 兼容、格式、CLI、中英文 README 与 CHANGELOG 口径一致。
- [x] 全仓 `ruff check pocketchat tests` 通过。
- [ ] 干净构建环境中的隔离 PEP 517 构建通过。
- [ ] Linux/Windows runner 结果已记录。
- [ ] 本候选已有真实 provider 任务运行记录。
- [x] tag、GitHub Release 与包索引发布已明确授权给 v1.1.0。
- [ ] 发布后的 tag、Release、身份与工件回读已记入发布台账（不在本预发布记录内）。

## Demo 证据

生成命令：

```bash
/usr/bin/python3 开源/pocketchat/examples/task-demo/demo.py \
  --output /tmp/pocketchat-v11-demo-final-20261030-084411
```

| 路径 | 结果 | Trace SHA-256 |
|---|---|---|
| `traces/success.jsonl` | completed，3 steps，1 tool | `98ae0888e75e47f6211567b8d7b679db0448568aa53cf22645c87157b58b665b` |
| `traces/run_resume.jsonl` | recovery step 4，context 2 条 | `565a8892a765947c957163e8ac3f72eb4ecd5bfd931ae48b12fc4e8655b62adf` |
| `traces/refusal.jsonl` | completed，tool state `refused` | `b52d729df2c88f33fdfc8fddcd5e3e4eb75bd2c96495732991d558571b105ab2` |

`trace-doctor validate` 对 10、2、9 个事件分别返回 exit 0。

replay 使用显式的第一份 request snapshot：

```bash
pocketchat replay traces/requests/success.turn-1.json --dry-run --json
```

结果：`match: true`、`sent: false`，保存值与重算值的 canonical request
SHA-256 均为
`3a5bacdb4014ee30a251de775bdc38555ad31a2e43acc58b515a3280c6d2880f`。
自定义 trace basename 下用 run id 未发现 `success.turn-N.json`；已验证显式
snapshot 选择，未验证自定义 trace 名的 run-id 发现。

## 不由本候选证明

本候选不证明真实 provider、计费、Linux/Windows、隔离构建可复现性、外部
Trace Doctor 消费者或发布后状态。demo 使用 in-process stub 与假凭据。取消仍是
终态；恢复证据使用真实 `checkpointed -> running` 边界。未创建 tag、Release
或包索引上传。
