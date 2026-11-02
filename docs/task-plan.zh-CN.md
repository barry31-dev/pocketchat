# TaskPlan v1

TaskPlan v1 是绑定到一份操作者编写的 TaskSpec 的、由模型生成的**不可执行计划**。
它是未发布源码，已发布的 v1.1.0 制品不含这项能力。解析计划从不执行 step、
调用工具或扩大 TaskSpec 权限。

## 形状

```json
{
  "schema_version": "pocketchat-task-plan.v1",
  "spec_sha256": "<规范化 TaskSpec 的 sha256>",
  "steps": [
    {
      "id": "read_input",
      "objective": "读取已声明输入",
      "action": "read_file",
      "inputs": ["input.md"],
      "expected_evidence": "有界文件内容",
      "depends_on": [],
      "retry_limit": 0
    }
  ],
  "revision": 0
}
```

根对象与每个 step 都是封闭对象，step id 必须唯一，且每个 step 至少声明一个 input。依赖只能引用更早的 step，
因此无法表示循环。文件动作必须由 TaskSpec 授权，inputs 必须留在该工具的
allowlist 内。`memory_search` step 恰好携带一个字面查询，不把查询伪装成文件路径。
`retry_limit` 受现有 provider 尝试上限约束，且它本身不会重试 step；在恢复拥有独立证据契约前，计划生成要求每个 step 的 retry_limit 为 0。

`spec_sha256` 按规范化后的 TaskSpec 字节计算。Spec 一旦变化，即使 step 文本
完全相同，旧计划也不可执行。计划自身另有基于规范化 plan 字节的独立 digest。

## 预览

```bash
pocketchat task plan preview plan.json --spec spec.json --json
```

预览会按指定 TaskSpec 重新校验计划，不写 task record、trace 或 session。
坏 JSON、spec hash 改变、未知字段、未知动作、越权与依赖错误均 exit 2。

## 有界生成

不执行计划，只生成一份可审阅计划：

```bash
pocketchat task plan generate --spec spec.json --run-id plan-review \
  --config pocketchat.json --profile local --json
```

默认把普通 TaskPlan 写到 `.pocketchat/task-plans/<run-id>.json`，也可用
工作区相对的 `--out` 指定新路径。输出的 `plan` 是计划路径，`plan_sha256`
可直接交给 `task exec`。计划本身不是执行授权；该命令不执行 step、不调用工具、
不创建 session、task record 或验收回执。

`generate_plan()` 接收 provider adapter、config 与显式 request 目录。调用
`complete()` 之前，它先用独占创建记录每个请求正文的 request snapshot。合法计划
只写入一次 plan snapshot，并携带自身 digest 与本次使用的 request snapshot 文件名；
两类 snapshot 都不覆盖已有文件。普通计划文件也独占创建，随后用同一 TaskSpec
从磁盘回读校验，输出的 digest 由回读内容重新计算。

只有非法计划获得一次修订。修订会对着同一份不可变 TaskSpec 重新解析，因此不能
扩大路径、工具或 retry 上限，且必须声明 `revision: 1`。provider 失败不重试、
不修订。最终没有合法计划时，不产生可执行计划，也不写 plan snapshot。

测试只使用本地 stub adapter。本格不调用真实 provider，也不能据此声称计划在
任意真实任务上都“好用”。provider 失败退出 1；非法输入、输出/快照已存在、
路径越界或生成契约拒绝退出 2。失败运行可能留下记录实际请求的 request snapshot，
但不会留下普通计划或 plan snapshot。一次解析失败后成功会留下两份 request
snapshot 与 revision-1 计划，且不会发送第三次请求。
## 命令 step

只有 TaskSpec 声明了命令模板时，TaskPlan 才可使用 `run_command`。它的
`inputs` 必须且只能包含一个已声明模板 id；计划不能追加 argv、shell 字符串或
环境变量。执行时必须显式传入命令 runner，且命令证据与任务验收分开。
