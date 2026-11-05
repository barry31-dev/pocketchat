# Profile explanation

`pocketchat.profile_explain.explain_profile` is a pure explanation layer over
one already-selected profile. It accepts two caller-supplied mappings and one
closed credential observation; it does not load a config file, enumerate the
environment, call a provider or write any artifact.

```python
from pocketchat.profile_explain import explain_profile

report = explain_profile(
    raw_config,
    selected_profile,
    explicit_profile,
    {"presence": "present", "source": "environment"},
)
```

## Inputs and selection

- `raw_config` is the schema-1 or schema-2 document the existing config layer
  accepted. It supplies the profile body and the default selection.
- `resolved_config` is one profile returned by the existing `profile_config`
  layer. It must name the same profile and carry the same effective model,
  endpoint, credential variable and credential requirement as the raw record.
- `explicit_profile` is a CLI selection or `None`. A named profile records
  `selection: cli`; `None` records the declared default as `selection: default`.
- `credential_presence` is exactly `presence` plus `source`. Presence is
  `present`, `missing` or `unknown`; source is `environment` or `not_required`.
  The caller reads the environment and supplies only this boolean-like
  observation. This module never receives a credential value.

An unknown explicit profile or an inconsistent raw/resolved selection returns
a closed refusal report rather than guessing another profile. Malformed
mappings, duplicate keys, unsafe endpoint user-info and unknown fields raise a
typed `ProfileExplainError` before a report can be trusted.

## Result

The complete report uses `pocketchat-profile-explain.v1` and contains the
selected `profile`, `selection`, `model`, a redacted `endpoint`, a closed
`credential` object and sorted `provenance` rows. The endpoint contains only
scheme, host and optional port: path, query, fragment and user information are
not copied. Provenance distinguishes fields explicitly present in the raw
profile from defaults supplied by the config layer.

A refusal has `status: refused` and closed `findings`; it does not include the
model, endpoint or credential object. Neither a complete nor a refused report
includes the profile body, system prompt, environment dump or a credential
value. Inputs are not mutated and identical observations produce identical
reports.

## Limits and non-goals

The canonical UTF-8 envelope is limited to 64 KiB and 128 structural fields.
An oversized report refuses instead of truncating. The report contains
`report_bytes` and `report_sha256` for its own bounded envelope.

This module does not inspect network health, test a credential, read a
configuration, mutate configuration, add a CLI, emit trace/session evidence,
call a provider or authorize execution. A `present` observation means only
that the caller observed the named environment variable as present; it does
not prove that a provider accepts it.
