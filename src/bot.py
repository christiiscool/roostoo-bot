"""Main trading bot orchestration."""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import requests
import schedule
from dotenv import load_dotenv

from src.api.client import RoostooClient
from src.risk.manager import RiskManager
from src.strategy import MeanReversionStrategy, MomentumStrategy, aggregate_signals


def _configure_logging() -> logging.Logger:
    log_level_name = os.getenv("LOG_LEVEL", "DEBUG").upper()
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

DEFAULT_PAIRS = ["BTC/USD", "ETH/USD", "BNB/USD", "SOL/USD"]
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH)
logger = _configure_logging()


class TradingBot:
    """Coordinates data collection, signal generation, risk checks, and execution."""

    def __init__(self) -> None:
        load_dotenv(dotenv_path=ENV_PATH)
        self.client = RoostooClient()
        self.client.refresh_exchange_rules()
        self.risk = RiskManager()
        self.strategies = [MomentumStrategy(), MeanReversionStrategy()]
        self.strategy_weights = {"momentum": 0.6, "mean_reversion": 0.4}
        self.price_history: dict[str, list[float]] = {}
        self.warmup_price_history()
        self.history_window = 50
        pairs_raw = os.getenv("TRADE_PAIRS", "BTC/USD,ETH/USD,BNB/USD,SOL/USD")
        self.pairs = [p.strip() for p in pairs_raw.split(",") if p.strip()]
        if not self.pairs:
            self.pairs = DEFAULT_PAIRS.copy()
        logger.info("Loaded %s pairs: %s", len(self.pairs), self.pairs)
        self.tick_interval_seconds = int(os.getenv("TICK_INTERVAL_SECONDS", 60))
        self.dry_run = self._is_truthy(os.getenv("DRY_RUN", "true"))
        self.base_signal_threshold = 0.2
        self.relaxed_signal_threshold = 0.15
        self.trades_executed = 0
        self.tick_count = 0
        self.last_signal_count = 0
        self.last_risk_evaluations = 0
        self.last_dry_run_orders = 0
        self.daily_tick_count = 0
        self.daily_trade_count = 0
        self.current_day = datetime.now(timezone.utc).date()
        self.latest_wallet: dict[str, dict[str, Any]] = {}
        self.latest_prices: dict[str, float] = {}
        self.initial_portfolio_value = 0.0
        self.simulated_wallet: dict[str, dict[str, float]] = {}
        self.pending_limit_orders: dict[str, dict[str, Any]] = {}
        self.entry_prices: dict[str, float] = {}

    def warmup_price_history(self) -> None:
        """Pre-load last 50 candles from Binance public API on startup."""
        binance_map = {
            "BTC/USD": "BTCUSDT",
            "ETH/USD": "ETHUSDT",
            "BNB/USD": "BNBUSDT",
            "SOL/USD": "SOLUSDT",
        }

        for pair, symbol in binance_map.items():
            try:
                response = requests.get(
                    "https://api.binance.com/api/v3/klines",
                    params={"symbol": symbol, "interval": "1m", "limit": 50},
                    timeout=10,
                )
                if response.status_code != 200:
                    logger.error("HTTP %s: %s", response.status_code, response.text)
                    continue
                candles = response.json()
                closes = [float(candle[4]) for candle in candles]
                if closes:
                    self.price_history[pair] = closes[-50:]
                    print(f"[WARMUP] {pair}: loaded {len(closes)} bars, last price: {closes[-1]}")
            except Exception as exc:
                print(f"[WARMUP] Failed for {pair}: {exc}")

        print("Warmup complete. Price history loaded. Bot ready to trade immediately.")

    def tick(self) -> None:
        """Run one full trading cycle."""
        if not self.pairs:
            logger.error("No pairs configured! Using defaults.")
            self.pairs = DEFAULT_PAIRS.copy()
        self._reset_daily_state_if_needed()
        self.tick_count += 1
        self.daily_tick_count += 1
        self.last_signal_count = 0
        self.last_risk_evaluations = 0
        self.last_dry_run_orders = 0
        if self.tick_count <= 3:
            logger.info("Tick %s pairs: %s", self.tick_count, self.pairs)

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
        self._check_stop_losses(wallet, tickers)
        wallet = self._current_wallet()
        self._manage_pending_orders(tickers)
        wallet = self._current_wallet()
        self._force_initial_positions(wallet, tickers)
        wallet = self._current_wallet()
        required_bars = max(strategy.required_bars() for strategy in self.strategies)
        signal_threshold = self._current_signal_threshold()
        new_buy_orders = 0
        logger.info("Processing %s pairs: %s", len(self.pairs), self.pairs)

        for pair in self.pairs:
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
            weighted_signal_value = self._weighted_signal_value(strategy_signals)
            final_signal = aggregate_signals(
                strategy_signals,
                self.strategy_weights,
                threshold=signal_threshold,
            )
            if final_signal != 0:
                self.last_signal_count += 1

            approved = False
            quantity = 0.0

            if final_signal != 0:
                if final_signal == -1:
                    coin = pair.split("/")[0]
                    coin_balance = self._wallet_free_balance(wallet, coin)
                    if coin_balance <= 0:
                        logger.debug("Skipping SELL %s: no holdings", pair)
                        continue
                self.last_risk_evaluations += 1
                approved, quantity = self.risk.approve_trade(
                    pair=pair,
                    signal=final_signal,
                    wallet=wallet,
                    current_prices=tickers,
                )
                if approved:
                    side = "BUY" if final_signal == 1 else "SELL"
                    if (
                        not self.dry_run
                        and side == "BUY"
                        and (self._count_pending_buys() >= 2 or new_buy_orders >= 2)
                    ):
                        logger.info("Skipping BUY %s: pending/new BUY order cap reached.", pair)
                        approved = False
                        quantity = 0.0
                        continue
                    amount_precision = self.client.amount_precision.get(pair, 6)
                    quantity = round(quantity, amount_precision)
                    mini = self.client.mini_order.get(pair, 1.0)
                    if quantity * last_price < mini:
                        logger.warning("Order too small: %s %s < MiniOrder %s", quantity, pair, mini)
                        continue
                    if self.dry_run:
                        limit_price = self._limit_price_for_signal(pair, side, last_price)
                        self._apply_simulated_fill(pair=pair, side=side, quantity=quantity, price=limit_price)
                        logger.info(
                            "[DRY RUN] Would place: %s %.6f %s LIMIT @ %.6f",
                            side,
                            quantity,
                            pair,
                            limit_price,
                        )
                        logger.info(
                            "ORDER mode=DRY_RUN order_type=LIMIT side=%s qty=%.6f pair=%s price=%.6f",
                            side,
                            quantity,
                            pair,
                            limit_price,
                        )
                        self.last_dry_run_orders += 1
                        self.daily_trade_count += 1
                    else:
                        if side == "SELL":
                            order_response = self.client.place_order(
                                pair=pair,
                                side=side,
                                quantity=f"{quantity:.6f}",
                                order_type="MARKET",
                            )
                            if order_response is not None:
                                self.trades_executed += 1
                                self.daily_trade_count += 1
                                self.entry_prices.pop(pair, None)
                                logger.info(
                                    "ORDER mode=LIVE order_type=MARKET side=%s qty=%.6f pair=%s price=%.6f",
                                    side,
                                    quantity,
                                    pair,
                                    last_price,
                                )
                        else:
                            limit_price = self._limit_price_for_signal(pair, side, last_price)
                            order_response = self.client.place_order(
                                pair=pair,
                                side=side,
                                quantity=f"{quantity:.6f}",
                                price=f"{limit_price:.6f}",
                                order_type="LIMIT",
                            )
                        if order_response is None:
                            approved = False
                            quantity = 0.0
                        elif side == "BUY":
                            self._register_limit_order(
                                pair=pair,
                                side=side,
                                quantity=quantity,
                                limit_price=limit_price,
                                order_response=order_response,
                            )
                            new_buy_orders += 1
                    if approved and self.dry_run:
                        self.risk.update_after_trade(pair)
                        self.trades_executed += 1
                        wallet = self._current_wallet()
                    elif approved and not self.dry_run and side == "SELL":
                        self.risk.update_after_trade(pair)
                        wallet = self._current_wallet()

            logger.debug(
                "%s: signal=%.3f threshold=%.3f approved=%s strategy_signals=%s final_signal=%s",
                pair,
                weighted_signal_value,
                signal_threshold,
                approved,
                strategy_signals,
                final_signal,
            )
            logger.info(
                "%s signal=%.3f threshold=%.3f approved=%s",
                pair,
                weighted_signal_value,
                signal_threshold,
                approved,
            )

            portfolio_summary = self.risk.summary()
            logger.info(
                "tick=%s pair=%s signal=%s approved=%s qty=%.6f threshold=%.2f summary=%s",
                datetime.now(timezone.utc).isoformat(),
                pair,
                final_signal,
                approved,
                quantity,
                signal_threshold,
                portfolio_summary,
            )

        self._emit_performance_summary()

    def run(self) -> None:
        """Start the bot loop and keep running on the configured schedule."""
        logger.info(
            "Starting trading loop for pairs: %s | mode=%s",
            ", ".join(self.pairs),
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
            if not self.simulated_wallet:
                self._ensure_simulated_wallet(tickers)
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
        self._ensure_simulated_wallet(self.latest_prices)
        base_coin = pair.split("/")[0]
        usd_wallet = self.simulated_wallet.setdefault("USD", {"Free": 0.0, "Lock": 0.0})
        coin_wallet = self.simulated_wallet.setdefault(base_coin, {"Free": 0.0, "Lock": 0.0})
        notional = quantity * price

        if side == "BUY":
            usd_wallet["Free"] = max(0.0, usd_wallet["Free"] - notional)
            coin_wallet["Free"] += quantity
            self.entry_prices[pair] = price
        else:
            coin_wallet["Free"] = max(0.0, coin_wallet["Free"] - quantity)
            usd_wallet["Free"] += notional
            if coin_wallet["Free"] <= 0:
                self.entry_prices.pop(pair, None)

        self.latest_wallet = {
            coin: {"Free": balances["Free"], "Lock": balances.get("Lock", 0.0)}
            for coin, balances in self.simulated_wallet.items()
        }

    def _current_wallet(self) -> dict[str, dict[str, Any]]:
        if self.dry_run:
            self._ensure_simulated_wallet(self.latest_prices)
            self.latest_wallet = {
                coin: {"Free": balances["Free"], "Lock": balances.get("Lock", 0.0)}
                for coin, balances in self.simulated_wallet.items()
            }
            return self.latest_wallet

        wallet_payload = self.client.get_balance()
        wallet = self._extract_wallet(wallet_payload or {})
        self.latest_wallet = wallet
        return wallet

    def _weighted_signal_value(self, signals: Mapping[str, int]) -> float:
        return sum(signal * self.strategy_weights.get(name, 0.0) for name, signal in signals.items())

    def _check_stop_losses(
        self,
        wallet: Mapping[str, Mapping[str, Any]],
        current_prices: Mapping[str, Any],
    ) -> None:
        for pair, entry_price in list(self.entry_prices.items()):
            coin = pair.split("/")[0]
            current_price = self._to_float(current_prices.get(pair))
            if current_price <= 0 or entry_price <= 0:
                continue

            pnl_pct = (current_price - entry_price) / entry_price
            if pnl_pct >= -0.02:
                continue

            logger.warning(
                "STOP LOSS triggered: %s entry=%.6f current=%.6f loss=%.2f%%",
                pair,
                entry_price,
                current_price,
                pnl_pct * 100.0,
            )
            coin_free = self._wallet_free_balance(wallet, coin)
            if coin_free <= 0:
                logger.warning("STOP LOSS unable to sell %s: no free balance in wallet.", coin)
                continue

            amount_precision = self.client.amount_precision.get(pair, 6)
            quantity = round(coin_free * 0.99, amount_precision)
            if quantity <= 0:
                continue

            if self.dry_run:
                self._apply_simulated_fill(pair=pair, side="SELL", quantity=quantity, price=current_price)
                self.trades_executed += 1
                self.daily_trade_count += 1
                self.risk.update_after_trade(pair)
                logger.info(
                    "[DRY RUN] STOP LOSS sell: SELL %.6f %s MARKET @ %.6f",
                    quantity,
                    pair,
                    current_price,
                )
                continue

            order_response = self.client.place_order(
                pair=pair,
                side="SELL",
                quantity=f"{quantity:.6f}",
                order_type="MARKET",
            )
            if order_response is None:
                logger.warning("STOP LOSS order failed for %s.", pair)
                continue

            self.entry_prices.pop(pair, None)
            self.trades_executed += 1
            self.daily_trade_count += 1
            self.risk.update_after_trade(pair)

    def _force_initial_positions(
        self,
        wallet: Mapping[str, Mapping[str, Any]],
        current_prices: Mapping[str, Any],
    ) -> None:
        if self.tick_count < 3:
            return
        if not self._is_all_cash(wallet):
            return

        for pair in ("BTC/USD", "ETH/USD"):
            price = self._to_float(current_prices.get(pair))
            free_usd = self._wallet_free_balance(wallet, "USD")
            if price <= 0 or free_usd <= 0:
                continue
            quantity = round((free_usd * 0.02) / price, 6)
            amount_precision = self.client.amount_precision.get(pair, 6)
            quantity = round(quantity, amount_precision)
            if quantity <= 0:
                continue
            mini = self.client.mini_order.get(pair, 1.0)
            if quantity * price < mini:
                logger.warning("Order too small: %s %s < MiniOrder %s", quantity, pair, mini)
                continue
            limit_price = self._limit_price_for_signal(pair, "BUY", price)
            if self.dry_run:
                self._apply_simulated_fill(pair=pair, side="BUY", quantity=quantity, price=limit_price)
                self.trades_executed += 1
                self.daily_trade_count += 1
                self.risk.update_after_trade(pair)
                logger.info(
                    "[DRY RUN] Force buy bootstrap: BUY %.6f %s LIMIT @ %.6f",
                    quantity,
                    pair,
                    limit_price,
                )
            else:
                order_response = self.client.place_order(
                    pair=pair,
                    side="BUY",
                    quantity=f"{quantity:.6f}",
                    price=f"{limit_price:.6f}",
                    order_type="LIMIT",
                )
                if order_response is not None:
                    self._register_limit_order(
                        pair=pair,
                        side="BUY",
                        quantity=quantity,
                        limit_price=limit_price,
                        order_response=order_response,
                    )

    def _is_all_cash(self, wallet: Mapping[str, Mapping[str, Any]]) -> bool:
        usd_balance = self._wallet_free_balance(wallet, "USD")
        coin_balances = sum(
            self._to_float(balance.get("Free", 0.0))
            for coin, balance in wallet.items()
            if coin != "USD"
        )
        return usd_balance > 0 and coin_balances == 0

    def _wallet_free_balance(self, wallet: Mapping[str, Mapping[str, Any]], coin: str) -> float:
        balances = wallet.get(coin, {})
        if not isinstance(balances, Mapping):
            return 0.0
        return self._to_float(balances.get("Free", 0.0))

    def _current_signal_threshold(self) -> float:
        if self.daily_tick_count >= 20 and self.daily_trade_count < 3:
            logger.info(
                "Relaxing signal threshold to %.2f after %s ticks with %s trades today.",
                self.relaxed_signal_threshold,
                self.daily_tick_count,
                self.daily_trade_count,
            )
            return self.relaxed_signal_threshold
        return self.base_signal_threshold

    def _reset_daily_state_if_needed(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today == self.current_day:
            return
        self.current_day = today
        self.daily_tick_count = 0
        self.daily_trade_count = 0

    def _ensure_simulated_wallet(self, current_prices: Mapping[str, Any]) -> None:
        if self.simulated_wallet:
            return

        exchange_info = self.client.get_exchange_info()
        initial_wallet = exchange_info.get("InitialWallet") if isinstance(exchange_info, Mapping) else None
        if initial_wallet is None and isinstance(exchange_info, Mapping):
            data = exchange_info.get("Data")
            if isinstance(data, Mapping):
                initial_wallet = data.get("InitialWallet")

        if isinstance(initial_wallet, Mapping):
            normalized_wallet: dict[str, dict[str, float]] = {}
            for coin, balance in initial_wallet.items():
                if isinstance(balance, Mapping):
                    normalized_wallet[str(coin)] = {
                        "Free": self._to_float(balance.get("Free", 0.0)),
                        "Lock": self._to_float(balance.get("Lock", 0.0)),
                    }
                else:
                    normalized_wallet[str(coin)] = {"Free": self._to_float(balance), "Lock": 0.0}
            self.simulated_wallet = normalized_wallet
        else:
            self.simulated_wallet = {"USD": {"Free": 0.0, "Lock": 0.0}}

        self.initial_portfolio_value = self.risk.portfolio_value(self.simulated_wallet, current_prices)

    def _limit_price_for_signal(self, pair: str, side: str, last_price: float) -> float:
        precision = self.client.price_precision.get(pair, 2)
        if side == "BUY":
            return round(last_price * 0.9998, precision)
        return round(last_price * 1.0002, precision)

    def _register_limit_order(
        self,
        pair: str,
        side: str,
        quantity: float,
        limit_price: float,
        order_response: Mapping[str, Any],
    ) -> None:
        order_id = self._extract_order_id(order_response)
        status = self._extract_order_status(order_response)
        logger.info(
            "ORDER mode=LIVE order_type=LIMIT side=%s qty=%.6f pair=%s price=%.6f status=%s",
            side,
            quantity,
            pair,
            limit_price,
            status or "UNKNOWN",
        )

        if status == "FILLED":
            self.trades_executed += 1
            self.daily_trade_count += 1
            if side == "BUY":
                self.entry_prices[pair] = limit_price
            else:
                self.entry_prices.pop(pair, None)
            return

        if order_id is None:
            return

        self.pending_limit_orders[order_id] = {
            "pair": pair,
            "side": side,
            "quantity": quantity,
            "limit_price": limit_price,
            "submitted_tick": self.tick_count,
            "submitted_at": time.time(),
        }

    def _manage_pending_orders(self, current_prices: Mapping[str, Any]) -> None:
        if self.dry_run:
            return
        should_query = bool(self.pending_limit_orders) or (self.tick_count % 5 == 0)
        if not should_query:
            return

        pending_payload = self.client.query_orders(pending_only=True)
        pending_orders = self._extract_orders(pending_payload or {})
        pending_lookup = {
            str(order.get("OrderId") or order.get("order_id")): order
            for order in pending_orders
            if order.get("OrderId") or order.get("order_id")
        }
        recovered_at = time.time()
        for order_id, order in pending_lookup.items():
            if order_id in self.pending_limit_orders:
                continue
            pair = self._extract_order_pair(order)
            side = self._extract_order_side(order)
            quantity = self._extract_order_quantity(order)
            if pair is None or side is None or quantity <= 0:
                continue
            self.pending_limit_orders[order_id] = {
                "pair": pair,
                "side": side,
                "quantity": quantity,
                "limit_price": self._extract_order_price(order, current_prices, pair),
                "submitted_tick": max(0, self.tick_count - 2),
                "submitted_at": recovered_at - 601,
            }
            logger.warning(
                "Recovered orphan pending order %s for %s %s qty=%.6f; scheduling immediate cleanup.",
                order_id,
                side,
                pair,
                quantity,
            )

        for order_id, metadata in list(self.pending_limit_orders.items()):
            if order_id not in pending_lookup:
                self.trades_executed += 1
                self.daily_trade_count += 1
                if str(metadata["side"]).upper() == "BUY":
                    self.entry_prices[str(metadata["pair"])] = float(metadata["limit_price"])
                else:
                    self.entry_prices.pop(str(metadata["pair"]), None)
                self.pending_limit_orders.pop(order_id, None)
                continue

            age_seconds = time.time() - float(metadata["submitted_at"])
            age_ticks = self.tick_count - int(metadata["submitted_tick"])
            side = str(metadata["side"])

            if age_seconds >= 600:
                self.client.cancel_order(order_id=order_id)
                self.pending_limit_orders.pop(order_id, None)
                logger.info("Cancelled stale limit order %s after %.0f seconds.", order_id, age_seconds)
                continue

            if age_ticks >= (1 if side == "BUY" else 2):
                pair = str(metadata["pair"])
                quantity = float(metadata["quantity"])
                self.client.cancel_order(order_id=order_id)
                market_response = self.client.place_order(
                    pair=pair,
                    side=side,
                    quantity=f"{quantity:.6f}",
                    order_type="MARKET",
                )
                self.pending_limit_orders.pop(order_id, None)
                if market_response is not None:
                    self.trades_executed += 1
                    self.daily_trade_count += 1
                    self.risk.update_after_trade(pair)
                    if side == "BUY":
                        self.entry_prices[pair] = self._to_float(current_prices.get(pair))
                    else:
                        self.entry_prices.pop(pair, None)
                    logger.info(
                        "ORDER mode=LIVE order_type=MARKET side=%s qty=%.6f pair=%s price=%.6f fallback_from=%s",
                        side,
                        quantity,
                        pair,
                        self._to_float(current_prices.get(pair)),
                        order_id,
                    )

    def _extract_order_id(self, payload: Mapping[str, Any]) -> Optional[str]:
        order_detail = payload.get("OrderDetail")
        if isinstance(order_detail, Mapping):
            order_id = order_detail.get("OrderId") or order_detail.get("order_id")
            if order_id is not None:
                return str(order_id)
        order_id = payload.get("OrderId") or payload.get("order_id")
        if order_id is not None:
            return str(order_id)
        data = payload.get("Data")
        if isinstance(data, Mapping):
            order_detail = data.get("OrderDetail")
            if isinstance(order_detail, Mapping):
                order_id = order_detail.get("OrderId") or order_detail.get("order_id")
                if order_id is not None:
                    return str(order_id)
        return None

    def _extract_order_status(self, payload: Mapping[str, Any]) -> str:
        order_detail = payload.get("OrderDetail")
        if isinstance(order_detail, Mapping):
            status = order_detail.get("Status") or order_detail.get("status")
            if status is not None:
                return str(status).upper()
        data = payload.get("Data")
        if isinstance(data, Mapping):
            order_detail = data.get("OrderDetail")
            if isinstance(order_detail, Mapping):
                status = order_detail.get("Status") or order_detail.get("status")
                if status is not None:
                    return str(status).upper()
        return ""

    def _extract_order_pair(self, payload: Mapping[str, Any]) -> Optional[str]:
        pair = payload.get("Pair") or payload.get("pair")
        if pair is None:
            return None
        return str(pair)

    def _extract_order_side(self, payload: Mapping[str, Any]) -> Optional[str]:
        side = payload.get("Side") or payload.get("side")
        if side is None:
            return None
        return str(side).upper()

    def _extract_order_quantity(self, payload: Mapping[str, Any]) -> float:
        for key in ("Quantity", "quantity", "RemainQty", "remain_qty", "RemainingQty", "remaining_qty"):
            if key in payload:
                return self._to_float(payload.get(key))
        return 0.0

    def _extract_order_price(
        self,
        payload: Mapping[str, Any],
        current_prices: Mapping[str, Any],
        pair: str,
    ) -> float:
        for key in ("Price", "price"):
            if key in payload:
                return self._to_float(payload.get(key))
        return self._to_float(current_prices.get(pair))

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
        for key in ("SpotWallet", "Wallet", "Data", "Result"):
            block = payload.get(key)
            if isinstance(block, Mapping):
                wallet_candidate = block
                if key in {"Data", "Result"}:
                    wallet_candidate = block.get("SpotWallet") or block.get("Wallet") or block
                if isinstance(wallet_candidate, Mapping):
                    return {
                        str(coin): dict(balance)
                        for coin, balance in wallet_candidate.items()
                        if isinstance(balance, Mapping)
                    }
        if all(isinstance(balance, Mapping) for balance in payload.values()):
            return {
                str(coin): dict(balance)
                for coin, balance in payload.items()
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

    def _count_pending_buys(self) -> int:
        return sum(
            1
            for order in self.pending_limit_orders.values()
            if str(order.get("side", "")).upper() == "BUY"
        )

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
