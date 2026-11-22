# OMEGA 6.0 — Adaptive Model Router (single-process, trusted providers)

The opt-in `AdaptiveModelRouter` selects registered models according to
**configured** capability, context window, quality prior and pricing, and
**measured** recent latency (EWMA). Priors do NOT represent independent
benchmarks. Model and tenant allowlists are static trusted configuration; no
models, URLs, API keys or handler functions are taken from LLM output.

## Contracts and accounting

- `ModelProfile`: fixed model identity, allowed capabilities, context size,
  configured quality score (0..1000), baseline latency estimate and integer
  pricing in **micro-USD per million tokens**.
- `TenantRoutePolicy`: fixed model allowlist, budget in micro-USD, per-tenant
  concurrency ceiling. Router has a global concurrency ceiling. Capacity is
  rejected, not queued, so no hidden blocking or unbounded queue.
- `RouteRequest`: tenant, capability, declared input tokens, max output tokens,
  JSON-isolated prompt/messages payload, explicit `allow_safe_fallback` flag.
  The caller must estimate tokens conservatively: actual usage is validated
  against request limits; a provider that reports more is treated as unknown.
- Before invocation, the maximum declared token cost is **reserved**. The
  router settles success with actual validated reported usage, releasing the
  difference. An ambiguous provider failure, contract violation, timeout or
  cancellation consumes the entire reservation (conservative upper bound).
  Cost cannot be recovered without external billing reconciliation.
- Fallback is permitted only when the trusted provider raises
  `RetryableModelError`, explicitly certifying failure **before any billable
  operation**; all other errors raise `ModelOutcomeUnknown` and never trigger
  blind retry. Consecutive failures trip a cooldown circuit breaker; its
  recovery is controlled by the injected monotonic clock.
- `RouterSnapshot` exposes accounting and observed latency/failure state,
  not prompt contents or secrets. All accounting and admission updates are
  synchronous within one asyncio event loop, with no await between release
  and charge. Concurrent calls from separate loops/hosts are unsupported.

## Concrete provider

`OpenAIChatProvider('http://127.0.0.1:8082/v1/chat/completions', 'trusted-model')`
implements actual OpenAI-compatible synchronous non-streaming HTTP calls
inside an asynchronous wrapper. It supports localhost **numeric loopback IP**
for cleartext local llama.cpp/vLLM servers, or HTTPS with default CA and
hostname validation for remote endpoints. URL path, model and optional bearer
key must come from trusted server configuration. It never follows redirects,
accepts dynamic tool roles, or forwards arbitrary paths. It sets fixed frame
size and timeout limits. A background Python thread that has entered network
I/O may survive caller cancellation, so separate OS/process supervision and
provider billing reconciliation are needed for strict global resource limits.

```python
from autogen_core import (
    AdaptiveModelRouter, ModelProfile, TenantRoutePolicy, OpenAIChatProvider,
    RouteRequest,
)
model='local-llama'
provider=OpenAIChatProvider('http://127.0.0.1:8082/v1/chat/completions',model)
profile=ModelProfile(model,frozenset({'text'}),8192,800,200,0,0)
router=AdaptiveModelRouter({model:(profile,provider)},
    {'tenant-a':TenantRoutePolicy(frozenset({model}),budget_micro_usd=0)})
# A local zero-cost model is permitted; allocate more budget for priced APIs.
# response = await router.route(RouteRequest('tenant-a','text',100,100,{'prompt':'hello'}))
```

## Verification and limitations

- Unit tests use deterministic fake clock/providers to prove score adaptation,
  quotas, circuit cooldown, safe-only fallback, cancellation accounting,
  invalid usage rejection and real `DurableWorkflowExecutor` replay.
- Actual HTTP parsing and API integration are tested against a real loopback
  `ThreadingHTTPServer`; **no real model was invoked** and no benchmark about
  accuracy/latency/quality is claimed. TDD red and green logs are included.
- Per-tenant budgets, circuit state and observations are **in memory**. They
  reset on restart, and do not coordinate across processes or hosts. Use an
  external durable, atomic billing ledger before production multi-tenant use.
- No adaptive training, real-model evaluations, quality oracle, external
  provider streaming, tool calling, per-token tokenizer guarantees or formal
  cost settlement against actual invoice has been implemented.
