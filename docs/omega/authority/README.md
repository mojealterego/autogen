# OMEGA 5.3 — local authority epochs and controlled handoff

**Scope:** fail-closed leadership for a single SQLite/WAL authority shared by
multiple independent local processes. This stage does **not** deliver replicated
consensus, multi-datacenter failover, or physical multi-host HA. Its guarantee
only holds when all coordinator instances use the same trusted SQLite database.
It is opt-in: after initializing `AuthorityLeaseStore`, *any* unguarded
`ClusterCoordinator` transaction for that database is denied.

`AuthorityLeaseStore(path, lease_ms=...)` acquires a renewable leader lease with
an increasing 64-bit epoch. The witness uses `BEGIN IMMEDIATE` and a persisted
nondecreasing clock high-water mark. `acquire` rejects a live leader; a new epoch
is created only after release or expiry. Promotion atomically invalidates all
old worker sessions and turns in-flight jobs to `uncertain`, unless declared
idempotent and eligible for retry. Previously returned worker lease and session
tokens never authorize commits after takeover.

`ClusterCoordinator(..., authority_guard=store.guard(lease))` checks leadership
**inside its existing SQLite transaction** (not as a separate preflight). This
closes a time-of-check/use race between leader expiration and state mutation.
The mTLS fabric returns a typed `LeadershipLost` denial with no secret data.

```python
from autogen_core import AuthorityLeaseStore, ClusterCoordinator

# Initialize the coordinator schema first, using a trusted local database.
# Base config (capacity/tenants/workers) is supplied by the application.
store = AuthorityLeaseStore('/private/authority.sqlite', lease_ms=30000)
lead = store.acquire('node-1')
coordinator = ClusterCoordinator('/private/authority.sqlite', capacity, tenants,
                                  workers, authority_guard=store.guard(lead))
# An external supervisor must call store.renew(lead) before lease expiry.
# If renew fails, stop serving and let the lease expire for controlled takeover.
# On permanent shutdown: store.release(lead) if still authorized.
```

## Critical limitations

- **No reconfiguration/automatic leader promotion**, state replication,
  quorum, external witness independent of the authority DB, or automatic
  membership discovery. Single SQLite file is a single point of failure.
- `AuthorityLeaseStore` must be used with the same DB path as the coordinator;
  SQLite WAL on network filesystems is unsupported. DB file access is trusted;
  an attacker with raw SQL write permissions can bypass the API fences.
- External effects still require their own fencing and idempotency. A worker
  that loses leadership may continue computing, but must not commit through
  the guarded authority. Other external recipients may still be vulnerable.
- All servers/worker gateways must use a guarded coordinator after enabling
  the witness. Only a *single* authoritative store prevents split-brain. Do
  not copy a live DB and start an independent authority from it.
- Clock faults and scheduling pauses can expire leadership; strict fail-closed
  rejection is preferred over availability. A supervised heartbeat loop must
  renew leadership. This milestone does not implement that supervisor.
- Real process and TCP/mTLS tests are loopback/single-host, not independent
  physical multi-host tests. Full HA release acceptance **BLOCKED**.

## Evidence

`TDD_RED.log` (missing module), `TESTS.txt` (8 passed), `FOCUSED.txt`
(current OMEGA suite), `COMPILE.txt`. Tests cover epoch re-acquisition,
revocation, rollback of the local clock, shared-database transaction fencing,
capability enforcement, independent spawned contender processes, and an actual
mTLS server refusing requests after leadership transfer.
