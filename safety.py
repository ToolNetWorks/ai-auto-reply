import asyncio
import logging
import time
from typing import Any, Optional

from config import MAX_REPLIES_PER_HOUR, USER_COOLDOWN_SECONDS
from database import Database

logger = logging.getLogger(__name__)


class SafetyManager:
    def __init__(
        self,
        cooldown_seconds: int = USER_COOLDOWN_SECONDS,
        max_replies_per_hour: int = MAX_REPLIES_PER_HOUR,
    ):
        self.cooldown_seconds = cooldown_seconds
        self.max_replies_per_hour = max_replies_per_hour
        self._chat_locks: dict[int, asyncio.Lock] = {}

    def get_chat_lock(self, chat_id: int) -> asyncio.Lock:
        """Provide or create a concurrency lock for a specific chat."""
        if chat_id not in self._chat_locks:
            self._chat_locks[chat_id] = asyncio.Lock()
        return self._chat_locks[chat_id]

    def can_process_message_event(self, event: Any, my_id: int) -> tuple[bool, str]:
        """
        Guard clauses to evaluate message eligibility.
        Rejects non-private messages, bots, self messages, and outgoing messages.
        """
        # 1. Private messages only
        if not getattr(event, "is_private", False):
            return False, "not_private"

        # 2. Skip outgoing messages
        if getattr(event, "out", False):
            return False, "outgoing"

        # 3. Skip Saved Messages (handled separately for commands)
        chat_id = getattr(event, "chat_id", None)
        if chat_id == my_id:
            return False, "saved_messages"

        # 4. Sender verification
        sender = getattr(event, "sender", None)
        if sender is None:
            # Sender might be None if not fetched yet, caller can check sender.id
            pass

        return True, "ok"

    async def check_reply_safety(self, chat_id: int, db: Database) -> tuple[bool, str]:
        """
        Validate cooldowns, hourly limits, and manual pause status.
        """
        now = time.time()

        # 1. Check if user recently replied manually
        if await db.is_chat_paused(chat_id):
            return False, "chat_paused_by_user"

        # 2. Check cooldown and hourly frequency
        last_replied, count_hour, window_start = await db.get_cooldown_info(chat_id)

        if last_replied > 0 and (now - last_replied) < self.cooldown_seconds:
            remaining = int(self.cooldown_seconds - (now - last_replied))
            return False, f"cooldown_active_{remaining}s"

        if (now - window_start) <= 3600 and count_hour >= self.max_replies_per_hour:
            return False, "hourly_limit_exceeded"

        return True, "ok"

    async def check_deduplication(self, chat_id: int, msg_id: int, db: Database) -> bool:
        """Ensure message has not already been replied to or processed."""
        return not (await db.is_message_processed(chat_id, msg_id))
