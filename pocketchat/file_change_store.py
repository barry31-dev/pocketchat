"""Private, bounded storage for pending file-change transactions.

The store is rooted at ``.pocketchat/changes``. All traversal after opening the
workspace is descriptor-relative and no-follow; unsupported runtimes fail closed.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Tuple
from uuid import uuid4

MAX_RECORD_BYTES = 64 * 1024
MAX_STORE_BYTES = 16 * 1024 * 1024
_CHANGE_ID = re.compile(r"change_[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class FileChangeStoreError(ValueError):
    """The private file-change store cannot be trusted or updated."""


class FileChangeStoreFull(FileChangeStoreError):
    """A write would cross the fixed logical store quota."""


class FileChangeStoreBusy(FileChangeStoreError):
    """Another cooperative writer owns the store mutation boundary."""


class FileChangeUnsupportedRuntime(FileChangeStoreError):
    """Required descriptor-relative, no-follow primitives are unavailable."""


def require_secure_runtime() -> None:
    required = (os.open, os.mkdir, os.stat, os.unlink, os.rename)
    if (not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")
            or any(function not in os.supports_dir_fd for function in required)):
        raise FileChangeUnsupportedRuntime(
            "secure file-change storage requires descriptor-relative no-follow I/O")


def _change_id(value: Any) -> str:
    if not isinstance(value, str) or not _CHANGE_ID.fullmatch(value):
        raise FileChangeStoreError("change id must be change_ plus 32 lowercase hex")
    return value


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise FileChangeStoreError("object digest must be lowercase SHA-256")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FileChangeStoreError("change record contains duplicate JSON fields")
        result[key] = value
    return result


def _canonical(record: Mapping[str, Any]) -> bytes:
    try:
        data = json.dumps(record, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise FileChangeStoreError("change record must be finite UTF-8 JSON")
    if len(data) > MAX_RECORD_BYTES:
        raise FileChangeStoreError("change record exceeds 65536 bytes")
    return data


def _mode_is_private(info: os.stat_result) -> bool:
    return stat.S_IMODE(info.st_mode) & 0o077 == 0


def _open_directory(parent_fd: int, name: str, *, create: bool,
                    private: bool) -> int:
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            raise FileChangeStoreError("cannot create private store directory: %s" % exc)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise FileChangeStoreError("cannot open private store directory: %s" % exc)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise FileChangeStoreError("private store path is not a directory")
        if private and not _mode_is_private(info):
            raise FileChangeStoreError("private store directory permissions are too broad")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _stat_regular(directory_fd: int, name: str, *, missing_ok: bool = False
                  ) -> os.stat_result | None:
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise FileChangeStoreError("private store file is missing")
    except OSError as exc:
        raise FileChangeStoreError("cannot inspect private store file: %s" % exc)
    if not stat.S_ISREG(info.st_mode):
        raise FileChangeStoreError("private store entries must be regular files")
    if not _mode_is_private(info):
        raise FileChangeStoreError("private store file permissions are too broad")
    return info


def _read_regular(directory_fd: int, name: str, *, limit: int) -> bytes:
    before = _stat_regular(directory_fd, name)
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise FileChangeStoreError("cannot open private store file: %s" % exc)
    try:
        opened = os.fstat(descriptor)
        if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or not stat.S_ISREG(opened.st_mode)):
            raise FileChangeStoreError("private store file changed while opening")
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        if ((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                != (opened.st_dev, opened.st_ino, opened.st_size,
                    opened.st_mtime_ns)):
            raise FileChangeStoreError("private store file changed while reading")
        if len(data) > limit or len(data) != opened.st_size:
            raise FileChangeStoreError("private store file exceeds its byte limit")
        return data
    except OSError as exc:
        raise FileChangeStoreError("cannot read private store file: %s" % exc)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(errno.EIO, "short private store write")
        view = view[written:]


def _write_exclusive(directory_fd: int, name: str, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = None
    created = False
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
        created = True
        _write_all(descriptor, data)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.fsync(directory_fd)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            try:
                os.unlink(name, dir_fd=directory_fd)
            except OSError:
                pass
        raise


def _replace(directory_fd: int, name: str, data: bytes) -> None:
    temporary = ".tmp-%s" % uuid4().hex
    try:
        _write_exclusive(directory_fd, temporary, data)
        os.rename(temporary, name, src_dir_fd=directory_fd,
                  dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    except Exception:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except OSError:
            pass
        raise


def _directory_size(directory_fd: int) -> int:
    total = 0
    try:
        names = os.listdir(directory_fd)
    except OSError as exc:
        raise FileChangeStoreError("cannot inspect private store quota: %s" % exc)
    for name in names:
        info = _stat_regular(directory_fd, name)
        total += info.st_size
    return total



class FileChangeWriterClaim:
    """Identity-bound cooperative ownership of the one mutation boundary."""

    def __init__(self, store: "FileChangeStore", change_id: str, owner: str,
                 token: str, identity: Tuple[int, int]) -> None:
        self.store = store
        self.change_id = change_id
        self.owner = owner
        self.token = token
        self._identity = identity
        self._released = False
        self._preserved = False

    def _payload(self) -> Dict[str, str]:
        return {
            "change_id": self.change_id,
            "owner": self.owner,
            "token": self.token,
        }

    def require_owned(self) -> None:
        if self._released or self._preserved:
            raise FileChangeStoreBusy("file-change writer claim is no longer live")
        with self.store._layout() as (changes_fd, _, _):
            info = _stat_regular(changes_fd, "writer.lock")
            if (info.st_dev, info.st_ino) != self._identity:
                raise FileChangeStoreBusy("file-change writer claim was replaced")
            payload = self.store._read_lock(changes_fd)
            if payload != self._payload():
                raise FileChangeStoreBusy("file-change writer claim belongs to another owner")

    def preserve(self) -> None:
        """Keep the claim as recovery evidence after an applied/unknown effect."""
        self.require_owned()
        self._preserved = True

    def release(self) -> None:
        if self._released or self._preserved:
            return
        try:
            with self.store._layout() as (changes_fd, _, _):
                info = _stat_regular(changes_fd, "writer.lock", missing_ok=True)
                if info is None or (info.st_dev, info.st_ino) != self._identity:
                    return
                if self.store._read_lock(changes_fd) != self._payload():
                    return
                os.unlink("writer.lock", dir_fd=changes_fd)
                os.fsync(changes_fd)
        except (OSError, FileChangeStoreError):
            return
        finally:
            self._released = True

    def __enter__(self) -> "FileChangeWriterClaim":
        self.require_owned()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


class FileChangeStore:
    """Persist content-addressed objects and closed change records."""

    def __init__(self, workspace: Path, *, max_bytes: int = MAX_STORE_BYTES) -> None:
        require_secure_runtime()
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise FileChangeStoreError("store quota must be a positive integer")
        self.workspace = Path(workspace).absolute()
        self.root = self.workspace / ".pocketchat" / "changes"
        self.objects = self.root / "objects"
        self.records = self.root / "records"
        self.max_bytes = max_bytes
        with self._layout():
            pass

    @contextmanager
    def _layout(self) -> Iterator[Tuple[int, int, int]]:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors = []
        try:
            workspace_fd = os.open(str(self.workspace), flags)
            descriptors.append(workspace_fd)
            pocketchat_fd = _open_directory(
                workspace_fd, ".pocketchat", create=True, private=False)
            descriptors.append(pocketchat_fd)
            changes_fd = _open_directory(
                pocketchat_fd, "changes", create=True, private=True)
            descriptors.append(changes_fd)
            objects_fd = _open_directory(
                changes_fd, "objects", create=True, private=True)
            descriptors.append(objects_fd)
            records_fd = _open_directory(
                changes_fd, "records", create=True, private=True)
            descriptors.append(records_fd)
            yield changes_fd, objects_fd, records_fd
        except FileChangeStoreError:
            raise
        except OSError as exc:
            raise FileChangeStoreError("cannot open private file-change store: %s" % exc)
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @staticmethod
    def _record_name(change_id: str) -> str:
        return _change_id(change_id) + ".json"

    def _size(self, objects_fd: int, records_fd: int) -> int:
        return _directory_size(objects_fd) + _directory_size(records_fd)

    @staticmethod
    def _read_lock(changes_fd: int) -> Dict[str, Any]:
        data = _read_regular(changes_fd, "writer.lock", limit=4096)
        try:
            payload = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise FileChangeStoreBusy("cannot decode file-change writer claim: %s" % exc)
        if (not isinstance(payload, dict)
                or set(payload) != {"change_id", "owner", "token"}):
            raise FileChangeStoreBusy("file-change writer claim is malformed")
        return payload

    def acquire_writer(self, change_id: str, *, owner: str) -> FileChangeWriterClaim:
        identifier = _change_id(change_id)
        if (not isinstance(owner, str) or not owner.strip() or len(owner) > 256
                or any(ord(char) < 32 for char in owner)):
            raise FileChangeStoreError("writer owner must be bounded non-empty text")
        token = uuid4().hex
        payload = _canonical({
            "change_id": identifier,
            "owner": owner,
            "token": token,
        })
        with self._layout() as (changes_fd, _, _):
            try:
                _write_exclusive(changes_fd, "writer.lock", payload)
            except FileExistsError as exc:
                raise FileChangeStoreBusy(
                    "another file-change writer already owns the mutation boundary") from exc
            except OSError as exc:
                raise FileChangeStoreError(
                    "cannot create file-change writer claim: %s" % exc) from exc
            info = _stat_regular(changes_fd, "writer.lock")
        return FileChangeWriterClaim(
            self, identifier, owner, token, (info.st_dev, info.st_ino))

    def write_object(self, data: bytes) -> str:
        if not isinstance(data, bytes):
            raise FileChangeStoreError("stored object must be bytes")
        digest = hashlib.sha256(data).hexdigest()
        with self._layout() as (_, objects_fd, records_fd):
            existing = _stat_regular(objects_fd, digest, missing_ok=True)
            if existing is not None:
                observed = _read_regular(objects_fd, digest, limit=len(data))
                if observed != data:
                    raise FileChangeStoreError("stored object digest does not match")
                return "objects/%s" % digest
            if self._size(objects_fd, records_fd) + len(data) > self.max_bytes:
                raise FileChangeStoreFull("file-change store quota would be exceeded")
            try:
                _write_exclusive(objects_fd, digest, data)
            except FileExistsError:
                observed = _read_regular(objects_fd, digest, limit=len(data))
                if observed != data:
                    raise FileChangeStoreError("stored object digest does not match")
            except OSError as exc:
                raise FileChangeStoreError("cannot persist file-change object: %s" % exc)
        return "objects/%s" % digest

    def read_object(self, reference: str, expected_sha256: str) -> bytes:
        digest = _digest(expected_sha256)
        if reference != "objects/%s" % digest:
            raise FileChangeStoreError("object reference does not match its digest")
        with self._layout() as (_, objects_fd, _):
            data = _read_regular(objects_fd, digest, limit=MAX_STORE_BYTES)
        if hashlib.sha256(data).hexdigest() != digest:
            raise FileChangeStoreError("stored object digest does not match")
        return data

    def create_record(self, record: Mapping[str, Any]) -> None:
        change_id = _change_id(record.get("change_id"))
        data = _canonical(record)
        name = self._record_name(change_id)
        with self._layout() as (_, objects_fd, records_fd):
            if _stat_regular(records_fd, name, missing_ok=True) is not None:
                raise FileChangeStoreError("change record already exists")
            if self._size(objects_fd, records_fd) + len(data) > self.max_bytes:
                raise FileChangeStoreFull("file-change store quota would be exceeded")
            try:
                _write_exclusive(records_fd, name, data)
            except FileExistsError:
                raise FileChangeStoreError("change record already exists")
            except OSError as exc:
                raise FileChangeStoreError("cannot persist change record: %s" % exc)

    def update_record(self, record: Mapping[str, Any]) -> None:
        change_id = _change_id(record.get("change_id"))
        data = _canonical(record)
        name = self._record_name(change_id)
        with self._layout() as (_, objects_fd, records_fd):
            old = _stat_regular(records_fd, name)
            projected = self._size(objects_fd, records_fd) - old.st_size + len(data)
            if projected > self.max_bytes:
                raise FileChangeStoreFull("file-change store quota would be exceeded")
            try:
                _replace(records_fd, name, data)
            except OSError as exc:
                raise FileChangeStoreError("cannot update change record: %s" % exc)

    def list_change_ids(self) -> list[str]:
        """List every persisted change record identity in deterministic order."""
        with self._layout() as (_, _, records_fd):
            try:
                names = sorted(os.listdir(records_fd))
            except OSError as exc:
                raise FileChangeStoreError(
                    "cannot list private change records: %s" % exc) from exc
            identifiers = []
            for name in names:
                if not name.endswith(".json"):
                    raise FileChangeStoreError(
                        "private change record directory contains an unknown entry")
                identifier = name[:-5]
                _change_id(identifier)
                _stat_regular(records_fd, name)
                identifiers.append(identifier)
            return identifiers

    def load_record(self, change_id: str) -> Dict[str, Any]:
        name = self._record_name(change_id)
        with self._layout() as (_, _, records_fd):
            data = _read_regular(records_fd, name, limit=MAX_RECORD_BYTES)
        try:
            payload = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                                 parse_constant=lambda value: (_ for _ in ()).throw(
                                     FileChangeStoreError(
                                         "change record contains non-finite JSON")))
        except FileChangeStoreError:
            raise
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise FileChangeStoreError("cannot decode change record: %s" % exc)
        if not isinstance(payload, dict):
            raise FileChangeStoreError("change record must be an object")
        if payload.get("change_id") != change_id:
            raise FileChangeStoreError("change record identity does not match its path")
        return payload
