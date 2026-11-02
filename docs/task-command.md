# Command templates v1

This unreleased slice executes only an operator-declared command template from
a TaskSpec. It never accepts a shell string and never enables `shell=True`.
The published v1.1.0 artifact does not contain this capability.

## TaskSpec

`commands` is a closed array. Every entry has exactly these fields:

```json
{
  "id": "run_tests",
  "argv": ["python3", "-m", "unittest"],
  "cwd": ".",
  "timeout_seconds": 30,
  "max_output_bytes": 4096
}
```

`id` is unique and stable. `argv` is a non-empty string array; shell
executables such as `sh`, `bash`, `zsh`, `cmd`, PowerShell and `pwsh` are
refused even when passed as a path. `cwd` must remain inside the workspace.
`timeout_seconds` is 1 through 300. `max_output_bytes` is per stream and is
bounded at 1 MiB.

## TaskPlan

A `run_command` step names exactly one declared template id in `inputs`.
It cannot carry an `argv`, a shell string, environment variables or an inline
working directory. A TaskSpec without commands cannot grant this action.

## Execution evidence

`run_command_template()` starts a process with argv and `shell=False`. It does
not inherit arbitrary environment variables; only basic process plumbing such
as `PATH`, locale and temporary-directory variables are retained. The command
receives a workspace-contained cwd.

The result records the template id, relative cwd, exit code, timeout flag,
duration, byte counts, full-stream SHA-256 values and bounded stdout/stderr
previews. A truncated preview ends with `[truncated]`; the hash still covers the
complete stream. The trace records `run.started`, `tool.call.started`, one
terminal tool event and `run.finished` or `run.failed`. It does not record the
full argv.

`exit 0` means the command succeeded. It does not mean the task passed:
`task_acceptance` remains `not_evaluated`. Nonzero exit and timeout are
evidence-bearing command results, not acceptance receipts.

## Limits

`task report` now reads the bounded execution report without rerunning the
command. This slice does not retry commands, repair failures, install
dependencies, delete files, call a real provider or publish a release. There is no OS-level network or filesystem sandbox; an
operator who declares a network-capable command is still responsible for that
choice. Only local macOS with CPython 3.13.9 is verified; Python 3.9, Windows,
Linux, wheel and clean-install paths remain unverified.
The bounded result is also written to
`traces/command-evidence/<trace-stem>.json` with schema
`pocketchat-command-evidence.v1`. If the configured credential appears in
that archive, the runner fails closed and does not write it.
