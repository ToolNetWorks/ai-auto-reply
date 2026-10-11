#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/telegram-auto-reply}"
BACKUP_DIR="${PROJECT_DIR}/backups/$(date +%Y%m%d_%H%M%S)"

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

echo "Starting backup to ${BACKUP_DIR}..."

# Backup app.py
if [ -f "${PROJECT_DIR}/app.py" ]; then
    cp -p "${PROJECT_DIR}/app.py" "${BACKUP_DIR}/app.py"
    chmod 600 "${BACKUP_DIR}/app.py"
    echo "Backed up app.py"
fi

# Backup .env
if [ -f "${PROJECT_DIR}/.env" ]; then
    cp -p "${PROJECT_DIR}/.env" "${BACKUP_DIR}/.env"
    chmod 600 "${BACKUP_DIR}/.env"
    echo "Backed up .env"
fi

# Backup sqlite if exists
if [ -f "${PROJECT_DIR}/data/auto_reply.db" ]; then
    python3 -c "import sqlite3; src=sqlite3.connect('${PROJECT_DIR}/data/auto_reply.db'); dst=sqlite3.connect('${BACKUP_DIR}/auto_reply.db'); src.backup(dst); dst.close(); src.close()"
    chmod 600 "${BACKUP_DIR}/auto_reply.db"
    echo "Backed up auto_reply.db"
fi

# Backup systemd service if exists
if [ -f "/etc/systemd/system/telegram-auto-reply.service" ]; then
    cp -p "/etc/systemd/system/telegram-auto-reply.service" "${BACKUP_DIR}/telegram-auto-reply.service"
    chmod 600 "${BACKUP_DIR}/telegram-auto-reply.service"
    echo "Backed up systemd service"
fi

# Create a fixed link to the latest backup
rm -rf "${PROJECT_DIR}/backups/latest"
ln -s "${BACKUP_DIR}" "${PROJECT_DIR}/backups/latest"

echo "Backup completed successfully at ${BACKUP_DIR}"
