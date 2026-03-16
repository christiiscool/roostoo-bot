[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

# Roostoo Bot

Roostoo Bot is an open-source Python scaffold for the SG vs HK Web3 Quant Trading Hackathon, designed for rapid experimentation on the Roostoo mock exchange while keeping architecture, risk controls, and deployment workflows clean and reproducible for a university team.

## Quickstart

```bash
git clone <your-repo-url>
cd roostoo-bot
python -m venv venv
venv\\Scripts\\activate
pip install -r requirements.txt
copy .env.example .env
python -m src.bot
```

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

## Strategy Summary

Strategy documentation will live in [STRATEGY.md](./STRATEGY.md). This placeholder will be replaced once the team finalizes signal design, parameter selection, and evaluation methodology.

## Risk Management

Risk controls will be documented after the initial scaffold phase. Expected coverage includes position sizing, drawdown controls, cooldown logic, and execution safety checks.

## Deployment

Deployment automation for AWS EC2 is provided in [scripts/deploy.sh](./scripts/deploy.sh). Use this script as the starting point for provisioning a remote runtime once the trading logic is ready.

## License

This project is released under the MIT License. See [LICENSE](./LICENSE) for details.
