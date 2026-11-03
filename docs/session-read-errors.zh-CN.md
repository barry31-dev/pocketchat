# 会话读取错误

PocketChat 把会话文件当作证据：要么完整解析，要么作为类型化的
`session_file` 错误拒绝。UTF-8 损坏也属于这条契约，不能变成未捕获的
解码异常。

```bash
pocketchat show notes --json
# 退出码 2；stdout 只有一个 JSON 错误对象；stderr 为空
```

错误对象沿用现有 schema：

```json
{
  "error": "session_file",
  "message": "cannot read sessions/notes.jsonl: ...",
  "ok": false,
  "schema_version": "1"
}
```

读取器不会重写、截断或修复输入。任意 JSONL 文件的非法 JSON 仍按
“不是 PocketChat 会话”拒绝；UTF-8 损坏保留具体的读取失败原因。
两条路径都是拒绝，都不会漏出 traceback。
