"""Analyze the bot trade journal and print a concise performance summary."""

from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, pstdev

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"


def _load_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _returns(records: list[dict]) -> list[float]:
    returns: list[float] = []
    previous_value: float | None = None
    for record in records:
        portfolio_value = float(record.get("portfolio_value", 0.0))
        if previous_value and previous_value > 0:
            returns.append((portfolio_value - previous_value) / previous_value)
        previous_value = portfolio_value
    return returns


def _max_drawdown(records: list[dict]) -> float:
    peak = 0.0
    max_dd = 0.0
    for record in records:
        value = float(record.get("portfolio_value", 0.0))
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = (peak - value) / peak
            max_dd = max(max_dd, drawdown)
    return max_dd


def _sharpe_like(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    sigma = pstdev(returns)
    if sigma == 0:
        return 0.0
    return mean(returns) / sigma * math.sqrt(len(returns))


def _sortino_like(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    downside = [value for value in returns if value < 0]
    if not downside:
        return 0.0
    downside_sigma = pstdev(downside)
    if downside_sigma == 0:
        return 0.0
    return mean(returns) / downside_sigma * math.sqrt(len(returns))


def main() -> None:
    load_dotenv(dotenv_path=ENV_PATH)
    journal_file = os.getenv("TRADE_JOURNAL_FILE", "trade_journal.jsonl")
    journal_path = Path(journal_file)
    if not journal_path.is_absolute():
        journal_path = PROJECT_ROOT / journal_path

    records = _load_records(journal_path)
    if not records:
        print(f"No trade records found at {journal_path}")
        return

    returns = _returns(records)
    final_portfolio = float(records[-1].get("portfolio_value", 0.0))
    first_portfolio = float(records[0].get("portfolio_value", 0.0))
    pnl = final_portfolio - first_portfolio
    pnl_pct = (pnl / first_portfolio * 100.0) if first_portfolio else 0.0
    max_dd = _max_drawdown(records) * 100.0

    reason_counts = Counter(str(record.get("reason", "unknown")) for record in records)
    side_counts = Counter(str(record.get("side", "unknown")) for record in records)
    pair_counts = Counter(str(record.get("pair", "unknown")) for record in records)
    reason_groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        reason_groups[str(record.get("reason", "unknown"))].append(record)

    print("=== ROOSTOO TRADE ANALYSIS ===")
    print(f"Journal:        {journal_path}")
    print(f"Records:        {len(records)}")
    print(f"Portfolio:      {final_portfolio:,.2f}")
    print(f"P&L:            {pnl:+,.2f} ({pnl_pct:+.2f}%)")
    print(f"Max Drawdown:   {max_dd:.2f}%")
    print(f"Sharpe-like:    {_sharpe_like(returns):.3f}")
    print(f"Sortino-like:   {_sortino_like(returns):.3f}")
    print()
    print("By Side:")
    for key, value in sorted(side_counts.items()):
        print(f"  {key:<6} {value}")
    print("By Reason:")
    for key, value in sorted(reason_counts.items()):
        print(f"  {key:<18} {value}")
    print("By Pair:")
    for key, value in sorted(pair_counts.items()):
        print(f"  {key:<10} {value}")
    print()
    print("Reason Performance:")
    for reason, items in sorted(reason_groups.items()):
        first_value = float(items[0].get("portfolio_value", 0.0))
        last_value = float(items[-1].get("portfolio_value", 0.0))
        reason_pnl = last_value - first_value
        reason_pnl_pct = (reason_pnl / first_value * 100.0) if first_value else 0.0
        print(
            f"  {reason:<18} count={len(items):<4} pnl={reason_pnl:+,.2f} "
            f"pnl_pct={reason_pnl_pct:+.2f}%"
        )
    print()
    print("Last 10 Records:")
    for record in records[-10:]:
        print(
            f"  {record.get('timestamp')} | {record.get('side')} {record.get('pair')} | "
            f"qty={record.get('quantity')} price={record.get('price')} | "
            f"{record.get('order_type')} | {record.get('reason')} | "
            f"pnl_pct={record.get('pnl_pct')}"
        )


if __name__ == "__main__":
    main()
