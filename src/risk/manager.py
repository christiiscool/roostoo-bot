"""Risk management utilities for the Roostoo bot."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from dotenv import load_dotenv

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"


class RiskManager:
    """Portfolio-level guardrails focused on downside protection."""

    def __init__(
        self,
        max_position_pct: Optional[float] = None,
        max_drawdown: Optional[float] = None,
        cooldown_seconds: Optional[int] = None,
        max_open_positions: int = 3,
        min_position_value_usd: Optional[float] = None,
    ) -> None:
        load_dotenv(dotenv_path=ENV_PATH)
        self.max_position_pct = float(max_position_pct or os.getenv("MAX_POSITION_PCT", 0.20))
        self.max_drawdown = float(max_drawdown or os.getenv("MAX_DRAWDOWN", 0.15))
        self.cooldown_seconds = int(cooldown_seconds or os.getenv("COOLDOWN_SECONDS", 300))
        self.max_open_positions = int(max_open_positions)
        self.min_position_value_usd = float(min_position_value_usd or os.getenv("MIN_POSITION_VALUE_USD", 10.0))
        self.last_trade_times: dict[str, float] = {}
        self.peak_portfolio: float = 0.0
        self.current_drawdown: float = 0.0
        self.open_positions: int = 0

    def approve_trade(
        self,
        pair: str,
        signal: int,
        wallet: Mapping[str, Mapping[str, Any]],
        current_prices: Mapping[str, Any],
    ) -> tuple[bool, float]:
        """Apply fail-fast portfolio gates and size from the live wallet snapshot."""
        normalized_wallet = self._normalize_wallet(wallet)
        logger.info("approve_trade wallet=%s", dict(normalized_wallet))
        current_drawdown = self.drawdown(normalized_wallet, current_prices)
        if current_drawdown >= self.max_drawdown:
            logger.warning(
                "Trade rejected for %s due to drawdown threshold: drawdown=%.4f threshold=%.4f",
                pair,
                current_drawdown,
                self.max_drawdown,
            )
            return False, 0.0

        now = time.time()
        last_trade_time = self.last_trade_times.get(pair)
        if signal == 1 and last_trade_time is not None and (now - last_trade_time) < self.cooldown_seconds:
            logger.info("Trade rejected for %s due to cooldown.", pair)
            return False, 0.0

        self.open_positions = self._count_open_positions(normalized_wallet, current_prices)
        if signal == 1 and self.open_positions >= self.max_open_positions:
            logger.info("Trade rejected for %s due to max open positions.", pair)
            return False, 0.0

        last_price = self._resolve_pair_price(pair, current_prices)
        if last_price is None or last_price <= 0:
            logger.warning("Trade rejected for %s due to invalid price.", pair)
            return False, 0.0

        if signal == 1:
            free_usd = self._free_balance(normalized_wallet, "USD")
            if free_usd <= 0:
                logger.info("Trade rejected for %s because live USD balance is empty.", pair)
                return False, 0.0
            quantity = round((free_usd * self.max_position_pct) / last_price, 6)
        elif signal == -1:
            coin = pair.split("/")[0]
            coin_free = self._free_balance(normalized_wallet, coin)
            logger.info(
                "SELL check: %s free=%s wallet keys=%s",
                coin,
                coin_free,
                list(normalized_wallet.keys()),
            )
            if coin_free <= 0:
                locked = self._locked_balance(normalized_wallet, coin)
                if locked > 0:
                    logger.warning(
                        "%s balance is locked (%s). Cancel pending orders first. Wallet=%s",
                        coin,
                        locked,
                        dict(normalized_wallet),
                    )
                else:
                    logger.warning("Cannot sell %s: zero balance. Wallet=%s", coin, dict(normalized_wallet))
                return False, 0.0
            quantity = round(coin_free * 0.80, 6)
        else:
            return False, 0.0

        if quantity <= 0 or (quantity * last_price) <= 1.0:
            logger.info(
                "Trade rejected for %s due to Roostoo mini order gate: quantity=%.6f price=%.6f",
                pair,
                quantity,
                last_price,
            )
            return False, 0.0

        return True, quantity

    def update_after_trade(self, pair: str) -> None:
        """Record the time of a successful trade for cooldown tracking."""
        self.last_trade_times[pair] = time.time()

    def portfolio_value(
        self,
        wallet: Mapping[str, Mapping[str, Any]],
        current_prices: Mapping[str, Any],
    ) -> float:
        """Return the total USD value of free and locked balances."""
        normalized_wallet = self._normalize_wallet(wallet)
        total_value = 0.0
        for coin, balances in normalized_wallet.items():
            free_balance = self._to_float(balances.get("Free", 0.0))
            lock_balance = self._to_float(balances.get("Lock", 0.0))
            total_units = free_balance + lock_balance
            if coin == "USD":
                total_value += total_units
                continue

            pair = f"{coin}/USD"
            price = self._resolve_pair_price(pair, current_prices)
            if price is None:
                logger.warning("Missing price for %s while computing portfolio value.", pair)
                continue
            total_value += total_units * price
        return total_value

    def drawdown(
        self,
        wallet: Mapping[str, Mapping[str, Any]],
        current_prices: Mapping[str, Any],
    ) -> float:
        """Return current drawdown from peak and update peak on new highs."""
        current_value = self.portfolio_value(wallet, current_prices)
        if current_value > self.peak_portfolio:
            self.peak_portfolio = current_value
            self.current_drawdown = 0.0
            return 0.0

        if self.peak_portfolio <= 0:
            self.current_drawdown = 0.0
            return 0.0

        self.current_drawdown = max(0.0, (self.peak_portfolio - current_value) / self.peak_portfolio)
        return self.current_drawdown

    def summary(self) -> dict[str, Any]:
        """Return a snapshot of portfolio-level guardrail state."""
        now = time.time()
        active_cooldowns = {
            pair: max(0, int(self.cooldown_seconds - (now - last_trade_time)))
            for pair, last_trade_time in self.last_trade_times.items()
            if (now - last_trade_time) < self.cooldown_seconds
        }
        return {
            "peak_portfolio": self.peak_portfolio,
            "current_drawdown": self.current_drawdown,
            "active_cooldowns": active_cooldowns,
            "open_positions": self.open_positions,
        }

    def _count_open_positions(
        self,
        wallet: Mapping[str, Mapping[str, Any]],
        current_prices: Mapping[str, Any],
    ) -> int:
        open_positions = 0
        for coin, balances in wallet.items():
            if coin == "USD":
                continue
            free_balance = self._to_float(balances.get("Free", 0.0))
            if free_balance <= 0:
                continue
            pair = f"{coin}/USD"
            price = self._resolve_pair_price(pair, current_prices)
            if price is None or price <= 0:
                continue
            if (free_balance * price) >= self.min_position_value_usd:
                open_positions += 1
        return open_positions

    def _free_balance(self, wallet: Mapping[str, Mapping[str, Any]], coin: str) -> float:
        balances = wallet.get(coin, {})
        return self._to_float(balances.get("Free", 0.0))

    def _locked_balance(self, wallet: Mapping[str, Mapping[str, Any]], coin: str) -> float:
        balances = wallet.get(coin, {})
        return self._to_float(balances.get("Lock", 0.0))

    def _normalize_wallet(self, wallet: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
        if "SpotWallet" in wallet and isinstance(wallet.get("SpotWallet"), Mapping):
            return wallet["SpotWallet"]
        if "Wallet" in wallet and isinstance(wallet.get("Wallet"), Mapping):
            return wallet["Wallet"]
        return wallet

    def _resolve_pair_price(self, pair: str, current_prices: Mapping[str, Any]) -> Optional[float]:
        value = current_prices.get(pair)
        if value is None:
            return None
        if isinstance(value, Mapping):
            for key in ("LastPrice", "last_price", "price"):
                if key in value:
                    return self._to_float(value[key])
            return None
        return self._to_float(value)

    def _to_float(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


__all__ = ["RiskManager", "logger"]
