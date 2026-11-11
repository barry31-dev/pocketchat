# Provider 适配器（v1.0）

Provider 边界刻意保持很小：Session 只向一个适配器索取请求 payload 与规范化响应，
不会绕过它调用 provider 专属函数。

## 契约

`pocketchat.provider.ProviderAdapter` 定义：

- `build_payload(messages, config)` 返回实际要发送的精确 JSON body。
- `complete(messages, config, api_key)` 返回规范化响应；失败时抛 `ProviderError`。

默认实现是 `OpenAICompatibleAdapter`，使用标准库 `urllib`，向 OpenAI-compatible
`/chat/completions` 端点发送非流式 `POST`。

规范化响应必须包含：

```json
{
  "content": "assistant text",
  "finish_reason": "stop",
  "model": "provider-model",
  "status_code": 200,
  "usage": {
    "input_tokens": 1,
    "output_tokens": 1,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "reasoning_tokens": 0
  }
}
```

Provider 未报告 usage 时用零表示，绝不估算。新增 `usage_reported` 布尔值区分「provider 返回了 usage 对象」与「0 是缺报占位」；既有 `usage` 字段及 0 语义不变。`content` 可以是空字符串。
`status_code` 在存在 HTTP 状态时记录。

流式响应使用同一套规范化形状。text/reasoning 增量按到达顺序拼接；tool-call 增量按
`index` 累积，`id`、`name` 与 arguments 字符串可分多个事件到达。只有完整 arguments
字符串解码为 JSON object 后才返回该 tool call。格式错误、identity 冲突或不完整 fragment
统一以 `malformed_response` 失败，绝不执行半截调用。只有工具、没有文本的响应合法，
表现为空 `content` 与 `finish_reason=tool_calls`。usage event 可以带 `choices: []`，
在已观察到正文或 tool call 后合并到同一结果；只有 usage、没有正文或 tool call 的流会
被拒绝，不生成空 assistant 回复。

## Session 集成

Session 在配置含 `config["provider_adapter"]` 时使用该适配器，否则使用默认适配器。
适配器名称记录在 `run.started.payload.config_summary.provider_adapter`，因此 trace
能说明这次用了哪条协议实现。

请求快照与真实调用必须走同一个适配器实例。这条不变量保证快照不会描述一个从未发出的请求。
流式标志也属于实际请求体：当 `on_delta` 显示回调启用时，Session 会在准入、写快照和
调用 provider 前对同一个 payload 标记 `stream: true`，因此快照记录的是实际发送的流式请求，
而不是 `build_payload()` 返回的非流式默认值。

Agent TUI 把每个用户回合当作一次 session run。该 run 结束时 session writer claim 会释放；
下一回合惰性取得新的 claim，同时保留同一个内存会话与累计 token 计数。进程重启时仍从 session
文件恢复。持久化失败会在回复旁明确显示为 trace-only warning，不会被写成一次成功的 session 写入。

## 错误契约

适配器抛 `ProviderError`，带稳定的 `error_type`、人类可读 message、可选
`status_code` 与 `retryable`。CLI 记录 `model.response.failed` 并结束 run；
PocketChat 不静默重试，也不自动切换 profile。失败记录带 `attempt_index` 与
`attempt_scope: "turn"`，用于把物理请求与结果配对；这两个字段不声明重试策略。

## 兼容

`v1.0.x` 保持 `ProviderAdapter` 方法名与规范化响应键稳定。新增可选响应键属于
向后兼容；删除或重命名方法、必需键，需要新大版本。`v1.0.x` 不捆绑第三方适配器。
