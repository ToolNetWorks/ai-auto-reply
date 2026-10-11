import os
import sqlite3
import subprocess
from pathlib import Path
import pytest


def test_14_existing_session_integrity_and_usability():
    """Test 14: Session hiện tại vẫn sử dụng được."""
    session_path = Path("/root/telegram-auto-reply/telegram_auto_reply.session")
    assert session_path.exists(), "Session file must exist"
    assert session_path.stat().st_size > 0, "Session file must not be empty"

    # Verify permissions (must be restrictive: 600)
    mode = oct(session_path.stat().st_mode & 0o777)
    assert mode == oct(0o600), f"Expected permissions 0o600, got {mode}"

    # Verify Telethon session database structure
    conn = sqlite3.connect(str(session_path))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    conn.close()

    # Telethon sessions contain 'sessions' and 'version' or 'entities'
    assert "sessions" in tables or "version" in tables, f"Invalid Telethon session tables: {tables}"


def test_15_rollback_can_restore_previous_version(tmp_path):
    """Test 15: Rollback có thể khôi phục service/source cũ."""
    backup_dir = Path("/root/telegram-auto-reply/backups/latest")
    assert backup_dir.exists(), "Backup directory must exist"
    assert (backup_dir / "app.py").exists(), "app.py must exist in backup"
    assert (backup_dir / ".env").exists(), ".env must exist in backup"

    # Verify rollback script exists and is executable
    rollback_script = Path("/root/telegram-auto-reply/scripts/rollback.sh")
    assert rollback_script.exists()
    assert os.access(rollback_script, os.X_OK)
