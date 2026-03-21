"""Quick historical replay backtest for the Roostoo strategy stack."""

from __future__ import annotations

import argparse
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Iterable

import requests

from src.strategy import DEFAULT_STRATEGY_WEIGHTS, aggregate_signals
from src.strategy.mean_reversion import MeanReversionStrategy
from src.strategy.momentum import MomentumStrategy


BINANCE_MAP = {
    "BTC/USD": "BTCUSDT",
    "ETH/USD": "ETHUSDT",
    "BNB/USD": "BNBUSDT",
    "SOL/USD": "SOLUSDT",
}

DEFAULT_PAIR_WEIGHTS = {
    "BNB/USD": 1.5,
    "BTC/USD": 1.0,
    "ETH/USD": 0.8,
    "SOL/USD": 0.5,
}


@dataclass
class Position:
    qty: float
    entry_price: float
    cost_basis: float
    stop_loss: float
    take_profit: float


@dataclass
class BacktestConfig:
    limit: int = 1000
    initial_cash: float = 50_000.0
    max_position_pct: float = 0.04
    max_open_positions: int = 2
    cooldown_bars: int = 2
    base_threshold: float = 0.25
    relaxed_threshold: float | None = None
    buy_fee: float = 0.0005
    sell_fee: float = 0.0010
    stop_loss_pct: float = 0.008
    take_profit_pct: float = 0.006
    momentum_weight: float = DEFAULT_STRATEGY_WEIGHTS["momentum"]
    mean_reversion_weight: float = DEFAULT_STRATEGY_WEIGHTS["mean_reversion"]
    fast_ema: int = 12
    slow_ema: int = 26
    rsi_period: int = 14
    rsi_overbought: float = 65.0
    rsi_oversold: float = 35.0
    bb_period: int = 20
    bb_std: float = 2.0
    zscore_entry: float = 1.5
    zscore_exit: float = 0.3
    pair_weights: dict[str, float] | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay the strategy on recent Binance minute candles.")
    parser.add_argument("--limit", type=int, default=1000, help="Number of 1m candles to fetch per pair.")
    parser.add_argument("--base-threshold", type=float, default=0.25, help="Aggregate signal threshold.")
    parser.add_argument("--zscore-entry", type=float, default=1.5, help="Mean reversion entry z-score.")
    parser.add_argument("--bnb-weight", type=float, default=1.5, help="Pair weight for BNB/USD.")
    parser.add_argument("--sol-weight", type=float, default=0.5, help="Pair weight for SOL/USD.")
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Run a small parameter sweep over threshold, z-score, and BNB weight.",
    )
    return parser.parse_args()


def fetch_close_series(limit: int) -> dict[str, list[float]]:
    series: dict[str, list[float]] = {}
    for pair, symbol in BINANCE_MAP.items():
        response = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": "1m", "limit": limit},
            timeout=20,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Failed to fetch {symbol}: HTTP {response.status_code} {response.text[:200]}")
        candles = response.json()
        series[pair] = [float(candle[4]) for candle in candles]
    return series


def build_config(args: argparse.Namespace) -> BacktestConfig:
    pair_weights = dict(DEFAULT_PAIR_WEIGHTS)
    pair_weights["BNB/USD"] = args.bnb_weight
    pair_weights["SOL/USD"] = args.sol_weight
    return BacktestConfig(
        limit=args.limit,
        base_threshold=args.base_threshold,
        zscore_entry=args.zscore_entry,
        pair_weights=pair_weights,
    )


def compute_sharpe_like(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    sigma = pstdev(returns)
    if sigma == 0:
        return 0.0
    return mean(returns) / sigma * math.sqrt(1440)


def compute_sortino_like(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    downside = [value for value in returns if value < 0]
    if not downside:
        return 0.0
    downside_sigma = math.sqrt(sum(value * value for value in downside) / len(downside))
    if downside_sigma == 0:
        return 0.0
    return mean(returns) / downside_sigma * math.sqrt(1440)


def max_drawdown(equity_curve: Iterable[float]) -> float:
    peak = 0.0
    max_dd = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak)
    return max_dd


def run_backtest(config: BacktestConfig, series: dict[str, list[float]]) -> dict[str, object]:
    pair_weights = config.pair_weights or DEFAULT_PAIR_WEIGHTS
    strategy_weights = {
        "momentum": config.momentum_weight,
        "mean_reversion": config.mean_reversion_weight,
    }
    momentum = MomentumStrategy(
        fast_ema=config.fast_ema,
        slow_ema=config.slow_ema,
        rsi_period=config.rsi_period,
        rsi_overbought=config.rsi_overbought,
        rsi_oversold=config.rsi_oversold,
    )
    mean_reversion = MeanReversionStrategy(
        bb_period=config.bb_period,
        bb_std=config.bb_std,
        zscore_entry=config.zscore_entry,
        zscore_exit=config.zscore_exit,
    )
    strategies = {
        "momentum": momentum,
        "mean_reversion": mean_reversion,
    }
    bars = min(len(values) for values in series.values())
    required_bars = max(strategy.required_bars() for strategy in strategies.values())

    cash = config.initial_cash
    positions: dict[str, Position] = {}
    last_trade_bar: defaultdict[str, int] = defaultdict(lambda: -10_000)
    trade_log: list[dict[str, object]] = []
    equity_curve: list[float] = []

    for index in range(required_bars, bars):
        prices = {pair: series[pair][index] for pair in series}

        for pair in list(positions.keys()):
            position = positions[pair]
            price = prices[pair]
            exit_reason = None
            if price >= position.take_profit:
                exit_reason = "take_profit"
            elif price <= position.stop_loss:
                exit_reason = "stop_loss"
            else:
                history = series[pair][: index + 1]
                signals = {name: strategy.compute_signal(history) for name, strategy in strategies.items()}
                final_signal = aggregate_signals(signals, strategy_weights, threshold=config.base_threshold)
                if final_signal == -1:
                    exit_reason = "signal_exit"

            if not exit_reason:
                continue

            gross = position.qty * price
            fee = gross * config.sell_fee
            proceeds = gross - fee
            cash += proceeds
            trade_log.append(
                {
                    "pair": pair,
                    "side": "SELL",
                    "reason": exit_reason,
                    "entry_price": position.entry_price,
                    "exit_price": price,
                    "qty": position.qty,
                    "pnl": proceeds - position.cost_basis,
                }
            )
            del positions[pair]
            last_trade_bar[pair] = index

        for pair in ["BNB/USD", "BTC/USD", "ETH/USD", "SOL/USD"]:
            if pair in positions:
                continue
            if len(positions) >= config.max_open_positions:
                continue
            if (index - last_trade_bar[pair]) < config.cooldown_bars:
                continue

            history = series[pair][: index + 1]
            signals = {name: strategy.compute_signal(history) for name, strategy in strategies.items()}
            final_signal = aggregate_signals(signals, strategy_weights, threshold=config.base_threshold)
            if final_signal != 1:
                continue

            price = prices[pair]
            deploy_notional = cash * config.max_position_pct * pair_weights.get(pair, 1.0)
            if deploy_notional <= 0:
                continue

            qty = deploy_notional / price
            gross_cost = qty * price
            fee = gross_cost * config.buy_fee
            total_cost = gross_cost + fee
            if total_cost > cash:
                qty = cash / (price * (1 + config.buy_fee))
                gross_cost = qty * price
                fee = gross_cost * config.buy_fee
                total_cost = gross_cost + fee
            if total_cost <= 0 or qty <= 0:
                continue

            cash -= total_cost
            positions[pair] = Position(
                qty=qty,
                entry_price=price,
                cost_basis=total_cost,
                stop_loss=price * (1 - config.stop_loss_pct),
                take_profit=price * (1 + config.take_profit_pct),
            )
            trade_log.append(
                {
                    "pair": pair,
                    "side": "BUY",
                    "reason": "signal_entry",
                    "entry_price": price,
                    "qty": qty,
                    "pnl": -fee,
                }
            )
            last_trade_bar[pair] = index

        equity_curve.append(cash + sum(position.qty * prices[pair] for pair, position in positions.items()))

    final_prices = {pair: values[-1] for pair, values in series.items()}
    final_equity = cash + sum(position.qty * final_prices[pair] for pair, position in positions.items())
    returns = [
        (current - previous) / previous
        for previous, current in zip(equity_curve, equity_curve[1:])
        if previous > 0
    ]
    closed_trades = [trade for trade in trade_log if trade["side"] == "SELL"]
    wins = [trade for trade in closed_trades if float(trade["pnl"]) > 0]
    pair_pnl: defaultdict[str, float] = defaultdict(float)
    reason_counts = Counter(str(trade["reason"]) for trade in trade_log)
    for trade in closed_trades:
        pair_pnl[str(trade["pair"])] += float(trade["pnl"])

    return {
        "bars_tested": max(0, bars - required_bars),
        "final_equity": final_equity,
        "pnl": final_equity - config.initial_cash,
        "pnl_pct": (final_equity / config.initial_cash - 1) * 100.0,
        "closed_trades": len(closed_trades),
        "win_rate": (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0,
        "max_drawdown_pct": max_drawdown(equity_curve) * 100.0,
        "sharpe_like": compute_sharpe_like(returns),
        "sortino_like": compute_sortino_like(returns),
        "pair_pnl": dict(pair_pnl),
        "trade_log": trade_log,
        "reason_counts": dict(reason_counts),
        "config": config,
    }


def print_report(result: dict[str, object]) -> None:
    config: BacktestConfig = result["config"]  # type: ignore[assignment]
    print("=== QUICK RANGE-MODE BACKTEST ===")
    print(
        "Config: "
        f"threshold={config.base_threshold:.2f} "
        f"zscore_entry={config.zscore_entry:.2f} "
        f"bnb_weight={config.pair_weights.get('BNB/USD', 1.0):.2f} "
        f"sol_weight={config.pair_weights.get('SOL/USD', 1.0):.2f}"
    )
    print(f"Bars tested: {result['bars_tested']}")
    print(f"Final equity: ${result['final_equity']:,.2f}")
    print(f"P&L: ${result['pnl']:,.2f} ({result['pnl_pct']:+.2f}%)")
    print(f"Closed trades: {result['closed_trades']} | Win rate: {result['win_rate']:.1f}%")
    print(f"Max drawdown: {result['max_drawdown_pct']:.2f}%")
    print(f"Sharpe-like: {result['sharpe_like']:.3f}")
    print(f"Sortino-like: {result['sortino_like']:.3f}")
    print("Reason counts:")
    for reason, count in sorted(result["reason_counts"].items()):  # type: ignore[union-attr]
        print(f"  {reason:<12} {count}")
    print("By pair pnl:")
    for pair in ["BNB/USD", "BTC/USD", "ETH/USD", "SOL/USD"]:
        print(f"  {pair}: ${result['pair_pnl'].get(pair, 0.0):,.2f}")  # type: ignore[index]
    print("Last 10 trades:")
    for trade in result["trade_log"][-10:]:  # type: ignore[index]
        print(f"  {trade}")


def run_sweep(series: dict[str, list[float]]) -> None:
    thresholds = [0.20, 0.25, 0.30]
    zscores = [1.5, 1.7, 2.0]
    bnb_weights = [1.0, 1.2, 1.5]
    results: list[dict[str, object]] = []
    for threshold in thresholds:
        for zscore in zscores:
            for bnb_weight in bnb_weights:
                config = BacktestConfig(
                    base_threshold=threshold,
                    zscore_entry=zscore,
                    pair_weights={
                        "BNB/USD": bnb_weight,
                        "BTC/USD": 1.0,
                        "ETH/USD": 0.8,
                        "SOL/USD": 0.4,
                    },
                )
                results.append(run_backtest(config, series))

    ranked = sorted(
        results,
        key=lambda item: (float(item["sharpe_like"]), float(item["pnl_pct"])),
        reverse=True,
    )
    print("=== PARAMETER SWEEP (TOP 10) ===")
    for result in ranked[:10]:
        config: BacktestConfig = result["config"]  # type: ignore[assignment]
        print(
            f"threshold={config.base_threshold:.2f} "
            f"zscore={config.zscore_entry:.2f} "
            f"bnb_weight={config.pair_weights.get('BNB/USD', 1.0):.2f} | "
            f"pnl_pct={result['pnl_pct']:+.2f}% "
            f"sharpe={result['sharpe_like']:.3f} "
            f"sortino={result['sortino_like']:.3f} "
            f"trades={result['closed_trades']}"
        )


def main() -> None:
    args = parse_args()
    series = fetch_close_series(args.limit)
    if args.sweep:
        run_sweep(series)
        return

    config = build_config(args)
    result = run_backtest(config, series)
    print_report(result)


if __name__ == "__main__":
    main()
