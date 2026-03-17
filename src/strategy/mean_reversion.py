"""Mean reversion strategy implementation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategy.base import BaseStrategy


class MeanReversionStrategy(BaseStrategy):
    """Bollinger-band and z-score based mean reversion strategy."""

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        zscore_entry: float = 1.5,
        zscore_exit: float = 0.5,
    ) -> None:
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.zscore_entry = zscore_entry
        self.zscore_exit = zscore_exit

    @property
    def name(self) -> str:
        return "mean_reversion"

    def required_bars(self) -> int:
        return self.bb_period + 10

    def compute_signal(self, price_history: list[float]) -> float:
        if len(price_history) < self.required_bars():
            return 0

        prices = pd.Series(price_history, dtype="float64")
        rolling_mean = prices.rolling(window=self.bb_period).mean()
        rolling_std = prices.rolling(window=self.bb_period).std(ddof=0)
        upper_band = rolling_mean + (self.bb_std * rolling_std)
        lower_band = rolling_mean - (self.bb_std * rolling_std)

        mean_value = rolling_mean.iloc[-1]
        std_value = rolling_std.iloc[-1]
        last_price = prices.iloc[-1]

        if pd.isna(mean_value) or pd.isna(std_value) or std_value == 0:
            return 0

        z_score = float((last_price - mean_value) / std_value)

        if z_score < -self.zscore_entry and last_price <= lower_band.iloc[-1]:
            return 0.8
        if z_score > self.zscore_entry and last_price >= upper_band.iloc[-1]:
            return -1
        if abs(z_score) < self.zscore_exit:
            return 0
        return 0
