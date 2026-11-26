#!/usr/bin/env bash
# One-time setup for the NTF bot on a fresh Ubuntu server (Oracle Cloud
# "Always Free" VM or any other). Run it from inside the cloned repo:
#
#     bash deploy/install.sh
#
# It installs Python, the bot's packages and a service that keeps the bot
# running 24/7. It does NOT contain or ask for the bot token: you put that
# in /etc/ntf-bot.env yourself (see deploy/ORACLE.md).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$HOME/ntf-data"
ENV_FILE="/etc/ntf-bot.env"

echo "==> Installing Python"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip

echo "==> Installing the bot's packages"
python3 -m venv "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install --upgrade pip
"$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements.txt"

echo "==> Creating the data folder ($DATA_DIR)"
mkdir -p "$DATA_DIR"

if [ ! -f "$ENV_FILE" ]; then
  echo "==> Creating $ENV_FILE (you still need to put the token in it)"
  sudo tee "$ENV_FILE" > /dev/null <<EOF
DISCORD_BOT_TOKEN=PUT_YOUR_TOKEN_HERE
DISCORD_GUILD_IDS=
PRO_CLUBS_DB_PATH=$DATA_DIR/pro_clubs.db
EOF
  sudo chown root:root "$ENV_FILE"
  sudo chmod 600 "$ENV_FILE"
else
  echo "==> $ENV_FILE already exists, leaving it alone"
fi

echo "==> Installing the service"
sed -e "s|/home/ubuntu/ntf-pro-clubs-bot-|$REPO_DIR|g" -e "s|^User=ubuntu|User=$USER|" \
  "$REPO_DIR/deploy/ntf-bot.service" | sudo tee /etc/systemd/system/ntf-bot.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable ntf-bot

echo
echo "Done. Next:"
echo "  1. sudo nano $ENV_FILE      (paste the bot token, save)"
echo "  2. sudo systemctl start ntf-bot"
echo "  3. sudo journalctl -u ntf-bot -f      (watch it come online)"
