"""Bounded offline OMEGA-X signal-event correlation; no hardware or RF access."""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class SignalEvent:
    tenant_id: str
    source_id: str
    domain: Literal["rf", "navigation", "usb", "network"]
    timestamp_ms: int
    kind: str
    severity: int

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.source_id, self.kind):
            if not isinstance(value, str) or not (1 <= len(value) <= 128) or not value.isascii() or not all(c.isalnum() or c in "._-" for c in value):
                raise ValueError("invalid_identifier")
        if self.domain not in ("rf", "navigation", "usb", "network"):
            raise ValueError("invalid_domain")
        if type(self.timestamp_ms) is not int or not 0 <= self.timestamp_ms <= 2**63 - 1:
            raise ValueError("invalid_timestamp")
        if type(self.severity) is not int or not 0 <= self.severity <= 10:
            raise ValueError("invalid_severity")

@dataclass(frozen=True)
class CorrelationEvidence:
    tenant_id: str
    event_count: int
    domains: tuple[str, ...]
    score: int
    classification: Literal["review", "insufficient_evidence"]
    evidence_sha256: str

def correlate_events(events: tuple[SignalEvent, ...], *, tenant_id: str, window_ms: int = 5000) -> CorrelationEvidence:
    """Correlate observations, not identities or proof of an attack."""
    if not isinstance(tenant_id, str) or not tenant_id or len(tenant_id) > 128:
        raise ValueError("invalid_tenant")
    if type(window_ms) is not int or not 1 <= window_ms <= 60000:
        raise ValueError("invalid_window")
    if not isinstance(events, tuple) or len(events) > 1024:
        raise ValueError("event_budget_exceeded")
    if any(not isinstance(e, SignalEvent) for e in events):
        raise ValueError("invalid_event")
    if any(e.tenant_id != tenant_id for e in events):
        raise PermissionError("cross_tenant_event")
    ordered = sorted(events, key=lambda e: (e.timestamp_ms, e.domain, e.source_id, e.kind, e.severity))
    best: tuple[SignalEvent, ...] = ()
    left = 0
    for right, event in enumerate(ordered):
        while ordered[left].timestamp_ms < event.timestamp_ms - window_ms:
            left += 1
        candidate = tuple(ordered[left:right + 1])
        if (len({e.domain for e in candidate}), sum(e.severity for e in candidate)) > (len({e.domain for e in best}), sum(e.severity for e in best)):
            best = candidate
    domains = tuple(sorted({e.domain for e in best}))
    score = min(100, sum(e.severity for e in best))
    classification: Literal["review", "insufficient_evidence"] = "review" if len(domains) >= 2 and score >= 12 else "insufficient_evidence"
    payload = [(e.tenant_id, e.source_id, e.domain, e.timestamp_ms, e.kind, e.severity) for e in ordered]
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
    return CorrelationEvidence(tenant_id, len(ordered), domains, score, classification, digest)
