# 协作式任务取消

本未发布 API 只在已确认的步骤边界取消显式授权的 TaskPlan，不安装信号处理器、不终止
正在进行的命令或 provider 请求、不改 CLI、不增加第二个执行器。进行中的步骤可能先完成，
取消请求才被观察到。英文合同见 [task-cancel.md](task-cancel.md)。

## 身份与请求

`TaskCancellation(spec_sha256, plan_sha256, owner_token=...)` 绑定精确 Spec/Plan 摘要及
32 位小写十六进制 owner token；不传 token 就生成。`request(...)` 先检查三个字段再设置
不可撤销的请求标记：首次有效请求返回 `True`，重复有效请求返回 `False`；其他 Spec、Plan
或 token 拒绝且不改变标记。原 token 不进入错误或报告，只保留 SHA-256 标识。

这是进程内调用方能力，不是账号鉴权、租约、跨进程通道或线程同步保证。不给 cancellation
参数时，既有执行行为保持原样；错误 owner 在读历史证据、调用 runner 或产生文件前拒绝。

## 边界与报告

执行器在第一步、后续步骤之前及最后一步成功之后观察取消。已完成的子任务、trace、session、
command archive 和文件变更保持不变，未开始后缀记为 `not_executed / task_cancelled`。
只写一份既有 execution report，再抛出携带 `report` 和 `results` 的 `TaskCancelledError`。
已经完成的子任务仍为 completed，不能为了“取消”改写已发生的事实；取消也不是验收通过。

报告新增可选封闭对象 `cancellation`：`schema_version=pocketchat-task-cancellation.v1`、
`state=cancelled`、精确 `spec_sha256` / `plan_sha256`、`owner_sha256` 和 `boundary_index`。
边界值是完整已完成前缀长度，前缀必须有全部 artifact hash，后缀只能有身份、动作和未开始标记。
重复 ID、字段/摘要矛盾、错误或证据问题、失败前缀、已开始后缀均拒绝。

如果 runner 抛异常、步骤失败或证据不足，仍保留 failed/unknown，不将取消请求包装成可恢复。
自定义 runner 没有完整 artifact 绑定时，产生普通执行错误与报告，不宣称安全边界。发生副作用后的
确认边界还依赖平台提供 descriptor-relative + no-follow 的安全证据读取；Python 未提供该原语时
（包括当前 Windows CI runner），执行器按同一普通错误/报告 fail closed，不冒充已确认边界。
副作用前取消与默认执行不受此限制。checkpoint schema 不变，精确 report hash 绑定取消对象。创建 checkpoint 兼容当前顶层和旧嵌套
command archive hash；两处声明不同则拒绝。

不支持同时传 cancellation owner 与 `model_repair="model"`：不论 owner 是否已请求取消，
执行器都在读取 artifact、调用 provider/command 或写 execution report 前以固定
`TaskExecutionError`（`cancellation cannot be combined with model repair`）拒绝。
成功 repair retry 的证据身份与原失败 archive 不同，本切片不放松成功核对来冒充可恢复。
不传 cancellation 的既有 model repair 行为不变；两者组合须另行批准完整 retry/repair 证据链契约。

最终模型响应期间收到取消也不能抹去预算失败：已写入的 bytes 不撤销，unknown usage 不当作零，
已报告的超限消耗不退款。恢复耗尽的账本不能新调 provider，已确认命令不再执行。
checkpoint 可以把失败步骤登记为 pending；该盘点不代表它已获准恢复。

取消和恢复不重验、不替换历史 acceptance receipt。已完成子任务上的失败验收在 execution report
形成累积后继后仍为 failed；执行完成本身不是验收通过。

## 显式恢复与不重复副作用

取消报告的恢复必须新建同 Spec/Plan 的、尚未请求取消的 owner。遗漏 owner、重用旧 token，
或新 owner 已请求取消，都在新步骤或报告写入之前拒绝。它不是自动重新开始。

直接选报告或使用既有 checkpoint resolver 后，仍运行 [原恢复核对](task-recovery.md)：
重新读取已完成证据、验证文件副作用、恢复已声明 token budget、检查后缀输出尚不存在，
只执行未开始后缀。完成全部步骤之后才观察到取消时，验证恢复是零新增文件的 no-op。
后继 cumulative report 绑定前身摘要；读 task report 不重复计数旧命令。旧报告仍用
`step_boundary` 规则，不据缺少取消字段推断曾发生取消。

## 验证范围与限制

聚焦用例覆盖 foreign/idempotent 请求、命令/provider 前取消、确认命令后取消、最后一步后取消、
checkpoint 与 direct resume、历史证据变化、失败/未知副作用、封闭字段及默认行为。
命令使用真实本地有界 Python 子进程，恢复后副作用计数为 1；provider 仅为进程内 stub。
实际测试数量、解释器与未验证范围见 `review-task-cancel-v1.json`，不由本文承诺。

仅覆盖协作式单写者本地核对，不承诺任意崩溃后 exactly-once、不防御同时替换全部证据与授权的
恶意方，也不保证核对之后没有并发改写。未声称 OS 信号取消、任意子进程终止、真实 provider、
新 CLI、deadline 接线、tag/Release/上传。回滚不改写旧报告；旧 reader 可能拒绝新增字段，
不能把拒绝写成“已经理解并恢复”。
