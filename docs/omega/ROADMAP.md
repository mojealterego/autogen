# Roadmap — evidence gates

| ID | Scope | Status | Acceptance evidence |
|---|---|---|---|
| OM-01 | Cancellation robustness | implemented / locally tested | cross-thread, callback isolation, release, upstream cancellation tests |
| OM-02 | Interceptor composition and queue accounting | implemented / integration tested locally | real runtime tests for drop, exception, None and pipeline |
| OM-03 | Typed bounded DAG / retry policy | implemented / locally tested | cycle, unauthorized handler, budget, timeout, concurrency, dependent skip |
| OM-04 | Runtime adapter | implemented / integration tested locally | real registered RoutedAgent with two dependent nodes |
| OM-05 | Linked audit metadata | implemented / locally tested | hash verification and tampering tests |
| OM-06 | Full upstream regression | BLOCKED | 387 passed, 2 collection errors (`pytest_mock`, `tiktoken`); missing optional provider dependencies; current evidence in `remote-reconcile/UPSTREAM_FINAL.txt` |
| OM-07 | Durable checkpoint/recovery (single host) | implemented / locally integration tested | SQLite WAL, real child process failure, replay, retry key, persisted deadline, lock and journal tests; see `durable/` |
| OM-08 | Process sandbox / cross-tenant isolation | open | negative authorized/unauthorized tests with real processes |
| OM-09 | Distributed scheduling and model evaluation | open | multi-host failure injection and reproducible model/cost measurements |
| OM-10 | Studio, .NET, AgentChat distribution builds | open | native builds and real integration tests, no assumptions |

| OM-11 | Durable journal integrity | implemented / locally tested | atomic cursor, chained output and transition verification, exact external head |
| OM-12 | Distributed durable execution and global admission control | partial (single-host substrate) | authenticated service, leases/fencing across hosts, real fault injection |
| OM-13 | Trusted Linux process workers | implemented / locally integration tested | per-process RLIMITs, bounded IPC, timeout and cancellation, durable replay; see `process/` |
| OM-14 | Adversarial isolation, aggregate quotas and supervisor | open | cgroups, separate OS identity, network/filesystem restrictions, crash cleanup and escaped-process tests |

| OM-15 | Shared single-loop resource admission | implemented / locally integration tested | FIFO, global and tenant reservations, bounded queues, cancellation cleanup, two durable executors with real process workers; see `scheduler/` |
| OM-16 | Cross-process/host global scheduler and fencing | partial | local durable multi-process coordinator (OM-17); cross-host authentication and HA (OM-18) remain open |
| OM-17 | Durable local multi-process job coordinator | implemented / locally integration tested | SQLite WAL transactions, capability and quota admission, real concurrent spawned processes, real process crash, lease renewal, fencing, clock-rollback regression; see `cluster/` |
| OM-18 | Authenticated cross-host scheduler transport and HA | partial (local mTLS transport verified) | authenticated worker/producer identities, network partitions, replicated authority and fencing enforced at external sinks; independent multi-host tests |
| OM-19 | Durable DAG-to-cluster adapter | partial / locally integrated | stable remote job identity, lost ACK and terminal uncertainty, now late outcome reconciliation; independent multi-host failure/recovery still open |
| OM-20 | Certificate-bound bounded scheduler transport | implemented / locally integration tested | real mTLS, independent remote worker process, role/pin denial, stale fencing, saturation and lost response reconciliation; see `fabric/` |
| OM-21 | Physical multi-host resilience and replicated authority | open | real network partition/reconnect, controlled failover with durable externally enforced fencing, no split brain |
| OM-22 | Durable DAG producer bridge (5.2) | implemented / locally integration tested | stable job IDs, real mTLS + worker, lost ACK, terminal uncertainty, tenant check; `distributed-dag/` |
| OM-23 | Late-outcome durable reconciliation | implemented / locally integration tested (13.0) | 18 tests, real worker and mTLS status reads, proof-bound journal updates, skipped descendant reopening without remote replay; `remote-reconcile/` |
| OM-24 | Shared-database authority epoch & fail-closed takeover (5.3 local) | implemented / locally integration tested | transaction-atomic guard, rollback resistance, session revocation, independent processes, real mTLS stale leader rejection; `authority/` |
| OM-25 | Replicated HA authority (5.3 distributed gate) | BLOCKED | independently replicated consensus store, physically separate hosts, partition/failover and external sink fencing required |
| OM-26 | Adaptive model selection (6.0 local) | implemented / locally integration tested | tenant+global budgets, circuit breaker, latency EWMA, trust-bound fallback, real HTTP provider, durable workflow integration; `model-router/` |
| OM-27 | Persistent distributed router cost ledger & genuine provider benchmark | open | atomic cross-process quotas, invoice reconciliation, real-provider evaluation dataset, p95/p99 benchmark and safe rollout |
| OM-28 | Hierarchical bounded local agent swarm (7.0) | implemented / locally integration tested | hierarchy and tenant authorization, independent verification, operator grants, DAG bounded execution; `swarm/` |
| OM-29 | Deterministic offline workflow policy evaluation and rollback gate (8.0) | implemented / locally tested | accuracy/cost/p95 gates, bounded samples, evidence fingerprint, explicit approval and canary rollback; `evolution/` |
| OM-30 | Bounded typed AST runtime policy proof gate (9.0) | implemented / locally integration tested | injection denial, typed validation, bounded execution, redacted evidence hash and guarded swarm invocation; `neurosymbolic/` |
| OM-31 | Local autonomous process service supervisor (10.0) | implemented / locally integration tested | real child processes, bounded restarts, health checks, secret allowlist, process-group cleanup, audit; `infrastructure/` |
| OM-32 | Four-stage OMEGA 7.0–10.0 local acceptance | partial — focused test gate PASS, upstream gate BLOCKED | `swarm/`, `evolution/`, `neurosymbolic/`, `infrastructure/`; 141 focused tests, 247 core tests, 10 dependency collection errors |
| OM-33 | Offline dependency integrity and replay gate | implemented / locally tested | 5 verified wheel-auditor tests, SHA-256 against pinned uv.lock, new full-core evidence, remaining blockers documented; `dependency-gate/` |
| OM-34 | Bounded OODA substrate (11.0) | implemented / locally integration tested | 17 new tests, 158 focused OMEGA pass; memory, deterministic world, ticket idempotency, HITL, actor integration; `cognitive-ooda/` |
| OM-35 | Learned Titans MAL and real model surprise gradients | partial — 14.0 test-time linear MAL-style layer locally trained | actual gradient surprise, momentum, tenant checkpoints; full Titans transformer/long-context benchmarks OPEN; `research-cognition/` |
| OM-36 | R³Mem virtual-token reversible context compression | partial — 14.0 learned virtual-token bottleneck | actual encoder/decoder training, UTF-8 hierarchical memory, measured reconstruction; full R³Mem LLM integration and verified reversibility OPEN; `research-cognition/` |
| OM-37 | Real CWM + isolated trajectory sandbox | partial — 15.0 Meta Fastgen HTTP route and bounded read-only traces | original Meta server source AST protocol cross-check, real loopback HTTP; original 32B model and isolated generated-code execution NOT TESTED; `research-cognition/` |
| OM-38 | LTN learned constraints + hard formal verifier | partial — 15.0 real LTNtorch 1.0.2 learned FOL satisfaction and hard veto | original LTNtorch quantifiers and SatAgg tested from supplied source, no SMT/unbounded proof claim; `research-cognition/` |
| OM-39 | Durable authenticated multi-host HITL | open | identity provider, PostgreSQL, genuine actor auth, revoke, signed audit, crash recovery |
| OM-40 | RSI controlled source evolution | partial — 14.0 restricted canonical Python source mutation | measured candidate acceptance, external HMAC authorization, SQLite CAS, signed audit, rollback; arbitrary code/CI deployment OPEN; `research-cognition/` |

| OM-41 | User-distribution provenance and dependency replay | partial / locally tested | original wheels, RECORD digests and official hash checks; 158 focused tests originally green; updated 181 OMEGA, 369 core pass, 2 collection errors; `dependency-gate/`, `defensive-agents/` |
| OM-42 | Shadow Hunter trusted telemetry review (12.0) | implemented / locally tested | tenant-agent endpoint allowlists; heuristic injection clues, no raw prompt in reports; negative URL and cross-tenant tests; `defensive-agents/` |
| OM-43 | Web Warden local read-only CDP inventory (12.0) | implemented / locally integration tested | actual loopback HTTP server, GET-only, query/title removal, duplicate JSON, size/redirect and host-denial tests; real Chrome PID mapping OPEN |
| OM-44 | Local cross-process economic governor (12.0) | implemented / locally integration tested | real independent process contention, token+micro-USD quota, duplicate discussion limit, cancellation charge, deterministic replay denial; provider invoicing and distributed ledger OPEN |
| OM-45 | Controlled offline Red Team (12.0) | implemented / locally tested subset | typed fixture labels, observed false-accept/false-reject confusion, bounded offline replay, deterministic evidence; 24/7 or external targets NOT IMPLEMENTED |
| OM-46 | Shadow + Economic + OODA + HierarchicalSwarm integration (12.0) | implemented / locally tested | trusted handler metering, real Swarm verifier and OODA read-only memory, no automatic actuator; `defensive-agents/` |
| OM-47 | Real telemetry agents / live Chrome PID mapping / production canary | open | authenticated network ingest, real Chrome CDP process lineage, exact browser session mapping, isolated red-team scope, secured provider credentials, long-running deployment and authorized rollback |

| OM-48 | Reconciliation 13.0 local integration acceptance | partial — focused PASS, upstream BLOCKED | 199/199 focused, 387 core pass + 2 dependency collection errors; external HA / sink E2E not done; `remote-reconcile/` |

| OM-49 | Five-direction research integration 14.0 | local subset under verification; production BLOCKED | bounded PyTorch MAL/R3, CWM HTTP fixture, soft LTN + hard finite proof, gated RSI source mutation; `research-cognition/` |

| OM-52 | Upstream LTNtorch 1.0.2 (15.0) | implemented / locally integrated with exact user source | real `ltn.Variable`, `Predicate`, `Connective`, `Quantifier` and `SatAgg`, learned FOL satisfaction and hard symbolic veto; no SMT completeness; `upstream-integration/` |
| OM-50 | Original Meta CWM Fastgen compatibility (15.0) | partial / local protocol tested | Meta `/chat/completions` route, `reasoning.enabled=false`, original server source AST comparison, read-only bounded trace parser and independent comparison; actual CWM 32B inference BLOCKED (no weights) |
| OM-51 | Pinned third-party source provenance (15.0) | implemented / locally tested | original uploaded LTNtorch and CWM archives pass SHA-256, ZIP CRC and anti-traversal/expansion checks; 6 negative/success tests; model weights absent |
| OM-53 | Offline IMEI and camera EXIF evidence validators (16.0) | implemented / standalone local tests PASS; repository integration not yet verified | strict 15-digit Luhn/TAC, metadata serial consistency, redacted digests, no network/location claims; `autogen_core._forensic_evidence`; 3 standalone local tests PASS, full core suite NOT RUN |
| OM-54 | OMEGA-X Signal Intelligence offline correlation (17.0) | implemented on main / 13 standalone local unit tests PASS on equivalent module; exact SHA test runner NOT RUN | four-domain event schema, strict tenant/domain validation, bounded 1024-event input, deterministic sliding-window correlation, evidence SHA-256; no live SDR/GNSS/eBPF integrations; `autogen_core._signal_intelligence`, `test_signal_intelligence.py` |
| OM-55 | OMEGA-X bounded authorized signal-event ingestion (17.1) | implemented on main / isolated local gateway tests PASS; exact remote SHA and full AutoGen integration NOT RUN | versioned v1 schema, source/domain authorization, atomic batches, per-tenant bounded queues, backpressure, atomic per-tenant drain and offline correlation; no live hardware or durable queue; `_signal_ingest.py`, `test_signal_ingest.py` |
