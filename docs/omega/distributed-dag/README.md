# OMEGA 5.2 — durable DAG to cluster adapter

`distributed_step_handler` is an opt-in bridge from the single-host journaled
`DurableWorkflowExecutor` to the producer API of `ClusterCoordinator` or a
certificate-bound `FabricClient`. The producer tenant, remote handler and
resources are *fixed by trusted application configuration*, not by an LLM.

A remote job ID is derived from the stable durable attempt operation key and
remote handler, and does not include the attempt number. Resubmitting an
identical job to the same authority is deduplicated by the existing cluster
fingerprint and tenant/job-id unique key. A transport error can occur after a
commit: the bridge first attempts a **read**, never a second ambiguous submit.
Polling `get()` (read-only) is bounded; `succeeded` returns output, `failed`
marks failed, and `uncertain`, absent/missing response, or polling exhaustion
marks the durable node **uncertain**, not failed or retryable. No automatic
execution retry follows an unknown external outcome. Cancelling the DAG leaves
a durable running intent, recovered conservatively by the existing executor.
Downstream dependent nodes are skipped when their predecessor is uncertain.

This is not exactly-once external side effects, and it does **not** currently
reopen terminal uncertain steps if a late result arrives. Applications must
reconcile an uncertain remote job explicitly (using the stable remote identity)
before deliberately starting downstream work in a separately identified run.
A full durable DAG continuation/reconciliation API is still open.

```python
from autogen_core import (
    distributed_step_handler, DurableWorkflowExecutor,
    Resources, WorkflowStep, WorkflowPolicy,
)
handler = distributed_step_handler(
    trusted_producer_backend, 'fixed-tenant', 'trusted-remote-handler',
    Resources(1, 128 * 1024**2, 1000), poll_interval=.1, poll_timeout=20,
)
executor = DurableWorkflowExecutor(
    '/private/journal.sqlite', {'remote': handler},
    WorkflowPolicy(frozenset({'remote'}), deadline=60),
)
# await executor.run([WorkflowStep('step-1','remote',timeout=30)],
#                    tenant_id='fixed-tenant',run_id='run-1',workflow_revision='v1')
```

## Verification

- TDD_RED.log: missing bridge export triggers import failure before implementation.
- TESTS.txt: 7 real tests, including two-dependent-node workflow with live
  ClusterWorker, actual mTLS/TCP producer and worker with ephemeral certificates,
  committed submission with lost ACK, unavailable authority, timeout uncertainty,
  definite remote failure, tenant mismatch and journal replay.
- All tests use the actual on-disk SQLite coordinator and durable journal.
- Further multi-host partition simulation, remote result reconciliation after
  durable terminal uncertainty, distributed HA, and production deployment
  remain open. Do not report this as distributed consensus or real multi-host HA.
