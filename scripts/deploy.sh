#!/bin/bash
set -e

if [ $# -lt 1 ]; then
  echo "Usage: bash deploy.sh /path/to/.env"
  exit 1
fi

ENV_SOURCE="$1"
REPO_DIR="$HOME/roostoo-bot"
REPO_URL="https://github.com/christiiscool/roostoo-bot.git"

if [ ! -f "$ENV_SOURCE" ]; then
  echo "Error: .env file not found at $ENV_SOURCE"
  exit 1
fi

sudo apt update
sudo apt install -y python3 python3-venv python3-pip git screen htop

if [ -d "$REPO_DIR/.git" ]; then
  cd "$REPO_DIR"
  git pull
else
  git clone "$REPO_URL" "$REPO_DIR"
  cd "$REPO_DIR"
fi

if [ ! -d "$REPO_DIR/venv" ]; then
  python3 -m venv "$REPO_DIR/venv"
fi

source "$REPO_DIR/venv/bin/activate"
pip install --upgrade pip
pip install -r requirements.txt

cp "$ENV_SOURCE" "$REPO_DIR/.env"

if ! grep -Eq '^ROOSTOO_API_KEY=.+$' "$REPO_DIR/.env"; then
  echo "Error: ROOSTOO_API_KEY is missing or empty in .env"
  exit 1
fi

if ! grep -Eq '^ROOSTOO_SECRET_KEY=.+$' "$REPO_DIR/.env"; then
  echo "Error: ROOSTOO_SECRET_KEY is missing or empty in .env"
  exit 1
fi

if ! python scripts/test_local.py; then
  echo "Tests failed. Bot NOT deployed."
  exit 1
fi

screen -S roostoo-bot -X quit 2>/dev/null || true
screen -dmS roostoo-bot bash -c 'source venv/bin/activate && python src/bot.py >> bot.log 2>&1'

sleep 5

if screen -list | grep -q '\.roostoo-bot[[:space:]]'; then
  echo "Bot deployed successfully. Monitor with: screen -r roostoo-bot"
else
  echo "Bot failed to start. Check bot.log"
  exit 1
fi

cat <<'EOF'
================================
ROOSTOO BOT DEPLOYMENT COMPLETE
================================
Repo:     ~/roostoo-bot
Log:      ~/roostoo-bot/bot.log
Monitor:  screen -r roostoo-bot
Detach:   Ctrl+A then D
Stop bot: screen -S roostoo-bot -X quit
================================
EOF
