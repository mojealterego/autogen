# Magnetic Zero OMEGA 15.0 — genuine upstream LTNtorch + Meta CWM protocol

**Branch:** `main`. **Status:** local research integration; upstream complete production and original CWM model inference are **BLOCKED**.

## What was actually supplied

| Artifact | Original content and license | Result |
|---|---|---|
| `LTNtorch-main.zip` | `ltn/core.py`, `ltn/fuzzy_ops.py`, tests, `setup.py` v1.0.2; MIT | verified source archive; genuine package imported from isolated original source |
| `cwm-main (1).zip` | Meta code base `serve/fgserve.py`, `serve/openai_api.py`, model evaluation and training code; BSD-3 | verified source archive; static reference server contract and live controlled HTTP transport |
| `2510.02387v1.pdf` | 58-page Meta FAIR CodeGen CWM research report; September 2025 | original model tracing grammar and architecture |
| `facebook_cwm*.mht` | three Hugging Face **HTML snapshots**, not model tensors | **no model weights**; gated/noncommercial access remains separate |
| `CodeGen ... access request form.mht` | Meta access form snapshot, not an access grant | model download BLOCKED pending legitimate user-granted access |
| `acl-anthology-master.zip` | ACL Anthology website/tooling source; Apache-2.0 | **not** an R³Mem implementation or checkpoint |
| `Titans ... Google Scholar.mht` | bibliographic HTML snapshot | **not** Titans model code/weights |

The official CWM repository distinguishes BSD-3 source code from weights published under a separate noncommercial research license. The Meta research report describes **32B parameters, 64 layers, 8k local / 131072 global context** and inference requiring significant GPU resources. The user-provided CWM source README says its default demos require **160 GB combined VRAM**; do not claim that the uploaded ZIP itself can run the 32B model.

## Production-code changes

- `autogen_core._research_upstream.LTNtorchBridge` imports the **original** `ltn` package only when instantiated. It uses `ltn.Variable`, `ltn.Predicate`, `ltn.Connective(NotStandard)`, `ltn.Quantifier(AggregPMeanError)` and `ltn.fuzzy_ops.SatAgg`. For trusted labeled positive and negative sets, it learns `forall positive: Authorized` and `forall negative: NOT Authorized` using genuine PyTorch backpropagation. This is learned fuzzy first-order satisfaction **not** a mathematical proof. `SymbolicGate` remains an independent mandatory authorization veto. Training data must be trusted, limited-size, finite CPU tensors with both classes present.
- `autogen_core._research_cwm.CWMClient` now has a fixed, allowlisted request route: existing generic `/v1/chat/completions`, or Meta's reference Fastgen `/chat/completions`. For Meta's route it uses `model='cwm'` and `reasoning: {enabled: false}`; for generic routes it retains `response_format=json_object`. It never accepts arbitrary tool-execution outputs; only bounded declared state changes in `WorldModel`.
- `autogen_core._cwm_trace.CWMTraceParser` parses the report's `<|frame_sep|>`, `<|call_sep|>`, `<|line_sep|>`, `<|return_sep|>`, `<|exception_sep|>`, `<|action_sep|>`, `<|arg_sep|>` and optional `<|trace_context_start|>` symbols into bounded typed structures. It **never executes** the generated source code. `compare_cwm_trace` calculates exact frame agreement with a separately collected **trusted** trace. Predicted traces are not ground truth.
- `audit_sources.py` checks immutable SHA-256 pins, required files, path traversal, symlinks, archive inflation, binary weights and ZIP CRC before any operator decides to install external sources. No third-party source package or checkpoint is copied into Magnetic-Zero.

## Reproduce from supplied local source archives

Unpack `LTNtorch-main.zip` and `cwm-main (1).zip` into isolated, trusted folders, after running `audit_sources.py` on the exact original archives. To run tests **without installing third-party code into the global environment**, use the original extracted LTNtorch directory on `PYTHONPATH`. The source archives are not part of the committed repository:

```sh
python docs/omega/upstream-integration/audit_sources.py \
  --ltn /path/LTNtorch-main.zip --cwm '/path/cwm-main (1).zip'
export PYTHONPATH="/path/LTNtorch-main:$PWD/python/packages/autogen-core/src:$PYTHONPATH"
export MAGNETIC_CWM_REFERENCE_SOURCE='/path/cwm-main'
python -m pytest -q python/packages/autogen-core/tests/test_omega_upstream15.py \
  python/packages/autogen-core/tests/test_omega_source_audit15.py
```

The environment additionally needs compatible installed `torch`, `numpy`, `pytest`, `pytest-asyncio`, normal AutoGen core runtime dependencies and core distribution metadata. The CWM reference schema was **statically** checked using AST because a compatible `openai` Python SDK was unavailable in the local environment. The HTTP test used an actual loopback server with Meta's documented route and payload; it was **not** a 32B model or full Fastgen process. In production use a trusted, externally authenticated HTTPS endpoint with egress restrictions against DNS rebinding, resource quotas, and origin-pinned credentials.

## Security and release limitations

Trained LTN probabilities are untrusted recommendations; only independently audited hard rules govern actions. The optional LTN source requires trusted installation and supply-chain review. Model outputs are untrusted data; never execute generated code as a result of receiving a CWM trace or declarative plan. The CWM source and reference route do **not** imply that CWM weights were obtained, loaded or licensed for commercial use.

Original Titans MAL and R³Mem reproductions, authenticated full Meta CWM 32B inference, user-approved model access, production-signed RSI builds, 3rd party sandbox hardening, and full upstream AutoGen regression remain separate acceptance gates. `GATE-09`: **BLOCKED**, not production verified.
