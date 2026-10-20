"""The request payload: one builder, one snapshot, one hash.

A trace says a request happened. This module is what makes the trace checkable
about *what* was sent, which is the part replay and diff both need and neither
can reconstruct: a provider body is gone the moment the socket closes.

Four decisions are load bearing:

- The provider builds its body here and nowhere else. A snapshot that describes
  a payload slightly different from the one sent is worse than no snapshot,
  because it looks like evidence.
- The hash is over canonical bytes, not over whatever the caller formatted.
  Two equal requests must produce equal digests, or a replay reports a
  mismatch on a request that did not change.
- The credential is not part of this module at all. It lives in a header, and
  the header is deliberately not recorded: the snapshot carries the request a
  reader can inspect, and nothing a reader could reuse to authenticate.
- The body is checked against the credential before it is written. A prompt
  that quotes a key -- a paste, a dotenv line, a support ticket -- would
  otherwise put it in the one artifact kept and replayed, so the writer
  refuses rather than producing a file that cannot be shown to anyone.

The snapshot is written with an exclusive create and is never rewritten. A run
that replaced its own snapshot would destroy the record of the run that
already happened, which is the one thing this file exists to prevent.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

REQUEST_SCHEMA_VERSION = "1"


class CredentialInRequestError(Exception):
    """The request body carries the credential, and no snapshot may hold it.

    The header is not this module's business and never is: the credential
    lives in a header the package does not record. The body is the risk,
    because the body is written to disk in full. A run whose operator quoted a
    key -- a paste, a dotenv line, a support ticket -- would otherwise store
    the credential in the one artifact that is kept and replayed.
    """


def contains_credential(payload_bytes: bytes, credential: Optional[str]) -> bool:
    """True when ``payload_bytes`` contains the literal credential.

    A substring test rather than a pattern: it catches the credential whatever
    quotes or escapes surround it, where a shape-based filter would only catch
    the shapes its author imagined. The cost is that it only ever protects the
    credential this process is holding, which is the one that matters: a key
    belonging to somebody else is not this package's to police.

    An empty credential is never "in" anything, so a run with no key cannot be
    refused for a reason that does not apply to it.
    """
    if not credential:
        return False
    return credential.encode("utf-8") in payload_bytes


def canonical_bytes(payload: Any) -> bytes:
    """Serialize ``payload`` to the exact bytes its digest is taken over.

    Keys are sorted and the separators are fixed, so two payloads holding the
    same request serialize identically no matter which order the caller's dict
    happened to use. ``ensure_ascii=False`` keeps a non-ASCII message in its own
    encoding: escaping it would produce a different byte string for the same
    request, and a snapshot written by one build would then fail to verify
    against another.

    The trailing newline is part of the canonical form. It is included so the
    file on disk is a normal text file, and the digest covers it just as it
    covers every other byte in the file.
    """
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2)
    return (text + "\n").encode("utf-8")


def request_digest(payload: Any) -> str:
    """The SHA-256 of the canonical bytes, as lowercase hex."""
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def request_snapshot_path(trace_path: Path, turn: int = 1) -> Path:
    """Where the snapshot for one request lives, given its trace.

    A run may make several requests, and each has its own context, so the file
    is named by turn rather than by run alone: two turns of one run are two
    requests and neither can stand in for the other.

    The directory is derived from the trace's *own* directory rather than from
    an assumed ``traces``/``requests`` pair of siblings. ``--trace`` lets an
    operator put a trace wherever they like, and a layout that assumed a fixed
    parent would then write the snapshot somewhere the recorded path does not
    resolve to -- which is the one thing a reference must never do. Deriving it
    from the trace keeps the two together wherever that is.
    """
    trace = Path(trace_path)
    if turn < 1:
        raise ValueError("turn must be 1 or greater")
    return trace.parent / "requests" / ("%s.turn-%d.json" % (trace.stem, turn))


def next_request_turn(trace_path: Path) -> int:
    """The turn number the next request of this trace should use.

    A resumed session adopts the run id and the trace of the run it continues,
    so it must not restart the numbering at one: it would collide with the
    snapshot already on disk, and the exclusive create would then refuse the
    very request it is meant to record. Counting the requests already recorded
    in the trace is what makes a resumed run continue the sequence instead of
    fighting it.

    The count comes from the trace itself rather than from a counter, because
    the trace is the only thing both processes see: a counter in memory starts
    over, and a counter on disk would be a second place for the truth to live.
    """
    trace = Path(trace_path)
    turn = 0
    if not trace.is_file():
        return 1
    try:
        with trace.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except ValueError:
                    continue
                if event.get("type") == "model.request.started":
                    turn += 1
    except OSError:
        return 1
    return turn + 1


def write_request_snapshot(path: Path, run_id: str, profile: Optional[str],
                           created: str, payload: Dict[str, Any],
                           credential: Optional[str] = None) -> str:
    """Write one request snapshot and return its digest.

    The file is created exclusively: if it already exists this raises rather
    than overwriting, because the existing file is the record of a request that
    was really sent. A caller that hits this has reused a run id, which is a
    bug worth surfacing rather than papering over.

    ``payload`` is the request body exactly as sent. It carries no header and
    no credential; the caller is responsible for handing over the body and not
    the request.

    ``credential`` is the one thing the body is checked *against*, and it is
    passed in rather than read from the environment: this module does not know
    where credentials live, only that the body must not contain one. The
    caller already holds the credential in order to send the request, so it is
    the caller's to hand over.
    """
    record = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "run_id": run_id,
        "profile": profile if isinstance(profile, str) else "",
        "created": created,
        "request": payload,
        # The digest is over the request body alone, so it stays comparable
        # across whatever metadata a snapshot carries around it.
        "request_sha256": request_digest(payload),
    }
    record_bytes = canonical_bytes(record)
    if contains_credential(record_bytes, credential):
        # A body holding the credential fails closed and leaves no file. A
        # redacted snapshot would be worse than none: it would look like the
        # evidence for a request whose body cannot be read.
        raise CredentialInRequestError(
            "the request body contains the configured credential; "
            "no snapshot was written")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(handle, "wb") as stream:
        stream.write(record_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    return record["request_sha256"]


def verify_request_snapshot(path: Path) -> Optional[str]:
    """Recompute a snapshot's digest and return it, or ``None`` if it changed.

    ``None`` means the file does not describe its own request: either the body
    was edited, or the recorded digest was. The caller reports a mismatch
    rather than trusting the stored value, because a digest is only worth
    storing if reading it back can disagree.
    """
    target = Path(path)
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    payload = record.get("request")
    stored = record.get("request_sha256")
    if not isinstance(stored, str):
        return None
    if request_digest(payload) != stored:
        return None
    return stored
