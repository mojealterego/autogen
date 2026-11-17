# AutoGen OMEGA — governed orchestration extension

Base: supplied AutoGen source archive, Python autogen-core 0.7.5. This is an independent source distribution; upstream package version and licenses are preserved. No dependency or external service is required by the new workflow engine itself.

## Durable extension 2.0

The opt-in `DurableWorkflowExecutor` now supports local persistent checkpoints and crash recovery. See [durable runtime documentation](durable/README.md) for its separate API, limits, and evidence. The original executor described below remains in-memory.

## What changed

- `WorkflowExecutor`: explicit acyclic workflows with validated named dependencies, bounded step and retry counts, sorted deterministic scheduling, bounded concurrency, per-attempt and global cooperative deadlines, fail-closed handler allowlist, output-size limits and JSON isolation between handlers.
- `WorkflowPolicy`, `WorkflowStep`, `WorkflowResult`: typed public configuration and results. Idempotency must be explicitly declared for retries, and only `RetryableError` enables retry. Timeouts are never automatically retried.
- `agent_workflow_handler`: bind a trusted fixed `AgentId` to a registered AutoGen runtime. Encode/decode functions must be supplied by the application. Cancellation is propagated through `CancellationToken`.
- `AuditRecord` and `verify_audit`: ordered SHA-256 chain of status metadata. No payload or exception text is included. Store the trusted head externally to detect deletion or complete rewriting; this is not a digital signature or immutable storage.
- Cancellation callbacks run outside a lock, isolate ordinary exceptions, and release completed futures; cancellation across threads is scheduled on the correct event loop.
- Runtime interceptor transformations now compose, `None` preserves the previous message, and early drop/error paths acknowledge the queue. Already-completed futures are not resolved again.

## Run locally

From the `python` directory, install upstream development dependencies using the project's documented `uv sync --all-extras --all-groups` or use an environment with `autogen-core`, `pytest` and `pytest-asyncio` installed. Install the local package via `uv pip install -e packages/autogen-core` into that environment.

```python
import asyncio
from autogen_core import WorkflowExecutor, WorkflowPolicy, WorkflowStep, verify_audit

async def plan(inputs):
    return {'topic': 'research', 'checks': ['sources', 'consistency']}

async def verify(inputs):
    return {'valid': len(inputs['plan']['checks']) == 2}

async def main():
    executor = WorkflowExecutor(
        {'plan': plan, 'verify': verify},
        WorkflowPolicy(frozenset({'plan', 'verify'}), max_parallel=2, deadline=30),
    )
    result = await executor.run([
        WorkflowStep('plan', 'plan'),
        WorkflowStep('verification', 'verify', ('plan',)),
    ])
    print(result.statuses, result.outputs)
    assert verify_audit(result.audit, expected_head=result.audit_head)

asyncio.run(main())
```

See `tests/test_omega_runtime.py` for integration with a real registered `RoutedAgent` and `SingleThreadedAgentRuntime` (no external LLM).

## Verification commands

From repository root, using the installed local package (and test utils for upstream tests):

```sh
python -m unittest discover -s python/packages/autogen-core/omega_tests -v
python -m pytest -q python/packages/autogen-core/tests/test_omega_runtime.py
python -m pytest -q python/packages/autogen-core/tests
```

The standalone unittest suite deliberately loads exact source files for offline testing without installing unrelated dependencies. The pytest integration tests use the normal public `autogen_core` import.

## Security and scope

Use trusted handlers and fixed recipients. The allowlist controls dispatch from this executor, not the permissions of agents after dispatch. Agent code can perform side effects and must implement its own authorization, idempotency storage and cancellation cooperation. A blocking handler or one that suppresses cancellation cannot be forcibly killed by asyncio. Enforce hard deadlines, CPU/memory limits, filesystem/network isolation and tenant authentication in an external process/container runtime. The output limit applies to serialized output, not peak memory allocated by a handler. Multiple concurrent executor runs each have their own budget; an external admission controller is needed for a global budget. Audit records are in memory until exported by the caller; this release does not implement crash recovery, signed durable checkpoints, distributed consensus, real-model benchmarks, or deployment.

## Resource Scheduler 4.0

See [scheduler](scheduler/README.md) for opt-in shared resource reservations and tenant admission in a single event loop. Hard aggregate OS quotas and cross-host coordination remain open.

## Cluster Coordinator 5.0

See [cluster](cluster/README.md) for the opt-in durable multi-process admission, worker sessions, per-job fencing, heartbeat and recovery milestone. Single host/SQLite; authenticated cross-host transport and replicated storage remain open.

## Distributed Fabric 5.1

See [fabric](fabric/README.md) for mutually authenticated bounded network RPC,
certificate-bound tenant/worker identities and the remote worker backend.
Single-authority network service; replication and independent multi-host gates
remain open.
