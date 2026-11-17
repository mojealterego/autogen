# AutoGen OMEGA — Resource Scheduler 4.0

Opt-in local resource admission shared by independent workflows in one asyncio event loop. The original AutoGen version remains 0.7.5. No new dependency.

## Contract

`Resources(slots, memory_bytes, cpu_millis)` declares positive integer simultaneous reservations. `ResourceScheduler(capacity, tenant_limits, max_queue=1024, max_queued_per_tenant=None)` copies configuration and rejects unknown tenants or impossible requests. Tenant quotas must fit global capacity. CPU millicores are accounting units, not measured CPU time. Strict FIFO avoids overtaking, with head-of-line blocking as an explicit tradeoff. There is a global queue limit and an optional per-tenant queue limit; default per-tenant queue limit equals the global queue limit. A zero limit permits immediate admission only. Waiting is cancellable and optionally time-bounded. The `reserve` context releases synchronously on cancellation, error or normal exit. `close()` rejects queued and new work, but does not cancel already granted work. `snapshot()` returns immutable accounting for the bound event loop. No locks are held across handler awaits. Do not share an instance across threads/event loops, or acquire nested reservations that may exceed capacity.

```python
from autogen_core import (
    DurableWorkflowExecutor, ProcessWorker, Resources, ResourceScheduler,
    WorkerLimits, WorkflowPolicy, WorkflowStep, scheduled_handler,
)

capacity = Resources(4, 512 * 1024**2, 4000)
quota = Resources(2, 256 * 1024**2, 2000)
scheduler = ResourceScheduler(capacity, {'tenant-a': quota, 'tenant-b': quota},
                              max_queue=64, max_queued_per_tenant=32)
worker = ProcessWorker('/trusted/worker.py', WorkerLimits(memory_bytes=128 * 1024**2))
handler = scheduled_handler(scheduler, 'tenant-a', Resources(1, 128 * 1024**2, 1000),
                            worker, admission_timeout=5)
executor = DurableWorkflowExecutor('local.sqlite', {'worker': handler},
                                   WorkflowPolicy(frozenset({'worker'})))
# From the same event loop, with a trusted authenticated tenant mapping:
# await executor.run([WorkflowStep('a', 'worker')], tenant_id='tenant-a',
#                    run_id='run-1', workflow_revision='scheduler-v1')
```

`scheduled_handler` uses a fixed application-supplied tenant identity; it does not authenticate callers or dynamically validate the durable context. Bind each tenant's executor to its authorized handlers and never accept a tenant or resource cost from LLM text. Queue waiting counts toward the existing workflow attempt timeout; a durable timeout can be recorded even if no worker was started. Replay of a completed step bypasses handler admission. Reconfiguration requires a new scheduler and appropriately revised workflow fingerprint. The scheduler is cooperative: cancelled handlers must stop their work before returning or raising. It cannot predict or contain a malicious handler or its descendants. Resource quotas cover declared reservations within one instance, not the whole OS, and are neither durable nor cross-host. Use a real OS sandbox and cgroups for adversarial isolation and hard aggregate limits. No production deployment or distributed consensus is claimed.

## Reproduction

With local AutoGen development environment installed:

```sh
python -m pytest -q python/packages/autogen-core/omega_tests python/packages/autogen-core/tests/test_omega_scheduler.py python/packages/autogen-core/tests/test_omega_runtime.py python/packages/autogen-core/tests/test_omega_durable.py
python -m pytest -q python/packages/autogen-core/tests --continue-on-collection-errors
```

Local execution used Python 3.13.5 and source imports with temporary package metadata and the bundled test-utils source. This is not an installed wheel build. Test logs: TDD_RED.txt, TDD_RED_TENANT.txt, GREEN.txt (initial 9 tests), TESTS.txt (47 passed), FULL_CORE_ATTEMPT.txt (153 passed; 10 collection errors due to missing dependencies). The full upstream suite is not green. Tests include two independent durable executors with actual subprocess workers, journal replay, concurrent stress with 80 reservations, FIFO, cancellation before and after grant, queue saturation, per-tenant accounting and closure. No throughput or multi-host performance claims.
