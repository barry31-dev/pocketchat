# Package manifest

`PC-PACKAGE-MANIFEST-1` adds a deterministic, bounded description of the
package bytes that the existing release smoke actually builds and installs.
The manifest is package evidence, not a release, signature, provenance claim or
publication authorization.

## API

```python
build_package_manifest(
    source,
    *,
    source_kind,
    max_members=2048,
    max_member_bytes=8_388_608,
    max_total_bytes=67_108_864,
    max_report_bytes=262_144,
)
```

`source_kind` is closed to:

- `wheel`: one caller-selected wheel archive;
- `installed_tree`: one caller-selected `site-packages` root. Only its exact
  `pocketchat/` package and matching `pocketchat-<version>.dist-info/` tree are
  traversed. Unrelated installed packages are not scanned.

The call reads package bytes but never changes the wheel or installed tree. It
does not build, install, import, call a provider, inspect credentials, access the
network or search an arbitrary workspace.

## Report contract

A successful result is a JSON-serializable object with this closed identity:

```json
{
  "schema": "pocketchat-package-manifest.v1",
  "schema_version": 1,
  "source_kind": "wheel",
  "package": "pocketchat",
  "version": "1.1.0",
  "entry_point": {
    "name": "pocketchat",
    "target": "pocketchat.cli:main"
  },
  "groups": {
    "metadata": [],
    "modules": [],
    "package_data": [],
    "schemas": []
  },
  "members": []
}
```

Each sorted `members` row contains exactly `path`, `category`, `bytes` and
`sha256`. Paths are normalized package-relative POSIX paths. The report never
contains a source absolute path or file body. `groups` is a deterministic
projection of those rows, not a second inventory.

Generated installation-only records are outside the comparable payload:
`__pycache__`, `.pyc`, `.pyo`, `INSTALLER`, `REQUESTED` and `direct_url.json`.
`RECORD` is reported but excluded from the wheel-versus-install hash comparison
because installers are allowed to rewrite it for installed files. All other
admitted module, schema, package-data and metadata bytes must agree.

## Required surface and refusals

Both source kinds must contain exactly one `pocketchat/` package and one matching
`pocketchat-<version>.dist-info/` tree. The existing release-critical modules,
five shipped schemas, `METADATA`, `WHEEL`, `RECORD` and
`entry_points.txt` are required. Metadata name/version and the console entry
point must agree with the package identity.

The inspection fails closed for:

- duplicate, absolute, parent, backslash, case-colliding or NFC-colliding paths;
- encrypted wheel members, wheel links, installed symlinks or other non-regular
  installed entries;
- an unknown source kind, package/version/entry-point mismatch, a second package
  identity, missing required resource or removed web/server surface;
- a non-positive limit, too many members, an oversized member, total byte budget
  overflow or canonical report overflow.

Limits refuse the input. They never truncate an inventory and then label it
complete.

## Release-smoke integration

`build_wheel()` attaches the wheel manifest to `WheelArtifact`.
`inspect_installed()` locates the clean virtual environment's `site-packages`,
builds the installed-tree manifest, and then preserves the existing installed
`--version` plus complete `task ... --help` smoke. `run_release_smoke()` compares
package identity and all comparable member hashes and adds both reports under
`package_manifests`, without removing any existing JSON fields.

Run from the product root:

```bash
python scripts/release_smoke.py --json
```

## Verification scope

On 2026-11-05, macOS with CPython 3.13.9 and pip 25.3 built and installed the
current wheel successfully. The final smoke reported 67 admitted members, wheel
SHA-256 `edc233bf0f7cb554440bea4ea5094202311f7571a03a84bebe370012f569f980`, matching
wheel/installed payloads, and all 11 task help commands. This proves that local
run only. Two additional builds produced different wheel container hashes
(`c563154f...` and `33da6dd4...`) but byte-identical canonical manifests with
SHA-256 `a4abfa4972e2e3cf80462df0eb8aeb3bb4ba50ec8fface137dc37591265a2b87`. The
manifest is deterministic for those runs; the ZIP container is not claimed to
be reproducible.

The Apple Command Line Tools CPython 3.9.6 environment uses pip 21.2.4. Its
isolated build produced `UNKNOWN-0.0.0`, and its explicit non-isolated fallback
used setuptools 58.0.4, below this repository's `setuptools>=61` requirement.
Those two failures did not reach a valid PocketChat package manifest and are not
a conclusion about all CPython 3.9 environments. Linux, Windows, exact-candidate
CI, tag, Release and package-index upload remain separately unverified unless the
implementation review records later evidence.
