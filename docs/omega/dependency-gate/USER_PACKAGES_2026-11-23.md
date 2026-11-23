# Magnetic-Zero dependency replay — user distributions (2026-11-23)

**Repository baseline:** `49fe2c5dbc3307b2b05f7eb7c2103ab3ecd8edb6` (`main`, OMEGA 11.0). Python 3.13.5 / Linux.

## Actual supplied artifacts

Seven distribution artifacts were inspected in total: **six newly supplied** and the previously supplied original `jsonref` wheel. Each wheel passes ZIP CRC plus **wheel RECORD SHA-256 verification**. Each `.tar.gz` source archive passes bounded tar-header inspection; shared source-module bytes match the corresponding wheel. Official PyPI JSON checksums were confirmed for `autogen-ext 0.7.5` and `mock 5.2.0`, and committed `python/uv.lock` checksums for `asyncio-atexit 1.0.1` and `jsonref 1.1.0`.

**Important distinctions:**

- `mock 5.2.0` is the *unittest.mock backport*, **not** `pytest-mock 3.14.0`. It does not provide the `pytest_mock` import or `mocker` pytest fixture.
- `autogen-ext 0.7.5` is the actual published wheel, with matching SHA-256 from [PyPI JSON](https://pypi.org/pypi/autogen-ext/0.7.5/json), installed locally without its optional dependencies. The `autogen-ext` workspace in `uv.lock` does not include a pinned external wheel hash. This check verifies the published wheel, not a reproducible workspace build.
- `asyncio-atexit 1.0.1` wheel is **exactly** pinned by the lock; the source archive has no independently verified expected source SHA-256 and is not considered a locked wheel.
- Official [mock release](https://pypi.org/pypi/mock/5.2.0/json) checksums match both supplied artifacts. No external code was executed during archive inspection.

## Commands executed

All installation occurred under `/mnt/data/mz_dep12/site`, not in the global Python installation, with `pip --no-index --no-deps --target`. Test-only `autogen_core-0.7.5.dist-info` metadata was derived from the checked-in core project, not presented as a genuine wheel or a deployed package.

```sh
python docs/omega/dependency-gate/check_wheels.py --lock python/uv.lock --wheelhouse /mnt/data/mz_dep12/wheelhouse
python -m pytest -q python/packages/autogen-core/omega_tests python/packages/autogen-core/tests/test_omega_*.py
python -m pytest -q python/packages/autogen-core/tests --continue-on-collection-errors
python -m pytest -q docs/omega/dependency-gate/test_check_wheels.py
```

Evidence files in this directory preserve exact results. `check_wheels.py` correctly returns nonzero: `asyncio-atexit=verified`, `jsonref=verified`, `pytest-mock=missing`.

| Gate | Actual result | Status |
|---|---:|---|
| Focused OMEGA suite | 158 passed | PASS |
| Offline wheel auditor tests | 5 passed | PASS |
| Core regression | 346 passed, 2 collection errors | **BLOCKED** |
| Lock replay | 2 matched, `pytest-mock` missing | **BLOCKED** |

The two core collection errors are: `ModuleNotFoundError: pytest_mock` (base-agent tests) and `ModuleNotFoundError: tiktoken` (model-context tests importing the published `autogen-ext` Ollama client). Its source also imports `ollama`, which is not installed; OpenAI-compatible extras should be verified separately before rerunning. These are genuine missing dependencies, not evidence that the library code passes/fails the currently uncollected tests.

**Required to unblock:** official `pytest_mock-3.14.0-py3-none-any.whl` with lock SHA-256 `0b72c38033392a5f4621342fe11e9219ac11ec9d375f8e2a0c164539e0d70f6f`, plus lock-compatible `tiktoken`/`ollama` and any remaining OpenAI extras. Do not force PASS. No production deployment or model-provider E2E performed.
