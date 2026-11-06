"""Bounded file-change proposals with no immediate file side effect."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from uuid import uuid4

from .file_change_store import (FileChangeStore, FileChangeStoreBusy,
                                FileChangeStoreError, FileChangeStoreFull,
                                FileChangeUnsupportedRuntime)
from .tools import BINARY_SUFFIXES, MAX_CONTENT_BYTES, SENSITIVE_NAMES

SCHEMA_VERSION = "pocketchat-file-change.v1"
MAX_DIFF_BYTES = 64 * 1024
_CHANGE_ID = re.compile(r"change_[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class FileChangeError(ValueError):
    """A proposed or persisted file change is invalid or unsafe."""

    def __init__(self, message: str, error_type: str = "file_invalid_record",
                 effect_disposition: str = "none") -> None:
        super().__init__(message)
        self.error_type = error_type
        self.effect_disposition = effect_disposition


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise FileChangeError("file change data must be finite UTF-8 JSON")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise FileChangeError("%s must be a lowercase SHA-256" % what)
    return value


def _path(value: Any) -> str:
    if not isinstance(value, str):
        raise FileChangeError("file path must be text",
                              error_type="file_permission_denied")
    normalized = value.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    parts = normalized.split("/")
    if (not normalized or normalized.startswith("/") or ":" in normalized
            or any(part in ("", ".", "..") for part in parts)
            or any(ord(char) < 32 for char in normalized)):
        raise FileChangeError("file path must stay inside the workspace",
                              error_type="file_permission_denied")
    if any(part in SENSITIVE_NAMES or part == ".pocketchat" for part in parts):
        raise FileChangeError("file path is refused by the safety policy",
                              error_type="file_permission_denied")
    if Path(normalized).suffix.lower() in BINARY_SUFFIXES:
        raise FileChangeError("file path is refused by the safety policy",
                              error_type="file_permission_denied")
    return normalized


def _allowed(path: str, values: Iterable[str]) -> bool:
    return path in {_path(value) for value in values}


def _identity(info) -> Dict[str, int]:
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "mode": info.st_mode,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
    }



def _require_store_workspace(root: Path, store: FileChangeStore) -> None:
    try:
        requested = os.stat(str(Path(root).absolute()), follow_symlinks=False)
        bound = os.stat(str(store.workspace), follow_symlinks=False)
    except OSError as exc:
        raise FileChangeError("cannot verify file-change workspace: %s" % exc) from exc
    if (not stat.S_ISDIR(requested.st_mode) or not stat.S_ISDIR(bound.st_mode)
            or (requested.st_dev, requested.st_ino) != (bound.st_dev, bound.st_ino)):
        raise FileChangeError("file-change store belongs to another workspace")


def _require_secure_target_runtime() -> None:
    required = (os.open, os.stat, os.unlink, os.link, os.rename)
    if (not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")
            or any(function not in os.supports_dir_fd for function in required)):
        raise FileChangeError(
            "safe file changes require descriptor-relative no-follow I/O",
            error_type="file_unsupported_runtime")


@contextmanager
def _open_parent(root: Path, path: str):
    _require_secure_target_runtime()
    parts = path.split("/")
    descriptors = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(str(Path(root).absolute()), flags)
        descriptors.append(descriptor)
        for part in parts[:-1]:
            descriptor = os.open(part, flags, dir_fd=descriptor)
            descriptors.append(descriptor)
        yield descriptor, parts[-1]
    except FileChangeError:
        raise
    except OSError as exc:
        raise FileChangeError("cannot open file-change parent directory: %s" % exc,
                              error_type="file_conflict") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short file-change write")
        view = view[written:]


def _read_target_at(parent_fd: int, name: str, *, allow_missing: bool
                    ) -> tuple[Optional[bytes], Optional[Dict[str, int]]]:
    try:
        observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if allow_missing:
            return None, None
        raise FileChangeError("file-change target is missing",
                              error_type="file_conflict")
    except OSError as exc:
        raise FileChangeError("cannot inspect file-change target: %s" % exc,
                              error_type="file_conflict") from exc
    if not stat.S_ISREG(observed.st_mode):
        raise FileChangeError("file-change target must be a regular file",
                              error_type="file_conflict")
    if observed.st_size > MAX_CONTENT_BYTES:
        raise FileChangeError("existing file exceeds 16384 bytes",
                              error_type="file_too_large")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise FileChangeError("cannot open file-change target: %s" % exc,
                              error_type="file_conflict") from exc
    try:
        opened = os.fstat(descriptor)
        if ((opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino)
                or not stat.S_ISREG(opened.st_mode)):
            raise FileChangeError("file-change target was replaced while opening",
                                  error_type="file_conflict")
        remaining = MAX_CONTENT_BYTES + 1
        chunks = []
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        if (_identity(after) != _identity(opened)
                or len(data) != opened.st_size):
            raise FileChangeError("file-change target changed while reading",
                                  error_type="file_conflict")
        if len(data) > MAX_CONTENT_BYTES:
            raise FileChangeError("existing file exceeds 16384 bytes",
                                  error_type="file_too_large")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FileChangeError("existing file is not UTF-8 text",
                                  error_type="file_permission_denied") from exc
        if "\x00" in text:
            raise FileChangeError("existing file contains NUL bytes",
                                  error_type="file_permission_denied")
        return data, _identity(opened)
    except OSError as exc:
        raise FileChangeError("cannot read file-change target: %s" % exc,
                              error_type="file_conflict") from exc
    finally:
        os.close(descriptor)


def _read_target(root: Path, path: str, *, allow_missing: bool
                 ) -> tuple[Optional[bytes], Optional[Dict[str, int]]]:
    with _open_parent(root, path) as (parent_fd, name):
        return _read_target_at(parent_fd, name, allow_missing=allow_missing)


def _matches_before(change: Dict[str, Any], data: Optional[bytes],
                    identity: Optional[Dict[str, int]]) -> bool:
    if change["operation"] == "create":
        return data is None and identity is None
    before = change["before"]
    return (data is not None and identity == before["file_identity"]
            and _sha256(data) == before["sha256"])


def _atomic_apply(root: Path, change: Dict[str, Any], candidate: bytes
                  ) -> tuple[bytes, Dict[str, int]]:
    temporary = ".pocketchat-change-%s.tmp" % uuid4().hex
    created_temporary = False
    replaced = False
    with _open_parent(root, change["path"]) as (parent_fd, name):
        current, identity = _read_target_at(
            parent_fd, name, allow_missing=change["operation"] == "create")
        if not _matches_before(change, current, identity):
            raise FileChangeError("target changed after proposal",
                                  error_type="file_conflict")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = None
        try:
            descriptor = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
            created_temporary = True
            _write_all(descriptor, candidate)
            mode = (stat.S_IMODE(identity["mode"])
                    if identity is not None else 0o644)
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            latest, latest_identity = _read_target_at(
                parent_fd, name, allow_missing=change["operation"] == "create")
            if not _matches_before(change, latest, latest_identity):
                raise FileChangeError("target changed during file preparation",
                                      error_type="file_conflict")
            if change["operation"] == "replace":
                os.rename(temporary, name, src_dir_fd=parent_fd,
                          dst_dir_fd=parent_fd)
                replaced = True
            else:
                os.link(temporary, name, src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd, follow_symlinks=False)
                replaced = True
                os.unlink(temporary, dir_fd=parent_fd)
            created_temporary = False
            os.fsync(parent_fd)
            observed, observed_identity = _read_target_at(
                parent_fd, name, allow_missing=False)
            if observed != candidate or observed_identity is None:
                raise FileChangeError(
                    "applied file failed independent readback",
                    error_type="file_recovery_required",
                    effect_disposition="applied")
            return observed, observed_identity
        except FileChangeError as exc:
            if replaced:
                raise FileChangeError(
                    "file was applied but independent readback could not be verified",
                    error_type="file_recovery_required",
                    effect_disposition="applied") from exc
            raise
        except OSError as exc:
            raise FileChangeError(
                "file change could not be applied: %s" % exc,
                error_type=("file_recovery_required" if replaced
                            else "file_conflict"),
                effect_disposition=("applied" if replaced else "none")) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if created_temporary:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except OSError:
                    pass


def _raise_store_error(exc: FileChangeStoreError) -> None:
    if isinstance(exc, FileChangeStoreFull):
        error_type = "file_store_full"
    elif isinstance(exc, FileChangeStoreBusy):
        error_type = "file_conflict"
    elif isinstance(exc, FileChangeUnsupportedRuntime):
        error_type = "file_unsupported_runtime"
    else:
        error_type = "file_invalid_record"
    raise FileChangeError(str(exc), error_type=error_type) from exc


def _object(data: bytes, store: FileChangeStore) -> Dict[str, Any]:
    digest = _sha256(data)
    try:
        reference = store.write_object(data)
    except FileChangeStoreError as exc:
        _raise_store_error(exc)
    return {
        "bytes": len(data),
        "sha256": digest,
        "object": reference,
    }


def _render_diff(path: str, before: str, after: str) -> str:
    rendered = "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="a/%s" % path,
        tofile="b/%s" % path,
    ))
    if len(rendered.encode("utf-8")) > MAX_DIFF_BYTES:
        raise FileChangeError("file diff exceeds 65536 bytes",
                              error_type="file_too_large")
    return rendered



def _closed(value: Any, required, optional=(), what: str = "file change") -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise FileChangeError("%s must be an object" % what)
    required = set(required)
    allowed = required | set(optional)
    if set(value) - allowed or required - set(value):
        raise FileChangeError("%s has missing or unknown fields" % what)
    return value


def _text(value: Any, what: str, *, maximum: int = 256,
          allow_none: bool = False) -> Optional[str]:
    if allow_none and value is None:
        return None
    if (not isinstance(value, str) or not value.strip()
            or len(value) > maximum or any(ord(char) < 32 for char in value)):
        raise FileChangeError("%s must be bounded non-empty text" % what)
    return value


def _integer(value: Any, what: str, *, low: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise FileChangeError("%s must be an integer of at least %d" % (what, low))
    return value


def _parse_owner(value: Any) -> Dict[str, Any]:
    owner = _closed(value, (
        "workspace_sha256", "session_id", "conversation_id", "branch"),
        what="file change owner")
    _digest(owner["workspace_sha256"], "owner.workspace_sha256")
    _text(owner["session_id"], "owner.session_id")
    _text(owner["conversation_id"], "owner.conversation_id")
    _text(owner["branch"], "owner.branch")
    return owner


def _parse_origin(value: Any) -> Dict[str, Any]:
    origin = _closed(value, ("run_id", "tool_call_id", "profile_id"),
                     what="file change origin")
    for field in ("run_id", "tool_call_id", "profile_id"):
        _text(origin[field], "origin.%s" % field)
    return origin


def _parse_identity(value: Any, what: str) -> Dict[str, int]:
    identity = _closed(value, ("device", "inode", "mode", "size", "mtime_ns"),
                       what=what)
    for field in ("device", "inode", "mode", "size", "mtime_ns"):
        _integer(identity[field], "%s.%s" % (what, field))
    return identity


def _parse_object(value: Any, what: str) -> Dict[str, Any]:
    item = _closed(value, ("bytes", "sha256", "object"), what=what)
    size = _integer(item["bytes"], "%s.bytes" % what)
    if size > MAX_CONTENT_BYTES:
        raise FileChangeError("%s exceeds 16384 bytes" % what,
                              error_type="file_too_large")
    digest = _digest(item["sha256"], "%s.sha256" % what)
    if item["object"] != "objects/%s" % digest:
        raise FileChangeError("%s object reference does not match its digest" % what)
    return item


def _parse_before(value: Any) -> Dict[str, Any]:
    item = _closed(value, ("exists", "bytes", "sha256", "object",
                           "file_identity"), what="file change before")
    if item["exists"] is not True:
        raise FileChangeError("replace before image must declare exists=true")
    _parse_object({key: item[key] for key in ("bytes", "sha256", "object")},
                  "file change before")
    identity = _parse_identity(item["file_identity"], "before.file_identity")
    if identity["size"] != item["bytes"]:
        raise FileChangeError("before identity size does not match before bytes")
    return item


def _parse_approval(value: Any, change: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    approval = _closed(value, ("proposal_sha256", "policy_sha256", "approval_id"),
                       what="file change approval")
    if (approval["proposal_sha256"] != change["proposal_sha256"]
            or approval["policy_sha256"] != change["policy_sha256"]):
        raise FileChangeError("file change approval binding is stale",
                              error_type="file_stale_proposal")
    _text(approval["approval_id"], "approval.approval_id", maximum=128)
    return approval


def _parse_receipt(value: Any, change: Dict[str, Any]) -> Dict[str, Any]:
    receipt = _closed(value, (
        "status", "change_id", "operation", "path", "before_sha256",
        "after_sha256", "before_lines", "after_lines", "file_identity",
        "acceptance", "undo_action"), what="file change receipt")
    if (receipt["status"] != "applied"
            or receipt["change_id"] != change["change_id"]
            or receipt["operation"] != change["operation"]
            or receipt["path"] != change["path"]
            or receipt["after_sha256"] != change["after"]["sha256"]):
        raise FileChangeError("file change receipt does not match its proposal")
    expected_before = (change["before"]["sha256"]
                       if change["operation"] == "replace" else None)
    if receipt["before_sha256"] != expected_before:
        raise FileChangeError("file change receipt has the wrong before digest")
    for field in ("before_lines", "after_lines"):
        _integer(receipt[field], "receipt.%s" % field)
    _parse_identity(receipt["file_identity"], "receipt.file_identity")
    if receipt["acceptance"] != "not_run":
        raise FileChangeError("file change receipt acceptance must be not_run")
    expected_undo = ("restore_before" if change["operation"] == "replace"
                     else "remove_created")
    if receipt["undo_action"] != expected_undo:
        raise FileChangeError("file change receipt has the wrong undo action")
    return receipt


def _parse_events(events: Any, change: Dict[str, Any]) -> None:
    if not isinstance(events, list) or not events:
        raise FileChangeError("file change events must be a non-empty list")
    transitions = {
        None: {"proposed"},
        "proposed": {"confirmed", "rejected"},
        "confirmed": {"prepared", "rejected", "conflict", "failed"},
        "prepared": {"applied", "conflict", "failed", "recovery_required"},
        "applied": set(),
        "rejected": set(),
        "conflict": set(),
        "failed": set(),
        "recovery_required": set(),
    }
    previous = None
    confirmed = None
    for index, event in enumerate(events, 1):
        if not isinstance(event, dict):
            raise FileChangeError("file change event must be an object")
        event_type = event.get("type")
        if event_type not in transitions.get(previous, set()):
            raise FileChangeError("invalid file change event transition")
        if event.get("sequence") != index:
            raise FileChangeError("file change event sequence must be consecutive")
        if event_type == "proposed":
            _closed(event, ("type", "sequence", "proposal_sha256"),
                    what="proposed event")
            if event["proposal_sha256"] != change["proposal_sha256"]:
                raise FileChangeError("proposed event has the wrong digest")
        elif event_type == "confirmed":
            _closed(event, ("type", "sequence", "proposal_sha256",
                            "policy_sha256", "approval_id"),
                    what="confirmed event")
            confirmed = {key: event[key] for key in (
                "proposal_sha256", "policy_sha256", "approval_id")}
            _parse_approval(confirmed, change)
        elif event_type == "prepared":
            _closed(event, ("type", "sequence", "proposal_sha256",
                            "policy_sha256"), what="prepared event")
            if (event["proposal_sha256"] != change["proposal_sha256"]
                    or event["policy_sha256"] != change["policy_sha256"]):
                raise FileChangeError("prepared event has a stale binding")
        elif event_type == "applied":
            _closed(event, ("type", "sequence", "receipt"),
                    what="applied event")
            _parse_receipt(event["receipt"], change)
        else:
            _closed(event, ("type", "sequence", "error_type", "reason",
                            "effect_disposition"), what="terminal file change event")
            if event["error_type"] not in {
                    "file_permission_denied", "file_stale_proposal",
                    "file_conflict", "file_confirmation_required",
                    "file_unsupported_runtime", "file_too_large",
                    "file_store_full", "file_invalid_record",
                    "file_recovery_required"}:
                raise FileChangeError("terminal event has an unknown error type")
            _text(event["reason"], "event.reason", maximum=256)
            if event["effect_disposition"] not in {"none", "applied", "unknown"}:
                raise FileChangeError("terminal event has an invalid effect disposition")
        previous = event_type
    approval = change["approval"]
    if approval is None and confirmed is not None:
        raise FileChangeError("confirmed event requires approval data")
    if approval is not None and confirmed != approval:
        raise FileChangeError("approval data does not match the confirmed event")


def parse_file_change(payload: Any) -> Dict[str, Any]:
    """Validate the closed file-change record and its state transitions."""
    import copy
    change = _closed(payload, (
        "schema_version", "change_id", "owner", "origin", "policy_sha256",
        "path", "operation", "after", "proposal_sha256", "approval", "events"),
        ("before",), what="file change record")
    if change["schema_version"] != SCHEMA_VERSION:
        raise FileChangeError("unsupported file change schema version")
    if not isinstance(change["change_id"], str) or not _CHANGE_ID.fullmatch(change["change_id"]):
        raise FileChangeError("change_id must be change_ plus 32 lowercase hex")
    _parse_owner(change["owner"])
    _parse_origin(change["origin"])
    _digest(change["policy_sha256"], "policy_sha256")
    normalized = _path(change["path"])
    if normalized != change["path"]:
        raise FileChangeError("file change path must already be normalized")
    if change["operation"] not in {"create", "replace"}:
        raise FileChangeError("file change operation must be create or replace")
    _parse_object(change["after"], "file change after")
    if change["operation"] == "replace":
        if "before" not in change:
            raise FileChangeError("replace file change requires a before image")
        _parse_before(change["before"])
    elif "before" in change:
        raise FileChangeError("create file change must not include a before image")
    proposal_sha256 = _digest(change["proposal_sha256"], "proposal_sha256")
    immutable_keys = [
        "schema_version", "change_id", "owner", "origin", "policy_sha256",
        "path", "operation", "after",
    ]
    immutable = {key: change[key] for key in immutable_keys}
    if change["operation"] == "replace":
        immutable["before"] = change["before"]
    if _sha256(_canonical(immutable)) != proposal_sha256:
        raise FileChangeError("proposal digest does not match immutable fields",
                              error_type="file_stale_proposal")
    _parse_approval(change["approval"], change)
    _parse_events(change["events"], change)
    return copy.deepcopy(change)


def propose_file_change(root: Path, path: str, content: str, *,
                        store: FileChangeStore,
                        owner: Mapping[str, Any],
                        origin: Mapping[str, Any],
                        policy_sha256: str,
                        read_paths: Iterable[str],
                        write_paths: Iterable[str],
                        change_id: Optional[str] = None) -> Dict[str, Any]:
    """Persist a bounded proposal and return its complete derived diff."""
    _require_store_workspace(root, store)
    normalized = _path(path)
    if not _allowed(normalized, write_paths):
        raise FileChangeError("write grant does not include the target",
                              error_type="file_permission_denied")
    if not isinstance(content, str):
        raise FileChangeError("candidate content must be UTF-8 text")
    try:
        after_bytes = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise FileChangeError(
            "candidate content must be UTF-8 text") from exc
    if len(after_bytes) > MAX_CONTENT_BYTES:
        raise FileChangeError("candidate exceeds 16384 bytes",
                              error_type="file_too_large")
    before_observed, before_identity = _read_target(
        root, normalized, allow_missing=True)
    if before_observed is None:
        operation = "create"
        before_bytes = b""
        before_text = ""
    else:
        if not _allowed(normalized, read_paths):
            raise FileChangeError("replace requires a read grant",
                                  error_type="file_permission_denied")
        operation = "replace"
        before_bytes = before_observed
        before_text = before_bytes.decode("utf-8")
        if before_bytes == after_bytes:
            raise FileChangeError("candidate content does not change the file")
    identifier = change_id or ("change_%s" % uuid4().hex)
    if not _CHANGE_ID.fullmatch(identifier):
        raise FileChangeError("change_id must be change_ plus 32 lowercase hex")
    rendered = _render_diff(normalized, before_text, content)
    after = _object(after_bytes, store)
    immutable = {
        "schema_version": SCHEMA_VERSION,
        "change_id": identifier,
        "owner": dict(owner),
        "origin": dict(origin),
        "policy_sha256": _digest(policy_sha256, "policy_sha256"),
        "path": normalized,
        "operation": operation,
        "after": after,
    }
    if operation == "replace":
        before = _object(before_bytes, store)
        before["exists"] = True
        before["file_identity"] = before_identity
        immutable["before"] = before
    proposal_sha256 = _sha256(_canonical(immutable))
    change = dict(immutable)
    change.update({
        "proposal_sha256": proposal_sha256,
        "approval": None,
        "events": [{
            "type": "proposed",
            "sequence": 1,
            "proposal_sha256": proposal_sha256,
        }],
    })
    change = parse_file_change(change)
    try:
        store.create_record(change)
    except FileChangeStoreError as exc:
        _raise_store_error(exc)
    return {
        "change": change,
        "diff": rendered,
        "diff_bytes": len(rendered.encode("utf-8")),
    }


def confirm_file_change(store: FileChangeStore, change_id: str, *,
                        proposal_sha256: str, policy_sha256: str,
                        approval_id: str) -> Dict[str, Any]:
    """Bind one local interactive confirmation to the exact proposal."""
    if (not isinstance(approval_id, str) or not approval_id.strip()
            or len(approval_id) > 128):
        raise FileChangeError("approval_id must be bounded non-empty text")
    expected_proposal = _digest(proposal_sha256, "proposal_sha256")
    expected_policy = _digest(policy_sha256, "policy_sha256")
    try:
        claim = store.acquire_writer(change_id, owner="confirm:%s" % approval_id)
    except FileChangeStoreError as exc:
        _raise_store_error(exc)
    try:
        try:
            change = parse_file_change(store.load_record(change_id))
        except FileChangeStoreError as exc:
            _raise_store_error(exc)
        if (change["proposal_sha256"] != expected_proposal
                or change["policy_sha256"] != expected_policy):
            raise FileChangeError(
                "confirmation does not match the current proposal and policy",
                error_type="file_stale_proposal")
        if change["approval"] is not None or change["events"][-1]["type"] != "proposed":
            raise FileChangeError("file change is not awaiting confirmation")
        approval = {
            "proposal_sha256": expected_proposal,
            "policy_sha256": expected_policy,
            "approval_id": approval_id,
        }
        change["approval"] = approval
        change["events"].append({
            "type": "confirmed",
            "sequence": len(change["events"]) + 1,
            **approval,
        })
        change = parse_file_change(change)
        try:
            store.update_record(change)
        except FileChangeStoreError as exc:
            _raise_store_error(exc)
        return change
    finally:
        claim.release()


def _persist_terminal(store: FileChangeStore, change: Dict[str, Any], *,
                      event_type: str, error_type: str, reason: str,
                      effect_disposition: str = "none") -> None:
    change["events"].append({
        "type": event_type,
        "sequence": len(change["events"]) + 1,
        "error_type": error_type,
        "reason": reason,
        "effect_disposition": effect_disposition,
    })
    parse_file_change(change)
    try:
        store.update_record(change)
    except FileChangeStoreError as exc:
        _raise_store_error(exc)


def _line_count(data: bytes) -> int:
    if not data:
        return 0
    return len(data.decode("utf-8").splitlines())


def apply_file_change(root: Path, store: FileChangeStore, change_id: str, *,
                      current_policy_sha256: str,
                      write_paths: Iterable[str]) -> Dict[str, Any]:
    """Apply one confirmed proposal and persist an independent readback receipt."""
    _require_store_workspace(root, store)
    try:
        change = parse_file_change(store.load_record(change_id))
    except FileChangeStoreError as exc:
        _raise_store_error(exc)
    if change["events"][-1]["type"] == "applied":
        return change["events"][-1]["receipt"]
    try:
        claim = store.acquire_writer(
            change_id, owner=change["owner"]["session_id"])
    except FileChangeStoreError as exc:
        _raise_store_error(exc)
    preserve_claim = False
    try:
        try:
            change = parse_file_change(store.load_record(change_id))
        except FileChangeStoreError as exc:
            _raise_store_error(exc)
        current_policy = _digest(
            current_policy_sha256, "current_policy_sha256")
        state = change["events"][-1]["type"]
        if state == "applied":
            return change["events"][-1]["receipt"]
        if state == "proposed":
            raise FileChangeError("trusted confirmation is required",
                                  error_type="file_confirmation_required")
        if state != "confirmed":
            raise FileChangeError("file change is already settled")
        approval = change["approval"]
        if change["policy_sha256"] != current_policy:
            _persist_terminal(
                store, change, event_type="rejected",
                error_type="file_stale_proposal", reason="policy_changed")
            raise FileChangeError("file grant policy changed after proposal",
                                  error_type="file_stale_proposal")
        if not _allowed(change["path"], write_paths):
            _persist_terminal(
                store, change, event_type="rejected",
                error_type="file_permission_denied", reason="write_grant_revoked")
            raise FileChangeError("write grant no longer includes the target",
                                  error_type="file_permission_denied")
        if (approval["proposal_sha256"] != change["proposal_sha256"]
                or approval["policy_sha256"] != current_policy):
            _persist_terminal(
                store, change, event_type="rejected",
                error_type="file_stale_proposal", reason="approval_binding_changed")
            raise FileChangeError("confirmation is stale",
                                  error_type="file_stale_proposal")
        try:
            current, current_identity = _read_target(
                root, change["path"],
                allow_missing=change["operation"] == "create")
            if not _matches_before(change, current, current_identity):
                raise FileChangeError("target changed after proposal",
                                      error_type="file_conflict")
        except FileChangeError as exc:
            _persist_terminal(
                store, change,
                event_type=("conflict" if exc.error_type == "file_conflict"
                            else "failed"),
                error_type=exc.error_type,
                reason=("target_changed" if exc.error_type == "file_conflict"
                        else "target_preflight_failed"),
                effect_disposition=exc.effect_disposition)
            raise
        prepared = {
            "type": "prepared",
            "sequence": len(change["events"]) + 1,
            "proposal_sha256": change["proposal_sha256"],
            "policy_sha256": current_policy,
        }
        change["events"].append(prepared)
        parse_file_change(change)
        try:
            store.update_record(change)
        except FileChangeStoreError as exc:
            _raise_store_error(exc)

        try:
            candidate = store.read_object(
                change["after"]["object"], change["after"]["sha256"])
        except FileChangeStoreError as exc:
            preserve_claim = True
            try:
                claim.preserve()
            except FileChangeStoreError:
                pass
            raise FileChangeError(
                "file change preparation is incomplete; recovery is required",
                error_type="file_recovery_required") from exc
        try:
            observed, after_identity = _atomic_apply(root, change, candidate)
        except FileChangeError as exc:
            if exc.effect_disposition in {"applied", "unknown"}:
                preserve_claim = True
                try:
                    claim.preserve()
                except FileChangeStoreError:
                    pass
            else:
                _persist_terminal(
                    store, change,
                    event_type=("conflict" if exc.error_type == "file_conflict"
                                else "failed"),
                    error_type=exc.error_type,
                    reason=("target_changed" if exc.error_type == "file_conflict"
                            else "apply_failed"),
                    effect_disposition=exc.effect_disposition)
            raise
        receipt = {
            "status": "applied",
            "change_id": change_id,
            "operation": change["operation"],
            "path": change["path"],
            "before_sha256": (change["before"]["sha256"]
                              if change["operation"] == "replace" else None),
            "after_sha256": _sha256(observed),
            "before_lines": _line_count(current or b""),
            "after_lines": _line_count(observed),
            "file_identity": after_identity,
            "acceptance": "not_run",
            "undo_action": ("restore_before" if change["operation"] == "replace"
                             else "remove_created"),
        }
        change["events"].append({
            "type": "applied",
            "sequence": len(change["events"]) + 1,
            "receipt": receipt,
        })
        parse_file_change(change)
        try:
            store.update_record(change)
        except FileChangeStoreError as exc:
            preserve_claim = True
            try:
                claim.preserve()
            except FileChangeStoreError:
                pass
            raise FileChangeError(
                "file was applied but its durable receipt could not be saved",
                error_type="file_recovery_required",
                effect_disposition="applied") from exc
        return receipt
    finally:
        if not preserve_claim:
            claim.release()
