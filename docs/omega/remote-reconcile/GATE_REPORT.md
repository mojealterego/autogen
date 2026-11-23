# OMEGA 13.0 — independent local verification / release gate

Project: `Magnetic-Zero`, strict `main` ancestry from `ddbb620029d4fac9e87c18a89d81067ef7041f3f`.

| Gate | Decision | Deterministic evidence |
|---|---|---|
| GATE-01 requirements | PASS (OM-23 local subset) | Only `uncertain` remote attempts may reconcile; subsequent dependent work is explicit |
| GATE-02 architecture | PASS (local) | Typed authority reads, no `submit` in reconciler, trusted fixed identity, callback timeout, original deadline persisted |
| GATE-03 implementation | PASS (local) | Actual journal state machine transition, testable resolver and standalone demo; no simulation-only stub |
| GATE-04 tests | PASS | 18 stage tests; 199 focused OMEGA tests; AST parsing 586 Python files |
| GATE-05 integration | PASS (local) / BLOCKED (multi-host) | Real worker and real independent TLS transport; physical multi-host partitions not executed |
| GATE-06 security | PASS (local boundaries) / BLOCKED (production) | No remote submission, pin mismatch veto, tenant/job spoof rejection, output bounds, tamper detection, timeout and failure cases |
| GATE-07 recovery | PASS (local) / BLOCKED (replicated) | SQLite WAL journal replay, independent executor restart, transitive DAG reopening, original workflow expiry preserved |
| GATE-08 documentation | PASS | `ACCEPTANCE.json`, source hashes, `TDD_RED.txt`, `STAGE_GREEN.txt`, `FOCUSED_FINAL.txt`, `UPSTREAM_FINAL.txt` and demo output |
| GATE-09 final production acceptance | BLOCKED | Core 387 passed, two collection errors (`pytest_mock`, `tiktoken`); no replicated consensus, external sink fencing or live deployment |

## Security model

**Trusted configuration required:** producer's authentication, tenant and remote handler are controlled by operator and cannot be selected by an LLM. Reconciliation reads only an existing, deterministic remote job ID. Returned `JobSnapshot` must match that tenant and job ID exactly. Only terminal `succeeded`/`failed` can change a durable `uncertain` event. Reconciled results are serialized into the original audit hash chain; a pinned external journal head detects stale reads and rewriting.

**No exactly-once claim:** a trustworthy cluster record establishes a reported remote terminal status, not proof of exactly-once effects in an uncooperative external API. The design never retries a non-idempotent external job during reconciliation. Dependent work is executed only if the separate authorized `run` call passes all existing checks and the original deadline remains valid.

## Residual risks

External authority authenticity depends on the configured backend (mTLS in integration tests). Journal head authenticity depends on external pinning; hashes alone are not signatures. Read-only calls are bounded by coroutine timeout but a blocking worker thread can continue if a remote backend does not enforce socket timeout. Production deployments require independently measured multi-host failures, side-effect sink fencing, credential rotation, policy review and security testing. Learned Titans, R³Mem, Meta CWM, LTN and autonomous RSI are **not** introduced by this change.
