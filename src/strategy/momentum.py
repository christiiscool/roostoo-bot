"""Momentum strategy implementation."""

from __future__ import annotations

import pandas as pd

from src.strategy.base import BaseStrategy


class MomentumStrategy(BaseStrategy):
    """Trend-following EMA plus RSI confirmation strategy."""

    def __init__(
        self,
        fast_ema: int = 12,
        slow_ema: int = 26,
        rsi_period: int = 14,
        rsi_overbought: float = 70,
        rsi_oversold: float = 30,
    ) -> None:
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold

    @property
    def name(self) -> str:
        return "momentum"

    def required_bars(self) -> int:
        return self.slow_ema + self.rsi_period + 5

    def _compute_rsi(self, prices: pd.Series) -> pd.Series:
        delta = prices.diff()
        gains = delta.clip(lower=0.0)
        losses = -delta.clip(upper=0.0)
        avg_gain = gains.ewm(alpha=1 / self.rsi_period, adjust=False, min_periods=self.rsi_period).mean()
        avg_loss = losses.ewm(alpha=1 / self.rsi_period, adjust=False, min_periods=self.rsi_period).mean()
        relative_strength = avg_gain / avg_loss.replace(0, pd.NA)
        rsi = 100 - (100 / (1 + relative_strength))
        return rsi.fillna(100)

    def compute_signal(self, price_history: list[float]) -> int:
        if len(price_history) < self.required_bars():
            return 0

        prices = pd.Series(price_history, dtype="float64")
        ema_fast = prices.ewm(span=self.fast_ema, adjust=False).mean()
        ema_slow = prices.ewm(span=self.slow_ema, adjust=False).mean()
        rsi = self._compute_rsi(prices)

        fast_value = float(ema_fast.iloc[-1])
        slow_value = float(ema_slow.iloc[-1])
        rsi_value = float(rsi.iloc[-1])

        if fast_value > slow_value and rsi_value < self.rsi_overbought:
            return 1
        if fast_value < slow_value and rsi_value > self.rsi_oversold:
            return -1
        return 0
