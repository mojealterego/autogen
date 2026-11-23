# OMEGA 9.0 — bounded neuro-symbolic policy verification

`SymbolicGate` parses a small, finite subset of Python's AST and interprets
it directly without `eval`, compilation to Python bytecode, reflection,
network calls or dynamic imports. Only named, typed, flat inputs are valid.

- Only boolean `and`, `or`, `not`, relational comparisons, bounded integer
  addition/subtraction, literals and known variables are supported. No calls,
  attribute reads, indexing, generator expressions, loops or exponentiation.
- Parse limits: 512 source characters/rule, 100 AST nodes, depth 16, 64 rules,
  32 variables; integer magnitude at most 10^12, bounded string length.
- Wrong types, missing/extra variables and arithmetic overflow fail closed.
- `SymbolicVerdict` contains a redacted boolean decision vector and SHA-256
  hashes for ruleset/context/evidence (not raw prompt or data). Unkeyed hashes
  are not signatures: pin a trusted head externally if tamper detection matters.
- `symbolically_guarded` wraps a trusted async worker, checking policy before
  any handler invocation. Extractor, rules and handler are trusted host config,
  never generated ad hoc by the model.

TDD_RED.log and TESTS.txt cover AST injection attempts, type confusion,
missing keys, numeric bounds, authorization, proof tamper and a real
HierarchicalSwarm denial-before-side-effect integration.

Limitations: This is a typed runtime contract and evidence layer, **not** a
formal SMT proof, Z3 theorem prover, or complete neural-symbolic reasoning
engine. No adversarial sandbox or cross-process authorization is implied.
