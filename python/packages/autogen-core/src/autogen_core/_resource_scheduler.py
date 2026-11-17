"""Single-event-loop resource admission across independent workflows."""
from __future__ import annotations

import asyncio
import math
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, TypeVar

Vector = tuple[int, int, int]
T = TypeVar('T')
_ZERO: Vector = (0, 0, 0)


class AdmissionDenied(RuntimeError):
    """Request is forbidden, impossible, or scheduler is closed."""


class QueueFull(AdmissionDenied):
    """No waiting slot is available."""


@dataclass(frozen=True)
class Resources:
    """Declared simultaneous worker slots, memory bytes and CPU millicores."""
    slots: int
    memory_bytes: int
    cpu_millis: int

    def __post_init__(self) -> None:
        if any(type(v) is not int or v <= 0 for v in self.vector):
            raise ValueError('resources must be positive integers')

    @property
    def vector(self) -> Vector:
        return self.slots, self.memory_bytes, self.cpu_millis


def _add(a: Vector, b: Vector) -> Vector:
    return a[0] + b[0], a[1] + b[1], a[2] + b[2]


def _sub(a: Vector, b: Vector) -> Vector:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _fits(a: Vector, b: Vector) -> bool:
    return all(x <= y for x, y in zip(a, b))


@dataclass(frozen=True)
class SchedulerSnapshot:
    used: Vector
    by_tenant: Mapping[str, Vector]
    active: int
    queued: int
    granted: int
    closed: bool


@dataclass(eq=False)
class _Request:
    tenant: str
    demand: Vector
    future: asyncio.Future[None]
    granted: bool = False


class ResourceScheduler:
    """Explicit reservations with bounded strict FIFO waiting.

    Use a single instance in one event loop for all participating workflows.
    Quotas are declarations, not measured usage or OS isolation. Strict FIFO
    prevents overtaking at the cost of head-of-line blocking. No external I/O
    occurs during admission, accounting or release.
    """

    def __init__(self, capacity: Resources, tenant_limits: Mapping[str, Resources], *, max_queue: int = 1024,
                 max_queued_per_tenant: int | None = None) -> None:
        if not isinstance(capacity, Resources) or type(max_queue) is not int or max_queue < 0:
            raise ValueError('invalid scheduler capacity or queue limit')
        if max_queued_per_tenant is not None and (type(max_queued_per_tenant) is not int or max_queued_per_tenant < 0):
            raise ValueError('invalid tenant queue limit')
        if not tenant_limits or any(not isinstance(k, str) or not k or not isinstance(v, Resources)
                                    or not _fits(v.vector, capacity.vector) for k, v in tenant_limits.items()):
            raise ValueError('invalid tenant quotas')
        self._capacity = capacity.vector
        self._limits = {k: v.vector for k, v in tenant_limits.items()}
        self._max_queue = max_queue
        self._tenant_queue_limit = max_queue if max_queued_per_tenant is None else max_queued_per_tenant
        self._used = _ZERO
        self._by_tenant = dict.fromkeys(self._limits, _ZERO)
        self._queue: deque[_Request] = deque()
        self._active = 0
        self._granted = 0
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def _bind(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError('scheduler belongs to another event loop')
        return loop

    def _available(self, request: _Request) -> bool:
        return (_fits(_add(self._used, request.demand), self._capacity)
                and _fits(_add(self._by_tenant[request.tenant], request.demand), self._limits[request.tenant]))

    def _grant(self, request: _Request) -> None:
        request.granted = True
        self._used = _add(self._used, request.demand)
        self._by_tenant[request.tenant] = _add(self._by_tenant[request.tenant], request.demand)
        self._active += 1
        self._granted += 1
        request.future.set_result(None)

    def _dispatch(self) -> None:
        while not self._closed and self._queue:
            request = self._queue[0]
            if not self._available(request):
                break
            self._queue.popleft()
            self._grant(request)

    def _release(self, request: _Request) -> None:
        if request.granted:
            request.granted = False
            self._used = _sub(self._used, request.demand)
            self._by_tenant[request.tenant] = _sub(self._by_tenant[request.tenant], request.demand)
            self._active -= 1
        else:
            try:
                self._queue.remove(request)
            except ValueError:
                pass
        if not request.future.done():
            request.future.cancel()
        elif not request.future.cancelled():
            request.future.exception()  # consume a concurrent close notification
        self._dispatch()

    @asynccontextmanager
    async def reserve(self, tenant: str, demand: Resources, *, timeout: float | None = None) -> AsyncIterator[None]:
        """Wait for quota, then hold it until the context exits (also on cancellation)."""
        loop = self._bind()
        if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError('invalid admission timeout')
        if not isinstance(tenant, str) or tenant not in self._limits or self._closed:
            raise AdmissionDenied('unknown tenant or closed scheduler')
        if not isinstance(demand, Resources) or not _fits(demand.vector, self._limits[tenant]):
            raise AdmissionDenied('demand exceeds tenant quota')
        request = _Request(tenant, demand.vector, loop.create_future())
        if not self._queue and self._available(request):
            self._grant(request)
        else:
            if (len(self._queue) >= self._max_queue
                    or sum(q.tenant == tenant for q in self._queue) >= self._tenant_queue_limit):
                raise QueueFull('scheduler queue full')
            self._queue.append(request)
        try:
            if not request.granted:
                await asyncio.wait_for(asyncio.shield(request.future), timeout)
            yield
        finally:
            self._release(request)

    def snapshot(self) -> SchedulerSnapshot:
        self._bind()
        return SchedulerSnapshot(self._used, MappingProxyType(dict(self._by_tenant)), self._active,
                                 len(self._queue), self._granted, self._closed)

    def close(self) -> None:
        """Reject new/pending reservations; active contexts continue to drain."""
        self._bind()
        self._closed = True
        while self._queue:
            self._queue.popleft().future.set_exception(AdmissionDenied('scheduler closed'))


def scheduled_handler(scheduler: ResourceScheduler, tenant: str, demand: Resources,
                      handler: Callable[[Mapping[str, Any]], Awaitable[T]], *,
                      admission_timeout: float | None = None) -> Callable[[Mapping[str, Any]], Awaitable[T]]:
    """Wrap a trusted handler with a fixed, application-configured quota identity."""
    async def invoke(inputs: Mapping[str, Any]) -> T:
        async with scheduler.reserve(tenant, demand, timeout=admission_timeout):
            return await handler(inputs)
    return invoke
