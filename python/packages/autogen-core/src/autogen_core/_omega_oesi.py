"""Deterministic OESI routing policy; caller owns scoped previous state."""
from __future__ import annotations


def route(surprise: float, entropy: float, previous_deep: bool) -> tuple[float, bool]:
    """Combine normalized signals with hysteresis (enter .7, exit .4)."""
    for value in (surprise, entropy):
        if type(value) not in (int, float) or not 0 <= value <= 1:
            raise ValueError("invalid_normalized_signal")
    if type(previous_deep) is not bool:
        raise ValueError("invalid_previous_state")
    score = (surprise + entropy) / 2
    return score, score > .4 if previous_deep else score >= .7


import re
from threading import RLock


class ScopedRouter:
    """Process-local routing state. Caller must authenticate the scope."""

    def __init__(self, capacity: int = 4096) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 4096:
            raise ValueError("invalid_capacity")
        self._capacity = capacity
        self._states: dict[tuple[str, str], bool] = {}
        self._lock = RLock()

    @staticmethod
    def _key(tenant_id: str, case_id: str) -> tuple[str, str]:
        for value in (tenant_id, case_id):
            if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
                raise ValueError("invalid_scope")
        return tenant_id, case_id

    def decide(self, tenant_id: str, case_id: str, surprise: float, entropy: float) -> tuple[float, bool]:
        key = self._key(tenant_id, case_id)
        with self._lock:
            result = route(surprise, entropy, self._states.get(key, False))
            if key not in self._states and len(self._states) >= self._capacity:
                raise OverflowError("routing_capacity_exhausted")
            self._states[key] = result[1]
            return result

    def clear(self, tenant_id: str, case_id: str) -> bool:
        key = self._key(tenant_id, case_id)
        with self._lock:
            return self._states.pop(key, None) is not None
