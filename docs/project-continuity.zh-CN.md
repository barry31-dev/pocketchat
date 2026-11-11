# 通过显式记忆保持项目连续性

PocketChat 可以把一条经过审阅的项目决定带到后续 Session，但不会把记忆自动当成权威。流程刻意拆开：

1. `memory propose` 只向明确指定的 v2 存储写入一条**未验证候选**。
2. `memory accept` 是独立的操作人动作。对显式用户记录，`--verification not_required` 表示这条偏好或决定不声称自己是经独立核验的事实。
3. profile 必须同时显式开启分层上下文投影与 v2 recall，并绑定准确的工作区身份和存储。
4. 每个新 Session 都在请求快照与投影 sidecar 中记录被选中的 memory ID、revision、内容摘要与来源引用。
5. 未接受、已退休、已过期、敏感或属于其他工作区的记录都不会被投影。

这是有界的项目连续性，不是自动采集、模型审批、向量检索、跨设备同步或安全删除。

## 创建并审阅候选

在项目工作区内运行，并明确指定 v2 存储：

```bash
pocketchat memory propose \
  "Project Orion uses Python 3.13 and keeps public command names English." \
  --workspace-id project-orion \
  --by operator \
  --category project_decision \
  --tag project-continuity \
  --store .pocketchat/project-memory.v2.jsonl \
  --json
```

结果包含 `admission_status: "candidate"`、`verification_status: "unverified"`、空的 `accepted_by`、revision `1`，以及与摘要绑定的 `memory:propose:<memory-id>` 来源引用。该命令不会读取或改写旧 v1 存储。遇到畸形元数据、不安全路径、符号链接或非普通文件、损坏或重复的 v2 记录、超限存储、以及竞争的首次写入时，它会拒绝并保留既有字节。

审阅候选后，再单独接受准确 ID：

```bash
pocketchat memory accept <memory-id> \
  --by operator \
  --verification not_required \
  --store .pocketchat/project-memory.v2.jsonl \
  --json
```

接受动作不会发明来源，也不会把内容改写成“已独立核验”。如果候选仍需要其他验证状态，就不要传 `--verification not_required`；未验证候选不能直接接受。

## 显式开启 profile recall

profile 必须显式开启分层上下文投影和 memory recall，并绑定同一存储与工作区身份。`profile set` 会替换一份完整 profile，所以要同时传入必需的 provider 字段和其他所需选项：

```bash
pocketchat profile set local \
  --base-url http://127.0.0.1:8123/v1 \
  --model example-model \
  --api-key-env POCKETCHAT_API_KEY \
  --context-projection \
  --memory-recall \
  --memory-recall-store .pocketchat/project-memory.v2.jsonl \
  --memory-recall-workspace-id project-orion
```

之后使用该 profile 的 `run`、`chat` 或重新打开的 Session，会针对当前 prompt 做有界只读检索。如果命中记录，准确请求快照会带一条 `pocketchat-memory-recall.v1` system message，其中包含 `memory_id`、`revision`、`workspace_id`、`content_sha256`、来源引用和有界摘录。trace 记录 recall 摘要，投影 sidecar 再把摘要绑定到请求摘要。没有符合条件的记录时，不会注入 recall message。

若只想检查选择结果而不发模型请求，可使用只读预览：

```bash
pocketchat memory recall "Python naming" \
  --workspace-id project-orion \
  --store .pocketchat/project-memory.v2.jsonl \
  --dry-run --json
```

## 撤销未来召回

退休动作是显式的，并只影响后续检索：

```bash
pocketchat memory retire <memory-id> \
  --store .pocketchat/project-memory.v2.jsonl \
  --json
```

退休记录仍保留在 v2 存储中作为生命周期证据，但不会进入新的请求。已过期记录和绑定到其他工作区身份的记录也会被排除。这些动作不会删除历史 trace、请求快照、session、备份或已经发送的请求中的内容。`memory forget` 可以从活动存储移除记录，但不承诺安全擦除。

## 离线证据 fixture

无需 provider 或网络即可运行：

```bash
python3 examples/project-continuity-demo.py --output /tmp/pocketchat-project-continuity
```

fixture 通过公开 CLI 提议并单独接受一条记录，然后分别为未接受、已接受、异工作区、已过期和已退休场景打开独立 Session。它会写入每次 trace、准确请求快照和 `evidence.json`，并检查只有“已接受 + 同工作区”场景携带与来源及 revision 绑定的 recall message。

该 fixture 只验证本地编排和证据形状；未验证真实 provider 的相关性与回答质量、任意项目、跨平台人工操作、安全删除、tag、Release 或包发布。
