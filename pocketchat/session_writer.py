"""One host-local owner per explicit session write lifecycle.

The claim is an exclusive regular file beside the canonical session path. Its
random token is independent of the run id: two stale views may share a run id
yet still race to append the same turn. Ownership is never inferred from a PID
or a timeout. A crash leaves a claim for explicit inspection instead of being
silently stolen by the next writer.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Optional, Tuple
from uuid import uuid4

from .sessions import SessionFileError


class SessionWriterBusy(SessionFileError):
    """A session path has a foreign, stale, unreadable or replaced claim."""


def _path_key(path: Path) -> Path:
    """Normalize spelling without resolving a symlink to a different file."""
    return Path(os.path.abspath(os.fspath(path)))


def _claim_path(path: Path) -> Path:
    return Path(str(path) + ".writer")


def _regular_identity(path: Path, label: str) -> Tuple[int, int]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SessionWriterBusy("cannot inspect %s %s: %s" %
                                (label, path, exc)) from exc
    if not stat.S_ISREG(info.st_mode):
        raise SessionWriterBusy("%s is not a regular file: %s" % (label, path))
    if info.st_nlink != 1:
        raise SessionWriterBusy("%s has hard-link aliases: %s" % (label, path))
    return info.st_dev, info.st_ino


def _read_payload(path: Path, identity: Tuple[int, int]) -> dict:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise SessionWriterBusy("cannot open writer claim %s: %s" %
                                (path, exc)) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or \
                (info.st_dev, info.st_ino) != identity:
            raise SessionWriterBusy("writer claim was replaced: %s" % path)
        raw = b""
        while len(raw) <= 4096:
            chunk = os.read(descriptor, 4096 - len(raw) + 1)
            if not chunk:
                break
            raw += chunk
    except SessionWriterBusy:
        raise
    except OSError as exc:
        raise SessionWriterBusy("cannot read writer claim %s: %s" %
                                (path, exc)) from exc
    finally:
        os.close(descriptor)
    if len(raw) > 4096:
        raise SessionWriterBusy("writer claim is too large: %s" % path)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SessionWriterBusy("writer claim is malformed: %s" % path) from exc
    if not isinstance(payload, dict) or set(payload) != {"owner", "token"}:
        raise SessionWriterBusy("writer claim has an unsupported shape: %s" % path)
    if not isinstance(payload["owner"], str) or not payload["owner"]:
        raise SessionWriterBusy("writer claim names no owner: %s" % path)
    if not isinstance(payload["token"], str) or not payload["token"]:
        raise SessionWriterBusy("writer claim names no token: %s" % path)
    return payload


class SessionWriterClaim:
    """The token and exact files this lifecycle owns; release verifies both."""

    def __init__(self, path: Path, owner: str, token: str,
                 identity: Tuple[int, int]) -> None:
        self.path = path
        self.owner = owner
        self.token = token
        self._claim_identity = identity
        self._session_identity: Optional[Tuple[int, int]] = None
        if path.is_file():
            self._session_identity = _regular_identity(path, "session file")
        self._released = False

    def _require_live(self) -> None:
        if self._released:
            raise SessionWriterBusy("session writer claim is no longer held")
        claim_path = _claim_path(self.path)
        identity = _regular_identity(claim_path, "writer claim")
        if identity != self._claim_identity:
            raise SessionWriterBusy("writer claim was replaced: %s" % claim_path)
        payload = _read_payload(claim_path, identity)
        if payload != {"owner": self.owner, "token": self.token}:
            raise SessionWriterBusy("writer claim belongs to another owner: %s" %
                                    claim_path)

    def bind_session_file(self, path: Path) -> None:
        """Bind ownership to the exact regular session file it names."""
        if _path_key(Path(path)) != self.path:
            raise SessionWriterBusy("writer claim names a different session")
        identity = _regular_identity(Path(path), "session file")
        if self._session_identity is not None and \
                identity != self._session_identity:
            raise SessionWriterBusy("session file was replaced: %s" % path)
        self._session_identity = identity

    def require_owned(self, path: Path) -> None:
        """Fail closed unless this exact token still owns the exact file."""
        self._require_live()
        if _path_key(Path(path)) != self.path:
            raise SessionWriterBusy("writer claim names a different session")
        identity = _regular_identity(Path(path), "session file")
        if self._session_identity is None:
            self._session_identity = identity
        elif identity != self._session_identity:
            raise SessionWriterBusy("session file was replaced: %s" % path)

    def release(self) -> None:
        """Remove only the claim whose identity and token still match mine."""
        if self._released:
            return
        claim_path = _claim_path(self.path)
        try:
            identity = _regular_identity(claim_path, "writer claim")
            if identity != self._claim_identity:
                return
            payload = _read_payload(claim_path, identity)
            if payload != {"owner": self.owner, "token": self.token}:
                return
            claim_path.unlink()
        except (OSError, SessionWriterBusy):
            return
        finally:
            self._released = True

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass


def acquire_session_writer(path: Path,
                           owner: Optional[str] = None) -> SessionWriterClaim:
    """Create the exclusive claim beside ``path`` without stealing one."""
    path = _path_key(Path(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise SessionWriterBusy("session file must not be a symlink: %s" % path)
    claim_path = _claim_path(path)
    token = uuid4().hex
    claim_owner = owner or ("writer-%s" % token[:12])
    payload = json.dumps(
        {"owner": claim_owner, "token": token}, sort_keys=True).encode("utf-8")
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL |
             getattr(os, "O_BINARY", 0))
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(claim_path), flags, 0o600)
    except FileExistsError as exc:
        raise SessionWriterBusy(
            "cannot write session: writer claim already exists for %s" %
            path) from exc
    except OSError as exc:
        raise SessionWriterBusy("cannot write session writer for %s: %s" %
                                (path, exc)) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        try:
            claim_path.unlink()
        except OSError:
            pass
        raise SessionWriterBusy("cannot initialize session writer: %s" % exc) from exc
    identity = _regular_identity(claim_path, "writer claim")
    return SessionWriterClaim(path, claim_owner, token, identity)


def require_session_writer(path: Path,
                           writer: Optional[SessionWriterClaim]) -> None:
    """The append boundary's ownership check; no claim is an explicit refusal."""
    if not isinstance(writer, SessionWriterClaim):
        raise SessionWriterBusy(
            "explicit session writes require an owner claim: %s" % path)
    writer.require_owned(Path(path))
