[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

# Roostoo Bot

Roostoo Bot is Team155-alpha SMU's open-source entry for the SG vs HK Web3 Quant Trading Hackathon. The project is designed around disciplined, risk-adjusted execution on the Roostoo mock exchange, with a hybrid momentum plus mean-reversion signal stack, downside-aware risk controls, and infrastructure that is straightforward for judges to review ahead of March 28.

## Competition

- Event: SG vs HK Web3 Quant Trading Hackathon
- Team: Team155-alpha SMU
- Exchange: Roostoo mock exchange
- Objective: maximize risk-adjusted performance, with Sharpe, Sortino, and Calmar ratios prioritized over raw aggression

## Strategy Summary

The bot combines two complementary engines:

- Momentum: EMA crossover with RSI gating to participate when trend strength is present without chasing the most overextended moves.
- Mean Reversion: Bollinger-band and z-score logic to buy dislocations below fair value and sell stretched moves above the local mean.
- Aggregation: weighted voting blends both strategies so the bot can stay selective in noisy conditions and become more active only when daily trade frequency needs support.

## Architecture

```text
+-------------------+
|   .env config     |
+---------+---------+
          |
          v
+-------------------+       +-------------------+
|   src.bot         +------>+ strategy modules  |
| main trading loop |       | signal generation |
+---------+---------+       +---------+---------+
          |                           |
          v                           v
+-------------------+       +-------------------+
|  api.client       |       | risk.manager      |
| Roostoo wrapper   |<------+ execution guards  |
+---------+---------+       +-------------------+
          |
          v
+-------------------+
| Roostoo mock API  |
+-------------------+
```

## How To Run

```bash
git clone https://github.com/christiiscool/roostoo-bot.git
cd roostoo-bot
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
copy .env.example .env
python -m src.bot
```

For a dry-run smoke test before deployment:

```bash
python scripts/test_local.py
```

## Deployment

Deployment automation for AWS EC2 is provided in [scripts/deploy.sh](./scripts/deploy.sh). The script installs dependencies, validates credentials, runs a dry-run smoke test, and starts the bot in a detached `screen` session.

## Professional Review Checklist

- Open-source repository with clear structure and documentation
- Hybrid strategy implementation aligned with hackathon judging criteria
- Limit-first execution to reduce commission drag
- Dynamic risk sizing from live wallet balances
- Monitoring and deployment tooling for a production-style presentation

## License

This project is released under the MIT License. See [LICENSE](./LICENSE) for details.
