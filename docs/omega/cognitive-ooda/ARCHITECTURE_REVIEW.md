# Research integration decisions — Magnetic Zero cognitive architecture

Sources assessed for **architectural orientation**, not deployment certification:

- AutoGen stable Magentic-One team docs: https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/magentic-one.html
- Google Titans paper: https://research.google/pubs/titans-learning-to-memorize-at-test-time/
- ACL 2025 R³Mem paper: https://aclanthology.org/2025.findings-acl.235/
- Meta CWM research: https://ai.meta.com/research/publications/cwm-an-open-weights-llm-for-research-on-code-generation-with-world-models
- LTNtorch code: https://github.com/logictensornetworks/LTNtorch
- NVIDIA RTX 4090 specifications: https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/
- E2B Python SDK: https://github.com/e2b-dev/E2B/tree/main/packages/python-sdk

## Engineering corrections to the supplied proposal

1. Local repository source is **AutoGen core 0.7.5**, a release in the 0.4+
   API family; it is not the old 0.2 synchronous stack.
2. Titans learns a *neural* memory at inference time; filtering messages
   by character length or frequency is not a gradient of a loss function.
   The current module intentionally names this a `novelty_proxy`.
3. R³Mem research uses learned reversible context compression and virtual
   memory tokens; storing `QdrantMemory` vectors is not identical. The current
   module guarantees **lossless JSON byte round-trips** with zlib only.
4. Meta's CWM is a trained 32B-parameter code model, not a blanket guarantee
   that generated arbitrary Python simulates an operating system accurately.
   The current module uses a restrictive deterministic *declarative* world.
5. LTNtorch provides differentiable fuzzy-logic objectives; high numerical
   satisfaction is not the same as an SMT proof or OS authorization invariant.
   Existing `SymbolicGate` enforces finite exact rules without claiming LTN.
6. The document's `if 'os.remove' in code` is an unsound security gate and
   must not control authorization. The sample includes `[span]` artifacts,
   so is not directly runnable Python; its E2B calls need SDK-specific validation.
7. NVIDIA GeForce RTX 4090 does **not** support NVLink, even though RTX 3090
   has an NVLink generation. Memory capacity across GPUs does not automatically
   combine into one contiguous 48 GB address space.
8. Remote sandboxing and SAST reduce risk but are not a proof of safety.
   Risky administrative operations must be executed through scoped, audited,
   explicit operator approvals and OS-enforced boundaries.
9. Autonomy and machine self-awareness are research/product assertions,
   not testable features provided by these imports. Unrestricted privilege
   escalation and auto-mutation of production code are excluded.

## Increments not yet implemented

- Learned Titans MAL and gradient-based surprise requiring an actual trained
  compatible neural architecture, checkpoint and reproducible evaluation.
- R³Mem virtual-token training, inverse retrieval and ground-truth recovery
  benchmarks; no equivalence to Qdrant or zlib should be claimed.
- Real Meta CWM inference, trajectory generation, fault injection and an
  independent sandbox with bounded network/filesystem resources.
- LTN/SMT formal+probabilistic hybrid; measure false accept/reject and prove
  only hard invariants in a verified solver or trusted state machine.
- Postgres-backed durable HITL with real authenticated identity, revocation,
  audit signatures, retention policy and resume of distributed workflows.
- Self-modifying source under RSI; require external review, CI, reproducible
  benchmarks and rollback before any code or model changes.
- Multi-host HA, external sink fencing, TLS and real cluster failover.
