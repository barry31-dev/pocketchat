# 有界 JSON 输入

`pocketchat.json_input` 只解码调用方**已经取得**的一段 bytes，并要求结果恰好是一个
JSON 值。它是纯边界：不打开文件、不读环境变量、不联系 provider、不运行命令，也不修改
任何现有 reader 或 CLI。

## 接口

```python
from pocketchat.json_input import JSONInputError, decode_json_bytes

value = decode_json_bytes(
    payload,
    max_bytes=64 * 1024,
    max_depth=16,
    max_nodes=4096,
)
```

`payload` 必须是精确的 `bytes`。三个限额都必须由调用方显式提供正整数；本契约不把布尔值
当作整数。`max_depth` 的有效范围是 1 到 64，使公开边界低于 Python 标准库 JSON
解码器的递归极限。

函数先检查 byte 限额，再做严格 UTF-8 解码，随后要求输入中恰好有一个完整 JSON 值。
值前后的 JSON 空白允许存在；第一个值之后还有第二个值或其他非空白内容时拒绝。

## 结构限额

`max_depth` 只计算数组和对象的嵌套：

- 标量根的深度为 0；
- 根数组或根对象的深度为 1；
- 根容器内再嵌套一层容器时深度为 2。

`max_nodes` 计算根值、每个数组元素以及每个对象成员的 value；对象 key 不计节点。
空数组和空对象仍各算一个容器节点。两个限额都包含边界：正好等于限额时接受，多一个时拒绝。

合法输入返回 JSON 解码后的普通 Python 值。模块不排序 key、不规范化文本、不做 canonical
数字处理，也不改写中文或 emoji 字符串。

## 拒绝结果

所有拒绝都抛出 `JSONInputError`。异常字符串固定，不含输入正文。公开元数据为：

- `error_type`：稳定的具体拒绝类型；
- `category`：`limit`、`encoding`、`syntax`、`structure` 或 `number`；
- `offset`：可选的 UTF-8 byte 偏移；
- `path`：可选的结构路径。

| `error_type` | `category` | 含义 | 位置元数据 |
|---|---|---|---|
| `invalid_limits` | `limit` | data 不是精确 bytes、限额不是正整数，或 `max_depth` 大于 64 | 无 |
| `byte_limit_exceeded` | `limit` | 输入 byte 数超过 `max_bytes` | 无 |
| `invalid_utf8` | `encoding` | bytes 不是严格 UTF-8 | byte `offset` |
| `invalid_json` | `syntax` | 第一个值缺失或语法错误 | 可取得时为 byte `offset` |
| `duplicate_key` | `structure` | 任意层对象出现重复成员名 | 无 |
| `nonfinite_number` | `number` | 值为 `NaN`、正负无穷，或数值溢出成非有限浮点数 | 可取得时为结构 `path` |
| `trailing_value` | `syntax` | 第一个完整值之后仍有非空白内容 | byte `offset` |
| `depth_limit_exceeded` | `limit` | 容器嵌套超过 `max_depth` | 可取得时为结构 `path` |
| `node_limit_exceeded` | `limit` | 值的节点数超过 `max_nodes` | 结构 `path` |

结构路径只由 `("array", index)` 与 `("object", value_ordinal)` 两种段组成；对象 ordinal
按源成员顺序计算。路径不会包含对象 key、字符串值、数值或源正文。

## 不做与证据边界

本模块不是文件 reader、JSONL reader、serializer、digest 所有者、schema 校验器、修复入口、
CLI 命令或 provider adapter。本切片不会让现有 session、task、replay、memory、acceptance、
checkpoint、selection 或 snapshot reader 使用它。聚焦测试只核对纯 bytes → value 契约；
它们不能证明后续 reader 接线或发布已经完成。
