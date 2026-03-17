"""Terminal dashboard for monitoring a running Roostoo bot."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional


PERF_PATTERN = re.compile(
    r"PERF tick=(?P<tick>\d+) mode=(?P<mode>\w+) portfolio=(?P<portfolio>-?\d+(?:\.\d+)?) "
    r"pnl=(?P<pnl>-?\d+(?:\.\d+)?) pnl_pct=(?P<pnl_pct>-?\d+(?:\.\d+)?) "
    r"trades=(?P<trades>\d+) drawdown_pct=(?P<drawdown>-?\d+(?:\.\d+)?)"
)
ORDER_PATTERN = re.compile(
    r"ORDER mode=(?P<mode>\w+) side=(?P<side>\w+) qty=(?P<qty>-?\d+(?:\.\d+)?) "
    r"pair=(?P<pair>[A-Z]+\/[A-Z]+) price=(?P<price>-?\d+(?:\.\d+)?)"
)
TIMESTAMP_PATTERN = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),")


def read_log(args: argparse.Namespace) -> str:
    if args.host:
        command = ["ssh"]
        if args.key_file:
            command.extend(["-i", args.key_file])
        destination = f"{args.user}@{args.host}" if args.user else args.host
        command.append(destination)
        command.append(f"tail -n 300 {args.log_file}")
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        return result.stdout

    log_path = Path(args.log_file)
    if not log_path.exists():
        return ""
    return log_path.read_text(encoding="utf-8", errors="ignore")


def parse_timestamp(line: str) -> Optional[datetime]:
    match = TIMESTAMP_PATTERN.match(line)
    if not match:
        return None
    return datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S")


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m"


def build_dashboard(log_text: str, interval: int) -> str:
    lines = [line for line in log_text.splitlines() if line.strip()]
    perf_lines = [line for line in lines if "PERF tick=" in line]
    order_lines = [line for line in lines if "ORDER mode=" in line]

    if not perf_lines:
        return (
            "=== ROOSTOO BOT MONITOR ===\n"
            "No performance data found yet.\n"
            "==========================="
        )

    first_perf = perf_lines[0]
    last_perf = perf_lines[-1]
    perf_match = PERF_PATTERN.search(last_perf)
    if perf_match is None:
        return (
            "=== ROOSTOO BOT MONITOR ===\n"
            "Latest performance line could not be parsed.\n"
            "==========================="
        )

    first_ts = parse_timestamp(first_perf) or datetime.now()
    last_ts = parse_timestamp(last_perf) or datetime.now()
    uptime = format_duration((last_ts - first_ts).total_seconds())

    last_trade_line = order_lines[-1] if order_lines else ""
    order_match = ORDER_PATTERN.search(last_trade_line) if last_trade_line else None
    last_trade_desc = "None"
    if order_match is not None:
        trade_ts = parse_timestamp(last_trade_line) or last_ts
        age = format_duration((datetime.now() - trade_ts).total_seconds())
        last_trade_desc = (
            f"{order_match.group('side')} {float(order_match.group('qty')):.6f} "
            f"{order_match.group('pair')} @ ${float(order_match.group('price')):,.2f} ({age} ago)"
        )

    next_tick = last_ts + timedelta(seconds=interval)

    return (
        "=== ROOSTOO BOT MONITOR ===\n"
        f"Uptime: {uptime} | Ticks: {perf_match.group('tick')} | Mode: {perf_match.group('mode')}\n"
        f"Portfolio: ${float(perf_match.group('portfolio')):,.2f} | "
        f"P&L: {float(perf_match.group('pnl')):+,.2f} ({float(perf_match.group('pnl_pct')):+.2f}%)\n"
        f"Peak: unavailable | Drawdown: -{abs(float(perf_match.group('drawdown'))):.2f}%\n"
        f"Last trade: {last_trade_desc}\n"
        f"Last tick: {last_ts.strftime('%H:%M:%S')} | Next tick: {next_tick.strftime('%H:%M:%S')}\n"
        "==========================="
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor a running Roostoo bot.")
    parser.add_argument("--host", help="Optional SSH host for a remote EC2 instance.")
    parser.add_argument("--user", help="Optional SSH username.")
    parser.add_argument("--key-file", help="Optional SSH private key path.")
    parser.add_argument("--log-file", default=os.getenv("LOG_FILE", "bot.log"))
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()

    try:
        while True:
            log_text = read_log(args)
            os.system("cls" if os.name == "nt" else "clear")
            print(build_dashboard(log_text, args.interval))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


if __name__ == "__main__":
    main()
