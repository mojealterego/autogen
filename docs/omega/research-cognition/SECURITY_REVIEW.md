# Security and acceptance review — OMEGA 14 (local research subset)

Baseline Git `main`: `cb74dd201498db6d75e14812c50da15822eea8b8`.

## Trust boundaries

| Boundary | Concrete control | Residual risk |
|---|---|---|
| Generated code / LLM response | CWM transport accepts only small bounded JSON `WorldAction` proposals, whose effects are **pure simulations** under the existing `WorldModel`. Python code is never `exec`ed. | Meta CWM model weights were not executed. No real E2B sandbox or external compiler-trace verification. |
| Endpoint SSRF and identity | HTTPS, predeclared hostname allowlist, no URL credentials/query/fragment and exact `/v1/chat/completions`; `127.0.0.1` HTTP only when explicitly configured for tests. Response identity mismatch denied when present. | DNS rebinding and remote attestation of the *actual hosted weights* are not proven. Production requires network egress policy, credential secret store and model provenance. |
| Titans learned neural state | CPU float tensors only, fixed 1–256 dimensions, finite inputs, bounded gradient clipping, SHA-256 checked checkpoint, safe `weights_only=True`. | External trust anchor for digest, host filesystem permissions and persistent isolation needed. Simplified linear state is not full Titans MAL. |
| R³ learned reconstruction | Byte vocabulary, fixed max sample size, capped training, explicit reconstruction fidelity, source and latent SHA-256, `reconstruct_verified()` refuses mismatched exact data. | Generalization untested; latent might be *larger* than source; no general lossless or reversible neural model. Use `EpisodicStore` for exact event recovery. |
| LTN-to-permission escalation | Fuzzy score informs a suggestion only; exact `SymbolicGate` rules independently veto. Finite invariant enumeration makes only bounded-domain claims. | No SMT theorem proof for unbounded states, and soft satisfiability not a mathematical safety guarantee. |
| RSI self-modification | Automatic bounded candidate search, deterministic evaluator, canonical `ast.literal_eval` policy-only Python, no `eval`/`exec`, fixed tenant ID, immutable signing key configured out-of-band, HMAC evidence, SQLite CAS, signed history verification, operator-authorized rollback. | No OIDC operator identity, physical HSM, distributed consensus, signed Git release or safe source hot-reload. Host with access to DB and signing key remains trusted. |
| Cross-tenant sharing | Tenant-specific memory bank and source database scope; tests for tenant identity and stale revision. | Single host, local trusted storage, no OS UID/process or Kubernetes isolation proven. |

## Fail-closed operation

- A failed neural decode is **not** a lossless result and must fail closed or
  read an exact record from the existing episodic store.
- Model text cannot introduce Python commands, Unix shell, tool bindings,
  browser state mutations or privileged Windows tokens.
- An LTN numeric confidence does not override an explicit symbolic veto.
- RSI candidate evaluation never promotes by itself, including when the
  automatically searched policy has perfect local fixture accuracy.
- Signed operator approval is a dedicated external action; the LLM must
  never receive approval keys or the method used to sign a candidate.
- No `SAST PASS`, production zero-trust isolation, pen-test certification,
  GPU performance claim or independent formal proof may be inferred from the
  local test evidence.

## Proof expectations left open

Physical multi-host fencing; real provider usage/cost; actual CWM 32B weights;
full LTNtorch semantics; Titans memory-vs-attention long-context benchmark;
R³Mem original reversible backward method and held-out long document retention;
provider-authenticated traces; authorization against a real identity provider;
reproducible torch wheel hashes; sign-off of generated code under a real
isolated runtime; integration and production canary gates.
