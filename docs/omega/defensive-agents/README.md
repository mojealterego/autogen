# Magnetic-Zero OMEGA 12.0 — Bounded defensive specialists

**Scope:** opt-in implementations of the new proposal's `Shadow Hunter`,
`Web Warden`, `Economic Optimizer`, and `Autonomous Red Teamer`, integrated
with the existing `HierarchicalSwarm` and `CognitiveOODA` from versions
7.0 and 11.0. Implemented in `autogen_core._defensive_agents`.

This is a **verified local defensive substrate**, not a production EDR,
security monitoring daemon, PID-to-tab attribution engine, LTN/Titans/CWM
model, live browser hardening, unattended adversarial service, or privileged
Windows/TrustedInstaller remediation solution.

## Supported capabilities

| Component | Actual security contract | Important limitation |
|---|---|---|
| `ShadowHunter` | Validate trusted `(tenant, agent, origin)` allowlists; parse observations without opening URLs; flag unknown origins/agents and simple prompt-injection markers | Inputs must come from an authenticated telemetry source; findings are heuristic leads, not confirmed compromise |
| `ReadOnlyWebWarden` | Bounded HTTP `GET /json/list` against operator-enabled **127.0.0.1** Chrome DevTools endpoint; strip secret-bearing URL paths, query, tab titles and WebSocket debug addresses | Actual Chrome was **not** present in the test environment; read-only transport was tested against a real loopback HTTP fixture. No PID inference, tab discard, script execution or browser mutation |
| `EconomicOptimizer` | SQLite WAL cross-process token and micro-USD quoted admission; bounded request counts and exact-content repetition; unique operation IDs, idempotent settlement, conservative charge after unknown outcome | Requires trusted actual-usage reports; prices are operator-supplied estimates, not provider invoices; local shared SQLite is **not** multi-host HA |
| `budgeted_swarm_handler` | Adapt a trusted metered async handler into `HierarchicalSwarm`; bind a fixed tenant/run/agent, reserve before dispatch, settle actual usage, prevent repeated execution | Never automatically replay a call after ambiguous provider result; handler is trusted; no untrusted generated code runs |
| `DefensiveRedTeamer` | Deterministic, strictly offline replay of labeled defensive fixtures; compute observed false-acceptance and false-rejection counts and evidence digest | No attacks on external systems, no vulnerability scanning, no 24/7 execution or model-fitness claims |
| `SecurityOODAObserver` | Record redacted finding codes and digest in the existing lossless episodic store; simulate a **read-only** `WorldAction` with no approval ticket | A finding never becomes authority to modify a host; action still requires independent operator approval |

### Standalone reproducible smoke test

From repository root, with `autogen-core` locally installed:

```sh
python docs/omega/defensive-agents/demo.py
```

The demo creates isolated temporary SQLite databases and executes **no external
LLM call, OS privilege change, browser connection, scanner or exploit**. It
prints one redacted JSON object with actual fixture results and economic usage.

### Running tests

```sh
python -m pytest -q python/packages/autogen-core/tests/test_omega_defensive_agents.py
python -m pytest -q python/packages/autogen-core/omega_tests python/packages/autogen-core/tests/test_omega_*.py
python -m pytest -q python/packages/autogen-core/tests --continue-on-collection-errors
```

Log files `TDD_RED.log`, `TDD_CLASSIFICATION_RED.log`, `TDD_REDACTION_RED.log`, `TDD_NESTING_RED.log`,
`STAGE_GREEN.txt`, `FOCUSED_FINAL.txt`, `UPSTREAM_FINAL.txt` record the local
results. `ACCEPTANCE.json` summarizes the exact environment and acceptance.

## Detailed integration contract

1. **Trusted binding** — the application configures static tenant/agent
   endpoint allowlists, budget policies, physical database locations and
   tool handlers. Never expose those constructors or policy mutation to an LLM.
2. **Telemetry** — the application may supply `ShadowObservation` with a raw
   URL and untrusted text. The classifier never connects to that URL; only
   `ShadowVerdict.origin`, findings and digest leave the classifier. The
   observation's raw fields are excluded from dataclass `repr`.
3. **Browser** — only the loopback CDP HTTP inventory endpoint is contacted.
   Local DevTools debugging grants powerful capabilities to other clients,
   so do **not** expose the port on a public interface, proxy it remotely,
   or enable it on sensitive profiles without authorization.
4. **Token reservation** — each operation ID is single-use. The local
   coordinator conservatively charges the maximum quote for uncertain
   or cancelled in-flight calls. A malicious or crashed external provider
   is not assumed to be exactly-once.
5. **Policy and OODA** — the read-only observation adapter stores only
   redacted metadata. A future remediation actuator must be separately
   authenticated, typed, scoped, approved, fenced and independently tested.
6. **Offline adversarial replay** — tests use predeclared synthetic examples,
   real OS processes for SQLite contention, and a real loopback HTTP server
   for CDP transport. **Neither Chrome itself nor a real malware sample was run.**

## Design corrections to supplied proposal

- The proposed `inspect_cdp_protocol(pid)` returned a **fabricated finding**:
  a PID and browser extension cannot be attributed without evidence. Chrome
  discovery (`/json/list`) does not provide an OS PID mapping.
- There is no standardized `Page.discard` operation in the CDP `Page` domain.
  Browser tab lifecycle/discard is a distinct concern; this release makes
  **no browser mutations**.
- A string comparison on `os.remove` cannot prove LTN constraints; exact
  authorization remains with the existing typed `SymbolicGate` and HITL.
- Semantic agent collusion cannot be decided reliably from identical text.
  The budget guard enforces **exact-content repetition and configured quotas**.
- An E2B sandbox, SAST scanner, LLM prompt or elevated Windows token is not
  a proof of program safety. No privileged executor was added.
- `AlphaEvolve`, Gödel-agent RSI, Titans/R³Mem, Meta CWM, LTN learning,
  GPU/FPGA acceleration and real model benchmark remain separate open
  roadmap requirements, not hidden simulation stubs.

References: [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/),
[CDP Target domain](https://chromedevtools.github.io/devtools-protocol/tot/Target/),
[CDP Page domain](https://chromedevtools.github.io/devtools-protocol/tot/Page/).

## Operational status

- `IMPLEMENTED`, `TESTED LOCALLY`, `INTEGRATED LOCALLY` — see direct test logs.
- `AUDITED LOCALLY` — static boundary review recorded here; not an external
  independent audit or full security certification.
- `DEPLOYED`, `PRODUCTION-VERIFIED` — **NOT PERFORMED**.
- Upstream test gate `OM-06` remains **BLOCKED** until the missing locked
  `pytest-mock` and `tiktoken`/provider extras are installed and all tests pass.
