# Magnetic-Zero OMEGA 13.0 — late remote outcome reconciliation (OM-23)

**Status:** implemented and locally integration tested. This is an opt-in, trusted single-host journal reconciler with a real authenticated mTLS read path, **not** cross-host HA, third-party side-effect fencing, model-world simulation, autonomous attack tooling or exactly-once remote execution.

## Problem / previous behavior

A remote job may commit successfully **after** the DAG polling deadline expires. `distributed_step_handler` previously raised `OutcomeUnknownError`, leaving the durable step `uncertain` and its dependents `skipped` permanently. Re-running the durable workflow did not retry that non-idempotent step (correct), but also could not incorporate a later confirmed result (incomplete).

## New implementation

`autogen_core.distributed_step_reconciler(backend, tenant, remote_handler)` binds **trusted** remote producer credentials, tenant and handler, and produces a **read-only** observer. It derives the identical durable remote job ID used during original submission and invokes only `backend.get()`, never `submit()`.

`DurableWorkflowExecutor.reconcile(steps, ..., readers=..., expected_head=...)`:

1. Validates the original workflow revision, policy, graph, tenant/run and caller-pinned journal head before reading external status.
2. Retrieves selected `uncertain` outcomes with bounded per-read timeout; `queued`, `leased`, `uncertain`, transport outages and missing reads do **not** become invented failures or successes.
3. Validates the remote `JobSnapshot` tenant and stable operation identity, a restricted terminal state (`succeeded`/`failed`), canonical JSON serialization and the existing per-step output bound.
4. Stages **all** candidate outcomes first, then appends verified transitions into the existing SQLite journal with the original chained SHA-256 audit.
5. Reopens skipped descendants only when all their dependencies are now either successful or safely awaiting execution, without running any handlers during reconciliation.
6. Returns the updated head. A **separate explicitly authorized** `run(..., expected_head=updated_head)` resumes only the newly actionable descendants. The original persisted workflow deadline **never extends**.

### Executable local example

Run the complete [standalone demonstration](demo.py), which opens real local
SQLite coordinator and journal databases, submits one remote job, records a
bounded timeout, executes the job once in a real cluster worker, reconciles its
outcome by read-only `get()`, and resumes only the previously skipped child:

```sh
python docs/omega/remote-reconcile/demo.py
```

The demonstration prints the initial, reconciled and final states and the
updated journal head as JSON. It requires no external model or internet API.

Do **not** synthesize `RemoteReconciliation` from model output. The reader is a trusted authority connector, not an LLM tool. A custom reader must independently authenticate its external source, provide the stable operation key and reject contradictory states. A post-crash result may be genuine while its external sink effect still needs separate proof; this module does not eliminate distributed transaction uncertainty.

## Testing and evidence

```sh
python -m pytest -q python/packages/autogen-core/tests/test_omega_reconciliation.py
python -m pytest -q python/packages/autogen-core/omega_tests python/packages/autogen-core/tests/test_omega_*.py
python -m pytest -q python/packages/autogen-core/tests --continue-on-collection-errors
```

See `TDD_RED.txt`, `STAGE_GREEN.txt`, `FOCUSED_FINAL.txt`, `UPSTREAM_FINAL.txt`, `AST_COMPILE.json` and `ACCEPTANCE.json` in this directory. Tests include a genuinely independent mTLS producer/worker network transport, real worker completion after a durable uncertain journal outcome, multi-level descendant reopening, journal tampering, new executor recovery, timeout, spoofed IDs, failed remote results, and the non-submission guarantee.

**Remaining:** `OM-25` replicated authority, multi-host link partitions, external sink fencing and full upstream dependency availability. `OM-06` remains `BLOCKED`: at this environment's dependency level 387 core tests pass but two collection errors remain (`pytest_mock`, `tiktoken`); no production deployment was executed. The tagged Python source is AutoGen Core 0.7.5, compatible with the user's AutoGen 0.4+ requirement but **not** a live claim of exact AutoGen 0.4.
