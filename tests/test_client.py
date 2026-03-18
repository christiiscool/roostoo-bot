import hashlib
import hmac
from unittest.mock import Mock, patch

import requests

from src.api.client import RoostooClient


def make_response(payload):
    response = Mock(spec=requests.Response)
    response.json.return_value = payload
    response.status_code = 200
    response.text = str(payload)
    return response


@patch("src.api.client.requests.get")
def test_get_server_time_returns_payload(mock_get) -> None:
    mock_get.return_value = make_response({"Success": True, "ServerTime": 1710000000000})
    client = RoostooClient(api_key="key", secret_key="secret", base_url="https://mock-api.roostoo.com")

    result = client.get_server_time()

    assert result == {"Success": True, "ServerTime": 1710000000000}
    mock_get.assert_called_once_with(
        "https://mock-api.roostoo.com/v3/serverTime",
        params=None,
        headers={},
        timeout=10,
    )


def test_hmac_signature_sorts_params_alphabetically() -> None:
    client = RoostooClient(api_key="key", secret_key="secret", base_url="https://mock-api.roostoo.com")
    params = {"timestamp": "1710000000000", "pair": "BTC/USD", "side": "BUY"}

    signature = client._sign_params(params)

    expected_payload = "pair=BTC/USD&side=BUY&timestamp=1710000000000"
    expected_signature = hmac.new(
        b"secret",
        expected_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert signature == expected_signature


@patch.object(RoostooClient, "_check_clock_skew")
@patch.object(RoostooClient, "get_exchange_info")
@patch.object(RoostooClient, "get_ticker")
@patch("src.api.client.requests.post")
def test_place_order_market_posts_form_encoded_payload(
    mock_post,
    mock_get_ticker,
    mock_get_exchange_info,
    mock_check_clock_skew,
) -> None:
    mock_get_ticker.return_value = {
        "Success": True,
        "Data": {"BTC/USD": {"LastPrice": "50000"}},
    }
    mock_get_exchange_info.return_value = {
        "Success": True,
        "TradePairs": {"BTC/USD": {"MiniOrder": "10"}},
    }
    mock_post.return_value = make_response({"Success": True, "OrderDetail": {"OrderId": "123"}})

    client = RoostooClient(api_key="key", secret_key="secret", base_url="https://mock-api.roostoo.com")
    with patch.object(client, "_timestamp_ms", return_value="1710000000000"):
        result = client.place_order(pair="BTC/USD", side="BUY", quantity="0.01")

    assert result == {"Success": True, "OrderDetail": {"OrderId": "123"}}
    _, kwargs = mock_post.call_args
    assert kwargs["data"]["pair"] == "BTC/USD"
    assert kwargs["data"]["side"] == "BUY"
    assert kwargs["data"]["type"] == "MARKET"
    assert kwargs["data"]["quantity"] == "0.010000"
    assert kwargs["data"]["timestamp"] == "1710000000000"
    assert kwargs["headers"]["RST-API-KEY"] == "key"
    assert "MSG-SIGNATURE" in kwargs["headers"]
    assert kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    mock_check_clock_skew.assert_called_once()


@patch.object(RoostooClient, "_check_clock_skew")
@patch.object(RoostooClient, "get_exchange_info")
@patch("src.api.client.requests.post")
def test_place_order_limit_includes_price_and_type(
    mock_post,
    mock_get_exchange_info,
    mock_check_clock_skew,
) -> None:
    mock_get_exchange_info.return_value = {
        "Success": True,
        "TradePairs": {"ETH/USD": {"MiniOrder": "10"}},
    }
    mock_post.return_value = make_response({"Success": True, "OrderDetail": {"OrderId": "456"}})

    client = RoostooClient(api_key="key", secret_key="secret", base_url="https://mock-api.roostoo.com")
    with patch.object(client, "_timestamp_ms", return_value="1710000000001"):
        result = client.place_order(
            pair="ETH/USD",
            side="SELL",
            quantity="1",
            price="2500",
            order_type="LIMIT",
        )

    assert result == {"Success": True, "OrderDetail": {"OrderId": "456"}}
    _, kwargs = mock_post.call_args
    assert kwargs["data"]["price"] == "2500.00"
    assert kwargs["data"]["type"] == "LIMIT"
    assert kwargs["data"]["quantity"] == "1.000000"
    assert kwargs["data"]["timestamp"] == "1710000000001"
    mock_check_clock_skew.assert_called_once()


@patch.object(RoostooClient, "_check_clock_skew")
@patch("src.api.client.requests.get")
def test_get_balance_returns_wallet_payload(mock_get, mock_check_clock_skew) -> None:
    mock_get.return_value = make_response(
        {"Success": True, "SpotWallet": {"BTC": {"Free": "1.0", "Lock": "0.0"}}}
    )
    client = RoostooClient(api_key="key", secret_key="secret", base_url="https://mock-api.roostoo.com")

    with patch.object(client, "_timestamp_ms", return_value="1710000000002"):
        result = client.get_balance()

    assert result == {"BTC": {"Free": "1.0", "Lock": "0.0"}}
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["timestamp"] == "1710000000002"
    assert kwargs["headers"]["RST-API-KEY"] == "key"
    assert "MSG-SIGNATURE" in kwargs["headers"]
    mock_check_clock_skew.assert_called_once()


@patch.object(RoostooClient, "_check_clock_skew")
@patch("src.api.client.requests.get")
def test_get_balance_falls_back_to_wallet_key(mock_get, mock_check_clock_skew) -> None:
    mock_get.return_value = make_response(
        {"Success": True, "Wallet": {"USD": {"Free": "100.0", "Lock": "0.0"}}}
    )
    client = RoostooClient(api_key="key", secret_key="secret", base_url="https://mock-api.roostoo.com")

    with patch.object(client, "_timestamp_ms", return_value="1710000000002"):
        result = client.get_balance()

    assert result == {"USD": {"Free": "100.0", "Lock": "0.0"}}


@patch("src.api.client.requests.get")
def test_returns_none_when_api_reports_unsuccessful_response(mock_get) -> None:
    mock_get.return_value = make_response({"Success": False, "Message": "bad request"})
    client = RoostooClient(api_key="key", secret_key="secret", base_url="https://mock-api.roostoo.com")

    result = client.get_server_time()

    assert result is None


@patch("src.api.client.requests.get")
def test_returns_none_on_non_200_http_status(mock_get) -> None:
    response = make_response({"Success": True})
    response.status_code = 451
    response.text = "blocked"
    mock_get.return_value = response
    client = RoostooClient(api_key="key", secret_key="secret", base_url="https://mock-api.roostoo.com")

    result = client.get_server_time()

    assert result is None
