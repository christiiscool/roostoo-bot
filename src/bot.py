"""Main trading bot orchestration."""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import schedule
from dotenv import load_dotenv

from src.api.client import RoostooClient
from src.risk.manager import RiskManager
from src.strategy import MeanReversionStrategy, MomentumStrategy, aggregate_signals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


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
        trade_pairs = os.getenv("TRADE_PAIRS", "")
        self.trade_pairs = [pair.strip() for pair in trade_pairs.split(",") if pair.strip()]
        self.tick_interval_seconds = int(os.getenv("TICK_INTERVAL_SECONDS", 60))
        self.trades_executed = 0
        self.latest_wallet: dict[str, dict[str, Any]] = {}
        self.latest_prices: dict[str, float] = {}

    def tick(self) -> None:
        """Run one full trading cycle."""
        ticker_payload = self.client.get_ticker()
        if ticker_payload is None:
            logger.warning("Skipping tick because ticker fetch failed.")
            return

        wallet_payload = self.client.get_balance()
        if wallet_payload is None:
            logger.warning("Skipping tick because balance fetch failed.")
            return

        tickers = self._extract_tickers(ticker_payload)
        wallet = self._extract_wallet(wallet_payload)
        if not tickers or not wallet:
            logger.warning("Skipping tick due to incomplete market or wallet data.")
            return

        self.latest_wallet = wallet
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
            approved = False
            quantity = 0.0

            if final_signal != 0:
                approved, quantity = self.risk.approve_trade(
                    pair=pair,
                    signal=final_signal,
                    wallet=wallet,
                    current_prices=tickers,
                )
                if approved:
                    side = "BUY" if final_signal == 1 else "SELL"
                    order_response = self.client.place_order(
                        pair=pair,
                        side=side,
                        quantity=f"{quantity:.6f}",
                    )
                    if order_response is not None:
                        self.risk.update_after_trade(pair)
                        self.trades_executed += 1
                    else:
                        approved = False
                        quantity = 0.0

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

    def run(self) -> None:
        """Start the bot loop and keep running on the configured schedule."""
        logger.info("Starting trading loop for pairs: %s", ", ".join(self.trade_pairs))
        self.tick()
        schedule.every(self.tick_interval_seconds).seconds.do(self.tick)

        try:
            while True:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutdown requested. Cancelling pending orders.")
            self._cancel_pending_orders()
        finally:
            final_value = self.risk.portfolio_value(self.latest_wallet, self.latest_prices)
            logger.info(
                "Bot stopped. final_portfolio_value=%.2f total_trades_executed=%s",
                final_value,
                self.trades_executed,
            )

    def health_check(self) -> dict[str, Any]:
        """Validate API reachability and basic account state before trading."""
        server_payload = self.client.get_server_time()
        server_time = self._extract_server_time(server_payload)
        local_time = int(time.time() * 1000)

        balance_payload = self.client.get_balance()
        pending_payload = self.client.get_pending_count()

        pending_orders = self._extract_pending_orders(pending_payload)

        return {
            "server_reachable": server_payload is not None,
            "local_time_skew_ms": abs(server_time - local_time) if server_time is not None else None,
            "balance_ok": balance_payload is not None,
            "pending_orders": pending_orders,
        }

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
