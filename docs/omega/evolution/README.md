# OMEGA 8.0 — Policy Evolution Gate (trusted offline evaluations)

Opt-in `WorkflowEvolutionEngine` compares a bounded candidate configuration
with the currently active baseline on an identical deterministic set of JSON
fixtures. It is a policy-selection engine, not self-modifying source code.

- `EvolutionCandidate` constrains `max_parallel`, `retry_base_ms`, and
  `step_timeout_ms` to a fixed typed allowlist and bounded ranges.
- Trusted `Evaluator(candidate, case)` returns `EvolutionObservation` with
  correctness, duration and micro-USD price. Evaluation executes in seeded
  deterministic fixture order with a per-observation timeout.
- `EvolutionDecision`: SHA-256 evidence digest binds baseline, candidate,
  fixtures, observations and version. A candidate must not regress accuracy,
  p95 beyond configured tolerance, or cost beyond configured tolerance, and
  must improve at least one metric. No claimed statistical significance.
- `promote` demands explicit operator consent, exact revision and the same
  internal decision object; forged or stale evidence cannot activate.
- `record_canary` evaluates trusted post-activation samples and can roll back
  if observed accuracy or p95 violates the baseline gate.

The evaluator must actually invoke the relevant workflow for real results.
Tests use deterministic evaluations; those are algorithmic validation, **not**
independent real LLM benchmarks. All control state is currently in process.
No automatic rollout, Git mutation, online learning, external effects, or
persistent promotion history is implemented. Do not claim production readiness.

TDD_RED.log proves the absent API; TESTS.txt contains eight tests including
accuracy regression, cost/latency veto, forged proof, stale revision, evaluator
failure, deterministic fixture order and canary rollback.
