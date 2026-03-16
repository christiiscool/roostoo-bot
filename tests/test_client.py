from src.api.client import RoostooClient


def test_sign_returns_hex_digest() -> None:
    client = RoostooClient(api_key="key", secret_key="secret", base_url="https://mock-api.roostoo.com")

    signature = client._sign("payload")

    assert len(signature) == 64
    assert isinstance(signature, str)


def test_headers_include_authentication_fields() -> None:
    client = RoostooClient(api_key="key", secret_key="secret", base_url="https://mock-api.roostoo.com")

    headers = client._headers("GET", "/markets")

    assert headers["X-API-KEY"] == "key"
    assert "X-SIGNATURE" in headers
    assert "X-TIMESTAMP" in headers
