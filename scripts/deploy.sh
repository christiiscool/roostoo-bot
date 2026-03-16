#!/usr/bin/env bash

set -euo pipefail

REPO_URL="YOUR_GITHUB_REPO_URL"
PROJECT_DIR="roostoo-bot"
SESSION_NAME="roostoo-bot"

# Install system dependencies needed to run the bot on a fresh Ubuntu EC2 host.
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git screen

# Clone the repository if it is not already present on the instance.
if [ ! -d "${PROJECT_DIR}" ]; then
  git clone "${REPO_URL}" "${PROJECT_DIR}"
fi

cd "${PROJECT_DIR}"

# Create and activate an isolated Python environment, then install Python packages.
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Refuse to start without runtime credentials and bot configuration.
if [ ! -f ".env" ]; then
  echo "Error: .env file is missing. Create .env before deploying the bot." >&2
  exit 1
fi

# Launch the trading bot inside a detached screen session so it survives SSH disconnects.
if screen -list | grep -q "\.${SESSION_NAME}[[:space:]]"; then
  screen -S "${SESSION_NAME}" -X quit
fi
screen -dmS "${SESSION_NAME}" bash -c "source venv/bin/activate && python3 -m src.bot"

# Print the follow-up command needed to attach to the running bot session.
echo "Bot deployed. Run 'screen -r roostoo-bot' to monitor."
