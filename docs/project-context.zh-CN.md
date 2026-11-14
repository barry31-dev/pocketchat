# 跨会话的项目指导与上下文

本流程复用现有显式 memory v2、Session 与请求快照，不增加自动接受记忆、语义检索、
后台调度或新执行入口。

## 保存、检查和接受

从项目工作区执行。先保存带精确工作区身份的候选，再由操作者明确接受：

```sh
pocketchat memory propose "patches: keep public interfaces stable" \
  --workspace-id project-a --by operator \
  --store .pocketchat/project-memory.jsonl --json
pocketchat memory accept MEMORY_ID --by operator --verification not_required \
  --store .pocketchat/project-memory.jsonl --json
```

用第一条返回的 `item.memory_id` 替换 `MEMORY_ID`。自己的偏好或工作方法可明确选
`not_required`；这不表示外部事实已核验。敏感、未接受、已退役、过期或其他工作区的记录
不参与召回。配置示例见[英文说明](project-context.md)：`system_prompt` 位于配置根，
profile 明确启用 `context_projection_enabled`、`memory_recall_enabled`，并指定
`memory_recall_store` 与 `memory_recall_workspace_id`。示例端点不是内置服务；
真实调用仍需操作者已授权的端点与凭据。

## 重开前预览下一轮

已有会话后，用**当前配置**检查准备发送的问题：

```sh
pocketchat context sessions/project.jsonl --prompt "patches: what should change?" \
  --config pocketchat.json --profile project --json
pocketchat run "patches: what should change?" --session sessions/project.jsonl \
  --config pocketchat.json --profile project --json
```

`context --prompt` 要求非空文本和显式 `--config`。不读取 API key、不调用 provider，
不创建快照，也不写会话、trace、配置或记忆库。JSON 返回 `preview_messages`、
`pending_messages`、`preview_mode`，启用召回时还有当前召回摘要。当前配置整体替换
旧 trace 设置，不继承旧 profile 的召回许可；固定系统指令按 Session 重开方式恢复。
相对记忆库沿用 Session 的工作区选择：显式 task root，其次记录的 traces 所属工作区，
否则调用者工作区；从其他目录观察会话不会悄悄换到另一份记忆库。

默认历史窗口与下一轮运行一致：分层模式保留一个完整最近回合；旧字符预算模式保留两个
最近消息。预览同时检查字符限制与当前分层 JSON 字节限制。显式 `--budget` 或
`--keep-recent` 是**假设性预览**，只有实际运行采用对应选择参数才可能一致；
`--keep-recent` 在分层模式按回合计，旧模式按消息计。

不带 `--prompt` 的 `context SESSION --json` 保留原来的历史 trace 配置预览语义，
不能据此断言当前配置或未来问题会召回什么。预览与发送不是原子操作：两者之间配置、记忆、
时间／过期或会话改变都可能改变请求；实际发送内容以请求快照为准。

固定系统指令在多轮分层压缩后仍保留。召回消息只属于本轮，每轮重新读取并按状态准入，
不提升为持久系统指令。

## 撤销后不再主动召回，但不抹历史

```sh
pocketchat memory retire MEMORY_ID --store .pocketchat/project-memory.jsonl --json
pocketchat context sessions/project.jsonl --prompt "patches: continue" \
  --config pocketchat.json --profile project --json
```

已退役记录不参与**新的召回**。旧请求、快照、回复与会话消息原样保留，可能已引用旧指导；
这不是安全擦除，也不证明模型无法从历史推断原内容。词法召回不保证每个相关约束都能匹配
任意问题或装进预算。

## 可重复的离线演示

在源码仓执行：

```sh
python3 examples/project-context-demo.py --output /tmp/pocketchat-project-context-demo
```

输出目录必须新建或为空。夹具启动自己的 loopback HTTP 服务，通过真实 CLI 进程走候选、
接受、重开与退役，逐条比较预览和实际 HTTP 消息，并核对请求快照。另检查跨工作区、敏感、
未接受记录的排除；过期使用明确的未来 dry-run 时点，不改系统时钟。
`steps.json`、`requests.json`、`evidence.json` 保留本次命令与有限验收结果。

这只验证本地选择、进程边界和产物连续性，不证明真实模型遵循指导的质量、所有平台的安全
文件能力、安装包验收或可发布。
