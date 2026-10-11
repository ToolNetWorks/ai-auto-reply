import asyncio
import logging
import signal
import sys
import time
from typing import Optional

from telethon import TelegramClient, events, functions, types

from activity import ActivityDetector, ActivityState, AwayMode
from ai_client import AIClient
from commands import CommandHandler
from config import (
    AI_API_KEY,
    AI_BASE_URL,
    AI_CONCURRENCY_LIMIT,
    AI_MAX_TOKENS,
    AI_MODEL,
    AI_TEMPERATURE,
    AI_TIMEOUT,
    LOGS_DIR,
    MANUAL_REPLY_PAUSE_SECONDS,
    TELEGRAM_API_HASH,
    TELEGRAM_API_ID,
    TELEGRAM_SESSION_NAME,
    validate_config,
)
from conversation import ConversationEngine
from database import Database, get_database
from safety import SafetyManager

# Configure logging
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "app.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("telegram_auto_reply")

# Validate configuration on import/run
validate_config(require_ai=True, require_session=True)

# Shared singletons
db = get_database()
activity_detector = ActivityDetector()
safety_manager = SafetyManager()
ai_client = AIClient(
    base_url=AI_BASE_URL,
    api_key=AI_API_KEY,
    model=AI_MODEL,
    timeout=AI_TIMEOUT,
    max_tokens=AI_MAX_TOKENS,
    temperature=AI_TEMPERATURE,
    concurrency_limit=AI_CONCURRENCY_LIMIT,
)
conversation_engine = ConversationEngine(ai_client)

client = TelegramClient(
    TELEGRAM_SESSION_NAME,
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
)

command_handler: Optional[CommandHandler] = None
my_id: int = 0
shutdown_event = asyncio.Event()


async def set_telegram_offline_status() -> None:
    """Explicitly tell Telegram servers not to mark this session as online."""
    try:
        await client(functions.account.UpdateStatusRequest(offline=True))
        logger.debug("Sent UpdateStatus(offline=True) to Telegram server")
    except Exception as e:
        logger.debug("Notice on UpdateStatus: %s", e)


async def background_offline_keepalive() -> None:
    """Periodically ensure Telegram session doesn't flag user as perpetually online."""
    while not shutdown_event.is_set():
        try:
            await asyncio.sleep(300)
            if not shutdown_event.is_set():
                await set_telegram_offline_status()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Error in background offline updater: %s", e)


async def background_pruning_job() -> None:
    """Run data retention cleanup every 6 hours."""
    while not shutdown_event.is_set():
        try:
            await asyncio.sleep(6 * 3600)
            if not shutdown_event.is_set():
                await db.prune_old_data(days_messages=30, days_logs=7)
                logger.info("Completed periodic database retention cleanup")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Error in pruning job: %s", e)


@client.on(events.Raw(types.UpdateReadHistoryInbox))
async def handle_read_history_inbox(update: types.UpdateReadHistoryInbox) -> None:
    """Triggered when the user reads incoming messages on their device."""
    peer_id = getattr(update.peer, "user_id", None) or getattr(update.peer, "chat_id", None)
    logger.info("User read messages in peer %s on device -> ACTIVE", peer_id)
    await activity_detector.record_user_activity("inbox_read", f"peer_{peer_id}", db)


@client.on(events.NewMessage(outgoing=True))
async def handle_outgoing_message(event: events.NewMessage.Event) -> None:
    """Handle outgoing messages sent from this account."""
    msg = event.message
    msg_id = msg.id

    # If the message was sent by AI auto-reply, ignore it
    if activity_detector.is_ai_message(msg_id):
        return

    chat_id = event.chat_id
    logger.info("Outgoing message from user in chat %s -> ACTIVE", chat_id)

    # 1. Record user activity and cancel pending tasks
    await activity_detector.record_user_activity("outgoing_msg", f"chat_{chat_id}", db)

    # 2. If it's a private chat, pause AI for this chat (user manually took over)
    if event.is_private and chat_id != my_id:
        await db.pause_chat(chat_id, MANUAL_REPLY_PAUSE_SECONDS)
        logger.info(
            "User actively replied to %s. Pausing AI for this chat for %ds",
            chat_id,
            MANUAL_REPLY_PAUSE_SECONDS,
        )

    # 3. Store in messages table
    await db.record_message(
        chat_id=chat_id,
        msg_id=msg_id,
        sender_id=my_id,
        is_outgoing=True,
        is_ai=False,
        text=event.raw_text or "",
        status="sent_by_user",
    )


@client.on(events.NewMessage(incoming=True))
async def handle_incoming_message(event: events.NewMessage.Event) -> None:
    """Handle incoming messages with safety checks and grace period timer."""
    chat_id = event.chat_id

    # Check for Saved Messages control commands
    if chat_id == my_id:
        text = (event.raw_text or "").strip()
        if text.startswith("/away") and command_handler:
            response = await command_handler.handle_command(
                text, event.sender_id, activity_detector, db
            )
            if response:
                await event.reply(response)
        return

    # Guard 1: Verify message eligibility
    can_process, reason = safety_manager.can_process_message_event(event, my_id)
    if not can_process:
        return

    sender = await event.get_sender()
    if sender is None or getattr(sender, "bot", False):
        return

    msg_id = event.message.id
    incoming_text = (event.raw_text or "").strip()

    # Guard 2: Skip empty messages or unparseable media without captions
    if not incoming_text:
        logger.debug("Skipping message with empty text from %s", chat_id)
        return

    # Guard 3: Deduplication
    if not await safety_manager.check_deduplication(chat_id, msg_id, db):
        logger.debug("Message %s from chat %s already processed", msg_id, chat_id)
        return

    # Record message in database
    await db.record_message(
        chat_id=chat_id,
        msg_id=msg_id,
        sender_id=sender.id,
        is_outgoing=False,
        is_ai=False,
        text=incoming_text,
        status="received",
    )

    # Guard 4: Initial rate limit and pause check
    is_safe, safety_reason = await safety_manager.check_reply_safety(chat_id, db)
    if not is_safe:
        logger.info("Safety check skipped reply to %s: %s", chat_id, safety_reason)
        await db.update_message_status(chat_id, msg_id, "ignored")
        return

    # Guard 5: Fail-safe state check
    current_state = activity_detector.get_state()
    if current_state != ActivityState.AWAY:
        logger.info(
            "Current activity state is %s (not AWAY). Postponing/Skipping immediate reply.",
            current_state.value,
        )

    # Schedule grace period countdown task
    reply_task = asyncio.create_task(
        _delayed_reply_worker(chat_id, event, incoming_text)
    )
    activity_detector.register_pending_task(chat_id, reply_task)


async def _delayed_reply_worker(
    chat_id: int, event: events.NewMessage.Event, incoming_text: str
) -> None:
    """Worker task that waits for the grace period and sends the reply if state remains AWAY."""
    msg_id = event.message.id
    try:
        # Wait grace period (default 120s)
        logger.info(
            "Waiting grace period (%ds) for chat %s msg_id=%s...",
            activity_detector.grace_period,
            chat_id,
            msg_id,
        )
        await asyncio.sleep(activity_detector.grace_period)

        # Acquire chat lock to prevent races
        chat_lock = safety_manager.get_chat_lock(chat_id)
        async with chat_lock:
            # 1. Fail-safe re-check: Must be AWAY
            if not activity_detector.is_away():
                logger.info(
                    "Cancelling auto-reply to %s: user state is %s",
                    chat_id,
                    activity_detector.get_state().value,
                )
                await db.update_message_status(chat_id, msg_id, "cancelled_user_active")
                return

            # 2. Re-check safety and manual pause
            is_safe, safety_reason = await safety_manager.check_reply_safety(chat_id, db)
            if not is_safe:
                logger.info("Safety check failed before sending to %s: %s", chat_id, safety_reason)
                await db.update_message_status(chat_id, msg_id, "ignored")
                return

            # 3. Typing indicator
            async with client.action(chat_id, "typing"):
                # 4. Generate AI response
                reply_text = await conversation_engine.generate_reply(
                    chat_id, incoming_text, db
                )
                if not reply_text:
                    logger.warning("No reply text returned by AI engine for %s", chat_id)
                    await db.update_message_status(chat_id, msg_id, "ai_failed")
                    return

                # 5. Final check before network call
                if not activity_detector.is_away():
                    logger.info("Abort sending to %s: state changed during generation", chat_id)
                    return

                # 6. Send reply
                sent_msg = await event.reply(reply_text)
                activity_detector.mark_ai_message(sent_msg.id)

                # 7. Record to DB
                await db.record_message(
                    chat_id=chat_id,
                    msg_id=sent_msg.id,
                    sender_id=my_id,
                    is_outgoing=True,
                    is_ai=True,
                    text=reply_text,
                    status="sent_by_ai",
                )
                await db.record_ai_reply(chat_id)
                await db.update_message_status(chat_id, msg_id, "replied")
                logger.info("Auto replied to chat_id=%s successfully", chat_id)

    except asyncio.CancelledError:
        logger.info("Delayed reply task for chat %s cancelled.", chat_id)
        await db.update_message_status(chat_id, msg_id, "cancelled")
    except Exception as e:
        logger.exception("Error in delayed reply worker for chat %s: %s", chat_id, e)
        await db.update_message_status(chat_id, msg_id, "error")
    finally:
        activity_detector.unregister_pending_task(chat_id)


bg_tasks: list[asyncio.Task] = []


async def main() -> None:
    global my_id, command_handler, bg_tasks

    logger.info("Initializing Telegram AI Auto Reply...")

    # 1. Initialize SQLite Database
    await db.init_db()

    # 2. Initialize Activity Detector state
    await activity_detector.init_from_db(db)

    # 3. Connect Telethon Client
    await client.connect()
    if not await client.is_user_authorized():
        logger.error("Telegram session is not authorized! Please verify session file.")
        sys.exit(1)

    me = await client.get_me()
    my_id = me.id
    command_handler = CommandHandler(my_id)

    logger.info(
        "Telegram Auto Reply connected as: %s %s (ID: %s)",
        me.first_name,
        me.last_name or "",
        me.id,
    )

    # 4. Sync initial user activity baseline if not already in DB
    if activity_detector.last_activity_time is None:
        try:
            last_ts: Optional[float] = None
            async for dialog in client.iter_dialogs(limit=25):
                msg = dialog.message
                if msg and getattr(msg, "out", False) and getattr(msg, "date", None):
                    ts = msg.date.timestamp()
                    if last_ts is None or ts > last_ts:
                        last_ts = ts
            if last_ts is not None:
                activity_detector.last_activity_time = last_ts
                await db.log_activity("sync_initial_outgoing", f"ts_{last_ts}")
                logger.info("Synced last user outgoing activity timestamp: %s", last_ts)
            else:
                baseline = time.time() - (activity_detector.grace_period + 60)
                activity_detector.last_activity_time = baseline
                await db.log_activity("sync_initial_baseline", f"ts_{baseline}")
                logger.info("Initialized baseline activity timestamp: %s", baseline)
        except Exception as e:
            logger.warning("Could not sync initial activity from dialogs: %s", e)

    # 5. Set offline status initially
    await set_telegram_offline_status()

    # 5. Launch background maintenance tasks
    bg_tasks = [
        asyncio.create_task(background_offline_keepalive()),
        asyncio.create_task(background_pruning_job()),
    ]

    logger.info(
        "Application running. Mode: %s, Current State: %s",
        activity_detector.mode.value,
        activity_detector.get_state().value,
    )

    await client.run_until_disconnected()


def handle_stop_signals(loop: asyncio.AbstractEventLoop) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))


async def shutdown() -> None:
    logger.info("Graceful shutdown requested...")
    shutdown_event.set()
    activity_detector.cancel_all_pending_tasks("Shutdown")

    for task in bg_tasks:
        if not task.done():
            task.cancel()
    if bg_tasks:
        await asyncio.gather(*bg_tasks, return_exceptions=True)

    await ai_client.close()
    if client.is_connected():
        await client.disconnect()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    handle_stop_signals(loop)
    try:
        loop.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        loop.run_until_complete(shutdown())
        loop.close()
