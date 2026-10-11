import asyncio
import os
import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Optional
import aiosqlite

from config import (
    DATABASE_PROVIDER,
    SQLITE_DB_PATH,
    TURSO_AUTH_TOKEN,
    TURSO_DATABASE_URL,
)


class BaseDatabaseRepository(ABC):
    """Abstract Repository interface for database persistence (SQLite / Turso)."""

    @abstractmethod
    async def init_db(self) -> None:
        """Run schema migrations and initialize tables."""
        pass

    @abstractmethod
    async def get_setting(self, key: str, default: str = "") -> str:
        pass

    @abstractmethod
    async def set_setting(self, key: str, value: str) -> None:
        pass

    @abstractmethod
    async def log_activity(self, activity_type: str, details: str = "") -> None:
        pass

    @abstractmethod
    async def get_last_activity_time(self) -> Optional[float]:
        pass

    @abstractmethod
    async def record_message(
        self,
        chat_id: int,
        msg_id: int,
        sender_id: int,
        is_outgoing: bool,
        is_ai: bool,
        text: str,
        status: str = "received",
    ) -> None:
        pass

    @abstractmethod
    async def is_message_processed(self, chat_id: int, msg_id: int) -> bool:
        pass

    @abstractmethod
    async def update_message_status(self, chat_id: int, msg_id: int, status: str) -> None:
        pass

    @abstractmethod
    async def get_recent_messages(self, chat_id: int, limit: int = 20) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    async def is_chat_paused(self, chat_id: int) -> bool:
        pass

    @abstractmethod
    async def pause_chat(self, chat_id: int, duration_seconds: int) -> None:
        pass

    @abstractmethod
    async def unpause_chat(self, chat_id: int) -> None:
        pass

    @abstractmethod
    async def get_cooldown_info(self, chat_id: int) -> tuple[float, int, float]:
        pass

    @abstractmethod
    async def record_ai_reply(self, chat_id: int) -> None:
        pass

    @abstractmethod
    async def log_ai_request(
        self,
        chat_id: int,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        status: str,
        error: str = "",
    ) -> None:
        pass

    @abstractmethod
    async def get_stats(self) -> dict[str, Any]:
        pass

    @abstractmethod
    async def prune_old_data(self, days_messages: int = 30, days_logs: int = 7) -> None:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass


class SQLiteRepository(BaseDatabaseRepository):
    """SQLite implementation using aiosqlite, with WAL mode and busy timeout."""

    def __init__(self, db_path: Path = SQLITE_DB_PATH):
        self.db_path = db_path
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def _connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode = WAL;")
            await conn.execute("PRAGMA busy_timeout = 5000;")
            await conn.execute("PRAGMA synchronous = NORMAL;")
            await conn.execute("PRAGMA foreign_keys = ON;")
            conn.row_factory = aiosqlite.Row
            yield conn

    async def init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        async with self._lock:
            async with self._connection() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at REAL NOT NULL
                    );
                    """
                )
                await conn.commit()

                cursor = await conn.execute("SELECT MAX(version) FROM schema_migrations")
                row = await cursor.fetchone()
                current_version = row[0] if (row and row[0] is not None) else 0

                if current_version < 1:
                    await self._apply_migration_1(conn)

        if self.db_path.exists():
            try:
                os.chmod(self.db_path, 0o600)
            except OSError:
                pass

    async def _apply_migration_1(self, conn: aiosqlite.Connection) -> None:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_type TEXT NOT NULL,
                details TEXT,
                timestamp REAL NOT NULL
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_activity_timestamp 
            ON user_activity(timestamp);
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                chat_id INTEGER PRIMARY KEY,
                first_seen REAL NOT NULL,
                last_msg_at REAL NOT NULL,
                is_paused INTEGER DEFAULT 0,
                pause_until REAL DEFAULT 0,
                reply_count_hour INTEGER DEFAULT 0,
                hour_window_start REAL DEFAULT 0
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                msg_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                is_outgoing INTEGER NOT NULL,
                is_ai INTEGER NOT NULL,
                text TEXT NOT NULL,
                timestamp REAL NOT NULL,
                status TEXT NOT NULL,
                UNIQUE(chat_id, msg_id)
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_chat_timestamp 
            ON messages(chat_id, timestamp);
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                latency_ms REAL NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                created_at REAL NOT NULL
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ai_requests_created 
            ON ai_requests(created_at);
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reply_cooldowns (
                chat_id INTEGER PRIMARY KEY,
                last_replied_at REAL NOT NULL,
                reply_count_hour INTEGER DEFAULT 0,
                hour_window_start REAL NOT NULL
            );
            """
        )
        await conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (1, ?)",
            (time.time(),),
        )
        await conn.execute(
            "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("away_mode", "auto", time.time()),
        )
        await conn.commit()

    async def get_setting(self, key: str, default: str = "") -> str:
        async with self._connection() as conn:
            cursor = await conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = await cursor.fetchone()
            return str(row["value"]) if row else default

    async def set_setting(self, key: str, value: str) -> None:
        now = time.time()
        async with self._lock:
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO settings (key, value, updated_at) 
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (key, value, now),
                )
                await conn.commit()

    async def log_activity(self, activity_type: str, details: str = "") -> None:
        now = time.time()
        async with self._lock:
            async with self._connection() as conn:
                await conn.execute(
                    "INSERT INTO user_activity (activity_type, details, timestamp) VALUES (?, ?, ?)",
                    (activity_type, details, now),
                )
                await conn.commit()

    async def get_last_activity_time(self) -> Optional[float]:
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT timestamp FROM user_activity ORDER BY timestamp DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return float(row["timestamp"]) if row else None

    async def record_message(
        self,
        chat_id: int,
        msg_id: int,
        sender_id: int,
        is_outgoing: bool,
        is_ai: bool,
        text: str,
        status: str = "received",
    ) -> None:
        now = time.time()
        async with self._lock:
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO messages 
                    (chat_id, msg_id, sender_id, is_outgoing, is_ai, text, timestamp, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chat_id,
                        msg_id,
                        sender_id,
                        1 if is_outgoing else 0,
                        1 if is_ai else 0,
                        text,
                        now,
                        status,
                    ),
                )
                await conn.execute(
                    """
                    INSERT INTO conversations (chat_id, first_seen, last_msg_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET last_msg_at = excluded.last_msg_at
                    """,
                    (chat_id, now, now),
                )
                await conn.commit()

    async def is_message_processed(self, chat_id: int, msg_id: int) -> bool:
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT status FROM messages WHERE chat_id = ? AND msg_id = ?",
                (chat_id, msg_id),
            )
            row = await cursor.fetchone()
            return (row["status"] in ("replied", "ignored", "sent_by_ai")) if row else False

    async def update_message_status(self, chat_id: int, msg_id: int, status: str) -> None:
        async with self._lock:
            async with self._connection() as conn:
                await conn.execute(
                    "UPDATE messages SET status = ? WHERE chat_id = ? AND msg_id = ?",
                    (status, chat_id, msg_id),
                )
                await conn.commit()

    async def get_recent_messages(self, chat_id: int, limit: int = 20) -> list[dict[str, Any]]:
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT sender_id, is_outgoing, is_ai, text, timestamp 
                FROM messages 
                WHERE chat_id = ? 
                ORDER BY timestamp DESC LIMIT ?
                """,
                (chat_id, limit),
            )
            rows = await cursor.fetchall()
            messages = [dict(row) for row in rows]
            messages.reverse()
            return messages

    async def is_chat_paused(self, chat_id: int) -> bool:
        now = time.time()
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT is_paused, pause_until FROM conversations WHERE chat_id = ?",
                (chat_id,),
            )
            row = await cursor.fetchone()
            if not row or not row["is_paused"]:
                return False
            return bool(row["pause_until"] > now)

    async def pause_chat(self, chat_id: int, duration_seconds: int) -> None:
        now = time.time()
        until = now + duration_seconds
        async with self._lock:
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO conversations (chat_id, first_seen, last_msg_at, is_paused, pause_until)
                    VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET is_paused = 1, pause_until = excluded.pause_until
                    """,
                    (chat_id, now, now, until),
                )
                await conn.commit()

    async def unpause_chat(self, chat_id: int) -> None:
        async with self._lock:
            async with self._connection() as conn:
                await conn.execute(
                    "UPDATE conversations SET is_paused = 0, pause_until = 0 WHERE chat_id = ?",
                    (chat_id,),
                )
                await conn.commit()

    async def get_cooldown_info(self, chat_id: int) -> tuple[float, int, float]:
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT last_replied_at, reply_count_hour, hour_window_start FROM reply_cooldowns WHERE chat_id = ?",
                (chat_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return (0.0, 0, 0.0)
            return (
                float(row["last_replied_at"]),
                int(row["reply_count_hour"]),
                float(row["hour_window_start"]),
            )

    async def record_ai_reply(self, chat_id: int) -> None:
        now = time.time()
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    "SELECT last_replied_at, reply_count_hour, hour_window_start FROM reply_cooldowns WHERE chat_id = ?",
                    (chat_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    await conn.execute(
                        """
                        INSERT INTO reply_cooldowns (chat_id, last_replied_at, reply_count_hour, hour_window_start)
                        VALUES (?, ?, 1, ?)
                        """,
                        (chat_id, now, now),
                    )
                else:
                    hour_window_start = float(row["hour_window_start"])
                    if now - hour_window_start > 3600:
                        new_count = 1
                        new_window = now
                    else:
                        new_count = int(row["reply_count_hour"]) + 1
                        new_window = hour_window_start

                    await conn.execute(
                        """
                        UPDATE reply_cooldowns 
                        SET last_replied_at = ?, reply_count_hour = ?, hour_window_start = ?
                        WHERE chat_id = ?
                        """,
                        (now, new_count, new_window, chat_id),
                    )
                await conn.commit()

    async def log_ai_request(
        self,
        chat_id: int,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        status: str,
        error: str = "",
    ) -> None:
        now = time.time()
        async with self._lock:
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO ai_requests 
                    (chat_id, model, prompt_tokens, completion_tokens, latency_ms, status, error, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (chat_id, model, prompt_tokens, completion_tokens, latency_ms, status, error, now),
                )
                await conn.commit()

    async def get_stats(self) -> dict[str, Any]:
        async with self._connection() as conn:
            c1 = await conn.execute("SELECT COUNT(*) FROM messages WHERE is_outgoing = 0")
            total_incoming = (await c1.fetchone())[0]

            c2 = await conn.execute("SELECT COUNT(*) FROM messages WHERE is_ai = 1")
            total_ai_replies = (await c2.fetchone())[0]

            c3 = await conn.execute(
                "SELECT SUM(prompt_tokens), SUM(completion_tokens), COUNT(*) FROM ai_requests WHERE status = 'success'"
            )
            ai_row = await c3.fetchone()
            prompt_toks = ai_row[0] or 0
            comp_toks = ai_row[1] or 0

            c4 = await conn.execute("SELECT COUNT(*) FROM ai_requests WHERE status != 'success'")
            api_errors = (await c4.fetchone())[0]

            now = time.time()
            c5 = await conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE is_paused = 1 AND pause_until > ?",
                (now,),
            )
            paused_chats = (await c5.fetchone())[0]

            return {
                "total_incoming": total_incoming,
                "total_ai_replies": total_ai_replies,
                "prompt_tokens": prompt_toks,
                "completion_tokens": comp_toks,
                "api_errors": api_errors,
                "paused_chats": paused_chats,
            }

    async def prune_old_data(self, days_messages: int = 30, days_logs: int = 7) -> None:
        now = time.time()
        msg_cutoff = now - (days_messages * 86400)
        logs_cutoff = now - (days_logs * 86400)
        async with self._lock:
            async with self._connection() as conn:
                await conn.execute("DELETE FROM messages WHERE timestamp < ?", (msg_cutoff,))
                await conn.execute("DELETE FROM user_activity WHERE timestamp < ?", (logs_cutoff,))
                await conn.execute("DELETE FROM ai_requests WHERE created_at < ?", (logs_cutoff,))
                await conn.commit()

    async def close(self) -> None:
        pass


class TursoRepository(BaseDatabaseRepository):
    """Turso / libSQL cloud implementation using libsql_client."""

    def __init__(self, url: str = TURSO_DATABASE_URL, auth_token: str = TURSO_AUTH_TOKEN):
        import libsql_client

        self.url = url
        self.auth_token = auth_token
        self._client: Optional[libsql_client.Client] = None
        self._lock = asyncio.Lock()

    def _get_client(self):
        import libsql_client

        if self._client is None:
            self._client = libsql_client.create_client(
                url=self.url, auth_token=self.auth_token or None
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None

    async def init_db(self) -> None:
        client = self._get_client()
        await client.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at REAL NOT NULL
            );
            """
        )
        rs = await client.execute("SELECT MAX(version) as ver FROM schema_migrations")
        row = rs.rows[0] if rs.rows else None
        current_version = row["ver"] if (row and row["ver"] is not None) else 0

        if current_version < 1:
            await self._apply_migration_1()

    async def _apply_migration_1(self) -> None:
        client = self._get_client()
        statements = [
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS user_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_type TEXT NOT NULL,
                details TEXT,
                timestamp REAL NOT NULL
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON user_activity(timestamp);",
            """
            CREATE TABLE IF NOT EXISTS conversations (
                chat_id INTEGER PRIMARY KEY,
                first_seen REAL NOT NULL,
                last_msg_at REAL NOT NULL,
                is_paused INTEGER DEFAULT 0,
                pause_until REAL DEFAULT 0,
                reply_count_hour INTEGER DEFAULT 0,
                hour_window_start REAL DEFAULT 0
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                msg_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                is_outgoing INTEGER NOT NULL,
                is_ai INTEGER NOT NULL,
                text TEXT NOT NULL,
                timestamp REAL NOT NULL,
                status TEXT NOT NULL,
                UNIQUE(chat_id, msg_id)
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_messages_chat_timestamp ON messages(chat_id, timestamp);",
            """
            CREATE TABLE IF NOT EXISTS ai_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                latency_ms REAL NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                created_at REAL NOT NULL
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_ai_requests_created ON ai_requests(created_at);",
            """
            CREATE TABLE IF NOT EXISTS reply_cooldowns (
                chat_id INTEGER PRIMARY KEY,
                last_replied_at REAL NOT NULL,
                reply_count_hour INTEGER DEFAULT 0,
                hour_window_start REAL NOT NULL
            );
            """,
        ]
        for stmt in statements:
            await client.execute(stmt)

        now = time.time()
        await client.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (1, ?)", [now]
        )
        await client.execute(
            "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES ('away_mode', 'auto', ?)",
            [now],
        )

    async def get_setting(self, key: str, default: str = "") -> str:
        client = self._get_client()
        rs = await client.execute("SELECT value FROM settings WHERE key = ?", [key])
        return str(rs.rows[0]["value"]) if rs.rows else default

    async def set_setting(self, key: str, value: str) -> None:
        client = self._get_client()
        now = time.time()
        await client.execute(
            """
            INSERT INTO settings (key, value, updated_at) 
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            [key, value, now],
        )

    async def log_activity(self, activity_type: str, details: str = "") -> None:
        client = self._get_client()
        now = time.time()
        await client.execute(
            "INSERT INTO user_activity (activity_type, details, timestamp) VALUES (?, ?, ?)",
            [activity_type, details, now],
        )

    async def get_last_activity_time(self) -> Optional[float]:
        client = self._get_client()
        rs = await client.execute(
            "SELECT timestamp FROM user_activity ORDER BY timestamp DESC LIMIT 1"
        )
        return float(rs.rows[0]["timestamp"]) if rs.rows else None

    async def record_message(
        self,
        chat_id: int,
        msg_id: int,
        sender_id: int,
        is_outgoing: bool,
        is_ai: bool,
        text: str,
        status: str = "received",
    ) -> None:
        client = self._get_client()
        now = time.time()
        await client.execute(
            """
            INSERT OR IGNORE INTO messages 
            (chat_id, msg_id, sender_id, is_outgoing, is_ai, text, timestamp, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                chat_id,
                msg_id,
                sender_id,
                1 if is_outgoing else 0,
                1 if is_ai else 0,
                text,
                now,
                status,
            ],
        )
        await client.execute(
            """
            INSERT INTO conversations (chat_id, first_seen, last_msg_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET last_msg_at = excluded.last_msg_at
            """,
            [chat_id, now, now],
        )

    async def is_message_processed(self, chat_id: int, msg_id: int) -> bool:
        client = self._get_client()
        rs = await client.execute(
            "SELECT status FROM messages WHERE chat_id = ? AND msg_id = ?",
            [chat_id, msg_id],
        )
        return (rs.rows[0]["status"] in ("replied", "ignored", "sent_by_ai")) if rs.rows else False

    async def update_message_status(self, chat_id: int, msg_id: int, status: str) -> None:
        client = self._get_client()
        await client.execute(
            "UPDATE messages SET status = ? WHERE chat_id = ? AND msg_id = ?",
            [status, chat_id, msg_id],
        )

    async def get_recent_messages(self, chat_id: int, limit: int = 20) -> list[dict[str, Any]]:
        client = self._get_client()
        rs = await client.execute(
            """
            SELECT sender_id, is_outgoing, is_ai, text, timestamp 
            FROM messages 
            WHERE chat_id = ? 
            ORDER BY timestamp DESC LIMIT ?
            """,
            [chat_id, limit],
        )
        messages = [
            {
                "sender_id": row["sender_id"],
                "is_outgoing": row["is_outgoing"],
                "is_ai": row["is_ai"],
                "text": row["text"],
                "timestamp": row["timestamp"],
            }
            for row in rs.rows
        ]
        messages.reverse()
        return messages

    async def is_chat_paused(self, chat_id: int) -> bool:
        client = self._get_client()
        now = time.time()
        rs = await client.execute(
            "SELECT is_paused, pause_until FROM conversations WHERE chat_id = ?",
            [chat_id],
        )
        if not rs.rows or not rs.rows[0]["is_paused"]:
            return False
        return bool(rs.rows[0]["pause_until"] > now)

    async def pause_chat(self, chat_id: int, duration_seconds: int) -> None:
        client = self._get_client()
        now = time.time()
        until = now + duration_seconds
        await client.execute(
            """
            INSERT INTO conversations (chat_id, first_seen, last_msg_at, is_paused, pause_until)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(chat_id) DO UPDATE SET is_paused = 1, pause_until = excluded.pause_until
            """,
            [chat_id, now, now, until],
        )

    async def unpause_chat(self, chat_id: int) -> None:
        client = self._get_client()
        await client.execute(
            "UPDATE conversations SET is_paused = 0, pause_until = 0 WHERE chat_id = ?",
            [chat_id],
        )

    async def get_cooldown_info(self, chat_id: int) -> tuple[float, int, float]:
        client = self._get_client()
        rs = await client.execute(
            "SELECT last_replied_at, reply_count_hour, hour_window_start FROM reply_cooldowns WHERE chat_id = ?",
            [chat_id],
        )
        if not rs.rows:
            return (0.0, 0, 0.0)
        row = rs.rows[0]
        return (
            float(row["last_replied_at"]),
            int(row["reply_count_hour"]),
            float(row["hour_window_start"]),
        )

    async def record_ai_reply(self, chat_id: int) -> None:
        client = self._get_client()
        now = time.time()
        rs = await client.execute(
            "SELECT last_replied_at, reply_count_hour, hour_window_start FROM reply_cooldowns WHERE chat_id = ?",
            [chat_id],
        )
        if not rs.rows:
            await client.execute(
                """
                INSERT INTO reply_cooldowns (chat_id, last_replied_at, reply_count_hour, hour_window_start)
                VALUES (?, ?, 1, ?)
                """,
                [chat_id, now, now],
            )
        else:
            row = rs.rows[0]
            hour_window_start = float(row["hour_window_start"])
            if now - hour_window_start > 3600:
                new_count = 1
                new_window = now
            else:
                new_count = int(row["reply_count_hour"]) + 1
                new_window = hour_window_start

            await client.execute(
                """
                UPDATE reply_cooldowns 
                SET last_replied_at = ?, reply_count_hour = ?, hour_window_start = ?
                WHERE chat_id = ?
                """,
                [now, new_count, new_window, chat_id],
            )

    async def log_ai_request(
        self,
        chat_id: int,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        status: str,
        error: str = "",
    ) -> None:
        client = self._get_client()
        now = time.time()
        await client.execute(
            """
            INSERT INTO ai_requests 
            (chat_id, model, prompt_tokens, completion_tokens, latency_ms, status, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [chat_id, model, prompt_tokens, completion_tokens, latency_ms, status, error, now],
        )

    async def get_stats(self) -> dict[str, Any]:
        client = self._get_client()
        c1 = await client.execute("SELECT COUNT(*) as cnt FROM messages WHERE is_outgoing = 0")
        total_incoming = c1.rows[0]["cnt"] if c1.rows else 0

        c2 = await client.execute("SELECT COUNT(*) as cnt FROM messages WHERE is_ai = 1")
        total_ai_replies = c2.rows[0]["cnt"] if c2.rows else 0

        c3 = await client.execute(
            "SELECT SUM(prompt_tokens) as p, SUM(completion_tokens) as c FROM ai_requests WHERE status = 'success'"
        )
        row = c3.rows[0] if c3.rows else None
        prompt_toks = (row["p"] or 0) if row else 0
        comp_toks = (row["c"] or 0) if row else 0

        c4 = await client.execute(
            "SELECT COUNT(*) as cnt FROM ai_requests WHERE status != 'success'"
        )
        api_errors = c4.rows[0]["cnt"] if c4.rows else 0

        now = time.time()
        c5 = await client.execute(
            "SELECT COUNT(*) as cnt FROM conversations WHERE is_paused = 1 AND pause_until > ?",
            [now],
        )
        paused_chats = c5.rows[0]["cnt"] if c5.rows else 0

        return {
            "total_incoming": total_incoming,
            "total_ai_replies": total_ai_replies,
            "prompt_tokens": prompt_toks,
            "completion_tokens": comp_toks,
            "api_errors": api_errors,
            "paused_chats": paused_chats,
        }

    async def prune_old_data(self, days_messages: int = 30, days_logs: int = 7) -> None:
        client = self._get_client()
        now = time.time()
        msg_cutoff = now - (days_messages * 86400)
        logs_cutoff = now - (days_logs * 86400)
        await client.execute("DELETE FROM messages WHERE timestamp < ?", [msg_cutoff])
        await client.execute("DELETE FROM user_activity WHERE timestamp < ?", [logs_cutoff])
        await client.execute("DELETE FROM ai_requests WHERE created_at < ?", [logs_cutoff])


def get_database(
    provider: Optional[str] = None,
    db_path: Optional[Path] = None,
    turso_url: Optional[str] = None,
    turso_token: Optional[str] = None,
) -> BaseDatabaseRepository:
    """Factory creating the appropriate repository backend (SQLite or Turso)."""
    prov = (provider or DATABASE_PROVIDER).lower()
    if prov == "turso":
        return TursoRepository(
            url=turso_url or TURSO_DATABASE_URL,
            auth_token=turso_token or TURSO_AUTH_TOKEN,
        )
    return SQLiteRepository(db_path=db_path or SQLITE_DB_PATH)


class Database(SQLiteRepository):
    """
    Unified database access class maintaining 100% backward compatibility.
    Allows passing db_path directly for unit tests while supporting the repository interface.
    """

    def __init__(self, db_path: Optional[Path] = None):
        super().__init__(db_path=db_path or SQLITE_DB_PATH)
