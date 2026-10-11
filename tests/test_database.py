import pytest
from database import Database
from safety import SafetyManager


@pytest.mark.asyncio
async def test_10_restart_deduplication_prevents_duplicate_replies(tmp_path):
    """Test 10: Restart → không gửi trùng."""
    db_file = tmp_path / "persistence.db"
    
    # 1. Run session 1
    db1 = Database(db_file)
    await db1.init_db()
    safety1 = SafetyManager()

    chat_id = 999
    msg_id = 12345

    # Process and mark message as replied
    await db1.record_message(
        chat_id=chat_id,
        msg_id=msg_id,
        sender_id=777,
        is_outgoing=False,
        is_ai=False,
        text="Hello",
        status="replied",
    )

    # 2. Simulate application restart: new Database instance connected to same file
    db2 = Database(db_file)
    await db2.init_db()
    safety2 = SafetyManager()

    # Deduplication check must detect that msg_id was already processed
    can_process = await safety2.check_deduplication(chat_id, msg_id, db2)
    assert can_process is False  # Duplicate prevented!


@pytest.mark.asyncio
async def test_database_factory_and_turso_repository(tmp_path):
    """Test get_database factory and TursoRepository backend operations."""
    from database import SQLiteRepository, TursoRepository, get_database

    # 1. Factory test
    sqlite_repo = get_database(provider="sqlite", db_path=tmp_path / "sqlite_test.db")
    assert isinstance(sqlite_repo, SQLiteRepository)

    turso_repo = get_database(provider="turso", turso_url="file:" + str(tmp_path / "turso_test.db"))
    assert isinstance(turso_repo, TursoRepository)

    # 2. Operations test on Turso backend
    await turso_repo.init_db()
    await turso_repo.set_setting("sync_key", "turso_val")
    val = await turso_repo.get_setting("sync_key")
    assert val == "turso_val"

    await turso_repo.record_message(
        chat_id=123,
        msg_id=456,
        sender_id=789,
        is_outgoing=False,
        is_ai=False,
        text="Turso message",
        status="received",
    )
    is_processed = await turso_repo.is_message_processed(123, 456)
    assert is_processed is False

    await turso_repo.update_message_status(123, 456, "replied")
    assert await turso_repo.is_message_processed(123, 456) is True

    await turso_repo.close()

