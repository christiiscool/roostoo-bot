"""Roostoo API client scaffold with HMAC signing support."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv


class RoostooClient:
    """Thin API wrapper for the Roostoo mock exchange."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        load_dotenv()
        self.api_key = api_key or os.getenv("ROOSTOO_API_KEY", "")
        self.secret_key = secret_key or os.getenv("ROOSTOO_SECRET_KEY", "")
        self.base_url = (base_url or os.getenv("ROOSTOO_BASE_URL", "")).rstrip("/")
        self.session = session or requests.Session()

    def _timestamp(self) -> str:
        return str(int(time.time() * 1000))

    def _sign(self, payload: str) -> str:
        return hmac.new(
            self.secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        timestamp = self._timestamp()
        payload = f"{timestamp}{method.upper()}{path}{body}"
        return {
            "X-API-KEY": self.api_key,
            "X-SIGNATURE": self._sign(payload),
            "X-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> requests.Response:
        body = "" if json is None else str(json)
        response = self.session.request(
            method=method,
            url=f"{self.base_url}{path}",
            headers=self._headers(method, path, body),
            params=params,
            json=json,
            timeout=10,
        )
        response.raise_for_status()
        return response

    def get_markets(self) -> requests.Response:
        return self._request("GET", "/markets")

    def get_ticker(self, symbol: str) -> requests.Response:
        return self._request("GET", f"/ticker/{symbol}")

    def get_orderbook(self, symbol: str) -> requests.Response:
        return self._request("GET", f"/orderbook/{symbol}")

    def get_balances(self) -> requests.Response:
        return self._request("GET", "/account/balances")

    def get_positions(self) -> requests.Response:
        return self._request("GET", "/account/positions")

    def list_orders(self) -> requests.Response:
        return self._request("GET", "/orders")

    def place_order(self, order: Dict[str, Any]) -> requests.Response:
        return self._request("POST", "/orders", json=order)

    def cancel_order(self, order_id: str) -> requests.Response:
        return self._request("DELETE", f"/orders/{order_id}")

    def get_trades(self, symbol: Optional[str] = None) -> requests.Response:
        params = {"symbol": symbol} if symbol else None
        return self._request("GET", "/trades", params=params)
