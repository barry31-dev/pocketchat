# Task Run demo

`demo.py` runs three bounded task evidence paths with an in-process stub
provider. It uses no network, credential, user account or third-party asset:

1. `success` reads an allowlisted file and completes;
2. `checkpoint_resume` restores a real task checkpoint and reports the recovery
   step;
3. `permission_refusal` asks for a path outside the policy and records a
   refused tool step.

Cancellation is terminal by contract, so the demo does not label a cancelled
task as resumable. The recovery path starts from a completed step boundary and
the `checkpointed -> running` transition.

Run from the package root:

```bash
/usr/bin/python3 examples/task-demo/demo.py
```

Pass `--output DIR` to retain the generated workspace for evidence review. The
directory must be new or empty:

```bash
/usr/bin/python3 examples/task-demo/demo.py --output /tmp/task-demo
```

The output is three JSON objects, one per path. It is a deterministic smoke
demo, not a provider-quality or performance test.
