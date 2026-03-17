"""Strategy package and signal aggregation helpers."""

from __future__ import annotations

from src.strategy.base import BaseStrategy
from src.strategy.mean_reversion import MeanReversionStrategy
from src.strategy.momentum import MomentumStrategy


def aggregate_signals(
    signals: dict[str, float],
    weights: dict[str, float],
    threshold: float = 0.3,
) -> int:
    """Aggregate strategy signals through a weighted majority vote."""
    weighted_sum = sum(signal * weights.get(name, 0.0) for name, signal in signals.items())
    if weighted_sum > threshold:
        return 1
    if weighted_sum < -threshold:
        return -1
    return 0


__all__ = [
    "BaseStrategy",
    "MomentumStrategy",
    "MeanReversionStrategy",
    "aggregate_signals",
]
