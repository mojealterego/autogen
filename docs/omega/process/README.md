# AutoGen OMEGA — Process Runtime 3.0

Opt-in extension of Durable 2.0; upstream package version remains 0.7.5.

`ProcessWorker` implements the same async handler contract used by `WorkflowExecutor` and `DurableWorkflowExecutor`. A trusted, absolute Python script reads one JSON object from stdin and writes one JSON value to stdout. Stderr is separately bounded and is never included in exceptions. Configure the worker in the application, not from agent text. The worker runs without a shell in a new Linux process session, with a minimal environment and Python isolated mode. The bootstrap applies RLIMIT_AS, CPU, NOFILE, FSIZE and disables core dumps without using preexec_fn. Limits are per-process and capped at inherited hard limits. Parent enforces separate bounded stdout/stderr, bounded encoded input and wall time; cancellation triggers process-group termination and waits for cleanup.

```python
from pathlib import Path
from autogen_core import DurableWorkflowExecutor, ProcessWorker, WorkerLimits, WorkflowPolicy, WorkflowStep

handler = ProcessWorker(Path('/trusted/worker.py'), WorkerLimits(timeout=5, cpu_seconds=2))
engine = DurableWorkflowExecutor('checkpoints.sqlite', {'worker': handler}, WorkflowPolicy(frozenset({'worker'})))
# In an async function:
# await engine.run([WorkflowStep('a', 'worker')], tenant_id='t', run_id='r', workflow_revision='worker-v1')
```

Example worker: `import json, sys; print(json.dumps({'echo': json.load(sys.stdin)}))`.

## Boundary and limitations

- Linux only. This is process separation and fault containment for cooperative trusted code, **NOT** an adversarial security sandbox. The worker has the same OS identity, filesystem/network permissions and can create descendant processes; descendants can escape the process group, retain pipes, or survive if the parent itself is killed. Escaped descendants retaining pipes can delay cleanup. Use a separate restricted identity, cgroups v2, namespaces, network policy and supervisor/microVM for untrusted code. Do not run untrusted scripts. No global aggregate CPU/memory/process quotas are enforced.
- Input/output byte caps apply to serialized data, not parent pre-serialization allocations; the stream readers have bounded buffering. Each of stdout and stderr receives its own cap. System site packages are trusted; Python isolated mode does not remove all system startup hooks. Keep the script and parent directories immutable to unauthorized writers. No credentials are injected. A durable attempt context is not automatically transferred into the subprocess; idempotency must be implemented by an explicit, authorized application protocol. A failed/timed-out worker can have performed external side effects.
- The separate process is not an AutoGen agent runtime itself; it is a callable handler integrated with the durable scheduler. The worker does not guarantee exactly-once execution. The `workflow_revision` must change if worker code or limits change.
- Focused and dependency-free tests passed locally. Entire upstream suite could not collect, due to missing jsonref, asyncio_atexit and autogen_test_utils; package metadata was supplied in a temporary local metadata directory for source-based testing, not an installation. Studio, .NET and multi-host deployments were not tested.

Evidence: TDD_RED.txt, TESTS.txt, CORE_FOCUSED.txt, FULL_CORE_ATTEMPT.txt, COMPILE.txt. All prior evidence remains in docs/omega. The new tests cover input/output quotas, real resource limits, minimal environment, malformed and nonfinite result, timeout, exit status, actual cancellation and a durable two-node workflow with replay.
