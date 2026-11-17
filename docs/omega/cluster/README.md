# OMEGA Cluster Coordinator 5.0 — local multi-process milestone

Opt-in SQLite WAL coordinator for a trusted **single host** with independent Python processes. This milestone is the durable scheduling substrate for future cross-host execution; it is not a replicated database, distributed consensus protocol, network gateway or adversarial sandbox. Original AutoGen package version (0.7.5) is unchanged.

## Contracts

- `ClusterCoordinator(path, capacity, tenants, workers, *, lease_ms=30000, max_jobs=10000, max_pending_per_tenant=1024, max_payload_bytes=65536, busy_timeout_ms=5000)`: settings affecting persistent behavior are fingerprinted. Reopening with different settings fails closed. Trusted configuration is copied. `TenantSpec` defines quotas and handler allowlists; `WorkerSpec` defines worker quotas, allowed tenants and handler capabilities.
- `submit(tenant, job_id, handler, payload, demand, idempotent=False, max_attempts=1)`: durable unique `(tenant, job_id)` identity, immutable specification fingerprint, replay of existing state on identical submission. A different specification conflicts. Unknown tenants/handlers, impossible resource requests and unconfigured capabilities are denied. Input and result JSON sizes, total retained jobs and outstanding jobs per tenant are bounded. There is deliberately no automatic deletion or ID reuse; retention exhaustion rejects new jobs. Export/archive and configuration migration are future work.
- `register(worker_id)` issues a random session token; only its SHA-256 is stored; live sessions cannot be replaced. `heartbeat` extends the worker session only. `claim` atomically admits an eligible queued job and returns a `JobLease` containing its increased fence and stable idempotency key. The first eligible job in submission order is selected; incompatible and currently quota-blocked jobs can be bypassed. This trades strict FIFO for utilization, so starvation is possible.
- A lease is valid only while the worker session and individual job lease are both live. `renew(session, lease)` extends both. `complete`, `fail`, and `abandon` use session and fencing validation inside a transaction. Stale sessions, stale fences and expired leases are rejected. `fail(retry=True)` queues a retry only for explicitly idempotent jobs with attempts remaining, otherwise records `failed`. `abandon` or expiry records `uncertain` unless an explicitly idempotent retry remains. `reap()` reconciles expired leases; operations also reconcile opportunistically. A rejected operation commits elapsed-clock reconciliation but rolls back its own mutations, avoiding resurrection after clock rollback.
- Resources are declared simultaneous slots, bytes and millicores; summed live leases must fit global, tenant and worker capacities. Expiration is based on the trusted local wall clock, with persisted nondecreasing high-water mark. Clock rollback cannot revive an observed-expired lease, but a large forward jump may expire live work and a backward jump may delay future expiry. Use monitored time synchronization. Busy/IO errors propagate, not silently retried.
- `ClusterWorker(coordinator, session, handlers, heartbeat_interval=...)` executes one job at a time, renews periodically, cancels cooperative handlers on renewal failure, and drains in-flight DB operations before returning from cancellation. A cancellation after claim leaves uncertainty for expiry/reconciliation; a completion may have committed before cancellation is delivered. Inspect `get()` rather than assume rollback. `current_cluster_lease()` exposes initial claim metadata in the handler's context, not an assertion it is still live. Idle workers must call `heartbeat` or re-register after expiry. Handler exceptions and unrepresentable results are conservatively treated as unknown outcomes.

## Example: real trusted subprocess worker

```python
import asyncio
from autogen_core import (ClusterCoordinator, ClusterWorker, ProcessWorker,
                          Resources, TenantSpec, WorkerSpec, WorkerLimits)

quota = Resources(1, 128 * 1024**2, 1000)
allowed = frozenset({'compute'})
coordinator = ClusterCoordinator(
    'jobs.sqlite', quota,
    {'tenant-a': TenantSpec(quota, allowed)},
    {'worker-a': WorkerSpec(quota, frozenset({'tenant-a'}), allowed)},
)
coordinator.submit('tenant-a', 'job-1', 'compute', {'x': 2}, quota)
session = coordinator.register('worker-a')
worker = ClusterWorker(coordinator, session, {
    'compute': ProcessWorker('/trusted/compute.py', WorkerLimits(memory_bytes=quota.memory_bytes))
})
print(asyncio.run(worker.run_once()))
```

The service must bind producer and worker identities to authenticated principals upstream; `register` is NOT proof of worker identity and `get` is not an access-control API. Never expose direct SQLite access to untrusted tenants; restrict file and directory permissions. Use a local filesystem; do not share WAL files over NFS/SMB. Multiple processes share one write authority and are not HA. Fencing is enforced at this coordinator only; impose fencing/idempotency at every external effect recipient. A worker with expired lease can still run; quotas count valid reservations, not actual hardware. ProcessWorker is for trusted scripts, not a sandbox, and cancellation must be cooperative or enforced by an OS supervisor. ClusterWorker does not automatically dispatch DurableWorkflowExecutor steps: task submission and worker loop are separate explicit APIs. A future durable workflow adapter must reconcile operation identity and unknown outcomes, not blindly retry. Do not claim exactly-once external effects.

## Evidence and reproduction

From repo root with a local AutoGen development environment (or source paths and package metadata):

```sh
python -m pytest -q python/packages/autogen-core/tests/test_omega_cluster.py
python -m pytest -q python/packages/autogen-core/omega_tests python/packages/autogen-core/tests/test_omega*.py
python -m pytest -q python/packages/autogen-core/tests --continue-on-collection-errors
```

See `TDD_RED.txt`, `GREEN.txt`, `TDD_RED_HARDENING.txt`, `GREEN_HARDENING.txt`, `FOCUSED_TESTS.txt`, `FULL_CORE_ATTEMPT.txt`, `COMPILE.txt`. The tests cover real concurrent spawned processes, aggregate quota admission, real abrupt child exit, stable idempotency and increasing fences, rejected-expiry rollback regression, heartbeat, lost lease, cancellation, and real subprocess execution. These are single-host tests, not a multi-host network or production deployment. No throughput benchmark is claimed.
