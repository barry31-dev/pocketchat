"""Host-owned session grants and proposal-only file work.

No model-callable method confirms, applies, undoes or changes permissions. The
transaction store owns durable evidence; this facade owns only current grants
and the complete pending diff for a later trusted terminal interface.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .file_change_store import FileChangeStore
from .file_changes import (FileChangeError, _path, _read_target_at,
                           _require_secure_target_runtime, propose_file_change)


class FileWorkError(ValueError):
    def __init__(self, message: str, error_type: str = 'file_permission_denied'):
        super().__init__(message)
        self.error_type = error_type


def _digest(value) -> str:
    data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def _profile(config):
    return {key: config.get(key) or '' for key in ('profile', 'model', 'base_url')} | {
        'adapter': getattr(config.get('provider_adapter'), 'name', 'openai-compatible'),
    }


class FileWork:
    """A session-local grant owner; nothing is implicitly restored on restart."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve(strict=True)
        info = self.workspace.stat()
        if not self.workspace.is_dir():
            raise FileWorkError('workspace must be a directory')
        self._workspace_identity = (info.st_dev, info.st_ino)
        self._binding = None
        self._host_identity = None
        self._read_paths = frozenset()
        self._write_paths = frozenset()
        self._generation = 0
        self._pending = None
        self._store = None

    def bind(self, config, session_id: str, *, conversation_id=None, branch="main",
             host_identity=None) -> None:
        if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
            raise FileWorkError('session identity must be bounded non-empty text')
        binding = {'session_id': session_id, 'profile': _profile(config),
                   'conversation_id': conversation_id or session_id, 'branch': branch}
        if self._host_identity is not None and host_identity is not self._host_identity:
            raise FileWorkError('file grants cannot be shared by different Session instances')
        if self._binding is not None and self._binding != binding:
            raise FileWorkError('file grants cannot be imported into another session or profile')
        self._binding = binding
        self._host_identity = host_identity

    def _invalidate(self) -> None:
        self._read_paths = frozenset()
        self._write_paths = frozenset()
        self._pending = None
        self._generation += 1

    def _require_binding(self, config=None) -> None:
        if self._binding is None:
            raise FileWorkError('file work must be bound to a session')
        try:
            info = self.workspace.lstat()
            same_workspace = (stat.S_ISDIR(info.st_mode)
                              and (info.st_dev, info.st_ino) == self._workspace_identity)
        except OSError:
            same_workspace = False
        if not same_workspace or (config is not None and _profile(config) != self._binding['profile']):
            self._invalidate()
            raise FileWorkError('workspace or provider changed; explicit reactivation is required',
                                'file_stale_grants')

    def disclosure(self):
        self._require_binding()
        profile = self._binding['profile']
        endpoint = urlsplit(profile['base_url'])
        # Display the destination, not URL userinfo/query credentials.
        host = endpoint.netloc.rsplit('@', 1)[-1]
        destination = urlunsplit((endpoint.scheme, host, endpoint.path, '', ''))
        return {'sha256': _digest(self._binding),
                'text': 'Selected file text used in an answer is sent to provider %s '
                        '(profile %s, model %s). Grants apply to this session only.'
                        % (destination, profile['profile'], profile['model'])}

    def _paths(self, values, *, reject_directories=True):
        if not isinstance(values, (list, tuple, set, frozenset)):
            raise FileWorkError('grants must be an explicit collection of exact file paths')
        result = set()
        for value in values:
            try:
                valid = isinstance(value, str) and len(value.encode('utf-8')) <= 1024
            except UnicodeEncodeError:
                valid = False
            if not valid:
                raise FileWorkError('grant paths must be bounded UTF-8 text')
            if any(char in value for char in '*?[]'):
                raise FileWorkError('directory and glob grants are not supported')
            try:
                path = _path(value)
            except FileChangeError as exc:
                raise FileWorkError(str(exc), exc.error_type) from exc
            if reject_directories and (self.workspace / path).is_dir():
                raise FileWorkError('directory grants are not supported')
            result.add(path)
        return frozenset(result)

    def grant(self, *, read_paths=(), write_paths=(), disclosure_sha256: str) -> None:
        """Called only by a trusted host after showing the provider disclosure."""
        self._require_binding()
        if disclosure_sha256 != self.disclosure()['sha256']:
            raise FileWorkError('provider disclosure does not match this session')
        reads = self._read_paths | self._paths(read_paths)
        writes = self._write_paths | self._paths(write_paths)
        if len(reads | writes) > 64:
            raise FileWorkError('at most 64 exact file grants are supported')
        if reads != self._read_paths or writes != self._write_paths:
            self._read_paths, self._write_paths = reads, writes
            self._generation += 1
            self._pending = None

    def revoke(self, paths) -> None:
        self._require_binding()
        paths = self._paths(paths, reject_directories=False)
        self._read_paths -= paths
        self._write_paths -= paths
        self._generation += 1
        self._pending = None

    @property
    def store(self):
        self._require_binding()
        if self._store is None:
            self._store = FileChangeStore(self.workspace)
        return self._store

    def state(self):
        return {'read_paths': sorted(self._read_paths),
                'write_paths': sorted(self._write_paths),
                'pending': copy.deepcopy(self._pending)}

    def _policy(self):
        return _digest({'binding': self._binding, 'generation': self._generation,
                        'workspace_identity': self._workspace_identity,
                        'read_paths': sorted(self._read_paths),
                        'write_paths': sorted(self._write_paths)})

    def request_config(self, config):
        self._require_binding(config)
        tools = []
        if self._read_paths:
            tools.append('read_file')
        if self._write_paths and self._pending is None:
            tools.append('propose_file_change')
        return {**config, 'file_work_tools': tools,
                'file_work_policy_sha256': self._policy()}

    def _read_granted(self, path):
        # Attest the directory actually opened, not just the pathname checked
        # earlier. Keep that descriptor pinned through all no-follow traversal.
        _require_secure_target_runtime()
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors = []
        try:
            parent = os.open(str(self.workspace), flags)
            descriptors.append(parent)
            info = os.fstat(parent)
            if (info.st_dev, info.st_ino) != self._workspace_identity:
                self._invalidate()
                raise FileWorkError('opened workspace differs from the granted identity',
                                    'file_stale_grants')
            parts = path.split('/')
            for part in parts[:-1]:
                parent = os.open(part, flags, dir_fd=parent)
                descriptors.append(parent)
            result = _read_target_at(parent, parts[-1], allow_missing=False)
            self._require_binding()
            return result
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def execute(self, name, arguments, *, config, policy_sha256: str,
                run_id: str, tool_call_id: str, max_bytes: int):
        self._require_binding(config)
        if (run_id != self._binding['session_id'] or policy_sha256 != self._policy()):
            raise FileWorkError('file grants changed after this request', 'file_stale_grants')
        if name not in ('read_file', 'propose_file_change'):
            raise FileWorkError('this file-work operation is not model-callable', 'unsupported_tool')
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise FileWorkError('result budget must be positive', 'invalid_result_limit')
        path = _path(arguments['path'])
        if name == 'read_file':
            if path not in self._read_paths:
                raise FileWorkError('read grant does not include the target')
            data, _ = self._read_granted(path)
            bounded = data[:min(max_bytes, 16 * 1024)].decode('utf-8', errors='ignore')
            return {'path': path, 'content': bounded, 'bytes': len(data),
                    'truncated': len(bounded.encode('utf-8')) < len(data)}
        if self._pending is not None:
            raise FileWorkError('one complete proposal is already awaiting review', 'file_pending_proposal')
        if path not in self._write_paths:
            raise FileWorkError('write grant does not include the target')
        owner = {'workspace_sha256': _digest(str(self.workspace)),
                 'session_id': self._binding['session_id'],
                 'conversation_id': self._binding['conversation_id'],
                 'branch': self._binding['branch']}
        result = propose_file_change(
            self.workspace, path, arguments['content'], store=self.store,
            owner=owner, origin={'run_id': run_id, 'tool_call_id': tool_call_id,
                                 'profile_id': self._binding['profile']['profile'] or 'default'},
            policy_sha256=self._policy(), read_paths=self._read_paths,
            write_paths=self._write_paths)
        self._pending = copy.deepcopy(result)
        change = result['change']
        # The provider gets a bounded receipt reference, never an applied claim.
        # The complete diff remains host-owned and is never approved via a
        # truncated model result. No unpaired tool message is added later.
        reference = {key: change[key] for key in ('change_id', 'proposal_sha256', 'path', 'operation')}
        return {'status': 'pending', 'path': path,
                'content': json.dumps(reference, sort_keys=True)}
