"""Bounded offline OMEGA-X signal-event correlation; no hardware or RF access."""
from __future__ import annotations
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal

_IDENTIFIER = re.compile(r"[A-Za-z0-9._-]{1,128}\\Z")
_DOMAINS = frozenset(("rf", "navigation", "usb", "network"))

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
            if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
                raise ValueError("invalid_identifier")
        if not isinstance(self.domain, str) or self.domain not in _DOMAINS:
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
    if not isinstance(tenant_id, str) or _IDENTIFIER.fullmatch(tenant_id) is None:
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
    left = 0
    total = 0
    counts: Counter[str] = Counter()
    best_domains: tuple[str, ...] = ()
    best_score = 0
    best_raw = -1
    for event in ordered:
        counts[event.domain] += 1
        total += event.severity
        while ordered[left].timestamp_ms < event.timestamp_ms - window_ms:
            old = ordered[left]
            counts[old.domain] -= 1
            if counts[old.domain] == 0:
                del counts[old.domain]
            total -= old.severity
            left += 1
        current_domains = tuple(sorted(counts))
        if (len(current_domains), total) > (len(best_domains), best_raw):
            best_domains, best_raw, best_score = current_domains, total, min(100, total)
    domains = best_domains
    score = best_score
    classification: Literal["review", "insufficient_evidence"] = "review" if len(domains) >= 2 and score >= 12 else "insufficient_evidence"
    payload = [(e.tenant_id, e.source_id, e.domain, e.timestamp_ms, e.kind, e.severity) for e in ordered]
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
    return CorrelationEvidence(tenant_id, len(ordered), domains, score, classification, digest)
