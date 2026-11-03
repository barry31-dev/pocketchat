# 记忆检索核心（仅候选）

归属：`pocketchat.memory_retrieval`。结果版本：
`pocketchat-memory-retrieval.v1`。

这是针对已校验 `pocketchat-memory.v2` 记录的纯函数核心。核心本身不读 store、不调
provider；本切片新增显式只读 `memory recall --dry-run` 命令和 opt-in Session 投影接线，
不改旧 `memory search` 的子串路径，未开启召回模式时也不注入记忆。

## 先准入，后匹配

先校验记录并做准入门控，再做查询匹配和 Top-K。工作区不一致、admission 不是
`accepted`、记录标了 `sensitive`，或者 `expires_at` 早于等于调用者显式传入的 UTC
`now`，该记录都排除。排除结果只带稳定的 reason code 和 memory id，不带被排除正文。

事实验证与召回准入仍是两件事。选中项会携带原记录的 verification 状态和摘要绑定的
source refs；检索不会把它们升级成已验证事实。

## 固定词法规则

`tokenizer_version` 固定为 `lexical-zh-en.v1`。NFKC 与 `casefold` 只作用于检索文本和
tags，英文词是 `[a-z0-9]+`；连续 CJK 字符按顺序切成二字片段，单个 CJK 字符就是一项。
原文永不被改写。

排序键依次是：规范化完整查询短语命中、命中查询项数、命中显式 tag 数、`updated`
较新优先、`memory_id` 升序。不使用未经校准的综合分。

## 双预算 excerpt

选择同时受 `max_results`、UTF-8 `max_excerpt_bytes` 总量和 `max_recall_tokens`
约束。token 是 `chars4.v1` 估算值：`ceil(utf8_bytes / 4)`，不是 provider usage。
选中项只返回有界 excerpt、bytes/token 计数、截断标记、命中项、命中 tags 与来源元数据，
不返回完整记忆正文。

`status: complete` 且 `empty_reason: no_matches` 与
`status: budget_exhausted` 分开；后者带
`error_type: memory_retrieval_budget_exhausted`，且至少有一条准入命中无法放入预算。
`status: partial` 分项报告截断或当前页遗漏原因。

## 稳定续查

`next_cursor` 是带版本的不透明 cursor，绑定精确查询、tokenizer 和有序命中记录签名。
命中记录被重排、增加、删除或修改后仍复用旧 cursor，会报
`memory_retrieval_cursor_stale`，不会静默改变页边界。结果分别报告 cursor 之前跳过的
条目，以及 `max_results`、byte 预算和 token 预算造成的遗漏。

这个核心离线且确定性。它不是语义相关性、向量索引、安全擦除、事实核验或发布证据。
只有同时显式开启 layered context projection 与 `memory_recall_enabled`，并给出精确 v2
store 与 workspace id，Session 才会自动注入召回内容；否则召回保持 dry-run/只读。

## 显式召回与投影接线

`pocketchat memory recall <query> --workspace-id <id> --store <path> --dry-run --json`
读取一个受 workspace 约束的 v2 store，返回与核心相同的选中项、排除项、预算与 cursor
语义，绝不修改 store。JSON 响应中的 `context_message` 只包含有界 excerpt 与元数据。

当 profile 同时显式开启 `context_projection_enabled` 与 `memory_recall_enabled` 时，
Session 使用当前 prompt 作为召回查询，并在投影请求前加入一条有界 `memory_recall`
system message。投影 sidecar 与 request-start 元数据绑定 query digest 和选择计数。
跨 workspace 记录在排序前排除；任一模式关闭时不注入召回内容。`context preview` 对给定
pending prompt 执行相同选择。
