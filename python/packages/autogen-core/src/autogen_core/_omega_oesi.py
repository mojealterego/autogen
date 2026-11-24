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
