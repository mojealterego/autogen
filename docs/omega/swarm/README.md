# OMEGA 7.0 — Hierarchical Agent Swarms (local, trusted agents)

Additive opt-in `HierarchicalSwarm` delegates typed finite DAG missions to a validated
single-root supervisor tree per tenant. The controller reuses `WorkflowExecutor` for
bounded concurrency, attempt and deadline control and linked status audit records.

- `SwarmAgent`: fixed tenant, role (`supervisor`, `worker`, `verifier`), parent,
  allowed capabilities and trusted async handler. At most one root per tenant;
  children may only descend from a supervisor in the same tenant. Orphan and
  cyclic organizations fail at registration.
- `SwarmNode`: fixed agent/capability, isolated JSON payload, dependencies and
  optional independent reviewer. Verifier output must be `{'approved': True}`;
  implicit approval is never accepted. Missing reviewer fails preflight.
- `SwarmPolicy`: limits agent/node counts, concurrency, deadline and protected
  capabilities; protected node execution requires explicit task-specific
  operator grants. Invalid grants and unauthorized models fail closed.
- All handlers and DAG assignments are application-supplied; model-produced text
  cannot register an agent, modify capabilities, grant approval or add DAG nodes.

## Verification

TDD_RED.log: missing public API; TESTS.txt: 8 real tests (graph acceptance and
veto, missing reviewer, approval gate, tenant/capability denial, malformed
hierarchy, self-review rejection, failed task propagation). FOCUSED.txt records
combined regression. This is local asyncio orchestration, **not** a distributed
agent fabric or independent process sandbox. Provenance of approvals and real
external tool authorization must be enforced by the host application.
