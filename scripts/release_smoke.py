#!/usr/bin/env python3
"""Build and install one PocketChat wheel, then verify release-critical surface."""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import stat
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Tuple

REQUIRED_SCHEMAS = (
    "pocketchat/schemas/acceptance.schema.json",
    "pocketchat/schemas/task-plan.schema.json",
    "pocketchat/schemas/task-spec.schema.json",
    "pocketchat/schemas/task.schema.json",
    "pocketchat/schemas/trace.schema.json",
)
REQUIRED_MODULES = (
    "pocketchat/__init__.py",
    "pocketchat/cli.py",
    "pocketchat/task_exec.py",
    "pocketchat/task_plan.py",
    "pocketchat/task_checkpoint.py",
    "pocketchat/task_model_repair.py",
)
FORBIDDEN_PREFIXES = ("pocketchat/web/", "pocketchat/server.py")
REQUIRED_TASK_COMMANDS = (
    "task context",
    "task spec",
    "task plan",
    "task exec",
    "task checkpoint",
    "task delegate",
    "task run",
    "task verify",
    "task status",
    "task resume",
    "task report",
)

PACKAGE_MANIFEST_SCHEMA = "pocketchat-package-manifest.v1"
PACKAGE_NAME = "pocketchat"
PACKAGE_ENTRY_POINT = "pocketchat.cli:main"
REQUIRED_DIST_INFO = ("METADATA", "WHEEL", "RECORD", "entry_points.txt")
DEFAULT_MAX_MANIFEST_MEMBERS = 2048
DEFAULT_MAX_MANIFEST_MEMBER_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_MANIFEST_TOTAL_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_MANIFEST_REPORT_BYTES = 256 * 1024


class ReleaseSmokeError(RuntimeError):
    """The built or installed package does not satisfy the release surface."""


@dataclass(frozen=True)
class WheelArtifact:
    path: Path
    size: int
    sha256: str
    members: Tuple[str, ...]
    version: str
    manifest: Dict[str, Any]


@dataclass(frozen=True)
class ReleaseSmokeResult:
    artifact: WheelArtifact
    installed: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "schema_version": "1",
            "version": self.artifact.version,
            "wheel": self.artifact.path.name,
            "wheel_bytes": self.artifact.size,
            "wheel_sha256": self.artifact.sha256,
            "members": len(self.artifact.members),
            "schemas": list(REQUIRED_SCHEMAS),
            "commands": sorted(self.installed["commands"]),
            "package_manifests": {
                "wheel": self.artifact.manifest,
                "installed": self.installed["package_manifest"],
            },
        }


def _positive_limit(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ReleaseSmokeError("%s must be a positive integer" % name)
    return value


def _safe_logical_path(name: str) -> str:
    if (not isinstance(name, str) or not name or name.startswith(("/", "\\"))
            or "\\" in name):
        raise ReleaseSmokeError("package contains an unsafe member path")
    candidate = PurePosixPath(name)
    if candidate.is_absolute() or any(part in ("", ".", "..")
                                     for part in candidate.parts):
        raise ReleaseSmokeError("package contains an unsafe member path")
    return candidate.as_posix()


def _is_generated_installed_member(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if "__pycache__" in parts or path.endswith((".pyc", ".pyo")):
        return True
    if ".dist-info/" in path:
        leaf = path.rsplit("/", 1)[-1]
        if leaf in ("INSTALLER", "REQUESTED", "direct_url.json"):
            return True
    return False


def _category_for_member(path: str, dist_info: str) -> str:
    if path.startswith("pocketchat/schemas/") and path.endswith(".json"):
        return "schema"
    if path.startswith("pocketchat/") and path.endswith(".py"):
        return "module"
    if path.startswith("pocketchat/"):
        return "package_data"
    if path.startswith(dist_info + "/"):
        return "metadata"
    raise ReleaseSmokeError("package contains an unexpected top-level member")


def _record_collision(path: str, collisions: Dict[str, str]) -> None:
    collision_key = unicodedata.normalize("NFC", path).casefold()
    previous = collisions.get(collision_key)
    if previous is not None and previous != path:
        raise ReleaseSmokeError("package contains colliding member paths")
    collisions[collision_key] = path


def _wheel_entries(path: Path, *, max_members: int, max_member_bytes: int,
                   max_total_bytes: int) -> List[Tuple[str, bytes]]:
    entries: List[Tuple[str, bytes]] = []
    total = 0
    seen = set()
    collisions: Dict[str, str] = {}
    try:
        with zipfile.ZipFile(Path(path)) as archive:
            for info in archive.infolist():
                raw_name = info.filename.rstrip("/")
                logical = _safe_logical_path(raw_name)
                if info.is_dir():
                    continue
                if logical in seen:
                    raise ReleaseSmokeError("wheel contains duplicate members")
                seen.add(logical)
                _record_collision(logical, collisions)
                if info.flag_bits & 0x1:
                    raise ReleaseSmokeError("wheel contains an encrypted member")
                mode = (info.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(mode)
                if file_type and not stat.S_ISREG(mode):
                    raise ReleaseSmokeError("wheel contains a non-regular member")
                if _is_generated_installed_member(logical):
                    continue
                if len(entries) >= max_members:
                    raise ReleaseSmokeError("package member count exceeds limit")
                if info.file_size > max_member_bytes:
                    raise ReleaseSmokeError("package member exceeds byte limit")
                total += info.file_size
                if total > max_total_bytes:
                    raise ReleaseSmokeError("package total bytes exceed limit")
                data = archive.read(info)
                if len(data) != info.file_size:
                    raise ReleaseSmokeError("wheel member size changed while reading")
                entries.append((logical, data))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseSmokeError("cannot read wheel archive: %s" % exc)
    if not entries:
        raise ReleaseSmokeError("wheel member list is empty")
    return entries


def _installed_entries(path: Path, *, max_members: int, max_member_bytes: int,
                       max_total_bytes: int) -> List[Tuple[str, bytes]]:
    root = Path(path)
    try:
        root_stat = os.lstat(root)
    except OSError as exc:
        raise ReleaseSmokeError("cannot read installed package root: %s" % exc)
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise ReleaseSmokeError("installed package root is not a regular directory")

    try:
        with os.scandir(root) as iterator:
            root_entries = list(iterator)
    except OSError as exc:
        raise ReleaseSmokeError("cannot list installed package root: %s" % exc)
    package_candidates = []
    dist_candidates = []
    for entry in root_entries:
        normalized = unicodedata.normalize("NFC", entry.name).casefold()
        if normalized == "pocketchat":
            package_candidates.append(entry)
        elif normalized.startswith("pocketchat-") and normalized.endswith(".dist-info"):
            dist_candidates.append(entry)
    if len(package_candidates) != 1 or package_candidates[0].name != "pocketchat":
        raise ReleaseSmokeError("installed tree must contain one pocketchat package")
    if len(dist_candidates) != 1 or not dist_candidates[0].name.startswith("pocketchat-"):
        raise ReleaseSmokeError("package must contain one pocketchat dist-info tree")
    selected = (package_candidates[0], dist_candidates[0])
    for entry in selected:
        if entry.is_symlink():
            raise ReleaseSmokeError("installed package contains a symlink")
        try:
            entry_stat = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise ReleaseSmokeError("cannot inspect installed package entry: %s" % exc)
        if not stat.S_ISDIR(entry_stat.st_mode):
            raise ReleaseSmokeError("installed package entry is non-regular")

    entries: List[Tuple[str, bytes]] = []
    total = 0
    collisions: Dict[str, str] = {}

    def walk(directory: Path, prefix: str) -> None:
        nonlocal total
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            raise ReleaseSmokeError("cannot list installed package entry: %s" % exc)
        for child in children:
            logical = _safe_logical_path(prefix + "/" + child.name)
            _record_collision(logical, collisions)
            if child.is_symlink():
                raise ReleaseSmokeError("installed package contains a symlink")
            try:
                child_stat = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise ReleaseSmokeError("cannot inspect installed package member: %s" % exc)
            if stat.S_ISDIR(child_stat.st_mode):
                if "__pycache__" in PurePosixPath(logical).parts:
                    continue
                walk(Path(child.path), logical)
                continue
            if not stat.S_ISREG(child_stat.st_mode):
                raise ReleaseSmokeError("installed package contains a non-regular member")
            if _is_generated_installed_member(logical):
                continue
            if len(entries) >= max_members:
                raise ReleaseSmokeError("package member count exceeds limit")
            if child_stat.st_size > max_member_bytes:
                raise ReleaseSmokeError("package member exceeds byte limit")
            total += child_stat.st_size
            if total > max_total_bytes:
                raise ReleaseSmokeError("package total bytes exceed limit")
            try:
                data = Path(child.path).read_bytes()
            except OSError as exc:
                raise ReleaseSmokeError("cannot read installed package member: %s" % exc)
            if len(data) != child_stat.st_size:
                raise ReleaseSmokeError("installed member size changed while reading")
            entries.append((logical, data))

    for entry in selected:
        walk(Path(entry.path), entry.name)
    if not entries:
        raise ReleaseSmokeError("installed package member list is empty")
    return entries


def _dist_info_identity(entries: List[Tuple[str, bytes]]) -> Tuple[str, str, str]:
    roots = sorted({path.split("/", 1)[0] for path, _ in entries
                    if "/" in path and path.split("/", 1)[0].endswith(".dist-info")})
    if len(roots) != 1 or not roots[0].casefold().startswith("pocketchat-"):
        raise ReleaseSmokeError("package must contain one pocketchat dist-info tree")
    dist_info = roots[0]
    by_path = dict(entries)
    missing = [leaf for leaf in REQUIRED_DIST_INFO
               if "%s/%s" % (dist_info, leaf) not in by_path]
    if missing:
        raise ReleaseSmokeError("package is missing required metadata: %s"
                                % ", ".join(missing))
    missing_schemas = [name for name in REQUIRED_SCHEMAS if name not in by_path]
    if missing_schemas:
        raise ReleaseSmokeError("package is missing required schema(s): %s"
                                % ", ".join(missing_schemas))
    missing_modules = [name for name in REQUIRED_MODULES if name not in by_path]
    if missing_modules:
        raise ReleaseSmokeError("package is missing required module(s): %s"
                                % ", ".join(missing_modules))
    forbidden = [name for name in by_path if name.startswith(FORBIDDEN_PREFIXES)]
    if forbidden:
        raise ReleaseSmokeError("package contains removed web/server surface: %s"
                                % ", ".join(forbidden))
    try:
        metadata_text = by_path[dist_info + "/METADATA"].decode("utf-8")
        metadata = Parser().parsestr(metadata_text)
    except UnicodeDecodeError as exc:
        raise ReleaseSmokeError("package METADATA is not UTF-8") from exc
    name = metadata.get("Name")
    version = metadata.get("Version")
    if not name or name.casefold() != PACKAGE_NAME or not version:
        raise ReleaseSmokeError("package metadata identity is invalid")
    root_version = dist_info[len("pocketchat-"):-len(".dist-info")].replace("_", "-")
    if root_version != version.replace("_", "-"):
        raise ReleaseSmokeError("package metadata version does not match dist-info")
    try:
        entry_text = by_path[dist_info + "/entry_points.txt"].decode("utf-8")
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(entry_text)
        target = config.get("console_scripts", "pocketchat")
    except (UnicodeDecodeError, configparser.Error, KeyError) as exc:
        raise ReleaseSmokeError("package entry point metadata is invalid") from exc
    target = target.strip()
    if target != PACKAGE_ENTRY_POINT:
        raise ReleaseSmokeError("package entry point target is invalid")
    return dist_info, version, target


def _validate_manifest_shape(manifest: Dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise ReleaseSmokeError("package manifest must be an object")
    if (manifest.get("schema") != PACKAGE_MANIFEST_SCHEMA
            or manifest.get("schema_version") != 1):
        raise ReleaseSmokeError("package manifest schema is invalid")
    if manifest.get("source_kind") not in ("wheel", "installed_tree"):
        raise ReleaseSmokeError("package manifest source kind is invalid")
    if manifest.get("package") != PACKAGE_NAME:
        raise ReleaseSmokeError("package manifest identity is invalid")
    if not isinstance(manifest.get("version"), str) or not manifest["version"]:
        raise ReleaseSmokeError("package manifest version is invalid")
    if manifest.get("entry_point") != {
            "name": "pocketchat", "target": PACKAGE_ENTRY_POINT}:
        raise ReleaseSmokeError("package manifest entry point is invalid")
    groups = manifest.get("groups")
    if not isinstance(groups, dict) or set(groups) != {
            "metadata", "modules", "package_data", "schemas"}:
        raise ReleaseSmokeError("package manifest groups are invalid")
    members = manifest.get("members")
    if not isinstance(members, list) or not members:
        raise ReleaseSmokeError("package manifest members are invalid")
    paths = []
    collisions: Dict[str, str] = {}
    category_groups = {
        "metadata": "metadata",
        "module": "modules",
        "package_data": "package_data",
        "schema": "schemas",
    }
    expected_groups = {name: [] for name in groups}
    for member in members:
        if not isinstance(member, dict) or set(member) != {
                "path", "category", "bytes", "sha256"}:
            raise ReleaseSmokeError("package manifest member is invalid")
        path = _safe_logical_path(member["path"])
        _record_collision(path, collisions)
        category = member["category"]
        if category not in category_groups:
            raise ReleaseSmokeError("package manifest member category is invalid")
        if (not isinstance(member["bytes"], int) or isinstance(member["bytes"], bool)
                or member["bytes"] < 0):
            raise ReleaseSmokeError("package manifest member size is invalid")
        digest = member["sha256"]
        if (not isinstance(digest, str) or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)):
            raise ReleaseSmokeError("package manifest member digest is invalid")
        paths.append(path)
        expected_groups[category_groups[category]].append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ReleaseSmokeError("package manifest member order is invalid")
    if groups != expected_groups:
        raise ReleaseSmokeError("package manifest groups do not match members")


def build_package_manifest(
        source: Path, *, source_kind: str,
        max_members: int = DEFAULT_MAX_MANIFEST_MEMBERS,
        max_member_bytes: int = DEFAULT_MAX_MANIFEST_MEMBER_BYTES,
        max_total_bytes: int = DEFAULT_MAX_MANIFEST_TOTAL_BYTES,
        max_report_bytes: int = DEFAULT_MAX_MANIFEST_REPORT_BYTES,
) -> Dict[str, Any]:
    """Build a bounded, deterministic manifest for one explicit package source."""
    max_members = _positive_limit(max_members, "max_members")
    max_member_bytes = _positive_limit(max_member_bytes, "max_member_bytes")
    max_total_bytes = _positive_limit(max_total_bytes, "max_total_bytes")
    max_report_bytes = _positive_limit(max_report_bytes, "max_report_bytes")
    if source_kind == "wheel":
        entries = _wheel_entries(Path(source), max_members=max_members,
                                 max_member_bytes=max_member_bytes,
                                 max_total_bytes=max_total_bytes)
    elif source_kind == "installed_tree":
        entries = _installed_entries(Path(source), max_members=max_members,
                                     max_member_bytes=max_member_bytes,
                                     max_total_bytes=max_total_bytes)
    else:
        raise ReleaseSmokeError("unknown package manifest source kind")
    dist_info, version, target = _dist_info_identity(entries)
    members = []
    groups = {"metadata": [], "modules": [], "package_data": [], "schemas": []}
    group_for_category = {
        "metadata": "metadata",
        "module": "modules",
        "package_data": "package_data",
        "schema": "schemas",
    }
    for path, data in sorted(entries):
        category = _category_for_member(path, dist_info)
        groups[group_for_category[category]].append(path)
        members.append({
            "path": path,
            "category": category,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    manifest = {
        "schema": PACKAGE_MANIFEST_SCHEMA,
        "schema_version": 1,
        "source_kind": source_kind,
        "package": PACKAGE_NAME,
        "version": version,
        "entry_point": {"name": "pocketchat", "target": target},
        "groups": groups,
        "members": members,
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    if len(encoded) > max_report_bytes:
        raise ReleaseSmokeError("package manifest report exceeds byte limit")
    _validate_manifest_shape(manifest)
    return manifest


def _manifest_comparable_members(manifest: Dict[str, Any]) -> Dict[str, str]:
    comparable = {}
    for member in manifest["members"]:
        if member["path"].endswith(".dist-info/RECORD"):
            continue
        comparable[member["path"]] = member["sha256"]
    return comparable


def _validate_manifest_pair(wheel: Dict[str, Any], installed: Dict[str, Any]) -> None:
    _validate_manifest_shape(wheel)
    _validate_manifest_shape(installed)
    if wheel["source_kind"] != "wheel" or installed["source_kind"] != "installed_tree":
        raise ReleaseSmokeError("package manifest pair has invalid source kinds")
    for field in ("package", "version", "entry_point"):
        if wheel[field] != installed[field]:
            raise ReleaseSmokeError("package manifest identity drift")
    if _manifest_comparable_members(wheel) != _manifest_comparable_members(installed):
        raise ReleaseSmokeError("package manifest payload drift")


def required_members() -> Tuple[str, ...]:
    return REQUIRED_MODULES + REQUIRED_SCHEMAS


def read_wheel_members(path: Path) -> Tuple[str, ...]:
    entries = _wheel_entries(
        Path(path),
        max_members=DEFAULT_MAX_MANIFEST_MEMBERS,
        max_member_bytes=DEFAULT_MAX_MANIFEST_MEMBER_BYTES,
        max_total_bytes=DEFAULT_MAX_MANIFEST_TOTAL_BYTES,
    )
    return tuple(name for name, _ in entries)


def validate_artifact(artifact: WheelArtifact) -> None:
    members = set(artifact.members)
    missing_schemas = [name for name in REQUIRED_SCHEMAS if name not in members]
    if missing_schemas:
        raise ReleaseSmokeError(
            "wheel is missing required schema(s): %s" % ", ".join(missing_schemas))
    missing_modules = [name for name in REQUIRED_MODULES if name not in members]
    if missing_modules:
        raise ReleaseSmokeError(
            "wheel is missing required module(s): %s" % ", ".join(missing_modules))
    forbidden = [name for name in artifact.members
                 if name.startswith(FORBIDDEN_PREFIXES)]
    if forbidden:
        raise ReleaseSmokeError(
            "wheel contains removed web/server surface: %s" % ", ".join(forbidden))
    if not artifact.sha256 or len(artifact.sha256) != 64:
        raise ReleaseSmokeError("wheel digest is not a SHA-256")
    if artifact.size <= 0:
        raise ReleaseSmokeError("wheel is empty")
    _validate_manifest_shape(artifact.manifest)
    if artifact.manifest["source_kind"] != "wheel":
        raise ReleaseSmokeError("wheel package manifest source kind is invalid")
    if artifact.manifest["version"] != artifact.version:
        raise ReleaseSmokeError("wheel version and package manifest disagree")


def _installed_version_value(reported: str) -> str:
    prefix = PACKAGE_NAME + " "
    return reported[len(prefix):] if reported.startswith(prefix) else reported


def validate_installed(installed: Dict[str, Any]) -> None:
    if not isinstance(installed, dict):
        raise ReleaseSmokeError("installed CLI result must be an object")
    if not isinstance(installed.get("version"), str) or not installed["version"]:
        raise ReleaseSmokeError("installed CLI did not report a version")
    commands = installed.get("commands")
    if not isinstance(commands, list) or any(not isinstance(item, str)
                                              for item in commands):
        raise ReleaseSmokeError("installed CLI command list is invalid")
    missing = sorted(set(REQUIRED_TASK_COMMANDS) - set(commands))
    if missing:
        raise ReleaseSmokeError(
            "installed CLI is missing task surface: %s" % ", ".join(missing))
    manifest = installed.get("package_manifest")
    _validate_manifest_shape(manifest)
    if manifest["source_kind"] != "installed_tree":
        raise ReleaseSmokeError("installed package manifest source kind is invalid")
    if manifest["version"] != _installed_version_value(installed["version"]):
        raise ReleaseSmokeError("installed version and package manifest disagree")


def _run(command, *, cwd=None, env=None) -> subprocess.CompletedProcess:
    proc = subprocess.run(command, cwd=str(cwd) if cwd else None, env=env,
                          capture_output=True, text=True, check=False)
    if proc.returncode:
        detail = (proc.stderr or proc.stdout).strip()
        raise ReleaseSmokeError(
            "command failed (%s): %s" % (" ".join(map(str, command)), detail))
    return proc


def build_wheel(repo: Path, out_dir: Path, *, python: str = sys.executable,
                isolated: bool = True) -> WheelArtifact:
    repo = Path(repo).resolve()
    out_dir = Path(out_dir).resolve()
    if not (repo / "pyproject.toml").is_file():
        raise ReleaseSmokeError("repository has no pyproject.toml")
    out_dir.mkdir(parents=True, exist_ok=True)
    command = [python, "-m", "pip", "wheel", str(repo), "--no-deps"]
    if not isolated:
        command.append("--no-build-isolation")
    command.extend(["-w", str(out_dir)])
    _run(command, cwd=repo)
    wheels = sorted(out_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise ReleaseSmokeError("expected exactly one built wheel")
    wheel = wheels[0]
    data = wheel.read_bytes()
    manifest = build_package_manifest(wheel, source_kind="wheel")
    members = tuple(item["path"] for item in manifest["members"])
    version = None
    prefix = "pocketchat-"
    if wheel.name.startswith(prefix):
        version = wheel.name[len(prefix):].split("-py3", 1)[0]
    if not version:
        raise ReleaseSmokeError("cannot determine wheel version from filename")
    if manifest["version"] != version:
        raise ReleaseSmokeError("wheel filename version does not match metadata")
    artifact = WheelArtifact(wheel, len(data), hashlib.sha256(data).hexdigest(),
                             members, version, manifest)
    validate_artifact(artifact)
    return artifact


def inspect_installed(cli: Path, python: Path) -> Dict[str, Any]:
    site_packages = _run([
        str(python),
        "-c",
        "import sysconfig; print(sysconfig.get_path('purelib'))",
    ]).stdout.strip()
    if not site_packages:
        raise ReleaseSmokeError("installed Python did not report site-packages")
    manifest = build_package_manifest(
        Path(site_packages), source_kind="installed_tree")
    version = _run([str(cli), "--version"]).stdout.strip()
    commands = []
    for command in REQUIRED_TASK_COMMANDS:
        parts = command.split()
        _run([str(cli), *parts, "--help"])
        commands.append(command)
    return {
        "version": version,
        "commands": commands,
        "package_manifest": manifest,
    }


def install_wheel(artifact: WheelArtifact, venv_dir: Path,
                  *, python: str = sys.executable) -> Dict[str, Any]:
    venv_dir = Path(venv_dir)
    _run([python, "-m", "venv", str(venv_dir)])
    if os.name == "nt":
        py = venv_dir / "Scripts" / "python.exe"
        cli = venv_dir / "Scripts" / "pocketchat.exe"
    else:
        py = venv_dir / "bin" / "python"
        cli = venv_dir / "bin" / "pocketchat"
    _run([str(py), "-m", "pip", "install", "--no-index",
          str(artifact.path.resolve())])
    installed = inspect_installed(cli, py)
    validate_installed(installed)
    return installed


def run_release_smoke(repo: Path, work_dir: Path, *, python: str = sys.executable,
                      isolated: bool = True, build=None,
                      install=None) -> Dict[str, Any]:
    work_dir = Path(work_dir).resolve()
    build = build or (lambda *a, **k: build_wheel(*a, **k))
    install = install or install_wheel
    artifact = build(Path(repo), work_dir / "dist", python=python,
                     isolated=isolated)
    validate_artifact(artifact)
    installed = install(artifact, work_dir / "venv", python=python)
    validate_installed(installed)
    _validate_manifest_pair(artifact.manifest, installed["package_manifest"])
    return ReleaseSmokeResult(artifact, installed).as_dict()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--no-build-isolation", action="store_true",
                        help="use only build dependencies already installed")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    temporary = None
    try:
        work = args.work_dir
        if work is None:
            temporary = tempfile.mkdtemp(prefix="pocketchat-release-")
            work = Path(temporary)
        result = run_release_smoke(args.repo, work,
                                   isolated=not args.no_build_isolation)
    except (ReleaseSmokeError, OSError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "schema_version": "1",
                              "error": "release_smoke", "message": str(exc)},
                             sort_keys=True))
        else:
            print("release-smoke: %s" % exc, file=sys.stderr)
        return 1
    finally:
        if temporary:
            shutil.rmtree(temporary, ignore_errors=True)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("wheel %s bytes=%d sha256=%s"
              % (result["wheel"], result["wheel_bytes"], result["wheel_sha256"]))
        print("version %s  schemas %d  command families %d"
              % (result["version"], len(result["schemas"]),
                 len(result["commands"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
