"""Base abstractions for trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    @abstractmethod
    def generate_signal(self, market_state: Dict[str, Any]) -> int:
        """Return 1 for BUY, -1 for SELL, and 0 for HOLD."""
