"""Main trading bot orchestration."""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import schedule
from dotenv import load_dotenv

from src.api.client import RoostooClient
from src.risk.manager import RiskManager
from src.strategy import MeanReversionStrategy, MomentumStrategy, aggregate_signals


def _configure_logging() -> logging.Logger:
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_file = os.getenv("LOG_FILE", "bot.log")
    logger_name = "roostoo.bot"
    logger = logging.getLogger(logger_name)

    if logger.handlers:
        return logger

    level = getattr(logging, log_level_name, logging.INFO)
    logger.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_file:
        log_path = Path(log_file)
        if not log_path.is_absolute():
            log_path = Path.cwd() / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


load_dotenv()
logger = _configure_logging()


class TradingBot:
    """Coordinates data collection, signal generation, risk checks, and execution."""

    def __init__(self) -> None:
        load_dotenv()
        self.client = RoostooClient()
        self.risk = RiskManager()
        self.strategies = [MomentumStrategy(), MeanReversionStrategy()]
        self.strategy_weights = {"momentum": 0.6, "mean_reversion": 0.4}
        self.price_history: dict[str, list[float]] = {}
        self.history_window = 50
        self.trade_pairs = self._load_trade_pairs()
        self.tick_interval_seconds = int(os.getenv("TICK_INTERVAL_SECONDS", 60))
        self.dry_run = self._is_truthy(os.getenv("DRY_RUN", "true"))
        self.trades_executed = 0
        self.tick_count = 0
        self.last_signal_count = 0
        self.last_risk_evaluations = 0
        self.last_dry_run_orders = 0
        self.latest_wallet: dict[str, dict[str, Any]] = {}
        self.latest_prices: dict[str, float] = {}
        self.initial_portfolio_value = 50000.0 if self.dry_run else 0.0
        self.simulated_wallet: dict[str, dict[str, float]] = {
            "USD": {"Free": self.initial_portfolio_value, "Lock": 0.0}
        }

    def tick(self) -> None:
        """Run one full trading cycle."""
        self.tick_count += 1
        self.last_signal_count = 0
        self.last_risk_evaluations = 0
        self.last_dry_run_orders = 0

        ticker_payload = self.client.get_ticker()
        if ticker_payload is None:
            logger.warning("Skipping tick because ticker fetch failed.")
            return

        tickers = self._extract_tickers(ticker_payload)
        if not tickers:
            logger.warning("Skipping tick due to incomplete ticker data.")
            return

        wallet = self._current_wallet()
        if not wallet:
            logger.warning("Skipping tick due to unavailable wallet data.")
            return

        self.latest_prices = tickers
        required_bars = max(strategy.required_bars() for strategy in self.strategies)

        for pair in self.trade_pairs:
            ticker_entry = self._ticker_entry(pair, ticker_payload, tickers)
            if ticker_entry is None or ticker_entry.get("LastPrice") is None:
                logger.warning("Ticker missing LastPrice for %s.", pair)
                continue

            last_price = self._to_float(ticker_entry.get("LastPrice"))
            if last_price <= 0:
                logger.warning("Invalid LastPrice for %s: %s", pair, ticker_entry.get("LastPrice"))
                continue

            history = self.price_history.setdefault(pair, [])
            history.append(last_price)
            if len(history) > self.history_window:
                self.price_history[pair] = history[-self.history_window :]
                history = self.price_history[pair]

            if len(history) < required_bars:
                continue

            strategy_signals = {
                strategy.name: strategy.compute_signal(history)
                for strategy in self.strategies
            }
            final_signal = aggregate_signals(strategy_signals, self.strategy_weights)
            if final_signal != 0:
                self.last_signal_count += 1

            approved = False
            quantity = 0.0

            if final_signal != 0:
                self.last_risk_evaluations += 1
                approved, quantity = self.risk.approve_trade(
                    pair=pair,
                    signal=final_signal,
                    wallet=wallet,
                    current_prices=tickers,
                )
                if approved:
                    side = "BUY" if final_signal == 1 else "SELL"
                    if self.dry_run:
                        self._apply_simulated_fill(pair=pair, side=side, quantity=quantity, price=last_price)
                        logger.info("[DRY RUN] Would place: %s %.6f %s at market", side, quantity, pair)
                        logger.info(
                            "ORDER mode=DRY_RUN side=%s qty=%.6f pair=%s price=%.6f",
                            side,
                            quantity,
                            pair,
                            last_price,
                        )
                        self.last_dry_run_orders += 1
                    else:
                        order_response = self.client.place_order(
                            pair=pair,
                            side=side,
                            quantity=f"{quantity:.6f}",
                        )
                        if order_response is None:
                            approved = False
                            quantity = 0.0
                        else:
                            logger.info(
                                "ORDER mode=LIVE side=%s qty=%.6f pair=%s price=%.6f",
                                side,
                                quantity,
                                pair,
                                last_price,
                            )
                    if approved:
                        self.risk.update_after_trade(pair)
                        self.trades_executed += 1
                        wallet = self._current_wallet()

            portfolio_summary = self.risk.summary()
            logger.info(
                "tick=%s pair=%s signal=%s approved=%s qty=%.6f summary=%s",
                datetime.now(timezone.utc).isoformat(),
                pair,
                final_signal,
                approved,
                quantity,
                portfolio_summary,
            )

        self._emit_performance_summary()

    def run(self) -> None:
        """Start the bot loop and keep running on the configured schedule."""
        logger.info(
            "Starting trading loop for pairs: %s | mode=%s",
            ", ".join(self.trade_pairs),
            "DRY_RUN" if self.dry_run else "LIVE",
        )
        self.tick()
        schedule.every(self.tick_interval_seconds).seconds.do(self.tick)

        try:
            while True:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutdown requested. Cancelling pending orders.")
            if not self.dry_run:
                self._cancel_pending_orders()
        finally:
            final_value = self.risk.portfolio_value(self._current_wallet(), self.latest_prices)
            logger.info(
                "Bot stopped. final_portfolio_value=%.2f total_trades_executed=%s mode=%s",
                final_value,
                self.trades_executed,
                "DRY_RUN" if self.dry_run else "LIVE",
            )

    def health_check(self) -> dict[str, Any]:
        """Validate API reachability and basic account state before trading."""
        server_payload = self.client.get_server_time()
        server_time = self._extract_server_time(server_payload)
        local_time = int(time.time() * 1000)

        ticker_payload = self.client.get_ticker()
        tickers = self._extract_tickers(ticker_payload or {})
        has_live_credentials = bool(self.client.api_key and self.client.secret_key)

        balance_ok = False
        balance_value = 0.0
        pending_orders = 0

        if self.dry_run and not has_live_credentials:
            balance_ok = True
            balance_value = self.risk.portfolio_value(self.simulated_wallet, tickers)
        else:
            balance_payload = self.client.get_balance()
            pending_payload = self.client.get_pending_count()
            wallet = self._extract_wallet(balance_payload or {})
            balance_ok = balance_payload is not None
            balance_value = self.risk.portfolio_value(wallet, tickers) if wallet and tickers else 0.0
            pending_orders = self._extract_pending_orders(pending_payload)

        return {
            "server_reachable": server_payload is not None,
            "local_time_skew_ms": abs(server_time - local_time) if server_time is not None else None,
            "balance_ok": balance_ok,
            "balance_value": balance_value,
            "tickers_loaded": len(tickers),
            "pending_orders": pending_orders,
        }

    def _emit_performance_summary(self) -> None:
        current_wallet = self._current_wallet()
        portfolio_value = self.risk.portfolio_value(current_wallet, self.latest_prices)
        if self.initial_portfolio_value <= 0:
            self.initial_portfolio_value = portfolio_value

        pnl = portfolio_value - self.initial_portfolio_value
        pnl_pct = (pnl / self.initial_portfolio_value * 100.0) if self.initial_portfolio_value else 0.0
        drawdown = self.risk.drawdown(current_wallet, self.latest_prices) * 100.0
        summary_line = (
            f"Tick #{self.tick_count} | Portfolio: ${portfolio_value:,.2f} | "
            f"P&L: {pnl:+,.2f} ({pnl_pct:+.2f}%) | Trades: {self.trades_executed} | "
            f"Drawdown: {drawdown:.2f}%"
        )
        print(summary_line)
        logger.info(
            "PERF tick=%s mode=%s portfolio=%.2f pnl=%.2f pnl_pct=%.2f trades=%s drawdown_pct=%.2f",
            self.tick_count,
            "DRY_RUN" if self.dry_run else "LIVE",
            portfolio_value,
            pnl,
            pnl_pct,
            self.trades_executed,
            drawdown,
        )

    def _apply_simulated_fill(self, pair: str, side: str, quantity: float, price: float) -> None:
        base_coin = pair.split("/")[0]
        usd_wallet = self.simulated_wallet.setdefault("USD", {"Free": 0.0, "Lock": 0.0})
        coin_wallet = self.simulated_wallet.setdefault(base_coin, {"Free": 0.0, "Lock": 0.0})
        notional = quantity * price

        if side == "BUY":
            usd_wallet["Free"] = max(0.0, usd_wallet["Free"] - notional)
            coin_wallet["Free"] += quantity
        else:
            coin_wallet["Free"] = max(0.0, coin_wallet["Free"] - quantity)
            usd_wallet["Free"] += notional

        self.latest_wallet = {
            coin: {"Free": balances["Free"], "Lock": balances.get("Lock", 0.0)}
            for coin, balances in self.simulated_wallet.items()
        }

    def _current_wallet(self) -> dict[str, dict[str, Any]]:
        if self.dry_run:
            self.latest_wallet = {
                coin: {"Free": balances["Free"], "Lock": balances.get("Lock", 0.0)}
                for coin, balances in self.simulated_wallet.items()
            }
            return self.latest_wallet

        wallet_payload = self.client.get_balance()
        wallet = self._extract_wallet(wallet_payload or {})
        self.latest_wallet = wallet
        return wallet

    def _load_trade_pairs(self) -> list[str]:
        trade_pairs = os.getenv("TRADE_PAIRS", "")
        return [pair.strip() for pair in trade_pairs.split(",") if pair.strip()]

    def _cancel_pending_orders(self) -> None:
        pending_payload = self.client.query_orders(pending_only=True)
        if pending_payload is None:
            return

        orders = self._extract_orders(pending_payload)
        for order in orders:
            order_id = order.get("OrderId") or order.get("order_id")
            pair = order.get("Pair") or order.get("pair")
            if order_id is not None:
                self.client.cancel_order(order_id=str(order_id))
            elif pair is not None:
                self.client.cancel_order(pair=str(pair))

    def _extract_tickers(self, payload: Mapping[str, Any]) -> dict[str, float]:
        for key in ("Data", "Ticker", "Tickers"):
            block = payload.get(key)
            normalized = self._normalize_ticker_block(block)
            if normalized:
                return normalized
        return self._normalize_ticker_block(payload)

    def _normalize_ticker_block(self, block: Any) -> dict[str, float]:
        if not isinstance(block, Mapping):
            return {}

        normalized: dict[str, float] = {}
        for pair, value in block.items():
            if isinstance(value, Mapping):
                price = value.get("LastPrice") or value.get("last_price") or value.get("price")
            else:
                price = value
            parsed_price = self._to_float(price)
            if parsed_price > 0:
                normalized[str(pair)] = parsed_price
        return normalized

    def _ticker_entry(
        self,
        pair: str,
        payload: Mapping[str, Any],
        normalized_prices: Mapping[str, float],
    ) -> Optional[dict[str, Any]]:
        for key in ("Data", "Ticker", "Tickers"):
            block = payload.get(key)
            if isinstance(block, Mapping) and isinstance(block.get(pair), Mapping):
                return dict(block[pair])
        if pair in normalized_prices:
            return {"LastPrice": normalized_prices[pair]}
        return None

    def _extract_wallet(self, payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        for key in ("Wallet", "Data", "Result"):
            block = payload.get(key)
            if isinstance(block, Mapping):
                wallet_candidate = block.get("Wallet") if key != "Wallet" else block
                if isinstance(wallet_candidate, Mapping):
                    return {
                        str(coin): dict(balance)
                        for coin, balance in wallet_candidate.items()
                        if isinstance(balance, Mapping)
                    }
        return {}

    def _extract_pending_orders(self, payload: Optional[Mapping[str, Any]]) -> int:
        if payload is None:
            return 0
        for key in ("PendingCount", "pending_count"):
            value = payload.get(key)
            if value is not None:
                return int(value)
        data = payload.get("Data")
        if isinstance(data, Mapping):
            for key in ("PendingCount", "pending_count"):
                value = data.get(key)
                if value is not None:
                    return int(value)
        return 0

    def _extract_orders(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        for key in ("Orders", "Data", "Result"):
            block = payload.get(key)
            if isinstance(block, list):
                return [dict(order) for order in block if isinstance(order, Mapping)]
            if isinstance(block, Mapping):
                orders = block.get("Orders")
                if isinstance(orders, list):
                    return [dict(order) for order in orders if isinstance(order, Mapping)]
        return []

    def _extract_server_time(self, payload: Optional[Mapping[str, Any]]) -> Optional[int]:
        if payload is None:
            return None
        for key in ("ServerTime", "serverTime"):
            value = payload.get(key)
            if value is not None:
                return int(value)
        data = payload.get("Data")
        if isinstance(data, Mapping):
            for key in ("ServerTime", "serverTime"):
                value = data.get(key)
                if value is not None:
                    return int(value)
        return None

    def _is_truthy(self, value: str) -> bool:
        return value.strip().lower() in {"1", "true", "yes", "on"}

    def _to_float(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


if __name__ == "__main__":
    bot = TradingBot()
    health = bot.health_check()
    if not health["server_reachable"]:
        sys.exit("Cannot reach Roostoo API. Check connectivity.")
    print(f"Health check passed. Starting bot. Balance: {health['balance_ok']}")
    bot.run()
