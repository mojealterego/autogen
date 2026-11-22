"""Bounded, single-event-loop adaptive model routing for trusted providers.

Prices and quality priors are configured, not claimed as benchmarks. Observed
latency comes from this process's monotonic clock. This router is not a
cross-process accounting authority and does not provide exactly-once billing.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


class ModelAdmissionDenied(RuntimeError):
    """Unknown tenant, missing capability, quota exceeded or invalid request."""


class NoModelAvailable(ModelAdmissionDenied):
    """No eligible healthy model remains for this request."""


class ModelOutcomeUnknown(RuntimeError):
    """The provider might have billed or produced side effects."""


class RetryableModelError(RuntimeError):
    """Provider guarantees it failed BEFORE any billable operation began."""


def _int(value: Any, minimum: int = 1, maximum: int = 2**53) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _name(name: Any) -> bool:
    return isinstance(name, str) and 0 < len(name) <= 128 and all(ord(ch) >= 32 for ch in name)


def _json(value: Any, max_bytes: int) -> str:
    try:
        payload = json.dumps(value, allow_nan=False, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    except (TypeError, OverflowError, ValueError, RecursionError) as exc:
        raise ValueError('JSON serialization failed') from exc
    if len(payload) > max_bytes:
        raise ValueError('JSON payload exceeds limit')
    return payload


@dataclass(frozen=True)
class ModelProfile:
    name: str
    capabilities: frozenset[str]
    context_window: int
    quality: int  # configured score 0..1000, not a verified benchmark
    baseline_latency_ms: int  # configured initial estimate
    input_micro_usd_per_million: int
    output_micro_usd_per_million: int

    def __post_init__(self) -> None:
        if (not _name(self.name) or not isinstance(self.capabilities, frozenset)
                or not self.capabilities or not all(_name(c) for c in self.capabilities)
                or not _int(self.context_window) or not _int(self.quality, 0, 1000)
                or not _int(self.baseline_latency_ms)
                or not _int(self.input_micro_usd_per_million, 0)
                or not _int(self.output_micro_usd_per_million, 0)):
            raise ValueError('invalid model profile')


@dataclass(frozen=True)
class TenantRoutePolicy:
    allowed_models: frozenset[str]
    budget_micro_usd: int
    max_inflight: int = 4

    def __post_init__(self) -> None:
        if (not isinstance(self.allowed_models, frozenset) or not self.allowed_models
                or not all(_name(m) for m in self.allowed_models)
                or not _int(self.budget_micro_usd, 0) or not _int(self.max_inflight)):
            raise ValueError('invalid tenant routing policy')


@dataclass(frozen=True)
class RouteRequest:
    tenant: str
    capability: str
    input_tokens: int
    max_output_tokens: int
    payload: Mapping[str, Any]
    allow_safe_fallback: bool = False

    def __post_init__(self) -> None:
        if (not _name(self.tenant) or not _name(self.capability)
                or not _int(self.input_tokens) or not _int(self.max_output_tokens)
                or not isinstance(self.payload, Mapping) or
                not all(isinstance(k, str) for k in self.payload)
                or type(self.allow_safe_fallback) is not bool):
            raise ValueError('invalid routing request')
        object.__setattr__(self, "payload", json.loads(_json(dict(self.payload), 65536)))


@dataclass(frozen=True)
class ModelResponse:
    content: Any
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class RouteResult:
    model: str
    response: ModelResponse
    charged_micro_usd: int
    attempted_models: tuple[str, ...]


@dataclass(frozen=True)
class TenantUsage:
    spent_micro_usd: int
    reserved_micro_usd: int
    inflight: int


@dataclass(frozen=True)
class ModelHealth:
    latency_ms: float | None
    failures: int
    successes: int
    circuit_open: bool


@dataclass(frozen=True)
class RouterSnapshot:
    by_tenant: Mapping[str, TenantUsage]
    by_model: Mapping[str, ModelHealth]
    global_inflight: int


@dataclass
class _Health:
    latency_ms: float | None = None
    failures: int = 0
    successes: int = 0
    open_until: float = 0.0


Provider = Callable[[RouteRequest], Awaitable[ModelResponse]]


class AdaptiveModelRouter:
    """Deterministic policy routing with local adaptive EWMA and conservative billing.

    Every operation reserves its maximum possible declared cost before dispatch.
    Successful calls charge actual validated usage; ambiguous outcomes and
    cancellation consume the full reservation to fail closed. A fallback is
    allowed only for the explicitly defined pre-dispatch, no-bill exception.
    """

    def __init__(self, models: Mapping[str, tuple[ModelProfile, Provider]],
                 tenants: Mapping[str, TenantRoutePolicy], *,
                 clock: Callable[[], float] | None = None,
                 circuit_threshold: int = 3, circuit_cooldown: float = 30.0,
                 max_global_inflight: int = 64, max_response_bytes: int = 65536,
                 request_timeout: float = 60.0) -> None:
        if (not models or not tenants or not _int(circuit_threshold)
                or not _int(max_global_inflight) or not _int(max_response_bytes)
                or any(type(v) not in (int,float) or not math.isfinite(v) or v <= 0
                       for v in (circuit_cooldown, request_timeout))):
            raise ValueError('invalid router limits')
        self._models = dict(models)
        self._tenants = dict(tenants)
        for name, pair in self._models.items():
            if (not _name(name) or not isinstance(pair, tuple) or len(pair) != 2
                    or not isinstance(pair[0], ModelProfile) or name != pair[0].name
                    or not callable(pair[1])):
                raise ValueError('invalid provider registry')
        if any(not _name(tenant) or not isinstance(p, TenantRoutePolicy)
               or not p.allowed_models <= self._models.keys() for tenant,p in self._tenants.items()):
            raise ValueError('invalid tenant configuration')
        self._clock = clock or time.monotonic
        self._threshold = circuit_threshold
        self._cooldown = float(circuit_cooldown)
        self._max_global = max_global_inflight
        self._max_output = max_response_bytes
        self._timeout = float(request_timeout)
        self._health = {name:_Health() for name in models}
        self._spent = {name:0 for name in tenants}
        self._reserved = {name:0 for name in tenants}
        self._active = {name:0 for name in tenants}
        self._total_active = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    def _bind(self) -> None:
        loop=asyncio.get_running_loop()
        if self._loop is None:
            self._loop=loop
        elif self._loop is not loop:
            raise RuntimeError('router is bound to another event loop')

    def _time(self) -> float:
        now=self._clock()
        if type(now) not in (int,float) or not math.isfinite(now):
            raise RuntimeError('invalid monotonic routing clock')
        return float(now)

    @staticmethod
    def _quote(p: ModelProfile, inp: int, out: int) -> int:
        return ((inp*p.input_micro_usd_per_million+out*p.output_micro_usd_per_million+999999)//1000000)

    def _choose(self, req: RouteRequest, excluded: set[str]) -> tuple[str,int]:
        tenant_policy=self._tenants[req.tenant]
        now=self._time()
        choices=[]
        for name in sorted(tenant_policy.allowed_models - excluded):
            p=self._models[name][0];h=self._health[name]
            if (req.capability not in p.capabilities or req.input_tokens+req.max_output_tokens > p.context_window
                    or h.open_until > now):
                continue
            quote=self._quote(p,req.input_tokens,req.max_output_tokens)
            if self._spent[req.tenant]+self._reserved[req.tenant]+quote > tenant_policy.budget_micro_usd:
                continue
            latency=h.latency_ms if h.latency_ms is not None else p.baseline_latency_ms
            score=p.quality*1000-latency*5-quote
            choices.append((-score,name,quote))
        if not choices:
            raise NoModelAvailable('no model matches capability, health and tenant budget')
        choices.sort()
        return choices[0][1],choices[0][2]

    def snapshot(self) -> RouterSnapshot:
        now=self._time()
        tenants={name:TenantUsage(self._spent[name],self._reserved[name],self._active[name]) for name in self._tenants}
        health={name:ModelHealth(v.latency_ms,v.failures,v.successes,v.open_until>now)
                for name,v in self._health.items()}
        return RouterSnapshot(MappingProxyType(tenants),MappingProxyType(health),self._total_active)

    async def route(self, request: RouteRequest) -> RouteResult:
        self._bind()
        if not isinstance(request,RouteRequest) or request.tenant not in self._tenants:
            raise ModelAdmissionDenied('tenant not admitted')
        tenant=request.tenant
        policy=self._tenants[tenant]
        if self._active[tenant] >= policy.max_inflight or self._total_active >= self._max_global:
            raise ModelAdmissionDenied('parallel admission budget exhausted')
        visited:set[str]=set()
        attempts:list[str]=[]
        while True:
            name,quote=self._choose(request,visited)
            visited.add(name)
            attempts.append(name)
            p,provider=self._models[name]
            self._reserved[tenant]+=quote
            self._active[tenant]+=1
            self._total_active+=1
            start=self._time()
            safe=False
            charge=quote   # conservative default for unknown billed effects
            try:
                response=await asyncio.wait_for(provider(request),self._timeout)
                if (not isinstance(response,ModelResponse) or not _int(response.prompt_tokens,0)
                        or not _int(response.completion_tokens,0)
                        or response.prompt_tokens > request.input_tokens
                        or response.completion_tokens > request.max_output_tokens):
                    raise ValueError('provider usage outside request bounds')
                _json(response.content,self._max_output)
                charge=self._quote(p,response.prompt_tokens,response.completion_tokens)
                elapsed_ms=max(0,(self._time()-start)*1000)
                health=self._health[name]
                health.latency_ms=elapsed_ms if health.latency_ms is None else (health.latency_ms+elapsed_ms)/2
                health.successes+=1
                health.failures=0
                health.open_until=0
                return RouteResult(name,response,charge,tuple(attempts))
            except RetryableModelError:
                # Explicit contract: this error is only legal before any
                # billable work is started. The reservation can be released.
                charge=0
                safe=True
                h=self._health[name]
                h.failures+=1
                if h.failures>=self._threshold:
                    h.open_until=self._time()+self._cooldown
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                h=self._health[name];h.failures+=1
                if h.failures>=self._threshold:
                    h.open_until=self._time()+self._cooldown
                raise ModelOutcomeUnknown('provider outcome unknown; budget charged conservatively') from exc
            finally:
                # No awaits: cancellation cannot interrupt accounting release.
                self._reserved[tenant]-=quote
                self._spent[tenant]+=charge
                self._active[tenant]-=1
                self._total_active-=1
            if not safe or not request.allow_safe_fallback:
                raise NoModelAvailable('safe fallback disabled or provider unavailable')
            # A safe, pre-dispatch failure may select the next eligible model.
            # If none remains, a bounded NoModelAvailable is raised.
