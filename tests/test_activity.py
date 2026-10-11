import asyncio
import time
import pytest
from activity import ActivityDetector, ActivityState, AwayMode
from database import Database


@pytest.fixture
def tmp_db(tmp_path):
    db_file = tmp_path / "test.db"
    return Database(db_file)


@pytest.mark.asyncio
async def test_1_active_does_not_reply(tmp_db):
    """Test 1: ACTIVE -> không trả lời."""
    await tmp_db.init_db()
    detector = ActivityDetector(grace_period=120)
    await detector.init_from_db(tmp_db)

    # Simulate recent user activity (10 seconds ago)
    await detector.record_user_activity("outgoing_msg", "test", tmp_db)
    
    assert detector.get_state() == ActivityState.ACTIVE
    assert detector.is_away() is False


@pytest.mark.asyncio
async def test_2_away_replies_when_conditions_met(tmp_db):
    """Test 2: AWAY -> trả lời khi đủ điều kiện."""
    await tmp_db.init_db()
    detector = ActivityDetector(grace_period=120)
    await detector.init_from_db(tmp_db)

    # Activity happened 200 seconds ago (> 120s grace period)
    detector.last_activity_time = time.time() - 200
    
    assert detector.get_state() == ActivityState.AWAY
    assert detector.is_away() is True


@pytest.mark.asyncio
async def test_3_unknown_does_not_reply(tmp_db):
    """Test 3: UNKNOWN -> không trả lời (fail-safe)."""
    await tmp_db.init_db()
    detector = ActivityDetector(grace_period=120)
    await detector.init_from_db(tmp_db)

    # Clean state with no past activity recorded
    detector.last_activity_time = None
    detector.mode = AwayMode.AUTO
    
    assert detector.get_state() == ActivityState.UNKNOWN
    assert detector.is_away() is False


@pytest.mark.asyncio
async def test_4_user_returns_cancels_pending_tasks(tmp_db):
    """Test 4: Người dùng quay lại -> hủy tác vụ chờ."""
    await tmp_db.init_db()
    detector = ActivityDetector(grace_period=120)
    await detector.init_from_db(tmp_db)

    executed = False

    async def dummy_reply_task():
        nonlocal executed
        await asyncio.sleep(10)
        executed = True

    task = asyncio.create_task(dummy_reply_task())
    detector.register_pending_task(12345, task)

    assert not task.done()

    # User activity detected (e.g., inbox read or outgoing message)
    await detector.record_user_activity("inbox_read", "peer_123", tmp_db)

    # Yield control to let cancellation take effect
    await asyncio.sleep(0.01)

    assert task.cancelled() or task.done()
    assert executed is False
    assert detector.get_state() == ActivityState.ACTIVE
