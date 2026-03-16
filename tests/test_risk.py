from src.risk.manager import RiskManager


def test_risk_manager_placeholder_exists() -> None:
    manager = RiskManager()

    assert manager is not None
