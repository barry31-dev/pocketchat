#!/usr/bin/env python3
"""Build and install one PocketChat wheel, then verify release-critical surface."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

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


class ReleaseSmokeError(RuntimeError):
    """The built or installed package does not satisfy the release surface."""


@dataclass(frozen=True)
class WheelArtifact:
    path: Path
    size: int
    sha256: str
    members: Tuple[str, ...]
    version: str


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
        }


def required_members() -> Tuple[str, ...]:
    return REQUIRED_MODULES + REQUIRED_SCHEMAS


def read_wheel_members(path: Path) -> Tuple[str, ...]:
    target = Path(path)
    try:
        with zipfile.ZipFile(target) as archive:
            names = tuple(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseSmokeError("cannot read wheel archive: %s" % exc)
    if not names or len(set(names)) != len(names):
        raise ReleaseSmokeError("wheel member list is empty or contains duplicates")
    for name in names:
        candidate = Path(name)
        if (not name or name.startswith(("/", "\\")) or "\\" in name
                or candidate.is_absolute() or ".." in candidate.parts):
            raise ReleaseSmokeError("wheel contains an unsafe member: %r" % name)
    return names


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


def _run(command, *, cwd=None, env=None) -> subprocess.CompletedProcess:
    proc = subprocess.run(command, cwd=str(cwd) if cwd else None, env=env,
                          capture_output=True, text=True, check=False)
    if proc.returncode:
        detail = (proc.stderr or proc.stdout).strip()
        raise ReleaseSmokeError(
            "command failed (%s): %s" % (" ".join(map(str, command)), detail))
    return proc


def build_wheel(repo: Path, out_dir: Path, *, python: str = sys.executable) -> WheelArtifact:
    repo = Path(repo).resolve()
    out_dir = Path(out_dir).resolve()
    if not (repo / "pyproject.toml").is_file():
        raise ReleaseSmokeError("repository has no pyproject.toml")
    out_dir.mkdir(parents=True, exist_ok=True)
    _run([python, "-m", "pip", "wheel", str(repo), "--no-deps",
          "--no-build-isolation", "-w", str(out_dir)], cwd=repo)
    wheels = sorted(out_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise ReleaseSmokeError("expected exactly one built wheel")
    wheel = wheels[0]
    data = wheel.read_bytes()
    members = read_wheel_members(wheel)
    version = None
    prefix = "pocketchat-"
    if wheel.name.startswith(prefix):
        version = wheel.name[len(prefix):].split("-py3", 1)[0]
    if not version:
        raise ReleaseSmokeError("cannot determine wheel version from filename")
    artifact = WheelArtifact(wheel, len(data), hashlib.sha256(data).hexdigest(),
                             members, version)
    validate_artifact(artifact)
    return artifact


def inspect_installed(cli: Path, python: Path) -> Dict[str, Any]:
    version = _run([str(cli), "--version"]).stdout.strip()
    commands = []
    for command in REQUIRED_TASK_COMMANDS:
        parts = command.split()
        _run([str(cli), *parts, "--help"])
        commands.append(command)
    return {"version": version, "commands": commands}


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
                      build=None, install=None) -> Dict[str, Any]:
    work_dir = Path(work_dir).resolve()
    build = build or (lambda *a, **k: build_wheel(*a, **k))
    install = install or install_wheel
    artifact = build(Path(repo), work_dir / "dist", python=python)
    validate_artifact(artifact)
    installed = install(artifact, work_dir / "venv", python=python)
    validate_installed(installed)
    return ReleaseSmokeResult(artifact, installed).as_dict()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    temporary = None
    try:
        work = args.work_dir
        if work is None:
            temporary = tempfile.mkdtemp(prefix="pocketchat-release-")
            work = Path(temporary)
        result = run_release_smoke(args.repo, work)
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
