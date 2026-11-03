# Memory retrieval core (candidate-only)

Owner: `pocketchat.memory_retrieval`. Result version:
`pocketchat-memory-retrieval.v1`.

This is a pure core over validated `pocketchat-memory.v2` records. It does not
read a store, call a provider, wire a CLI, inject context, or change the legacy
`memory search` substring path.

## Admission before matching

Records are validated and gated before query matching or Top-K selection. A
record is excluded when its workspace differs, its admission status is not
`accepted`, it is marked `sensitive`, or `expires_at` is at or before the
caller's explicit UTC `now`. Exclusion output carries stable reason codes and
memory ids, never excluded content.

Fact verification remains separate from recall admission. The result carries
each selected record's verification status and digest-bound source references;
it never upgrades them into verified facts.

## Fixed lexical rules

`tokenizer_version` is `lexical-zh-en.v1`. NFKC normalization and `casefold`
apply only to retrieval text and tags. English terms are `[a-z0-9]+`. A
contiguous CJK run produces ordered two-character fragments; a single CJK
character is its own term. Original content is never rewritten.

Ranking is deterministic and uses these ordered keys: normalized full-query
phrase hit, matched query-term count, matched explicit-tag count, newer
`updated`, then ascending `memory_id`. No learned or uncalibrated score is
used.

## Dual excerpt budgets

Selection is bounded by `max_results`, the total UTF-8 `max_excerpt_bytes`, and
`max_recall_tokens`. Tokens are estimates under `chars4.v1`:
`ceil(utf8_bytes / 4)`, never provider usage. Each selected item carries only a
bounded excerpt, its byte and token counts, a truncation flag, matched terms,
matched tags, and source metadata; the full memory content is not returned.

`status: complete` with `empty_reason: no_matches` is different from
`status: budget_exhausted`, which has `error_type:
memory_retrieval_budget_exhausted` and at least one admitted match that cannot
fit. `status: partial` reports truncated items or current-page omissions by
cause.

## Stable continuation

`next_cursor` is an opaque versioned cursor bound to the exact query,
tokenizer, and ordered matched-record signatures. Reusing it with reordered,
added, removed or changed matching records raises
`memory_retrieval_cursor_stale` rather than silently changing page boundaries.
The result reports items skipped before the cursor and omissions caused by
`max_results`, byte budget and token budget separately.

The core is offline and deterministic. It is not semantic relevance, a vector
index, secure deletion, fact verification, release evidence or authorization to
inject recalled data into a request.
