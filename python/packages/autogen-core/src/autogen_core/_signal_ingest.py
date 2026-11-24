"""Offline OMEGA-X ingestion gateway with strict source authorization and bounded queues."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Mapping

from autogen_core._signal_intelligence import SignalEvent, CorrelationEvidence, correlate_events

_FIELDS = frozenset({'version', 'tenant_id', 'source_id', 'domain', 'timestamp_ms', 'kind', 'severity'})
_DOMAINS = frozenset({'rf', 'navigation', 'usb', 'network'})


@dataclass(frozen=True)
class SourcePolicy:
    tenant_id: str
    allowed_domains: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id:
            raise ValueError('invalid_tenant')
        if not isinstance(self.allowed_domains, frozenset) or not self.allowed_domains or not self.allowed_domains <= _DOMAINS:
            raise ValueError('invalid_domains')


class SignalIngestor:
    """Bounded in-memory gateway; not a persistent event store or hardware adapter."""

    def __init__(self, policies: Mapping[str, SourcePolicy], *, max_queue: int = 1024) -> None:
        if type(max_queue) is not int or not 1 <= max_queue <= 1024:
            raise ValueError('invalid_queue_limit')
        if not isinstance(policies, Mapping) or not policies or len(policies) > 1024:
            raise ValueError('invalid_policies')
        if any(not isinstance(key, str) or not isinstance(value, SourcePolicy) for key, value in policies.items()):
            raise ValueError('invalid_policy_entry')
        self._policies = dict(policies)
        self._queue: dict[str, list[SignalEvent]] = {policy.tenant_id: [] for policy in policies.values()}
        self._limit = max_queue
        self._lock = asyncio.Lock()

    def _validate(self, raw: Mapping[str, object]) -> SignalEvent:
        if not isinstance(raw, Mapping) or frozenset(raw.keys()) != _FIELDS or type(raw['version']) is not int or raw['version'] != 1:
            raise ValueError('invalid_event_schema')
        try:
            event = SignalEvent(raw['tenant_id'], raw['source_id'], raw['domain'], raw['timestamp_ms'], raw['kind'], raw['severity'])
        except (TypeError, ValueError) as exc:
            raise ValueError('invalid_event_values') from exc
        policy = self._policies.get(event.source_id)
        if policy is None or policy.tenant_id != event.tenant_id or event.domain not in policy.allowed_domains:
            raise PermissionError('source_not_authorized')
        return event

    async def submit(self, raw: Mapping[str, object]) -> None:
        await self.submit_batch((raw,))

    async def submit_batch(self, records: tuple[Mapping[str, object], ...]) -> None:
        if not isinstance(records, tuple) or not 1 <= len(records) <= 1024:
            raise ValueError('invalid_batch')
        validated = tuple(self._validate(record) for record in records)
        async with self._lock:
            sizes: dict[str, int] = {}
            for event in validated:
                sizes[event.tenant_id] = sizes.get(event.tenant_id, 0) + 1
            if any(len(self._queue[tenant]) + count > self._limit for tenant, count in sizes.items()):
                raise OverflowError('queue_capacity_exceeded')
            for event in validated:
                self._queue[event.tenant_id].append(event)

    async def correlate(self, tenant_id: str, *, window_ms: int = 5000) -> CorrelationEvidence:
        if tenant_id not in self._queue:
            raise PermissionError('unknown_tenant')
        async with self._lock:
            snapshot = tuple(self._queue[tenant_id])
        return correlate_events(snapshot, tenant_id=tenant_id, window_ms=window_ms)
