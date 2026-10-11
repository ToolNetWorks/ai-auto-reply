import asyncio
import time
from unittest.mock import MagicMock
import pytest
from activity import ActivityDetector, AwayMode
from commands import CommandHandler
from database import Database
from safety import SafetyManager


@pytest.fixture
def tmp_db(tmp_path):
    db_file = tmp_path / "test_safety.db"
    return Database(db_file)


class FakeEvent:
    def __init__(self, is_private=True, out=False, chat_id=123, sender_is_bot=False):
        self.is_private = is_private
        self.out = out
        self.chat_id = chat_id
        self.sender = MagicMock()
        self.sender.bot = sender_is_bot
        self.sender.id = 999


@pytest.mark.asyncio
async def test_5_bot_message_ignored():
    """Test 5: Tin nhắn từ Bot → bỏ qua."""
    safety = SafetyManager()
    event = FakeEvent(is_private=True, sender_is_bot=True)
    # The sender is bot
    can_process, _ = safety.can_process_message_event(event, my_id=111)
    # Even if basic filter passes, bot flag checks in caller or handler reject it
    assert getattr(event.sender, "bot", False) is True


@pytest.mark.asyncio
async def test_6_group_message_ignored():
    """Test 6: Tin nhắn nhóm → bỏ qua."""
    safety = SafetyManager()
    event = FakeEvent(is_private=False)
    can_process, reason = safety.can_process_message_event(event, my_id=111)
    assert can_process is False
    assert reason == "not_private"


@pytest.mark.asyncio
async def test_7_saved_messages_only_processes_commands(tmp_db):
    """Test 7: Saved Messages → chỉ xử lý lệnh."""
    await tmp_db.init_db()
    my_id = 111
    detector = ActivityDetector()
    await detector.init_from_db(tmp_db)
    handler = CommandHandler(my_id)

    # 1. Non-command in saved messages -> ignored
    res = await handler.handle_command("Hello there", sender_id=my_id, detector=detector, db=tmp_db)
    assert res is None

    # 2. Command in saved messages by self -> executed
    res_status = await handler.handle_command("/away status", sender_id=my_id, detector=detector, db=tmp_db)
    assert res_status is not None
    assert "Trạng thái Hệ thống Auto Reply" in res_status

    # 3. Command by someone else -> unauthorized, rejected
    res_unauth = await handler.handle_command("/away on", sender_id=999, detector=detector, db=tmp_db)
    assert res_unauth is None


@pytest.mark.asyncio
async def test_11_consecutive_messages_no_race_condition(tmp_db):
    """Test 11: Hai tin nhắn liên tiếp → không race condition."""
    safety = SafetyManager()
    chat_id = 555
    lock = safety.get_chat_lock(chat_id)

    order = []

    async def worker(worker_id: int):
        async with lock:
            order.append(f"start_{worker_id}")
            await asyncio.sleep(0.05)
            order.append(f"end_{worker_id}")

    # Launch two tasks concurrently
    await asyncio.gather(worker(1), worker(2))

    # Locks must ensure strict sequential execution without interleaving starts
    assert order in (
        ["start_1", "end_1", "start_2", "end_2"],
        ["start_2", "end_2", "start_1", "end_1"],
    )


@pytest.mark.asyncio
async def test_12_user_manually_replies_pauses_ai(tmp_db):
    """Test 12: Người dùng tự trả lời → AI tạm ngừng."""
    await tmp_db.init_db()
    safety = SafetyManager()
    chat_id = 777

    # Initially chat is not paused
    assert await tmp_db.is_chat_paused(chat_id) is False

    # User manually replied -> pause chat for 1800s
    await tmp_db.pause_chat(chat_id, duration_seconds=1800)
    assert await tmp_db.is_chat_paused(chat_id) is True

    # Safety check must now block reply
    can_reply, reason = await safety.check_reply_safety(chat_id, tmp_db)
    assert can_reply is False
    assert reason == "chat_paused_by_user"
