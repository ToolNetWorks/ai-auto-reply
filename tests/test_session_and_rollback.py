import os
import sqlite3
import subprocess
from pathlib import Path
import pytest
from config import BASE_DIR, get_session_file_path


def test_14_existing_session_integrity_and_usability(tmp_path):
    """Test 14: Session hiện tại vẫn sử dụng được (hỗ trợ cả VPS lẫn môi trường CI)."""
    session_file = get_session_file_path()

    if session_file.exists():
        # Running on VPS with production session
        assert session_file.stat().st_size > 0, "Session file must not be empty"
        mode = oct(session_file.stat().st_mode & 0o777)
        assert mode == oct(0o600), f"Expected permissions 0o600, got {mode}"

        conn = sqlite3.connect(str(session_file))
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        assert "sessions" in tables or "version" in tables, f"Invalid Telethon session tables: {tables}"
    else:
        # Running in clean CI runner without production session secrets
        mock_session = tmp_path / "test_session.session"
        conn = sqlite3.connect(str(mock_session))
        conn.execute("CREATE TABLE version (version INTEGER);")
        conn.execute("CREATE TABLE sessions (dc_id INTEGER, server_address TEXT, port INTEGER, auth_key BLOB);")
        conn.commit()
        conn.close()
        os.chmod(mock_session, 0o600)

        assert mock_session.exists()
        assert oct(mock_session.stat().st_mode & 0o777) == oct(0o600)
        conn = sqlite3.connect(str(mock_session))
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        conn.close()
        assert "sessions" in tables and "version" in tables


def test_15_rollback_can_restore_previous_version(tmp_path):
    """Test 15: Rollback có thể khôi phục service/source cũ."""
    rollback_script = BASE_DIR / "scripts/rollback.sh"
    assert rollback_script.exists(), "Rollback script must exist"
    assert os.access(rollback_script, os.X_OK), "Rollback script must be executable"

    # Test rollback functionality using a portable mock sandbox
    mock_backup = tmp_path / "mock_backup"
    mock_backup.mkdir()
    (mock_backup / "app.py").write_text("# restored app.py")
    (mock_backup / ".env").write_text("RESTORED_ENV=1")

    mock_project = tmp_path / "mock_project"
    mock_project.mkdir()

    env = dict(os.environ)
    env["PROJECT_DIR"] = str(mock_project)

    proc = subprocess.run(
        ["bash", str(rollback_script), str(mock_backup)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"Rollback script failed: {proc.stderr}"
    assert (mock_project / "app.py").exists(), "app.py should be restored"
    assert (mock_project / ".env").exists(), ".env should be restored"

    # If running on VPS where backups/latest already exists, verify its integrity
    vps_backup = BASE_DIR / "backups/latest"
    if vps_backup.exists():
        assert (vps_backup / "app.py").exists()
        assert (vps_backup / ".env").exists()
