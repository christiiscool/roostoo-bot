"""Base abstractions for trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseStrategy(ABC):
    """Abstract base class for all signal strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""

    @abstractmethod
    def compute_signal(self, price_history: list[float]) -> float:
        """Return a directional score where positive is BUY and negative is SELL."""

    @abstractmethod
    def required_bars(self) -> int:
        """Return the minimum price history length required by the strategy."""
