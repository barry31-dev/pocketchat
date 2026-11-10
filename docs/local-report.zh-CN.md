# 本地资料、报告、修订与恢复

> **已复审源码候选**：Session 结果现与固定 trace 的读取完成事件交叉绑定，task record
> 工作区根也与授权读取 policy 交叉绑定。两个红灯负例已转绿；macOS CPython 3.9.6
> 上聚焦 63 项测试与离线 fixture 通过。准确候选 CI 与产品 main 回读仍待完成，且这不是
> 正式发布。见 [审查记录](review-local-report-v1.json)。

`PC-LOCAL-REPORT-1` 沿已委托的单 agent 本地资料路线，复用原有 `task exec`、
TaskSpec/TaskPlan、Session、文件工具和恢复，不另造研究运行时。
目标、不做项、准确十路径、验收与回滚见 [英文契约](local-report.md) 和
[审查记录](review-local-report-v1.json)；发布状态只在 [发布台账](release-ledger.md)。

## 可直接运行的离线闭环

在产品根目录运行：

```bash
python3 examples/local-report-demo.py --output /tmp/pocketchat-local-report-evidence
```

输出目录必须新建或为空。实走 task CLI、授权、Session、真实文件读写、执行回执与恢复；
仅配置加载和 provider adapter 在进程内注入，profile 选择仍经真实校验。
不启动服务、不联网、不用账号或凭据、不安装依赖。产物包括源资料、spec/plan、
trace/session、首版报告、单独命名的修订版以及含六项断言的 `evidence.json`。
离线 provider 根据实际收到的来源内容计算 fixture 报告，不靠预写资料结论；
它只证明接线和来源追溯，不证明真实模型的分析、引用质量或事实正确性。

## 实际资料如何接到报告

每份资料在 TaskSpec 中明确授予读取，一个 read step 对应一个输入。
将问题写在授权 TaskPlan 的报告 step objective 中；write step 必须明确依赖所用的
read steps，输出单独列入 create-only allowlist。仍通过准确 plan SHA 授权执行，
问题、权限或 plan 改动后必须使用新摘要。文件正文不是工具或权限指令。

报告收到的是已完成读取留下的 UTF-8 内容、工作区相对路径、字节数和内容 SHA-256，
以及绑定的 Session/trace 位置与摘要。原有 policy/task-record/trace/session 证据校验必须
一致，且来源路径必须是实际已完成读取的路径，不能借另一份 allowlist 文件顶替。
模型自己的总结、未声明依赖的结果都不当来源证据。

它们是**历史读取快照**，不冒充当前文件的新核验。恢复即便遇到资料随后变化，仍只用原快照，
不重读、不重放已完成写入。证据改变/缺失、证据软链接、身份冲突、截断内容或总上下文超限，
都在报告 provider/写入前拒绝；12,000 字符的来源 JSON 和原 step context budget 双重限界，
不静默裁剪。原输入缺失会停在 read，而不是补造资料。

用户要求修订时，将旧报告明确授予读取，选择新输出名和新的 step IDs；首版保留不覆盖。
检查真实报告内容与引用；需要时使用现有 [产物验收](task-acceptance.zh-CN.md)。
边界接续保存 execution report 路径与字节摘要，并同时给 `--resume-report` / `--report-sha256`；
具体见 [恢复契约](task-recovery.zh-CN.md)。

新增的是 prompt 内的 `pocketchat-task-sources.v1` 数据，原 metadata / execution-report schema 不变。
不依赖 read 的写步骤和只读步骤保留原路径。缺完整绑定读取产物的自定义 runner 或旧产物
不能承担新的资料到报告交接。

## 未验证范围

本轮 focused real-file/CLI fixture 验证本地交接、修订、历史与恢复拒绝。
本地全量 release build、真实 provider、人工终端 UX、其他本地平台、联网调研与复杂格式
**未运行**；CI 只证明准确候选及对应 workflows。不创建 tag/Release，不上传包，
这次源码变动不重新认证此前已冻结的发布制品。
