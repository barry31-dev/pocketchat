# Bounded JSON input

`pocketchat.json_input` decodes one JSON value from bytes that a caller has
already acquired. It is a pure boundary: it does not open a file, read the
environment, contact a provider, run a command, or change any existing reader
or CLI.

## Interface

```python
from pocketchat.json_input import JSONInputError, decode_json_bytes

value = decode_json_bytes(
    payload,
    max_bytes=64 * 1024,
    max_depth=16,
    max_nodes=4096,
)
```

`payload` must be exact `bytes`. All three limits must be explicit positive
integers; booleans are not integers for this contract. `max_depth` is limited
to 1 through 64 so the public boundary stays below the standard-library JSON
decoder's recursion limit.

The byte limit is checked before UTF-8 decoding. The decoder then requires
strict UTF-8 and exactly one complete JSON value. Leading and trailing JSON
whitespace are allowed. A second value or any other non-whitespace tail is
refused.

## Structure limits

`max_depth` counts only nested arrays and objects:

- a scalar root has depth 0;
- a root array or object has depth 1;
- a container inside that root has depth 2.

`max_nodes` counts the root plus every array element and every object member
value. Object keys are not nodes. An empty array or object still counts as its
container node. Both limits are inclusive: the exact boundary is accepted and
one over is refused.

A valid result preserves the ordinary Python value produced by JSON decoding.
The module does not sort keys, normalize text, canonicalize numbers, or rewrite
CJK and emoji strings.

## Refusals

Every refusal raises `JSONInputError`. Its string is fixed and does not include
input text. The public metadata is:

- `error_type`: the stable specific refusal;
- `category`: one of `limit`, `encoding`, `syntax`, `structure`, or `number`;
- `offset`: an optional UTF-8 byte offset;
- `path`: an optional structural path.

| `error_type` | `category` | Meaning | Location metadata |
|---|---|---|---|
| `invalid_limits` | `limit` | Data is not exact bytes, a limit is not a positive integer, or `max_depth` is above 64 | none |
| `byte_limit_exceeded` | `limit` | Input bytes exceed `max_bytes` | none |
| `invalid_utf8` | `encoding` | The bytes are not strict UTF-8 | byte `offset` |
| `invalid_json` | `syntax` | The first value is malformed or absent | byte `offset` when available |
| `duplicate_key` | `structure` | An object repeats a member name at any depth | none |
| `nonfinite_number` | `number` | A value is `NaN`, positive/negative infinity, or overflows to a non-finite float | structural `path` when available |
| `trailing_value` | `syntax` | Non-whitespace remains after the first complete value | byte `offset` |
| `depth_limit_exceeded` | `limit` | A container exceeds `max_depth` | structural `path` when available |
| `node_limit_exceeded` | `limit` | A value exceeds `max_nodes` | structural `path` |

A structural path is a tuple made only from `("array", index)` and
`("object", value_ordinal)` segments. Object ordinals follow source member
order. Paths never contain object keys, string values, numbers, or source text.

## Non-goals and evidence boundary

This module is not a file reader, JSONL reader, serializer, digest owner, schema
validator, repair path, CLI command, or provider adapter. No existing session,
task, replay, memory, acceptance, checkpoint, selection, or snapshot reader uses
it in this slice. Focused tests verify only the pure bytes-to-value contract;
they do not prove a later reader integration or publication.
