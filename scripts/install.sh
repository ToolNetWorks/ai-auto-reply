#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/root/telegram-auto-reply"
cd "${PROJECT_DIR}"

echo "=== Installing Telegram AI Auto Reply ==="

# 1. Virtual Environment
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

echo "Installing / upgrading dependencies..."
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 2. Directories and permissions
mkdir -p data logs backups
chmod 700 backups data logs
if [ -f ".env" ]; then
    chmod 600 .env
fi
if [ -f "telegram_auto_reply.session" ]; then
    chmod 600 telegram_auto_reply.session
fi

# 3. Systemd service setup
SERVICE_SRC="${PROJECT_DIR}/telegram-auto-reply.service"
SERVICE_DST="/etc/systemd/system/telegram-auto-reply.service"

if [ -f "${SERVICE_SRC}" ]; then
    echo "Installing systemd service..."
    cp "${SERVICE_SRC}" "${SERVICE_DST}"
    systemctl daemon-reload
    systemctl enable telegram-auto-reply.service
    echo "Service enabled."
fi

echo "Installation complete."
