# OMEGA 11.0 — Bounded Cognitive OODA (local substrate)

## Scope and evidence

An opt-in practical substrate for the user's Magnetic Zero cognitive architecture.
This is a **real locally tested implementation**, not an implementation of
Titans neural test-time memorization, R³Mem virtual tokens, Meta CWM model
inference, Logic Tensor Networks, Godel-machine self-modification, E2B remote
execution, production multi-host HA or Windows TrustedInstaller workflows.

Exports from `autogen_core`:

- `CognitiveEvent`, `EpisodicStore`, `MemoryReceipt`: canonical JSON observations,
  zlib lossless compression, SHA-256 integrity on read, durable SQLite WAL,
  per-tenant access key and row capacity, replay-safe event identity, bounded
  recent retrieval. `novelty_proxy = 1 / prior_kind_occurrences` measures
  encounter frequency; **not** Titans gradient-based surprise.
- `WorldAction`, `WorldModel`, `SimulationOutcome`: finite typed state transitions
  (`read`, `set`, `increment`) on bounded nonnegative integer resources. Pure
  simulation, immutable resource keys, deterministic trace and digest. No
  generated Python gets imported, evaluated or executed.
- `ApprovalLedger`: SQLite WAL `pending -> approved -> consumed` tickets with
  operation idempotency, trusted actor allowlist, expiry, durable clock floor
  and atomic single-use consumption. The application must authenticate the
  human approver; the ledger itself has no login or cryptographic identity.
- `CognitiveOODA`: async observe -> novelty orientation -> deterministic world
  simulation -> optional existing `SymbolicGate` policy -> approval ticket ->
  explicitly authorized, trusted host callback. A failed/timeout actuator is
  recorded as **unknown** (ticket already consumed), never blindly retried.
  The `CognitiveDecision` digest binds state, event, plan and simulation.
  Actor-based `HierarchicalSwarm` integration is covered by an actual test.

### Example: safe planning only (no effects)

```python
import asyncio
from tempfile import TemporaryDirectory
from pathlib import Path
from autogen_core import (
    CognitiveEvent, EpisodicStore, ApprovalLedger, WorldModel,
    WorldAction, CognitiveOODA,
)

async def main():
    with TemporaryDirectory() as directory:
        path = Path(directory)
        app = CognitiveOODA(
            EpisodicStore(path/'events.sqlite'),
            WorldModel(frozen_keys=frozenset({'system'})),
            ApprovalLedger(path/'approval.sqlite', {'project': frozenset({'operator'})}),
        )
        decision = await app.assess(
            CognitiveEvent('project', 'evt-1', 'telemetry', {'cpu': 72}),
            {'workers': 2, 'system': 1},
            (WorldAction('increment', 'workers', 1),),
        )
        assert decision.status == 'awaiting_approval'
        assert decision.final_state['workers'] == 3
        # No real system changes occurred and no model can self-approve.

asyncio.run(main())
```

### Integration contract

Trusted host code supplies the tenant identity, policy, frozen keys, actor
registry and action callback. All text from prompts, web pages and LLMs is
untrusted input. The actor may propose bounded values only; it cannot choose
new privileges, tools, approvers, subprocesses or external endpoints. A real
actuator must check its own target state, tenant and fences atomically, as
pure simulation is not an authorization proof against TOCTOU.

Never pass a model-generated Python string to `act`; the handler must be a
trusted application-provided async callable. Never expose `ApprovalLedger`
`approve()` or `consume()` directly as model-callable tools.

### Threat model and non-goals

- No hardware/OS sandbox: a trusted callback can perform arbitrary effects.
- No encryption-at-rest or dedicated database tenant isolation: protect the
  SQLite path by OS user controls and disk encryption.
- SHA256 is unkeyed: an adversary with write access can replace both data
  and digest. Pin head externally or use signed append-only logs for stronger
  integrity. Content may be subject to retention and deletion requirements.
- Tickets are **at-most-one dispatch attempts**, not globally exactly-once
  effects. An application crash after `consume()` leaves an ambiguous outcome.
- Expiry uses a persisted wall-clock floor; strong multi-host time ordering
  needs a single authoritative time service or distributed consensus.
- `asyncio.wait_for` is a cooperative cancellation guard, not a safe external
  command timeout. Untrusted code requires a separate sandbox/OS identity.
- A source-code string scan for `os.remove` does not implement LTN, security
  correctness, or formal verification; the provided paper example is not used.
- Do not automatically elevate Windows privileges or alter protected objects.

### TDD and verification

`TDD_RED.log` records import failure before implementation. `STAGE_GREEN.txt`
records 17 tests covering persistence, tenant boundaries, tamper, conflict,
replay, quotas, deterministic simulation, parser rejection, expiry, rollback,
parallel actor race, unknown actuator outcome and swarm integration.
`FOCUSED.txt` records the broader 158-test OMEGA regression.
`UPSTREAM_ATTEMPT.txt` records the full core attempt and any dependency
collection errors; it is **not** a successful full release gate.

Tests (run from repository root with installed dependencies):

```sh
python -m pytest -q python/packages/autogen-core/tests/test_omega_cognitive_ooda.py
python -m pytest -q python/packages/autogen-core/omega_tests python/packages/autogen-core/tests/test_omega_*.py
python -m pytest -q python/packages/autogen-core/tests --continue-on-collection-errors
```
