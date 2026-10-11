#!/usr/bin/env bash
set -uo pipefail

PROJECT_DIR="/root/telegram-auto-reply"
cd "${PROJECT_DIR}"

echo "=== Telegram AI Auto Reply Health Check ==="

# 1. Check systemd service status
echo -n "1. Service status: "
if systemctl is-active --quiet telegram-auto-reply.service; then
    echo "ACTIVE (Running)"
else
    echo "INACTIVE or FAILED"
fi

# 2. Check running process
echo -n "2. Python Process: "
PID=$(pgrep -f "python.*app.py" || true)
if [ -n "${PID}" ]; then
    echo "Running with PID ${PID}"
else
    echo "Not running"
fi

# 3. Check SQLite database
echo -n "3. Database check: "
if [ -f "data/auto_reply.db" ]; then
    DB_INFO=$(python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('data/auto_reply.db')
    c = conn.cursor()
    c.execute('SELECT MAX(version) FROM schema_migrations;')
    ver = c.fetchone()[0]
    c.execute('PRAGMA journal_mode;')
    mode = c.fetchone()[0]
    conn.close()
    print(f'OK (Version {ver}, Mode {mode.upper()})')
except Exception as e:
    print(f'Error: {e}')
")
    echo "${DB_INFO}"
else
    echo "Database file not found yet"
fi

# 4. Check ToolNet API connectivity
echo -n "4. ToolNet API connection: "
API_RES=$(.venv/bin/python -c "
import os, sys
from dotenv import load_dotenv
import httpx
load_dotenv()
key = os.getenv('AI_API_KEY')
url = os.getenv('AI_BASE_URL', 'https://api.toolnet.tech/v1')
if not key:
    print('AI_API_KEY missing')
    sys.exit(1)
try:
    with httpx.Client(timeout=5.0) as client:
        resp = client.get(f'{url}/models', headers={'Authorization': f'Bearer {key}'})
        if resp.status_code == 200:
            print('Connected (HTTP 200)')
            sys.exit(0)
        else:
            print(f'Error HTTP {resp.status_code}')
            sys.exit(1)
except Exception as e:
    print(f'Exception: {e}')
    sys.exit(1)
" 2>&1)

echo "${API_RES}"

# 5. Show recent journal log
echo ""
echo "=== Recent Logs (Last 5 lines) ==="
journalctl -u telegram-auto-reply.service -n 5 --no-pager 2>/dev/null || true
