# Roadmap — evidence gates

| ID | Scope | Status | Acceptance evidence |
|---|---|---|---|
| OM-01 | Cancellation robustness | implemented / locally tested | cross-thread, callback isolation, release, upstream cancellation tests |
| OM-02 | Interceptor composition and queue accounting | implemented / integration tested locally | real runtime tests for drop, exception, None and pipeline |
| OM-03 | Typed bounded DAG / retry policy | implemented / locally tested | cycle, unauthorized handler, budget, timeout, concurrency, dependent skip |
| OM-04 | Runtime adapter | implemented / integration tested locally | real registered RoutedAgent with two dependent nodes |
| OM-05 | Linked audit metadata | implemented / locally tested | hash verification and tampering tests |
| OM-06 | Full upstream regression | blocked | missing development dependencies and intermittent process/timing failure in 5.1 full suite; see `fabric/` evidence |
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
| OM-19 | Durable DAG-to-cluster adapter | open | stable operation identity, remote result reconciliation, unknown-outcome recovery, cancellation and retry under process/network failures |
| OM-20 | Certificate-bound bounded scheduler transport | implemented / locally integration tested | real mTLS, independent remote worker process, role/pin denial, stale fencing, saturation and lost response reconciliation; see `fabric/` |
| OM-21 | Physical multi-host resilience and replicated authority | open | real network partition/reconnect, controlled failover with durable externally enforced fencing, no split brain |
| OM-22 | Durable DAG producer bridge (5.2) | implemented / locally integration tested | stable job IDs, real mTLS + worker, lost ACK, terminal uncertainty, tenant check; `distributed-dag/` |
| OM-23 | Late-outcome durable reconciliation | open | externally confirmed terminal result, journal continuation without replaying side effects |
| OM-24 | Shared-database authority epoch & fail-closed takeover (5.3 local) | implemented / locally integration tested | transaction-atomic guard, rollback resistance, session revocation, independent processes, real mTLS stale leader rejection; `authority/` |
| OM-25 | Replicated HA authority (5.3 distributed gate) | BLOCKED | independently replicated consensus store, physically separate hosts, partition/failover and external sink fencing required |
| OM-26 | Adaptive model selection (6.0 local) | implemented / locally integration tested | tenant+global budgets, circuit breaker, latency EWMA, trust-bound fallback, real HTTP provider, durable workflow integration; `model-router/` |
| OM-27 | Persistent distributed router cost ledger & genuine provider benchmark | open | atomic cross-process quotas, invoice reconciliation, real-provider evaluation dataset, p95/p99 benchmark and safe rollout |
