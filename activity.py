import asyncio
import logging
import time
from enum import Enum
from typing import Optional

from config import ACTIVITY_GRACE_PERIOD
from database import Database

logger = logging.getLogger(__name__)


class ActivityState(str, Enum):
    ACTIVE = "ACTIVE"
    AWAY = "AWAY"
    UNKNOWN = "UNKNOWN"


class AwayMode(str, Enum):
    AUTO = "auto"
    MANUAL_ON = "manual_on"
    MANUAL_OFF = "manual_off"


class ActivityDetector:
    def __init__(self, grace_period: int = ACTIVITY_GRACE_PERIOD):
        self.grace_period = grace_period
        self.mode = AwayMode.AUTO
        self.last_activity_time: Optional[float] = None
        self.last_activity_type: str = "none"
        self._pending_tasks: dict[int, asyncio.Task] = {}
        self._ai_msg_ids: set[int] = set()

    async def init_from_db(self, db: Database) -> None:
        """Load saved away mode and last recorded activity from database."""
        saved_mode_str = await db.get_setting("away_mode", AwayMode.AUTO.value)
        try:
            self.mode = AwayMode(saved_mode_str)
        except ValueError:
            self.mode = AwayMode.AUTO

        self.last_activity_time = await db.get_last_activity_time()
        logger.info(
            "ActivityDetector initialized: mode=%s, last_activity_time=%s, grace_period=%ss",
            self.mode.value,
            self.last_activity_time,
            self.grace_period,
        )

    async def set_mode(self, mode: AwayMode, db: Database) -> None:
        """Update away mode and persist in database."""
        self.mode = mode
        await db.set_setting("away_mode", mode.value)
        logger.info("Away mode set to %s", mode.value)
        if mode == AwayMode.MANUAL_OFF:
            self.cancel_all_pending_tasks("Mode changed to MANUAL_OFF")

    def get_state(self) -> ActivityState:
        """
        Evaluate current activity state:
        - MANUAL_OFF: strictly ACTIVE (AI disabled).
        - MANUAL_ON: AWAY, unless user was active within grace period.
        - AUTO:
            - If no activity has ever been recorded: UNKNOWN (fail-safe).
            - If time since last user activity < grace_period: ACTIVE.
            - If time since last user activity >= grace_period: AWAY.
        """
        if self.mode == AwayMode.MANUAL_OFF:
            return ActivityState.ACTIVE

        now = time.time()

        if self.mode == AwayMode.MANUAL_ON:
            if self.last_activity_time is not None:
                if now - self.last_activity_time < self.grace_period:
                    return ActivityState.ACTIVE
            return ActivityState.AWAY

        # Mode AUTO
        if self.last_activity_time is None:
            return ActivityState.UNKNOWN

        elapsed = now - self.last_activity_time
        if elapsed < self.grace_period:
            return ActivityState.ACTIVE

        return ActivityState.AWAY

    def is_away(self) -> bool:
        """Return True only if status is reliably confirmed as AWAY."""
        return self.get_state() == ActivityState.AWAY

    async def record_user_activity(
        self, activity_type: str, details: str, db: Database
    ) -> None:
        """
        Record a confirmed user activity (outgoing message from user, inbox read, command).
        Cancels any pending auto-reply tasks immediately.
        """
        now = time.time()
        self.last_activity_time = now
        self.last_activity_type = activity_type

        await db.log_activity(activity_type, details)
        cancelled_count = self.cancel_all_pending_tasks(
            f"User activity detected ({activity_type})"
        )
        logger.info(
            "User activity recorded: %s (cancelled %d pending replies)",
            activity_type,
            cancelled_count,
        )

    def mark_ai_message(self, msg_id: int) -> None:
        """Register a message ID sent by AI so it's not treated as manual user activity."""
        self._ai_msg_ids.add(msg_id)
        # Limit set size to avoid memory growth
        if len(self._ai_msg_ids) > 1000:
            # Keep the latest 500
            self._ai_msg_ids = set(list(self._ai_msg_ids)[-500:])

    def is_ai_message(self, msg_id: int) -> bool:
        return msg_id in self._ai_msg_ids

    def register_pending_task(self, chat_id: int, task: asyncio.Task) -> None:
        """Register an async reply task currently waiting out the grace period."""
        existing = self._pending_tasks.get(chat_id)
        if existing and not existing.done():
            existing.cancel()
        self._pending_tasks[chat_id] = task

    def unregister_pending_task(self, chat_id: int) -> None:
        self._pending_tasks.pop(chat_id, None)

    def cancel_chat_task(self, chat_id: int) -> bool:
        """Cancel a pending reply task for a specific chat."""
        task = self._pending_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def cancel_all_pending_tasks(self, reason: str = "") -> int:
        """Cancel all pending reply tasks."""
        cancelled = 0
        for chat_id, task in list(self._pending_tasks.items()):
            if not task.done():
                task.cancel()
                cancelled += 1
        self._pending_tasks.clear()
        if cancelled > 0 and reason:
            logger.info("Cancelled %d pending reply tasks. Reason: %s", cancelled, reason)
        return cancelled
