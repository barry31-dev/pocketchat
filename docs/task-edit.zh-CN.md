# 受控覆盖 v1

本未发布切片允许 TaskSpec 授权替换一个已存在的 UTF-8 工作区文件。它不是通用
patch 或删除面。编辑器把每次替换绑定到先前快照读取的精确字节，再委托现有
`execute_write_file` 完成原子写入。公开 v1.1.0 制品不含该模块。

## 授权

TaskSpec 可增加可选 `overwrite_paths` 数组。每一项也必须出现在 `inputs` 与
`tools.write_file.allow_paths` 中；仅进入 write allowlist 不等于允许覆盖。
编辑器只接受该精确列表内的路径。

## 快照与应用

`snapshot_overwrite()` 记录一个既有普通文件的规范化路径、SHA-256、字节数与
UTF-8 文本。符号链接、敏感或二进制后缀、NUL 字节、坏 UTF-8、超限文件以及
Git 中已 dirty / untracked 的目标均拒绝。

`apply_overwrite()` 在写入前立即重读文件，digest 与快照不同即拒绝。真正替换调用
`execute_write_file(..., overwrite=True)` 并只传 TaskSpec 的精确 write allowlist，
沿用其原子临时文件替换与 trace 事件。结果带原/新 hash、字节数以及有界的增删行数。
它不删除文件，也不应用自由 patch。

拒绝 dirty/untracked 目标，避免任务把预先存在的修改冒充为自己的改动。该校验只覆盖
一个 Git worktree 目标，不覆盖 Git 之外的并发文件写者；最终防陈旧覆盖仍靠 hash 重检。
