import numpy as np

from src.strategy import aggregate_signals
from src.strategy.mean_reversion import MeanReversionStrategy
from src.strategy.momentum import MomentumStrategy


def test_momentum_returns_buy_for_trending_up_series() -> None:
    strategy = MomentumStrategy(rsi_overbought=101)
    prices = np.linspace(100.0, 150.0, strategy.required_bars() + 10).tolist()

    signal = strategy.compute_signal(prices)

    assert signal == 1


def test_mean_reversion_returns_buy_when_price_is_far_below_mean() -> None:
    strategy = MeanReversionStrategy()
    base = [100.0] * (strategy.required_bars() - 1)
    prices = base + [90.0]

    signal = strategy.compute_signal(prices)

    assert signal == 1


def test_momentum_returns_hold_when_trend_strength_is_weak() -> None:
    strategy = MomentumStrategy(rsi_overbought=101)
    prices = [100.0 + ((index % 2) * 0.05) for index in range(strategy.required_bars() + 10)]

    signal = strategy.compute_signal(prices)

    assert signal == 0


def test_aggregate_signals_respects_mean_reversion_dominant_weights() -> None:
    signals = {"momentum": 1, "mean_reversion": -1}
    weights = {"momentum": 0.25, "mean_reversion": 0.75}

    result = aggregate_signals(signals, weights)

    assert result == -1
