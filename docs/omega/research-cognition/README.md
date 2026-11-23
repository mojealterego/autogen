# Magnetic-Zero OMEGA 14.0 — five bounded research integrations

Branch: **main** only. Baseline: `cb74dd201498db6d75e14812c50da15822eea8b8`.

This milestone contains **real trainable and executable local components**, not
claims of a faithful, GPU-scale implementation of Google Titans MAL, the R³Mem
paper, Meta CWM 32B, LTNtorch, or unlimited Gödel-agent recursive improvement.

Optional imports do **not** alter default AutoGen dependencies:

```python
from autogen_core.research import (
    TitansMAL, TenantTitansBank, VirtualTokenCodec, HierarchicalR3Memory,
    LTNAxiomLearner, HardLTNGate, FiniteInvariantProver,
    CWMClient, RestrictedRSI, canonical_policy_source,
)
```

The optional research module needs **PyTorch** (`torch 2.10.0+cpu` was actually
used in this local verification). It is not imported when users import the
ordinary `autogen_core` root. Do not silently upgrade the repository's
upstream `uv.lock` without an actual lock regeneration and verification.

## Scope and acceptance distinctions

| Requirement | Implemented and tested | Not implemented / not claimed |
|---|---|---|
| OM-35 Titans MAL | A bounded test-time-learned **linear memory layer** using the **actual MSE gradient norm** as surprise, per-step momentum, decay, and MAL-style residual path; tenant-specific SHA-pinned checkpoints saved and restored with `weights_only=True` | Full Titans paper memory architecture, trained token transformer, test-time meta-optimizer, long-context benchmarks, millions of tokens |
| OM-36 R³Mem | Differentiable input-token → K learned latent/virtual-token bottleneck → trainable reconstruction; UTF-8 document/paragraph/entity hierarchy; measured loss and fidelity, latent integrity digest plus verified exact-reconstruction gate; pinned decoder checkpoint | Faithful virtual-token injection into an LLM, original paper's backward reconstruction method, exact retrieval without raw backing store, arbitrary/infinite history |
| OM-37 Meta CWM | Real OpenAI-style HTTP transport client, optional fixed host allowlist, strict endpoint and schema, bounded response, typed `WorldAction`, deterministic `WorldModel` simulation, **real local HTTP-server integration tests** | Download/inference of Meta's 32B weights, verified GPU backend model provenance, safe execution of proposed Python, E2B cloud sandbox, GPU benchmark |
| OM-38 LTN and hard proof | Actual PyTorch neural predicate fitted with gradient, differentiable fuzzy universal pMeanError satisfaction, `HardLTNGate` reusing existing `SymbolicGate`; **exhaustive finite-state proof for explicitly enumerated domains** with counterexample | Full LTNtorch package, quantifier-rich arbitrary FOL, Z3/SMT over unbounded states; fuzzy score is **not** a proof |
| OM-40 RSI | Actual bounded modification of canonical Python source containing one `POLICY` literal, trusted fixture-based candidate comparison using `WorkflowEvolutionEngine`, bounded automatic candidate search, mandatory external operator HMAC approval, atomic SQLite CAS, append-only signed audit, source materialization/rollback, denial of dynamic code | Arbitrary production source rewrites, autonomous approval, live runtime code import/hot reload, agent mutation of own security policy, CI deployment, unrestricted self-improving optimizer |

## Standalone demonstration

```bash
# Repository-root environment with locally installed autogen-core, torch and pytest
python docs/omega/research-cognition/demo.py
```

Outputs **measured** local loss changes, fixture-only decoder reconstruction,
actual LTN satisfaction, a bounded counterexample, and signed RSI source
revision. Does not call an LLM, external internet or privileged runtime.

## Tests

```bash
python -m pytest -q python/packages/autogen-core/tests/test_omega_research14.py
python -m pytest -q python/packages/autogen-core/omega_tests python/packages/autogen-core/tests/test_omega_*.py
python -m pytest -q python/packages/autogen-core/tests --continue-on-collection-errors
```

The first test includes real loopback HTTP against an OpenAI-compatible test
server. The second suite covers existing durable workflows, local mTLS and
OS process tests. Existing upstream collection failures are a distinct
release gate, not proof of successful regression.

## Mandatory security boundaries

- PyTorch checkpoints are **size-bounded and SHA-pinned** before
  `torch.load(weights_only=True)`. An external immutable digest must be managed
  by the trusted application; a local digest cannot prevent complete rewrite.
- The learned R³ decoder is **lossy unless accuracy is measured as 100% on a
  specific sample**; no unseen-data guarantee is made. The earlier
  `EpisodicStore` maintains original events for exact retrieval.
- For CWM, operator-owned static `https://` endpoints and explicit host
  allowlists are mandatory. Only `127.0.0.1` may use HTTP with an explicit
  test flag. **No source-code execution** of LLM replies. A remote service
  claiming to be `facebook/cwm` is not proof of loaded Meta weights.
- LTN is used for soft risk scoring, **never** as authorization. Existing hard
  `SymbolicGate` rejects invalid actions independently; exhaustive proof
  applies only to the explicitly bounded domain.
- RSI does not evaluate Python via `eval`, `exec`, or `import`. It only
  parses canonical Python literals with AST and generates an operator-signed
  policy artifact. Signing keys never enter an LLM prompt or agent tool
  registry. A decision cannot approve itself. PostgreSQL/HA, signed Git
  releases, external identity and canary deployments remain open.
- `RestrictedRSI` DB + source artifact must be on a **trusted local filesystem**.
  Database is authoritative; a crash between DB commit and file materialization
  can be healed by `materialize()`. External immutable pinning is required for
  protection from whole-history replacement.
- No external scanning, OS privilege escalation or production actions are
  performed by these research modules.

## Original technical references

- Google Research, **Titans: Learning to Memorize at Test Time** (NeurIPS 2025): https://research.google/pubs/titans-learning-to-memorize-at-test-time/
- Wang et al., **R³Mem: Bridging Memory Retention and Retrieval via Reversible Compression** (ACL Findings 2025): https://aclanthology.org/2025.findings-acl.235/
- Meta FAIR, **CWM: An Open-Weights LLM for Research on Code Generation with World Models**: https://ai.meta.com/research/publications/cwm-an-open-weights-llm-for-research-on-code-generation-with-world-models
- **LTNtorch**: https://github.com/logictensornetworks/LTNtorch

Each local abstraction is named for its architectural direction, but its
limitations above are part of the acceptance contract. Training on synthetic
fixtures is **not** an external accuracy or safety benchmark.
