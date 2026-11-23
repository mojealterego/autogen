# OMEGA 15.0 — local independent gates and limitations

| Gate | Decision | Evidence |
|---|---|---|
| GATE-01 Requirements | **PASS (local research subset)** | Source-accurate LTNtorch and Meta CWM protocol; original weights correctly remain blocked |
| GATE-02 Architecture | **PASS (local subset)** | Optional research imports, typed LTN adapter, hard `SymbolicGate`, read-only CWM parser, strict fixed HTTP routes |
| GATE-03 Implementation | **PASS (local subset)** | Actual LTNtorch 1.0.2 operators + differentiable learning, Meta route, non-executing trace format, source auditor |
| GATE-04 Deterministic tests | **PASS (local)** | 15 new tests; 240 focused OMEGA tests; AST parse 598 files; actual LTN source imported |
| GATE-05 Integration | **PASS (local)** / BLOCKED (real CWM model) | Real localhost HTTP test and AST of user-supplied original Fastgen server; no 32B weight inference |
| GATE-06 Security | **PASS (defined local boundaries)** / BLOCKED (external) | Hard authorization veto, tenant/source pin, bounds and negative tests, no model-executed code, no outbound privileged tools |
| GATE-07 Recovery | **NOT_APPLICABLE (new stateless adapters)** | Existing durable OODA/RSI recovery unchanged; production model/sandbox recovery not tested |
| GATE-08 Documentation | **PASS** | `SOURCE_PROVENANCE.json`, `SUPPLY_CHAIN_AUDIT.json`, TDD red/green, unit and upstream logs, acceptance evidence |
| GATE-09 Final release | **BLOCKED** | 428 upstream tests pass, but two collection errors (`pytest_mock`, `tiktoken`); missing CWM weights and no external deployment |

## Regression stabilization

The first broad regression showed a deadline-sensitive *existing* EconomicOptimizer spawned-process test: its second `ready.get(timeout=6)` could expire under optional PyTorch library collection load. The original acceptance assertion remained unchanged (two independent spawned processes; exactly one successful 65-token reservation; zero lost quota). The test now uses explicit longer handshake waits and always terminates and joins children, even after failures. A standalone run passed (1/1), and the final broader regression had no test failures; the two collection blockers are separate dependency problems. Source changes and test evidence are recorded with SHA-256.

## Third-party permissions

LTNtorch 1.0.2 source MIT; CWM code BSD-3; Meta weights separately gated and subject to FAIR noncommercial research restrictions. No model checkpoints, user tokens, external API keys, downloaded binary models or original repositories are bundled. MHTML snapshots do not grant model access.

## Integrity limitations

SHA-256 pinning detects changes relative to the files supplied by the user but is not independent origin authentication or a software supply-chain signature. Untrusted archives were never imported directly, automatically executed or installed into global Python. The original LTN library was imported from its extracted isolated source for tests. The Meta source contract was checked statically; the actual model and SDK-backed server were not launched. `GATE-09 PASS` is **not** claimed.
