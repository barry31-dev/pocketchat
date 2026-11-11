# Release acceptance preflight

`release-preflight` is a read-only check over an explicit observation file. It
reports which remaining version 1.2 release gates are supported by the supplied
facts and which are still pending, unknown or blocked. It is not a provider call,
manual acceptance, tag, Release, upload or proof that the original files were
rechecked.

## Command

```bash
pocketchat release-preflight release-acceptance.json --workspace . --json
```

The argument names one regular, non-symlink, workspace-relative JSON file of at
most 64 KiB. Omit `--json` for the human summary; omit `--workspace` to use the
current directory. The command reads only that file. It does not read the wheel,
source tree, traces, provider configuration, credentials, Git metadata or the
network, and it writes no file. It requires the same descriptor-relative
no-follow file primitives as `evidence verify`; unsupported platforms return
`refused` with reason `unsupported_platform`.

Exit codes are:

| Status | Exit |
|---|---:|
| `complete` | 0 |
| `pending` | 1 |
| `unknown` | 1 |
| `blocked` | 1 |
| `refused` | 2 |

`complete` means only that every required gate was explicitly supplied and no
identity contradiction was found. It does not mean that the supplied evidence
was re-executed, that a provider answered, that a person used the terminal, or
that publication happened.

## Observation shape

The outer object is closed:

```json
{
  "schema_version": "pocketchat-release-acceptance-observations.v1",
  "release": {
    "version": "1.2.0",
    "source_sha": "<40 lowercase hex>",
    "wheel_sha256": "<64 lowercase hex>"
  },
  "evidence": {},
  "blockers": []
}
```

`release` is the artifact identity every supplied gate must agree with. The
seven ordered gates are:

1. `offline_fixture`: exact source and wheel plus a fixture evidence digest.
2. `exact_ci`: exact source, workflow run id, exactly seven checks and evidence
   digest.
3. `real_provider`: exact source/wheel, provider and model scope, evidence digest.
4. `manual_tty`: exact source/wheel, operator scope, evidence digest.
5. `publication_authority`: exact source/wheel, `action: "tag_release"`, authority
   digest.
6. `publication`: exact source/wheel, tag `v<version>`, evidence digest.
7. `public_install_readback`: same version/wheel and evidence digest.

Any missing gate without a blocker is `pending`. A blocker row selects exactly
one supplied gate and one of `unknown`, `not_run` or `failed`. Supplying evidence
and a blocker for the same gate is refused. Evidence for a later gate while an
earlier gate is absent or blocked is refused as out-of-order; this prevents a
publication claim from appearing to satisfy a skipped prerequisite.

Identity mismatch, malformed or open shapes, duplicate blockers, invalid
digests, wrong versions, wrong tag, a CI count other than seven and invalid JSON
are refused. Provider/model/operator/tag/action names are validated but omitted
from the report; only the release digests and fixed gate states are returned.
The report includes `scope: "supplied_observations"`,
`files_rechecked: false`, deterministic gate order, `source_sha256` for the
exact input bytes and a canonical `report_sha256` computed over every other
report field. A reader can remove only `report_sha256`, canonicalize the rest as
sorted compact UTF-8 JSON and recompute the digest.

## Honest next actions

The report returns fixed next-action names such as
`run_authorized_real_provider_acceptance`,
`run_manual_trusted_terminal_acceptance`,
`obtain_exact_tag_release_authorization`,
`publish_the_exact_authorized_artifact` and
`install_and_read_back_the_public_artifact`. These are workflow labels, not
authority. This package does not infer a paid-call, credential, release or
upload authorization from the observation file.

## Current release boundary

The current source candidate is version 1.2.0. Its source preparation, offline
fixture and exact-candidate CI are separate from the still-missing real
tool-capable provider run, trusted manual terminal acceptance, specific
tag/Release authorization, publication and public-artifact install readback.
The preflight can make that gap explicit once those observations exist; it
cannot manufacture any of them.
