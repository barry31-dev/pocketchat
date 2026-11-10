# PocketChat v1.2 候选准备

这是原 `PC-FILE-RELEASE-1` 的发布准备，不是发布授权。发布事实只在
[发布台账](release-ledger.md)；精确范围和整张发布卡的完成条件在
[产品队列](slice-queue.md)。准备分支走既有日常授权，正式 tag/Release 不自动获得许可。

现有运行时与打包元数据更新为 `1.2.0`，不引入新的版本存储。复用已合入的 Session
授权/提案和可信终端完整 diff → apply → 回读 → 持久 undo；取消使未确认动作失效，
已写入回执仍为真相。保留既有 CLI/JSON/create-only 工具契约，不给模型自批/立即写入；
安全描述符不支持的运行时拒绝相关文件操作，不把 CI 跳过当作实测。

随包输入完成、审查后才冻结最终 SHA/wheel。复用 `scripts/release_smoke.py` 构建和
干净安装，保留精确源码 SHA、wheel 名称/字节/哈希、manifest、解释器、命令和退出码。
把黄金 fixture 单独复制到源码外目录，用该 wheel 的干净解释器、清除源码导入路径、
绑定 `--source-sha`；普通/dirty-Git 路径应验收，错误输出必须失败。命令见
[fixture 说明](file-work-validation.zh-CN.md)，不可转抄旧 v1.1 开发 wheel 的绿。

准备可在日常授权下合入，但原发布卡只在以下闭环成立后完成：该精确产物的真实
工具 provider/人工文件终端验收 → 该目标/冻结候选的具体 tag/Release 授权 → 同产物
发布与远端 tag 身份/Release/wheel 哈希/公开产物干净安装回读 → 台账和证据登记。
离线 provider 替身不证明真实调用，也不包含新增付费/凭据许可。包索引上传不是
GitHub Release 的隐形前置；冻结后输入变化必须另冻候选，不移动已发布 tag。

未满足的条件保留在原卡/候选，不将 source main、branch push、开发 wheel 或旧复审
当作整张发布卡完成。
