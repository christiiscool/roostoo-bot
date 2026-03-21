from unittest.mock import patch

from src.risk.manager import RiskManager


def test_drawdown_halts_trading_at_threshold() -> None:
    manager = RiskManager(max_drawdown=0.15)
    high_wallet = {"USD": {"Free": 1000, "Lock": 0}}
    low_wallet = {"USD": {"Free": 850, "Lock": 0}}
    prices = {"BTC/USD": 50000}

    manager.drawdown(high_wallet, prices)
    approved, quantity = manager.approve_trade("BTC/USD", 1, low_wallet, prices)

    assert approved is False
    assert quantity == 0.0


def test_cooldown_prevents_rapid_retrading() -> None:
    manager = RiskManager(cooldown_seconds=300)
    wallet = {"USD": {"Free": 1000, "Lock": 0}}
    prices = {"BTC/USD": 50000}

    with patch("src.risk.manager.time.time", return_value=1000.0):
        manager.update_after_trade("BTC/USD")
    with patch("src.risk.manager.time.time", return_value=1100.0):
        approved, quantity = manager.approve_trade("BTC/USD", 1, wallet, prices)

    assert approved is False
    assert quantity == 0.0


def test_cooldown_does_not_block_sell_exits() -> None:
    manager = RiskManager(cooldown_seconds=300)
    wallet = {
        "USD": {"Free": 100, "Lock": 0},
        "BTC": {"Free": 1.0, "Lock": 0},
    }
    prices = {"BTC/USD": 50000}

    with patch("src.risk.manager.time.time", return_value=1000.0):
        manager.update_after_trade("BTC/USD")
    with patch("src.risk.manager.time.time", return_value=1100.0):
        approved, quantity = manager.approve_trade("BTC/USD", -1, wallet, prices)

    assert approved is True
    assert quantity == 0.8


def test_position_sizing_is_correct_for_buy_and_sell() -> None:
    manager = RiskManager(max_position_pct=0.20)
    buy_wallet = {"USD": {"Free": 1000, "Lock": 0}}
    sell_wallet = {
        "USD": {"Free": 100, "Lock": 0},
        "BTC": {"Free": 2.0, "Lock": 0},
    }
    prices = {"BTC/USD": 50000}

    approved_buy, buy_qty = manager.approve_trade("BTC/USD", 1, buy_wallet, prices)
    approved_sell, sell_qty = manager.approve_trade("BTC/USD", -1, sell_wallet, prices)

    assert approved_buy is True
    assert buy_qty == 0.0016
    assert approved_sell is True
    assert sell_qty == 1.6


def test_position_tiers_adjust_position_size() -> None:
    manager = RiskManager(max_position_pct=0.20)
    wallet = {"USD": {"Free": 1000, "Lock": 0}}
    prices = {"TAO/USD": 500, "SUI/USD": 100, "BNB/USD": 50}

    approved_tao, tao_qty = manager.approve_trade("TAO/USD", 1, wallet, prices)
    approved_sui, sui_qty = manager.approve_trade("SUI/USD", 1, wallet, prices)
    approved_bnb, bnb_qty = manager.approve_trade("BNB/USD", 1, wallet, prices)

    assert approved_tao is True
    assert approved_sui is True
    assert approved_bnb is True
    assert tao_qty == 0.4
    assert sui_qty == 1.5
    assert bnb_qty == 1.6


def test_sell_uses_spot_wallet_coin_balance() -> None:
    manager = RiskManager()
    wallet = {
        "SpotWallet": {
            "USD": {"Free": 100, "Lock": 0},
            "ETH": {"Free": 1.25, "Lock": 0},
        }
    }
    prices = {"ETH/USD": 2500}

    approved, quantity = manager.approve_trade("ETH/USD", -1, wallet, prices)

    assert approved is True
    assert quantity == 1.0


def test_sell_rejects_when_balance_is_locked_only() -> None:
    manager = RiskManager()
    wallet = {
        "BTC": {"Free": 0, "Lock": 0.5},
        "USD": {"Free": 100, "Lock": 0},
    }
    prices = {"BTC/USD": 50000}

    approved, quantity = manager.approve_trade("BTC/USD", -1, wallet, prices)

    assert approved is False
    assert quantity == 0.0


def test_mini_order_gate_rejects_tiny_orders() -> None:
    manager = RiskManager(max_position_pct=0.20)
    wallet = {"USD": {"Free": 4, "Lock": 0}}
    prices = {"BTC/USD": 50000}

    approved, quantity = manager.approve_trade("BTC/USD", 1, wallet, prices)

    assert approved is False
    assert quantity == 0.0


def test_portfolio_value_sums_all_assets() -> None:
    manager = RiskManager()
    wallet = {
        "USD": {"Free": 1000, "Lock": 100},
        "BTC": {"Free": 0.1, "Lock": 0.05},
        "ETH": {"Free": 1.0, "Lock": 0.5},
    }
    prices = {"BTC/USD": 50000, "ETH/USD": 2500}

    value = manager.portfolio_value(wallet, prices)

    assert value == 1100 + (0.15 * 50000) + (1.5 * 2500)


def test_dust_positions_do_not_count_toward_max_open_positions() -> None:
    manager = RiskManager(max_open_positions=3, min_position_value_usd=10.0)
    wallet = {
        "USD": {"Free": 1000, "Lock": 0},
        "BTC": {"Free": 0.2, "Lock": 0},
        "BNB": {"Free": 1.0, "Lock": 0},
        "ETH": {"Free": 0.0001, "Lock": 0},
        "SOL": {"Free": 0.006, "Lock": 0},
    }
    prices = {
        "BTC/USD": 50000,
        "BNB/USD": 650,
        "ETH/USD": 2157.19,
        "SOL/USD": 89.26,
    }

    approved, quantity = manager.approve_trade("ETH/USD", 1, wallet, prices)

    assert approved is True
    assert quantity > 0
    assert manager.open_positions == 2
