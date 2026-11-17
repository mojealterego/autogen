# OMEGA Durable Runtime — extension 2.0

Base: AutoGen 0.7.5, OMEGA Governed archive 2026-11-17. The upstream version is unchanged. New opt-in public API: `DurableWorkflowExecutor`, `DurableWorkflowResult`, `AttemptContext`, `current_attempt`, `RunBusyError`, `CheckpointConflictError`, `CheckpointIntegrityError`. Existing `WorkflowExecutor` remains compatible and in-memory.

## Execution protocol

1. Validate the DAG, handler allowlist, retry rules, resource limits and dependencies using the existing governed executor. Bind the tenant/run pair to a hash of the workflow, policy and explicit handler revision. The caller MUST bump `workflow_revision` when handler semantics or code change.
2. Acquire the per-run exclusive SQLite lock on a separate lock database. Local competing workers fail closed with `RunBusyError`. The database locks are released by the OS when a process dies. All workers must use the same resolved database path on a trusted local filesystem. Never delete or move active lock files.
3. Verify the event journal against its independently stored committed sequence/head and optional external head. The event chain binds the run, spec, deadline, sequence, transition, number of attempts and canonical JSON output. State transitions are revalidated on replay.
4. Commit `running` before invoking the handler. Commit outcome and the new journal cursor in the same SQLite transaction (WAL / synchronous FULL). No agent invocation occurs while the checkpoint writer transaction is held. Schedule ready steps in sorted bounded batches; actual concurrent event/completion order depends on scheduling.
5. Restore committed outputs without re-invoking their handlers. A previously running non-idempotent or exhausted step becomes `uncertain`; descendants are skipped. An explicitly idempotent step with remaining attempts may be retried with the same operation key. Pending retry attempts and the original wall-clock expiry survive restarts. Roll-back of the system clock can extend the effective budget, so a trustworthy clock is required.
6. The attempt context exposes a stable operation key scoped to tenant, run and step. Use it at the actual external side-effect boundary. An idempotency declaration is NOT an exactly-once guarantee. Timeout, failure, uncertainty, or cancellation do not prove that an external effect did not occur.

## Working example

```python
import asyncio
from pathlib import Path
from autogen_core import DurableWorkflowExecutor, WorkflowPolicy, WorkflowStep, current_attempt

async def plan(inputs):
    ctx = current_attempt()
    assert ctx is not None
    return {'task': 'verify sources'}

async def verify(inputs):
    return {'valid': inputs['plan']['task'] == 'verify sources'}

async def main():
    engine = DurableWorkflowExecutor(Path('private/workflows.sqlite'),
        {'plan': plan, 'verify': verify},
        WorkflowPolicy(frozenset({'plan', 'verify'}), deadline=60))
    steps = [WorkflowStep('plan', 'plan'), WorkflowStep('verify', 'verify', ('plan',))]
    result = await engine.run(steps, tenant_id='authenticated-tenant',
                              run_id='stable-request-id', workflow_revision='handlers-v1')
    print(result.outputs, result.statuses, result.journal_head)
    # Pin the exact latest head in trusted external storage to detect full rewrites.
    cached = await engine.run(steps, tenant_id='authenticated-tenant',
                              run_id='stable-request-id', workflow_revision='handlers-v1',
                              expected_head=result.journal_head)
    assert cached == result

asyncio.run(main())
```

## Operational boundaries

- The API is a single-host local durable executor, not distributed consensus, a process sandbox, a service authentication layer, or a hard resource quota system. The handlers are trusted and cooperative; background tasks spawned by handlers must be managed by the handler. Non-cooperative handlers can delay cancellation. SQLite writes are synchronous in the event loop and busy timeout is five seconds; measure workload latency before deploying. Separate runs have separate concurrency limits; this is not a global admission controller.
- Tenant identifiers namespace data and locks; callers must authenticate and authorize them. The database is not encrypted; outputs are stored as plaintext. Restrict access to the DB, WAL, SHM, parent and lock directory; set retention externally. New database files use mode 0600 on POSIX, subject to OS semantics; pre-existing permissions are not modified. No network filesystem guarantees. Use SQLite's online backup mechanism or a quiesced, checkpointed backup, not a live copy of only the database file.
- SHA-256 chaining and the cursor detect accidental loss or local tampering that does not rewrite all metadata. A trusted external exact head detects complete rewrite or truncation relative to that head. No signature, MAC, trusted timestamp or remote anchoring is provided. Pinning an old head after legitimate progress intentionally fails. The storage schema is new; no migrations for arbitrary pre-existing omega tables are provided.
- Outputs must be canonical JSON and individually bounded. The sum of outputs can reach max_steps * max_result_bytes. There is no limit on Python allocations before serialization, cumulative persistent database size, or number of runs. Apply filesystem quotas and retention. Handler body code, tokens, exception strings and credentials should never be written to outputs unless authorized.
- Stable operation keys assume a stable tenant/run identity and the same storage lineage. Reusing run IDs for a different operation is forbidden. Deleting a database removes deduplication history. Do not blindly repeat an uncertain operation: reconcile with the external service first.

## Verification evidence

See the logs in this directory. TDD: initial collection failed because the new API did not exist; an additional explicit regression failed when truncation was not detected, then passed after atomic cursor binding. Two child processes terminate with `os._exit(71)` during a handler; a distinct worker also holds the run lock and is forcibly killed. Tests cover replay, revision/DAG binding, stable idempotency keys, cancellation, namespace isolation, output tampering, external pin, unpinned truncation, persisted deadline, parallel partial completion, cross-process exclusion and real AutoGen agent integration. Actual test count is listed in the logs; no external model was called.
