# OM-75 — scoped routing validation

Base commit: `c396c0c1aa230cf8426418ebe6bd7ad04cacf218`. Existing source and both original test files were retrieved from this commit and reconstructed locally with identical Git blob SHA-1. Full checkout failed: GitHub DNS resolution unavailable in the test container. The three test modules ran against the isolated real routing module in a namespace package, not the complete AutoGen package.

## Defect and correction
A Python str subclass with custom equality/hash could alias an existing scope in the in-process dictionary. Require exact built-in str for both scope identifiers before lookup/mutation. This is defensive input validation, not authentication. Normal strings and the route API are unchanged.

## Execution
Linux, Python 3.13.5, pytest 9.0.2, pluggy 1.6.0. Run from isolated workspace containing autogen_core/_omega_oesi.py and tests/.
- Baseline: `PYTHONHASHSEED=0 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -v tests`: 12 passed in 0.03s.
- RED: `PYTHONHASHSEED=0 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_omega_oesi_scope_types.py`: exit 1, 4 failed (DID NOT RAISE ValueError), 0.03s.
- Compilation: `python -m compileall -q autogen_core tests`: exit 0.
- GREEN: `PYTHONHASHSEED=0 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -v tests`: exit 0, 16 passed in 0.02s.

Test identifiers: test_thresholds; test_invalid (6 cases); test_invalid_state; test_scope_isolation; test_capacity_and_cleanup; test_invalid_does_not_allocate; test_concurrent_capacity; test_scope_subclass_cannot_alias_or_clear_existing_state (0-decide, 0-clear, 1-decide, 1-clear). All GREEN tests executed.

| File | Git blob SHA-1 |
|---|---|
| Base routing source | 934d8f07017d8d2a75a5724b9969f929a14fc15c |
| Patched routing source | f748d29b1b10462fc4632b56c93d5a359bac9c87 |
| Original route tests | 2f3e84ba31cf3eceb42530ddffea498d769338c1 |
| Original scoped tests | 3c975b256f34cc55c14602979f6fc9cc1bbb7e62 |
| New regression tests | 1ab4e65773ce80d7d711f576601eee8abf11c343 |

Patched source SHA-256: eaf7765bd799d1541f4dd1839cd63381b2f56e039e4c13a73ca27c2c897213ad.
New test SHA-256: acedf39d26409c8eb7bc0d5d38dc20264718b0747a6a4c163dd106f3520fbc7a.

## Acceptance
Scoped patch compilation and unit tests PASS. GATE-05 full AutoGen integration BLOCKED by unavailable checkout; authenticated principal binding, calibration and production isolation remain open. No full-suite, production deployment, or GATE-09 PASS claim. This record is associated with its containing commit.
