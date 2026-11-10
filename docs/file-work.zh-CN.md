# 聊天与文件工作：首个闭环设计

> 提案/确认/apply 与持久撤销已作为库级原语实现；Session 的可选 read/propose 与可信的
> 全屏/line 终端确认检查点也已接入。取消验收、可复用黄金路径、开发 wheel 验收与发布凭证仍待完成。
> 依据用户 2026-11-05 的首个聊天/读写闭环要求。
> 批准记录与顺序见 [slice queue](slice-queue.md)，发布事实只见 [release ledger](release-ledger.md)。
> [English](file-work.md)

## 1. 目标与边界

用户打开已有 `pocketchat`，聊天、显式授权文件、提出修改需求、查看 diff、确认执行、核对回执，
并可撤销。不要求手写 policy/spec JSON，也不要求切到 `task exec`。
建议候选版本为 v1.2.0，须另过兼容性与精确发布授权；本设计不修改版本。

复用 Session、profile/provider、request/trace、已有工具/预算/验收。未进入新文件模式的
run/chat/task、profile grants 和 create-only write_file 保持原义。不做 GUI、server、
通用审批框架、自由 shell、网络工具、目录/glob、大文件/二进制、递归委派或任意删除。

支持普通本地文件夹与 dirty Git 文本。原 task overwrite 的干净 Git 基线限制保持；
新模式以用户提案时的真实 bytes 为撤销基线，不恢复 Git HEAD。

## 2. 当前可信终端检查点

当前检查点只在 stdin 与 stdout 都是真实 TTY 时开放下列命令。全屏 UI 与 line fallback
共用活动 `Session` 所有的同一个 `FileWork`：

```text
/files
/files read a.txt
/confirm <grant-digest>
/files write a.txt report.md
/confirm <grant-digest>
总结 a.txt 并提出修改。
/diff                         # 或 /apply [change-id]
/confirm <apply-digest>
/changes
/undo <change-id>
/confirm <undo-digest>
/files revoke a.txt
```

- `/files read|write` 先展示所选 provider 告知和精确路径；第二次 `/confirm` 绑定告知、
  路径集、当前 profile 与授权代数后才激活授权。聊天里提到文件名不等于授权。
- 授权后的 turn 只提供 `read_file` 与 `propose_file_change`。模型不能授权、确认、apply、
  undo，也不能调用立即 `write_file`；模型 run completed 可与 pending 提案并存，不代表字节已应用。
- `/diff` 与 `/apply [change-id]` 都只展示完整 pending unified diff，不立即写入。C0/C1 与 Unicode
  format 控制符会转义，全屏渲染保留 diff 行空白。只有另行展示、绑定 digest 的 `/confirm` 才调用
  交易核心。apply 成功返回核心 readback receipt；若 apply 被拒，已确认的内存 pending 会被消费，
  持久记录继续作为事实源。
- `/changes` 只列同一 workspace、conversation 与 branch 的持久变更。`/undo <change-id>`
  要求重新取得该精确路径的 write grant，从持久 before/after 对象展示完整补偿 diff，并把确认
  绑定到已应用 receipt 后才调用核心 undo。
- 每条文件控制命令都重新取得现有 session writer。并发 owner、在飞 reply、授权/profile/session
  变化、过期提案或错误 digest 都拒绝动作；撤权会使已展示确认失效。
- pipe/非 TTY 输入不能执行任何文件控制命令（包括 `/files`），并在创建 Session、trace 或 change
  store 前拒绝。重启不恢复授权或批准；可信终端无需路径 grant 即可列出该 conversation 的持久变更
  元数据，但 undo 仍要求目标路径的 fresh exact write grant。

目录/glob 授权、session-wide undo、取消验收、可复用黄金 fixture 与开发 wheel 验证仍不属于本检查点。
整包需等这些必需路径接线并复审后才完成，本检查点不代表发布。

## 3. 记录与结果

候选 schema 为 `pocketchat-file-change.v1`。封闭字段与精确含义以英文页 §3 表格为准，
字段为 schema_version、change_id、owner、origin、policy_sha256、path、operation、before、
after、proposal_sha256、approval、events。owner 绑定 workspace 与已有 session/conversation/branch；
origin 绑定 run/tool-call/profile；before/after 绑定字节长度、hash、私有对象引用和目标身份/存在性。
operation 只允许 create/replace。proposal digest 不含自身、approval 和可追加 events。
已确认 undo 事件还必须记录 restores_change_id / restored_file_identity，绑定前一修改与恢复后身份。
未知版本/字段、重复身份、不完整绑定或非法迁移均拒绝。对象引用的实际 bytes 必须验证 hash，
不能只绑定可变文件名。只有 approval 和追加事件可演进；变更 owner/profile/path 重新提案。

触碰目标前必须保存 before image（新建则证明 absent）、候选内容和 prepared 记录。
内部目录 `.pocketchat/changes/` 禁止工具授权、模型读取/上下文与发布打包；目录拒绝 symlink/替换，
支持的系统上使用私有文件/目录权限。隐藏不等于 Git 忽略，文档必须提醒用户不要公开这些记录。
不自动清理仍可撤销的记录。

文件变更服务位于 `pocketchat.file_changes`、`pocketchat.file_change_store`
与尚未注册的 `tools.execute_propose_file_change` helper：保存封闭提案、
before/candidate 对象、可信确认、prepared/apply 回执及 identity-bound undo 回执。
重启后重新打开 workspace store，即可撤销已成功修改或计算 dependency-safe session
补偿顺序；内存缓存不具权威性。helper 本身仍不是默认模型/用户修改命令。

### Session read/propose 检查点

可信宿主可显式创建 `FileWork(workspace)` 并作为 `Session(..., file_work=service)` 传入；
普通 CLI/默认 Session 不自动开启。服务初始无授权，不导入 profile 既有授权，并绑定工作区 identity、
Session run identity 与 provider/profile/model。宿主必须先展示 `service.disclosure()`，再携带
匹配的 disclosure digest 调用 `service.grant(...)`，明确 exact read/write paths。
展示告知属于宿主责任；模型文本或文件名提及不能调用授权。仅支持精确文本文件路径（最多 64 个，
每个最多 1024 UTF-8 字节），拒绝目录、glob、内部存储与敏感路径。

该模式仅提供已授权 `read_file` 与 `propose_file_change`；立即 `write_file`、grant、apply、approve、
undo 均不向模型开放。提案声明不扩大 legacy task/profile 工具清单。读取与提案复用核心的
descriptor-relative no-follow 边界；缺少安全原语时拒绝该模式文件 I/O，不降级，也不改普通聊天/旧工具行为。

提案先持久保存 before/candidate 与记录，再返回一个配对的 `pending` result。模型得到有界
change/proposal/path/operation 引用，不是已执行声明；trace 的既有 effect enum 不增加 pending；提案 completed event 单独记录
`file_change_status=proposed` 与 `change_id`，不伪报 applied 或 read-only。完整 diff 从
`service.state()["pending"]` 的防御性副本读取，本检查点不批准它。
存在待审提案时，拒绝另一提案，并从下一次 provider offer 去掉提案工具。授权变更使 pending 展示与
request-policy binding 失效；即使目标变成目录仍可撤权。工作区替换、不同 Session 或
provider/profile/model 改变后，拒绝沿用旧授权，不向新目的地再发起该绑定的请求。
重启新服务不自动激活授权/批准；已存提案只是证据，不是重启授权。

可信终端检查点已把该服务接到两个现有终端控制器；apply/readback 与持久单变更 undo 只有在
另一次 digest-bound 确认后才执行。`PC-FILE-CHAT-1` 整包仍未完成：取消验收与可复用的开发 wheel
黄金路径还需接续；模型 run 完成仍不代表文件已修改。

边界：UTF-8，单原文件/候选文件沿用 16 KiB；完整 diff 64 KiB，元数据记录 64 KiB，
每 workspace 修改存储 16 MiB，每 turn 一个 pending。按编码后字节计数，包含事件和对象文件。
超额先拒绝新操作，不丢旧 before image；截断/不完整 diff 不能批准。

文件生命周期 proposed → prepared → applied → undone，其他出口包括 rejected、failed、
conflict、recovery_required。文件记录是事实源，session/trace 只引用 change_id。
模型 completed 与文件 pending 可以同时成立；提案不回报 written。apply/undo 不调用 provider，
后续 turn 只装配已确认回执的有界引用，不改写历史或伪造无配对 tool result。

## 4. 安全写入与撤销

复用路径/敏感/二进制规则、大小、hash/行数；不能把当前覆写函数直接接到模型批准后：
路径检查与固定 `.tmp` 并不是新事务的完整契约。使用 no-follow/目录描述符相对访问、
同目录排他临时文件、合作写者所有权、目标身份/hash 再核对、原子替换及独立写后回读。
缺必需原语或无法证明所有权时拒绝变更，聊天/只读按各自能力合同；原语存在不等于运行通过。
保留现有 OS/Python CI，首要实际验证环境 macOS CPython 3.13。

已实现核心使用descriptor-relative `O_NOFOLLOW` 遍历、0700/0600 私有存储、
绑定身份的独占 writer claim、同目录独占临时文件、文件/目录 `fsync`、replace
原子替换（create 使用独占 link）以及重新打开后的 readback。缺 primitive 时返回
`file_unsupported_runtime`。已观察到 rename/link 成功之后，回读拒绝或回执存储错误
返回 `file_recovery_required`、`effect_disposition=applied`，不能记成写前 conflict 或 no-op。
保留现有 before-image/记录证据并跳过正常 claim release；即使 claim 核验/保留再次失败，
这一恢复分类与不释放决定也不能被覆盖。存储不可用不证明回执已持久化或 claim 当前所有权，
更不授权重放或自动接管旧 claim。成功 undo 必须带显式有界批准标识，并匹配精确的持久 lineage。

hash 预检加原子替换**不是通用跨进程 CAS**。明确合作单写者/本地文件系统边界；
可观察的外部编辑/路径替换必须拒绝且 outside 文件不变，不承诺恶意进程、网络盘或任意崩溃 exactly-once。

applied 需要目标回读等于候选及回执持久化。显示 operation/path、before/after hash、行数、change id、
验收和 undo 指引。写成功、内容验收通过、用户满意分开；写后验收失败也保留真实 applied 事实和撤销入口。

undo 是受限补偿：当前目标身份/hash 仍匹配那次 after 且当前授权允许，才恢复 before bytes；
冲突保留用户新内容。新建文件只可移除自己排他创建且未被修改/替换的那一个，不提供任意删除工具。
两次修改 A→B→C 按 C→B→A 撤销。原子恢复可能生成新 inode：undo 回执绑定被恢复的前一修改
及新观察身份，下一次 undo 只接受这条已验证补偿关系，不能依赖旧 inode，也不接受任意同 hash 替换。
重复 undo 返回同一份持久回执而不重放。库从持久 session 记录计算确定性的逆序，首个冲突停止，
报告已撤销和未撤销项，不宣称原子回滚。

checkpoint/rewind 不改业务文件，文件 undo 不抹聊天/trace。重启从持久记录回查。
未闭合 prepared 先进入恢复盘点；bytes/身份/owner/事件能证明时才收口，不能证明则保持 recovery_required，
不凭相似内容认定成功、不静默重写或盲目回滚。

## 5. 取消与错误

复用 Session 取消及 issue #31 的任务确认边界，不新造 task 状态机。确认前取消不改用户文件；
本地提交段在可观察边界收口并报告实际副作用；取消下一步不隐藏/重放上一步，保留证据与显式 undo。

新 envelope 错误种类冻结为 file_permission_denied、file_stale_proposal、file_conflict、
file_confirmation_required、file_unsupported_runtime、file_too_large、file_store_full、
file_invalid_record、file_recovery_required。说明有界脱敏且有下一步。
适配旧错误但不改旧工具 error type/CLI exit code；UI 与服务对 success/pending/refusal 的理解一致。

## 6. 验收与发布

最小 fixture：两轮聊天、授权读取、preview/拒绝、确认修改、确认新建、文件/文本断言、undo、
冲突拒绝、重启回读；补非 Git/dirty Git、二改、授权/profile/session 切换、二进制/超限/越界/
symlink/配额/竞争、prepare/replace/receipt 中断负例。离线 CLI 测试操作真实文件，不 mock apply/undo；
错误输出必须让验收失败。TUI 与 line 测相同服务，人工体验另记。

使用 unittest.mock 的测试显式导入并独立可跑，不靠别的测试 import 副作用。
测试维护不新立产品卡；本规划不运行产品全套。

冻结候选后用**仓库外安装的 wheel**和一个已授权、真实支持工具的 provider 跑全路径。
记录精确 version/SHA、wheel hash、OS/Python、profile/model 范围、session/run/change 引用、文件 hash、
断言与退出码。router 只证明该路由，不冒充托管直连/其他模型；stub、真实 provider、人工终端体验分开。
本设计不授权新付费调用或凭据。

聊天交付包内完成可复用黄金路径 fixture 与开发验收，不再单开 E2E 卡；开发 wheel 不自动成为发布证明。发布包先完成
版本/打包/随包说明，再冻结最终 SHA/wheel，之后复用该 fixture 对这个产物执行最终真实 provider/
人工验收及必要全量/lint/精确 SHA CI/install。不能把旧版本/wheel 的绿转给新产物。

最终运行证据先存候选树外并绑定 SHA/wheel，发布后单独提交 validation/证据矩阵/台账，不移动 tag。
冻结后代码、打包输入或随包行为说明变化，必须按影响重新冻结/验证；单独追加发布后登记不要求重跑无关检查。公开 push/合入/tag/Release/包索引均经受控路径和各自精确授权。
GitHub Release 不隐含依赖包索引凭据。发布后回读 tag 身份、Release 状态、wheel hash 和公开产物干净安装，
再登记台账。

首版后再做有限目录授权展开、候选文件选择、多文件审批体验、诊断与实测平台/provider 缺口；
不要求先完成全部 memory/context/evidence 候选，本设计也不充当实现或验证结果。


## 7. 按交付包推进

按用户 2026-11-05「任务不要拆得太细」要求，剩余工作收敛为**两个实现包加一个发布包**：
安全修改与持久撤销 → 聊天接入与开发验收 → 最终产物与发布。精确 ID、依赖、写范围和旧卡吸收关系
只在 [slice queue](slice-queue.md#first-chat-and-file-work-sequence-2026-11-05) 维护，不重复已合入规划。
测试、schema、CLI、双语文档和必要复审属于包内工作，不单开卡。一个包可多次提交、跨会话续做；
会话预算耗尽就留检查点，租约到期仍按原所有权流程停止/重新领取，不能借大包无限执行或绕过审查。
持久撤销、可信确认、最终 wheel 验收和公开动作授权均不减项。

后续只在 [开发路线](agent-development-master-plan.zh-CN.md) 保留连续任务、记忆/Skill、有界协作的阶段目标；
远程执行需另行产品决策，不预建几十张卡，也不把框架迁移放在首版之前。
