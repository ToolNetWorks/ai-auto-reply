#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/root/telegram-auto-reply"
BACKUP_TARGET="${1:-${PROJECT_DIR}/backups/latest}"

if [ ! -d "${BACKUP_TARGET}" ]; then
    echo "Error: Backup directory ${BACKUP_TARGET} does not exist!"
    exit 1
fi

echo "Rolling back from ${BACKUP_TARGET}..."

# Stop systemd service if running
if systemctl is-active --quiet telegram-auto-reply.service 2>/dev/null; then
    echo "Stopping telegram-auto-reply service..."
    systemctl stop telegram-auto-reply.service
fi

# Restore app.py
if [ -f "${BACKUP_TARGET}/app.py" ]; then
    cp -p "${BACKUP_TARGET}/app.py" "${PROJECT_DIR}/app.py"
    chmod 644 "${PROJECT_DIR}/app.py"
    echo "Restored app.py"
fi

# Restore .env
if [ -f "${BACKUP_TARGET}/.env" ]; then
    cp -p "${BACKUP_TARGET}/.env" "${PROJECT_DIR}/.env"
    chmod 600 "${PROJECT_DIR}/.env"
    echo "Restored .env"
fi

# Restore database if in backup
if [ -f "${BACKUP_TARGET}/auto_reply.db" ]; then
    mkdir -p "${PROJECT_DIR}/data"
    cp -p "${BACKUP_TARGET}/auto_reply.db" "${PROJECT_DIR}/data/auto_reply.db"
    chmod 600 "${PROJECT_DIR}/data/auto_reply.db"
    echo "Restored auto_reply.db"
fi

echo "Rollback completed."
echo "You can start the previous version manually with: cd ${PROJECT_DIR} && .venv/bin/python app.py"
