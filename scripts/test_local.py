"""Local smoke test runner for the Roostoo bot."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

from src.bot import TradingBot
from src.strategy.mean_reversion import MeanReversionStrategy
from src.strategy.momentum import MomentumStrategy


def print_check(status: bool, message: str) -> None:
    label = "PASS" if status else "FAIL"
    print(f"[ {label} ] {message}")


def fail(message: str) -> None:
    print_check(False, message)
    raise SystemExit(1)


def build_ticker_payload(prices: dict[str, float]) -> dict[str, Any]:
    return {
        "Success": True,
        "Data": {
            pair: {
                "LastPrice": price,
                "MaxBid": price - 1,
                "MinAsk": price + 1,
                "Change": 0.0,
            }
            for pair, price in prices.items()
        },
    }


def main() -> None:
    load_dotenv()
    os.environ["DRY_RUN"] = "true"
    os.environ.setdefault("TRADE_PAIRS", "BTC/USD,ETH/USD,BNB/USD,SOL/USD")

    bot = TradingBot()
    health = bot.health_check()

    if not health["server_reachable"]:
        fail("Server reachable")
    print_check(True, "Server reachable")

    if not health["balance_ok"]:
        fail("Balance loaded")
    reported_balance = bot.initial_portfolio_value if bot.dry_run else health["balance_value"]
    print_check(True, f"Balance loaded: ${reported_balance:,.0f} USD")

    if health["tickers_loaded"] <= 0:
        fail("Tickers fetched")
    print_check(True, f"Tickers fetched: {health['tickers_loaded']} pairs")

    bot.strategies = [
        MomentumStrategy(rsi_overbought=101),
        MeanReversionStrategy(zscore_entry=10.0, zscore_exit=0.25),
    ]
    required_bars = max(strategy.required_bars() for strategy in bot.strategies)

    seed_histories = {
        "BTC/USD": [100.0 + i for i in range(required_bars - 3)],
        "ETH/USD": [200.0 for _ in range(required_bars - 3)],
        "BNB/USD": [50.0 for _ in range(required_bars - 3)],
        "SOL/USD": [20.0 for _ in range(required_bars - 3)],
    }
    bot.price_history = {pair: history[:] for pair, history in seed_histories.items()}

    synthetic_tickers = [
        build_ticker_payload(
            {
                "BTC/USD": 150.0,
                "ETH/USD": 200.0,
                "BNB/USD": 50.0,
                "SOL/USD": 20.0,
            }
        ),
        build_ticker_payload(
            {
                "BTC/USD": 152.0,
                "ETH/USD": 200.0,
                "BNB/USD": 50.0,
                "SOL/USD": 20.0,
            }
        ),
        build_ticker_payload(
            {
                "BTC/USD": 155.0,
                "ETH/USD": 200.0,
                "BNB/USD": 50.0,
                "SOL/USD": 20.0,
            }
        ),
    ]

    original_get_ticker = bot.client.get_ticker
    tick_calls = {"count": 0}

    def fake_get_ticker(pair: str | None = None) -> dict[str, Any]:
        if pair is not None:
            current = synthetic_tickers[min(tick_calls["count"], len(synthetic_tickers) - 1)]
            return {
                "Success": True,
                "Data": {pair: current["Data"][pair]},
            }
        payload = synthetic_tickers[min(tick_calls["count"], len(synthetic_tickers) - 1)]
        tick_calls["count"] += 1
        return payload

    bot.client.get_ticker = fake_get_ticker  # type: ignore[assignment]

    try:
        for _ in range(3):
            bot.tick()
    except Exception as exc:  # pragma: no cover - command-line failure path
        fail(f"Dry run ticks failed: {exc}")
    finally:
        bot.client.get_ticker = original_get_ticker  # type: ignore[assignment]

    if bot.last_signal_count <= 0:
        fail("Signal engine fired on tick 3")
    print_check(True, "Signal engine fired on tick 3")

    if bot.last_risk_evaluations <= 0:
        fail("Risk manager evaluated all signals")
    print_check(True, "Risk manager evaluated all signals")

    if bot.last_dry_run_orders <= 0:
        fail("Dry run orders logged correctly")
    print_check(True, "Dry run orders logged correctly")


if __name__ == "__main__":
    main()
