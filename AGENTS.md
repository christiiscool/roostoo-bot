# AGENTS.md

- Always load `.env` before running the bot or related scripts.
- Never commit `.env`; commit only `.env.example`.
- Always run `python -m pytest tests/` before marking a task done.
- Signal naming convention: `1=BUY`, `-1=SELL`, `0=HOLD`.
