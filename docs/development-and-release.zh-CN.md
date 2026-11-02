# 开发与发布流程

> 范围：PocketChat 如何开发、验证、发布与维护。产品方向与范围见路线图；
> 发布状态见 `docs/release-ledger.md`。本页是本包唯一的流程来源，可随公开仓库独立使用。
>
> 版本：2026-10-29。适用于 CLI-only 产品。

## 1. 运行模型

PocketChat 是单用户、本地优先的 CLI。长期资产是可回查的证据：trace、请求快照、
session、compare 记录与发布产物必须能让后来的人回答“发了什么、回了什么、
还有哪些未验证”。

每次改动都走这条回路：

```text
选格 -> 写完成判据 -> 先红测试 -> 最小实现 -> 按层验证
-> checkpoint（commit + 证据）-> 集成
-> 冻结发布候选 -> 跑门禁 -> 用户授权 -> 发布
-> 回读远端 -> 维护或回滚
```

阶段是位置，不是审批门。小的文档修正不需要发布候选；较新版本也不能继承旧提交、
旧 wheel 或另一台机器的证据。

## 2. 开工前

### 2.1 入口与范围

只读本次需要的材料：

- 当前格：本仓库的产品 issue；不以 issue 跟踪时记在 `docs/slice-queue.md`；
- 本文；
- `compatibility.md`、`security.md`、`cli.md`、`formats.md` 与相关测试。

每格必须写清：

```text
目标：
明确不做：
完成判据：
证据位置：
预算 / 停止条件：
回滚 / 下一步：
```

产品代码改动必须带当前格记录的更新，才能进入 `main`：可以是本仓库被引用的
issue，也可以是不以 issue 跟踪时的 `docs/slice-queue.md`。队列条目必须已经批准：
机器只能确认文件或 issue 变了，**不能把“有变更”写成“已批准”**。
`CHANGELOG.md` 是已交付历史，不能替代当前格。纯文档、流程工具与测试维护不新增
产品切片。

目标必须可检验。“优化 agent”不是目标；“为五类失败记录稳定 `error_type`，并保持旧 JSON
字段不变”才是。

### 2.2 产品边界

当前产品只做 CLI。以下能力不是改动的隐含附带项；要进入实现，必须先改路线图与切片队列：

- 通用工具调用、显式本地工具之外的文件访问、shell 执行、沙箱或审批闸；
- 多 agent 编排、委派、自动规划或自动选优；
- memory、skills、MCP、cron 或后台任务；
- Web UI、本地 API、桌面端、账号体系或远程服务。

测试、分支或 mock 让某能力“可做”，不使它成为当前能力。先更新路线图，再实现一个切片。

### 2.3 证据

以下三维分别记录，不合并成一个状态标签：

| 维度 | 取值 |
|---|---|
| 来源类型 | 官方文档（D）、当前代码/测试/schema（C）、逆向结论（R）、本机实测（V）、开源参考（O）、项目解释 |
| 验证状态 | 已验证 / 待验证 / 未验证 / 未运行 |
| 适用范围 | 实际覆盖的版本、commit、系统、Python、provider、工作区或 fixture |

本机 trace 只证明那次运行；stub 证明适配器形状，不证明真实 provider；
schema 声明不证明事件真的被写出。

## 3. 开发

### 3.1 先红

行为改动与修复先写会失败的测试。失败必须被实际观察到，并且是因为预期原因失败，
不是因为测试导入了错误模块。必须先单独运行测试看它红，再写实现；
测试与实现同批写完，不能证明测试真的能失败。

CLI 的可观察契约是 stdout、stderr 或退出码时，测进程边界；内部状态测所有者模块；
trace 与文件格式补正反两个 fixture。CLI 代码改动触及已文档化契约时，必须同步
`docs/cli.md` 与 `docs/cli.zh-CN.md`，并运行本包的文档守卫。

纯文档、可逆文本和生成元数据可以用 diff 检查，不为了凑流程加假测试。

### 3.2 最小实现

只实现当前格需要的内容：

- 除兼容策略明确允许外，保持既有命令、退出码、JSON 与文件格式契约；
- 不把 retry、fallback、模型选择、评分或第二条 provider 路径作为未计划的副作用加入；
- 不静默重写已有 session、trace 或请求快照；
- 凭据不得进入配置、JSON、trace、manifest 或测试输出。

### 3.3 Checkpoint

每完成一个里程碑，在任务消息、commit 或交接中写 checkpoint：

```text
切片 / commit：
改了什么：
工作目录：
命令与退出码：
已验证：
未验证：
下一步：
```

checkpoint 故意短于完整日志。原始 trace 或命令输出留在可重新打开的路径；
不依赖聊天摘要或模型记忆。

### 3.4 停止条件

出现以下情况先停止并报告，不要空转：

- 同一失败无新证据地重复两次；
- 测试显示通过，但实际一个用例都没执行（`--filter` 或收集错误）；
- 另一个写者正在改同一文件或工作区；
- 凭据、外部账号或 provider 不可用，而拟议修复要换账号或绕门；
- 准备执行不可逆发布，但授权没有绑定当前版本与 SHA；
- 支撑结论的证据已丢失且无法重新读取。

只有动作幂等、旧尝试确认结束、重试有明确上限时才重试。

## 4. 验证矩阵

选能证明本次行为的最小层。不同层不能互相顶替。

| 改动 | 最小证据 | 不能证明什么 |
|---|---|---|
| 文档、路线图、发布清单 | diff、链接、术语、范围一致性 | 产品行为 |
| Python 逻辑 | 相关 unittest + 负向样本 | CLI 包络或真实 provider |
| CLI / JSON / 退出码 | 进程级 stdout、stderr、退出码 | 所有 shell 或系统 |
| trace / schema | schema 检查、终止事件、畸形事件 | 远端 provider 质量 |
| provider 适配器 | 本地 stub HTTP；真实路由另记 | 所有 provider 或账单 |
| 打包 | wheel 内容、干净 venv 安装、安装后 CLI 冒烟 | 跨平台矩阵 |
| 发布树 | clean-room、必需文件、身份门 | 公开发布授权 |

### 4.1 发布候选的本地必查

必须在 **PocketChat 包根**执行。若从 仓库根直接跑
`unittest discover`，Python 找不到 `pocketchat` 包，看到的是环境错误，
不是产品失败。

```bash
python -m unittest discover -s tests -v
ruff check pocketchat tests
```

再在干净环境构建与安装：

```bash
python -m build
python -m venv /tmp/pocketchat-release-venv
/tmp/pocketchat-release-venv/bin/python -m pip install dist/pocketchat-*.whl
/tmp/pocketchat-release-venv/bin/pocketchat --version
/tmp/pocketchat-release-venv/bin/pocketchat --help
```

Windows 使用对应的 `Scripts/` 或 `bin/` 路径。记录解释器版本、命令、输出与退出码。

CI 矩阵只证明它报告的那个 commit 与 workflow run。推到另一个仓库、或 workflow 没运行，
都不能继承旧的绿灯。

### 4.2 验证计划用词

**缺失的路径**（uncovered path）是“有源码改动、但没有对应测试文件”的路径，
不是缺少验证层，也不是测试失败。报告时必须写出路径和缺失的测试，
不能用层名代替。

### 4.3 兼容与安全

发布前检查：

- 命令名、退出码类别与已文档化 JSON 字段是否符合 `compatibility.md`；
- 未知格式版本是否拒绝而不是猜测；
- 配置、JSON、请求快照、trace、manifest 与测试产物中是否出现 key；
- 已文档化的证据路径是否保持工作区相对路径；
- 包内是否又出现了已删除的 Web/server 面。

这些是发布门，不是产品测试的替代品。

## 5. Review Ladder 与 Harness 实践

### 5.1 Review Ladder

本节把 Anthropic 的
[How the Claude Code team does code review](https://claude.dev/blog/solving-code-review-at-anthropic/)
按 PocketChat 的规模与产品边界做了裁剪。

代码审查有两项工作：在上线前找出并修掉问题，以及让维护者共享代码上下文。
逐行读完每份 diff 不是目标。稀缺资源是人的注意力，因此审查深度取决于
“漏掉问题的代价”，不取决于作者身份或 diff 大小。

风险由改动决定：

| 档位 | 适用 | 必需审查 |
|---|---|---|
| low | 文档、链接、生成元数据与非发布配置 | `quick`：diff、术语、链接及适用的文档守卫 |
| medium | Python 行为、CLI/JSON 契约、schema、provider 适配器与打包 | `standard`：quick + 相关测试 + 对 diff 和契约的 fresh-context 复审 |
| high | 安全边界、兼容策略、发布/身份路径、发布与回滚 | `deep`：standard + 命令或外部回读证据 + 专门的安全与回滚审查 |

未知路径按 `medium`，不得默认降成 `low`。一个改动混有多个档位时，
按最高档位审查整个改动。

每个准备进入 `main` 的改动都留下简短审查记录：

```text
changed paths：
commit / candidate SHA：
risk tier / review depth：
rounds：
findings（id、severity、status、reason/fingerprint）：
reviewed evidence：
```

合并入口必须校验**整份记录**：根据 changed paths 重算风险档位与审查深度，
根据 rounds 重算轮次结论；paths/categories 缺失、finding 状态未知、fingerprint
缺失或记录与重算结果不一致，都必须 fail-closed。record paths 必须与 task diff
完全一致，categories 与风险档位必须从该 diff 重算，不能信作者自报。
只检查 findings 列表不算审查门。

finding 状态只能是 `open`、`fixed`、`dismissed` 或 `deferred`。
`dismissed` 与 `deferred` 必须写原因。同类问题用 fingerprint 标成一类，
只报告一次。`blocker` 或 `major` 的 open finding 阻止合入。
测试、审查记录和发布检查是三类独立证据，不能互相顶替。

审查要收敛，不允许无限轮：

- 第一轮审完整 diff，后续轮次只审新增 diff；
- 不重复贴已经关闭的 finding；
- 最多 3 轮；第 3 轮后停止并重新考虑实现方案；
- 第 3 轮后只允许继续审新增 diff；
- 在轮次上限内仍不能一致时升级处理，不开始第 4 次全量审查。

每个改动都要经过自动审查。作者用测试或外部回读证明行为并写证据。
fresh-context 复审者只看目标、diff、契约和测试，不直接接受作者的结论。
人工批准按风险缩放：`low` 留下机器审查记录即可，`medium` 合入前需要人工签字，
`high` 需要具名人工批准；涉及外部动作时，发布授权另行绑定。
机器人 finding 或 approval 不能顶替要求的人工批准。

合入前要问“这个改动可能出什么错，哪一层能拦住”。方向不同要早说：在实现完成前
就在任务讨论里提出更简单的路线，必要时关闭或重塑改动；关闭一个便宜的错误方向，
比审完它更省成本。改动合入后仍出错时，通常由落地作者修复，因为他拥有最多上下文；
事故复盘保持无责，分析的是系统而不是个人。

把事故、回滚与回滚演练作为流程改进输入。反复出现的问题补成回归测试、lint 规则、
兼容守卫或本文规则，不能只要求下一轮 reviewer 更严格。改动保持小而可逆，
这样更容易审查、调试和撤销。

### 5.2 Harness 实践

PocketChat 不是通用 agent harness；它是有可审计模型调用路径的 CLI。
Harness 经验用于围绕它的工作：

- **上下文**：只加载当前切片、兼容与安全页，不读所有历史计划；限制 prompt 与工具输出体积；
- **状态**：checkpoint 写清 commit 与证据路径；trace 是发生了什么的可持久记录，不是记忆；
- **观测**：记录 model/profile、命令、退出码、trace 哈希、wheel 哈希与已报告成本；
  未知成本写 `unknown`，不写 0；
- **评测**：用固定 fixture、一次只改一个变量；单次通过不是基准；
- **审批**：授权绑定仓库、版本、SHA、tag/Release 与外部动作，不授权另一棵树或后续版本；
- **恢复**：中断后先读分支、commit、diff、最近 trace、测试与 checkpoint；
  不重放副作用未知的请求。

## 6. 发布候选

发布候选是一个冻结元组：

```text
版本：
候选 SHA：
分支 / 仓库：
wheel 路径 + 大小 + sha256：
测试 / lint / 安装结果：
审查记录：
证据路径：
已知未验证：
```

冻结前工作树必须干净。SHA 记录后任何文件再变，都要重跑门禁；
旧绿灯不是新 SHA 的证据。

### 6.1 版本专属清单

每个版本的清单放在本包 `docs/` 下。v1.1 记录见 `release-1.1.md`；v1.0 候选放在
`release-1.0.md`，它是历史候选记录，不是通用流程。

## 7. 发布

发布分两部分：机器检查与用户的外部授权。

### 7.1 机器门

- 候选 SHA 上全量本地测试与 lint 通过；
- wheel 能构建、安装并报告预期版本；
- 兼容、安全与 clean-room 检查通过；
- 审查记录绑定候选 SHA、达到要求深度，且没有 open blocking finding；
- 必需的 CI run 归到同一个 SHA；缺失 CI 要记为未验证平台缺口；
- commit 与 annotated tag 身份通过仓库身份门；
- 发布树不含内部路径、凭据或本地状态。

### 7.2 授权

在创建公开仓库、首次 push、tag、GitHub Release 或上传包索引前，先取得明确授权。
记录仓库、版本、SHA、动作与渠道。机器门全绿只表示“可以交给用户判断”，不授权动作本身。

发布必须走仓库批准的发布路径。不得绕门手工推送，也不得用私人身份创建 annotated tag。

### 7.3 发布后回读

发布后重新读取：

- tag 对象与它指向的 commit；
- Release 的 draft/prerelease 状态与正文；
- commit author/committer 与 tagger；
- 发布树与 wheel 资产哈希；
- 适用时，从已发布产物做一次全新安装。

把实际 URL、时间、版本与未验证范围登记到 `docs/release-ledger.md`。
本地文件或构建日志不是远端发布事实。

## 8. 回滚与维护

| 情况 | 动作 |
|---|---|
| 候选在发布前失败 | 修复并重建；已发布 tag 永不回退 |
| 已发布 wheel 有缺陷 | 发布新 patch；保留旧 tag 与证据 |
| 凭据/路径/证据缺陷 | 停止发布，登记受影响版本，清理线上泄露面，发布修正版本 |
| 兼容性破坏 | 新大版本 + 迁移说明；按策略弃用 |
| CI 缺失或过期 | 记为未验证；不从另一个 SHA 复制绿灯 |

事故与回滚是审查流程的输入。反复出现的失败补成最小回归测试或静态守卫，
并写进审查证据；只有失败暴露出缺失规则时才更新本文。这样校准审查的依据是
实际失败，而不是每次虚惊之后无限增加检查。

公开章程的身份与素材规则适用。内部仓库路径、自动化名、worktree 路径与本地凭据
不得进入公开树或发布说明。

## 9. 文档地图

| 问题 | 所有者 |
|---|---|
| 产品方向与里程碑 | internal roadmap |
| 本流程 | `docs/development-and-release.md` 与中文对照 |
| 兼容/弃用 | `docs/compatibility.md` |
| 安全边界 | `docs/security.md` |
| CLI、JSON 与退出码 | `docs/cli.md` |
| 文件格式 | `docs/formats.md` |
| 字段证据与缺口 | `docs/pocketchat-evidence-matrix.md` |
| 版本专属候选 | `docs/release-1.1.md` 与 v1.0 清单 |
| 当前公开状态 | `docs/release-ledger.md` |

规则属于以上某个所有者时，链接过去，不要在这里复制。

## 10. 最小完成卡

复制到任务、commit 正文或交接；小改动可省略无关行，但发布不得省略完成判据与未验证范围：

```text
目标 / 不做：
完成判据：
风险档位 / 审查深度 / 轮次：
环境（cwd / Python / OS）：
命令 / 输出 / 退出码：
findings / 处置：
证据路径：
未验证：
回滚 / 下一步：
```

仓库内的 release smoke 入口会真实构建 wheel、检查成员、在干净 venv 安装并核对
版本与完整 task 命令面，同时输出机器可读证据：

```bash
python scripts/release_smoke.py --work-dir /tmp/pocketchat-release --json
```

检查要求恰好一个 wheel、5 个随包 schema、task/recovery 模块存在，且不含
`pocketchat/web/` 或 `pocketchat/server.py`；安装后的 `task ... --help` 命令面必须
完整。JSON 含稳定的 wheel 文件名、字节数、SHA-256、成员数、schema 与命令族。CI
运行同一脚本并上传 JSON 与 wheel 为 `pocketchat-release-smoke` artifact。默认使用
PEP 517 隔离构建，干净 runner 不依赖基础解释器预装 setuptools；
`--no-build-isolation` 只是显式离线/调试回退，仅在构建依赖已存在时使用。CI artifact
只证明其精确 SHA，不授权 tag、Release 或包索引发布。
