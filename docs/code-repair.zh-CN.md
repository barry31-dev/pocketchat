# 来源绑定的代码修复

复用现有 `task exec --repair-model`、声明式命令、受保护编辑、执行报告与恢复；
不新增代码 agent 运行时、shell 或自动授权。契约和本轮审查见
[review-code-repair-v1.json](review-code-repair-v1.json)。

## 离线 Swift 夹具

在产品根目录，使用已经安装的 Swift 工具链和 Git：

```bash
python3 examples/code-repair-demo.py --output /tmp/pocketchat-code-repair-evidence
```

输出目录必须新建或为空，并位于现有 Git 检出之外。夹具建立自己的临时 Git 基线，
生成一个无外部依赖的 Swift Package，以显式模板串行执行 `swift test --jobs 1`。
每次命令超时 180 秒；不安装依赖、不启动模拟器、不使用服务器、联网 provider 或凭据。
命令模板不是操作系统沙箱，声明可执行文件的副作用仍由操作者负责。

离线 provider 从请求里实际绑定的源码中修复一个已知的半开区间错误。真实 Swift 测试先失败，
受保护替换后，同一模板仅重跑一次。绿灯证明该夹具的编译/测试和本地编排，
**不证明真实模型诊断质量或任意代码正确性**。CI 的便携 Python 夹具测试使用另行显式声明的
小型本地校验器，不能称为 Swift 编译。正向 acceptance / 无重放恢复夹具仍要求安全的
descriptor-relative 读取；平台缺少该能力时，运行时明确返回 `unsupported_platform`，
正向测试按能力跳过，不用弱化 reader 来换取通过。

`evidence.json` 汇总七项检查；源码、测试、`repair.diff`、spec/plan/acceptance、两次命令的
独立 trace/archive、修复请求/回执、执行报告、`execution.json` 与 `resume.json` 可原件回查。
修复回执上的 `task_acceptance_pass` 仍为 false；独立源码/命令验收另算，不把 exit 0 当发布。

## 已有项目里的使用边界

先声明缺陷、明确输入/overwrite/write 路径和命令的 argv/cwd/超时/输出限额，
再批准精确 plan digest 并选择是否启用 `--repair-model`。模型不得另选命令或带 shell。
修复请求开始前冻结可安全读取的授权 UTF-8 源码，并携带正文、路径、字节数、SHA-256
和真实失败指纹。未使用的脏/缺失/不安全路径不会转发；选择这样的目标即拒绝。
整个请求受上下文预算和既有 admission 约束；配置凭据出现在源码里时，在构造 provider
payload 前拒绝。请求文件在 provider 调用前先固定字节摘要，并在任何编辑/复测之前、
以及最终回执生成前再次核对；请求被替换、修改或不可读时拒绝，不把变化后的字节冒充
原始修复证据。源码/日志只作为不可信数据，不授予权限。

模型只可返回一个封闭的单文件替换动作；应用时复用**请求前**快照。
即使用户在模型返回期间修改并提交文件，也不能把新内容当成旧修复的基线。
复测仍是原模板，仅一次；回读实际 diff、候选字节和独立验收。

`--resume-report` 加 `--report-sha256` 绑定精确执行报告。修复后的完成步骤指向成功 retry，
同时绑定首次失败、修复请求/回执和修复后的文件；证据或源码变化后，先拒绝再谈接续。
完整任务的恢复是校验后的 no-op，不重放命令、模型调用、编辑或后继报告。
旧修复记录缺少完整绑定时不静默升级为可恢复。其它命令/读写边界维持原契约。

真实 provider 质量、任意项目、其它平台的人工 Swift 运行、正式 tag/Release/包上传均未据此验证。
底层契约见 [命令](task-command.md)、[编辑](task-edit.md)、
[修复](task-repair.md)、[恢复](task-recovery.md)；英文说明见 [code-repair.md](code-repair.md)。

### 当前本地验证边界

2026-11-11，首次本地 Command Line Tools 运行在测试前无法解析 `XCTest`；仅显式
传入完整 SDK 仍失败。通过操作者声明的命令模板，使用 `/usr/bin/env` argv 包装
显式绑定已有完整 Xcode Swift、SDK 与 `DEVELOPER_DIR` 后才成功；命令运行器
不会整体继承父进程环境。

实测环境为 macOS 27 arm64、Apple Swift 6.4、单构建 job。首次 XCTest 断言失败
（exit 1），受保护编辑后，同一模板的一个 XCTest 通过（exit 0）；七项夹具证据
检查全部通过，包括源码/diff 绑定及无重放恢复。修复 provider 是确定性离线替身，
没有验证真实模型诊断质量。全局 developer 选择仍为 CLT，没有安装组件；默认夹具
仍要求本地已有可用测试工具链，不会自动修复宿主配置。
