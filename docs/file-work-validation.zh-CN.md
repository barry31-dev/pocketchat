# 文件工作流开发验收

`examples/file-work-demo.py` 是原聊天文件包的可复用离线黄金 fixture。只操作全新的
临时目录，通过双终端共用的可信控制器执行真实 Session、授权、事务、持久化与撤销；
仅注入离线 provider，不模拟 apply/undo，不联网、不使用账号、付费调用或用户文件。

覆盖两次聊天、明确 provider 披露和精确授权、配对读取、提案拒绝、完整 diff、
替换/创建和回读、取消后保留此前已写入效果、同一对话重启后的新授权及持久撤销、
非 Git/已有 dirty Git 基线恢复、外部修改后的冲突拒绝。`--wrong-output` 故意产生
错误 fixture 输出，必须 exit 1；成功 exit 0。已存在目录拒绝，不在同一目录重复跑。

执行命令及 wheel 链路见[英文操作说明](file-work-validation.md)。fixture 不自行
注入源码路径；使用仓库现有 `scripts/release_smoke.py` 构建并安装精确开发 wheel 后，
将 fixture 单独复制到源码外目录，清除 `PYTHONPATH`，用新环境解释器运行。将 wheel
哈希、源码 SHA、版本、平台/Python、命令、退出码及 fixture JSON 放在同一证据目录。
JSON 包含 session/run/change 身份、文件哈希、断言及终态；冲突负例保留外部修改。

离线源码、已安装开发 wheel、真实 provider、人工 curses 易用性与正式发布各自
记录。本 fixture 不是人工操作终端，也不证明任意崩溃/并发安全。安全描述符能力
不支持的平台按测试条件明确跳过，不将 CI 绿色外推为被跳过用例已运行。开发 wheel
不认证另一个版本/产物，未创建 tag、Release 或上传包。
