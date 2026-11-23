# OMEGA dependency gate — verified offline packages and partial core regression

Evidence date: 2026-11-23; baseline `Magnetic-Zero/main` SHA
`df2ece7c641f7989e757c868b568517f4936aa8e` (OMEGA 10.0).
Python 3.13.5 / Linux; tests run against local AutoGen source with an
external test-only metadata shim for `autogen-core 0.7.5`, **not** a wheel build.

## User-provided materials and trust boundaries

- `jsonref-1.1.0-py3-none-any.whl`: authentic pinned wheel, verified SHA-256
  `590dc7773df6c21cbf948b5dac07a72a251db28b0238ceecce0a2abfa8ec30a9`
  and size 9,425 bytes against `python/uv.lock` and supplied PyPI page. Installed
  only under a private test target with `--no-index --no-deps --target`.
- `jsonref-1.1.0.tar.gz.tar`: a gzip source distribution (the outer `.tar`
  filename is misleading); it was inspected, not executed or installed.
- The uploaded `asyncio-atexit` PyPI page and `pytest-mock` documentation are
  **documentation**, not distribution wheels. Do not claim they provide an
  installable package. The core lock pins `asyncio-atexit 1.0.1` and
  `pytest-mock 3.14.0` (not 3.17.0).
- For test diagnosis only, the exact 3,032-byte public source file
  `asyncio_atexit.py` from upstream Git tag `1.0.1` was staged in the
  isolated local test target. Its standalone regression test passed (1/1).
  This is a *source-tag verification*, not a verified installed PyPI wheel;
  source is **not** vendored into the repository.

## Reproducible offline wheel audit

```sh
python docs/omega/dependency-gate/check_wheels.py \
  --lock python/uv.lock --wheelhouse /path/to/verified/wheels
python -m pytest -q docs/omega/dependency-gate/test_check_wheels.py
```

The checker does not install, import or execute third-party code. It validates
three exact `py3-none-any` wheels from the committed lock. Exit 0 means all
three are verified; 2 means missing or invalid artifacts. It uses standard
library `tomllib` (Python >=3.11), SHA-256, strict file checks and bounded
in-memory ZIP inspection, and is fail-closed for missing/unverified files.

The signed-wheel provenance, where available, is independent of the lock hash;
this tool verifies only the digest recorded in the local lock, not software
supply-chain build attestations.

## Current test outcomes

- Baseline OMEGA 10.0 full core (before user-supplied wheel):
  **247 passed, 10 collection errors**, exit 1; recorded separately in
  `../infrastructure/FULL_CORE_ATTEMPT.txt`.
- With only original `jsonref` wheel: **328 passed, 3 collection errors**, exit 1.
  New missing optional import `autogen_ext` surfaced once `jsonref` loaded.
- With `jsonref`, upstream `asyncio_atexit` source file, core source and
  `autogen-ext` source path: **329 passed, 2 collection errors**, exit 1.
  See `CORE_WITH_PARTIAL_DEPS.txt` for the exact traceback.
- Remaining missing runtime/test requirements: `pytest_mock`, `autogen_ext`
  package metadata plus its `ollama`, `tiktoken` optional provider extras.
  Test fixture `OpenAIChatCompletionClient` additionally requires its own
  extras. These have **not** been fabricated or stubbed.
- OMEGA focused suite with authenticated `jsonref` and tagged `asyncio-atexit`: **141/141 PASSED**, exit 0; `FOCUSED_RECHECK.txt`.
- Wheel verifier TDD: `TDD_RED.log` (unavailable import) followed by
  `TESTS.txt` (**5 passed**).
- `LOCK_AUDIT.json` correctly yields `jsonref=verified`, while the two
  unretrieved wheels are `missing`; expected exit code **2**.

## Follow-up gate

For strict lock replay, supply exact wheels:

- `asyncio_atexit-1.0.1-py3-none-any.whl`: SHA-256
  `d93d5f7d5633a534abd521ce2896ed0fbe8de170bb1e65ec871d1c20eac9d376`.
- `pytest_mock-3.14.0-py3-none-any.whl`: SHA-256
  `0b72c38033392a5f4621342fe11e9219ac11ec9d375f8e2a0c164539e0d70f6f`.

Both hashes come from committed `python/uv.lock`. Additionally install the
required `autogen-ext` extras (`ollama`, `openai` and `tiktoken`) into an
isolated environment before running the full upstream suite. Never mark
`OM-06 PASS` before a real `pytest` exit 0 and native distribution verification.
No deployment or native .NET/Studio build was performed in this activity.

## User-supplied distribution update (2026-11-23)

See [`USER_PACKAGES_2026-11-23.md`](USER_PACKAGES_2026-11-23.md) and JSON provenance.
The official `asyncio-atexit` wheel now exactly matches `python/uv.lock`,
`autogen-ext` 0.7.5 is installed in an isolated test target, and `mock`
5.2.0 is **not** `pytest-mock`. With supplied wheels: core **346 passed, 2
collection errors**, and focused **158 passed**. Full regression gate **BLOCKED**.
