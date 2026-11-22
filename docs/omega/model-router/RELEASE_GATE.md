# Stage 6.0 release gate — evidence, not percentage

- Source parent: `29004ccf9b43944d3f3139f611a6469fc4e9743a` (Magnetic-Zero/main, 5.3).
- Runtime: Linux / Python 3.13.5, pytest; development PYTHONPATH includes
  local core source, test-utils source, and external temporary package metadata.
  This is source execution, **not** a wheel or full distribution build.
- RED: `TDD_RED.log`, `PROVIDER_RED.log`, `EDGE_RED.log`.
- GREEN command: `python -m pytest -q python/packages/autogen-core/omega_tests python/packages/autogen-core/tests/test_omega_*.py`
  Result: **102 passed** (`FOCUSED.txt`). No real model execution.
- Full core command: `python -m pytest -q python/packages/autogen-core/tests --continue-on-collection-errors`
  Result: **208 passed, 10 collection errors, exit code 1**; missing upstream
  development dependencies `jsonref`, `asyncio_atexit`, `pytest_mock`
  (`FULL_CORE_ATTEMPT.txt`). This must not be reported as a green full suite.
- Syntax compilation: `COMPILE.txt`.
- API integration: real loopback HTTP transport, deterministic test responder;
  output usage parsing verified, no actual LLM or externally billed call.
- Single-process router policy: IMPLEMENTED and LOCALLY TESTED.
- Persistent billing and cross-process quotas: OPEN.
- Physical multi-host HA / replicated authority: BLOCKED.
- Release to production: NOT PERFORMED. Repository commit does not imply a
  deployment, production verification, or full roadmapped acceptance.
