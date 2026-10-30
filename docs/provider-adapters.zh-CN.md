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

## Session 集成

Session 在配置含 `config["provider_adapter"]` 时使用该适配器，否则使用默认适配器。
适配器名称记录在 `run.started.payload.config_summary.provider_adapter`，因此 trace
能说明这次用了哪条协议实现。

请求快照与真实调用必须走同一个适配器实例。这条不变量保证快照不会描述一个从未发出的请求。

## 错误契约

适配器抛 `ProviderError`，带稳定的 `error_type`、人类可读 message、可选
`status_code` 与 `retryable`。CLI 记录 `model.response.failed` 并结束 run；
PocketChat 不静默重试，也不自动切换 profile。失败记录带 `attempt_index` 与
`attempt_scope: "turn"`，用于把物理请求与结果配对；这两个字段不声明重试策略。

## 兼容

`v1.0.x` 保持 `ProviderAdapter` 方法名与规范化响应键稳定。新增可选响应键属于
向后兼容；删除或重命名方法、必需键，需要新大版本。`v1.0.x` 不捆绑第三方适配器。
