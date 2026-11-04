# 会话写入所有权

显式 `--session` 路径同一时刻只允许一个活跃写者。所有权是会话旁的
`<session>.writer` 独占文件，在创建 header、trace 或发送任何 provider 请求之前取得。
owner token 独立于 run id，因此两个沿用同一 run id 的旧视图也不能继续写重复 turn。

claim 与每次 append 使用同一个 canonical 路径。取得所有权时会绑定会话文件身份；
会话或 claim 路径被替换即 fail closed。所有权不从 PID 推断、不使用 TTL，
遇到无法读取或属于别的所有者的 claim 时绝不自动抢占。

```text
取得 claim -> 打开/接管会话 -> 绑定文件身份
-> 运行 -> 写 terminal trace event -> 只释放自己的 claim
```

初始化失败只释放本进程创建的 claim。正常、失败或取消的 run 在 terminal trace event
之后释放。不同会话路径各自独立，可以并发工作。

## 失败行为

已有 claim 时，`Session` 在发送 provider 请求之前按既有配置拒绝路径报错。claim 文件保留，
供操作者检查；进程不猜测其 owner 是否仍存活。后续正常写入必须由操作者显式移除残留 claim，
不能静默修复。

本契约只覆盖单机和单个显式会话路径，不是分布式锁、OS 沙箱或历史改写许可。无 session
的 run 与只读命令不受影响。
