"""Production-ready API client for the Roostoo mock exchange."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"


class RoostooClient:
    """HTTP client for the Roostoo mock exchange API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 10,
    ) -> None:
        load_dotenv(dotenv_path=ENV_PATH)
        self.api_key = api_key or os.getenv("API_KEY") or os.getenv("ROOSTOO_API_KEY", "")
        self.secret_key = secret_key or os.getenv("SECRET_KEY") or os.getenv("ROOSTOO_SECRET_KEY", "")
        self.base_url = (
            base_url
            or os.getenv("BASE_URL")
            or os.getenv("ROOSTOO_BASE_URL", "https://mock-api.roostoo.com")
        ).rstrip("/")
        self.timeout = timeout

    def _timestamp_ms(self) -> str:
        return str(int(time.time() * 1000))

    def _normalize_params(self, params: Mapping[str, Any]) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        for key, value in params.items():
            if value is None:
                continue
            normalized[key] = str(value)
        return normalized

    def _signature_payload(self, params: Mapping[str, Any]) -> str:
        normalized = self._normalize_params(params)
        return "&".join(f"{key}={normalized[key]}" for key in sorted(normalized))

    def _sign_params(self, params: Mapping[str, Any]) -> str:
        payload = self._signature_payload(params)
        return hmac.new(
            self.secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _auth_headers(self, params: Mapping[str, Any]) -> Dict[str, str]:
        return {
            "RST-API-KEY": self.api_key,
            "MSG-SIGNATURE": self._sign_params(params),
        }

    def _parse_json(self, response: requests.Response) -> Optional[Dict[str, Any]]:
        try:
            payload = response.json()
        except ValueError:
            logger.exception("Failed to decode JSON response from Roostoo.")
            return None

        if not isinstance(payload, dict):
            logger.error("Unexpected Roostoo payload type: %s", type(payload).__name__)
            return None

        if payload.get("Success") is False:
            logger.error("Roostoo API returned Success=false: %s", payload)
            return None

        return payload

    def _extract_server_time_ms(self, payload: Mapping[str, Any]) -> Optional[int]:
        candidates = (
            payload.get("ServerTime"),
            payload.get("serverTime"),
            payload.get("Data", {}).get("ServerTime") if isinstance(payload.get("Data"), dict) else None,
            payload.get("Data", {}).get("serverTime") if isinstance(payload.get("Data"), dict) else None,
            payload.get("Result", {}).get("ServerTime") if isinstance(payload.get("Result"), dict) else None,
            payload.get("Result", {}).get("serverTime") if isinstance(payload.get("Result"), dict) else None,
        )
        for value in candidates:
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                logger.warning("Unable to parse server time value: %s", value)
                return None
        return None

    def _check_clock_skew(self) -> None:
        try:
            payload = self.get_server_time()
            if payload is None:
                return
            server_time = self._extract_server_time_ms(payload)
            if server_time is None:
                return
            local_time = int(time.time() * 1000)
            skew = abs(server_time - local_time)
            if skew > 5000:
                logger.warning(
                    "Detected clock skew above threshold: server=%s local=%s skew_ms=%s",
                    server_time,
                    local_time,
                    skew,
                )
        except Exception:
            logger.exception("Clock skew check failed.")

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        signed: bool = False,
    ) -> Optional[Dict[str, Any]]:
        request_params = self._normalize_params(params or {})
        headers: Dict[str, str] = {}

        if signed:
            request_params.setdefault("timestamp", self._timestamp_ms())
            headers.update(self._auth_headers(request_params))
            self._check_clock_skew()

        url = f"{self.base_url}{path}"

        try:
            if method.upper() == "GET":
                response = requests.get(url, params=request_params or None, headers=headers, timeout=self.timeout)
            elif method.upper() == "POST":
                headers = {
                    **headers,
                    "Content-Type": "application/x-www-form-urlencoded",
                }
                response = requests.post(url, data=request_params or None, headers=headers, timeout=self.timeout)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            return self._parse_json(response)
        except requests.RequestException:
            logger.exception("Roostoo request failed for %s %s", method.upper(), path)
            return None
        except Exception:
            logger.exception("Unexpected error during Roostoo request for %s %s", method.upper(), path)
            return None

    def _extract_trade_pairs(self, exchange_info: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
        for key in ("TradePairs", "tradePairs"):
            trade_pairs = exchange_info.get(key)
            if isinstance(trade_pairs, dict):
                return trade_pairs
        data = exchange_info.get("Data")
        if isinstance(data, dict):
            for key in ("TradePairs", "tradePairs"):
                trade_pairs = data.get(key)
                if isinstance(trade_pairs, dict):
                    return trade_pairs
        return {}

    def _validate_order_inputs(
        self,
        pair: str,
        side: str,
        quantity: Any,
        price: Optional[Any],
        order_type: Optional[str],
    ) -> Optional[Dict[str, str]]:
        normalized_side = side.upper()
        if normalized_side not in {"BUY", "SELL"}:
            logger.error("Invalid order side: %s", side)
            return None

        resolved_type = (order_type or ("MARKET" if price is None else "LIMIT")).upper()
        if resolved_type not in {"MARKET", "LIMIT"}:
            logger.error("Invalid order type: %s", order_type)
            return None
        if resolved_type == "LIMIT" and price is None:
            logger.error("Limit orders require a price.")
            return None

        try:
            quantity_decimal = Decimal(str(quantity))
        except (InvalidOperation, TypeError, ValueError):
            logger.error("Invalid quantity: %s", quantity)
            return None

        if quantity_decimal <= 0:
            logger.error("Quantity must be positive: %s", quantity)
            return None

        trade_value: Optional[Decimal] = None
        serialized_price: Optional[str] = None

        if resolved_type == "LIMIT":
            try:
                price_decimal = Decimal(str(price))
            except (InvalidOperation, TypeError, ValueError):
                logger.error("Invalid price: %s", price)
                return None
            if price_decimal <= 0:
                logger.error("Price must be positive: %s", price)
                return None
            trade_value = quantity_decimal * price_decimal
            serialized_price = str(price)
        else:
            ticker = self.get_ticker(pair=pair)
            if ticker is None:
                logger.error("Unable to fetch ticker for market order validation.")
                return None
            ticker_entry = ticker.get("Data", {}).get(pair) if isinstance(ticker.get("Data"), dict) else None
            if ticker_entry is None and isinstance(ticker.get(pair), dict):
                ticker_entry = ticker.get(pair)
            if not isinstance(ticker_entry, dict) or ticker_entry.get("LastPrice") is None:
                logger.error("Ticker response missing LastPrice for pair %s.", pair)
                return None
            try:
                trade_value = quantity_decimal * Decimal(str(ticker_entry["LastPrice"]))
            except (InvalidOperation, TypeError, ValueError):
                logger.error("Invalid ticker LastPrice for pair %s: %s", pair, ticker_entry.get("LastPrice"))
                return None

        exchange_info = self.get_exchange_info()
        if exchange_info is None:
            logger.error("Unable to fetch exchange info for order validation.")
            return None
        trade_pairs = self._extract_trade_pairs(exchange_info)
        pair_info = trade_pairs.get(pair)
        if not isinstance(pair_info, dict):
            logger.error("Pair %s not found in exchange info.", pair)
            return None

        mini_order = pair_info.get("MiniOrder")
        try:
            mini_order_decimal = Decimal(str(mini_order))
        except (InvalidOperation, TypeError, ValueError):
            logger.error("Invalid MiniOrder value for pair %s: %s", pair, mini_order)
            return None

        if trade_value is None or trade_value <= mini_order_decimal:
            logger.error(
                "Order value must be greater than MiniOrder for %s: value=%s mini_order=%s",
                pair,
                trade_value,
                mini_order_decimal,
            )
            return None

        payload = {
            "pair": pair,
            "side": normalized_side,
            "quantity": str(quantity),
            "order_type": resolved_type,
        }
        if serialized_price is not None:
            payload["price"] = serialized_price
        return payload

    def get_server_time(self) -> Optional[Dict[str, Any]]:
        """Fetch the exchange server time."""
        return self._request("GET", "/v3/serverTime")

    def get_exchange_info(self) -> Optional[Dict[str, Any]]:
        """Fetch exchange metadata including wallet and trade pair settings."""
        return self._request("GET", "/v3/exchangeInfo")

    def get_ticker(self, pair: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch ticker data for one pair or all supported pairs."""
        params = {"timestamp": self._timestamp_ms()}
        if pair:
            params["pair"] = pair
        return self._request("GET", "/v3/ticker", params=params)

    def get_balance(self) -> Optional[Dict[str, Any]]:
        """Fetch signed wallet balances."""
        payload = self._request("GET", "/v3/balance", signed=True)
        if payload is None:
            return None
        spot_wallet = payload.get("SpotWallet") or payload.get("Wallet", {})
        if isinstance(spot_wallet, dict):
            return spot_wallet
        logger.error("Roostoo balance payload missing SpotWallet/Wallet data: %s", payload)
        return {}

    def get_pending_count(self) -> Optional[Dict[str, Any]]:
        """Fetch the number of currently pending orders."""
        return self._request("GET", "/v3/pending_count", signed=True)

    def place_order(
        self,
        pair: str,
        side: str,
        quantity: Any,
        price: Optional[Any] = None,
        order_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Submit a signed market or limit order after MiniOrder validation."""
        payload = self._validate_order_inputs(
            pair=pair,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
        )
        if payload is None:
            return None
        return self._request("POST", "/v3/place_order", params=payload, signed=True)

    def query_orders(
        self,
        order_id: Optional[str] = None,
        pair: Optional[str] = None,
        pending_only: Optional[bool] = None,
        limit: int = 100,
    ) -> Optional[Dict[str, Any]]:
        """Query historical or pending orders."""
        params: Dict[str, Any] = {"limit": limit}
        if order_id is not None:
            params["order_id"] = order_id
        if pair is not None:
            params["pair"] = pair
        if pending_only is not None:
            params["pending_only"] = str(pending_only).lower()
        return self._request("POST", "/v3/query_order", params=params, signed=True)

    def cancel_order(
        self,
        order_id: Optional[str] = None,
        pair: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Cancel one order by id or cancel orders for a pair."""
        if order_id is None and pair is None:
            logger.error("cancel_order requires order_id or pair.")
            return None
        params: Dict[str, Any] = {}
        if order_id is not None:
            params["order_id"] = order_id
        if pair is not None:
            params["pair"] = pair
        return self._request("POST", "/v3/cancel_order", params=params, signed=True)


__all__ = ["RoostooClient", "logger"]
