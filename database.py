"""SQLite persistence for the AtiChat profile and local message history."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class Database:
    """Small connection-per-operation SQLite data store."""

    def __init__(self, path: str | Path = "k1chat.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS profile (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    user_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    avatar TEXT NOT NULL DEFAULT '',
                    startup_enabled INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL CHECK (kind IN ('public', 'private', 'file')),
                    sender_id TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    recipient_id TEXT NOT NULL DEFAULT '',
                    recipient_name TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    file_path TEXT NOT NULL DEFAULT '',
                    timestamp TEXT NOT NULL,
                    outgoing INTEGER NOT NULL CHECK (outgoing IN (0, 1))
                );
                CREATE INDEX IF NOT EXISTS messages_public_history
                    ON messages(kind, timestamp);
                CREATE INDEX IF NOT EXISTS messages_private_history
                    ON messages(kind, sender_id, recipient_id, timestamp);
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO profile
                    (singleton, user_id, display_name, avatar, startup_enabled)
                VALUES (1, ?, 'کاربر AtiChat', '', 0)
                """,
                (str(uuid.uuid4()),),
            )
            for legacy_name in (
                "K1chat User",
                "کاربر K1chat",
                "کاربر K1Chat",
            ):
                connection.execute(
                    "UPDATE profile SET display_name = 'کاربر AtiChat' "
                    "WHERE singleton = 1 AND display_name = ?",
                    (legacy_name,),
                )

    def get_profile(self) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT user_id, display_name, avatar, startup_enabled
                FROM profile WHERE singleton = 1
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("نمایهٔ AtiChat راه‌اندازی نشد.")
        return dict(row)

    def save_profile(
        self,
        user_id: str,
        display_name: str,
        avatar: str,
        startup_enabled: bool,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE profile
                SET user_id = ?, display_name = ?, avatar = ?, startup_enabled = ?
                WHERE singleton = 1
                """,
                (user_id, display_name, avatar, int(startup_enabled)),
            )

    def add_message(self, message: dict[str, Any]) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO messages (
                    message_id, kind, sender_id, sender_name, recipient_id,
                    recipient_name, body, file_path, timestamp, outgoing
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message["message_id"],
                    message["kind"],
                    message["sender_id"],
                    message["sender_name"],
                    message.get("recipient_id", ""),
                    message.get("recipient_name", ""),
                    message.get("body", ""),
                    message.get("file_path", ""),
                    message["timestamp"],
                    int(message["outgoing"]),
                ),
            )

    def delete_message(self, message_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM messages WHERE message_id = ?", (message_id,)
            )
        return cursor.rowcount > 0

    def delete_public_messages(self) -> int:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM messages WHERE kind = 'public'")
        return cursor.rowcount

    def delete_private_conversation(self, user_id: str, peer_id: str) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                DELETE FROM messages
                WHERE kind IN ('private', 'file')
                  AND ((sender_id = ? AND recipient_id = ?)
                    OR (sender_id = ? AND recipient_id = ?))
                """,
                (user_id, peer_id, peer_id, user_id),
            )
        return cursor.rowcount

    def delete_all_messages(self) -> int:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM messages")
        return cursor.rowcount

    def get_public_messages(self, limit: int = 500) -> list[dict[str, Any]]:
        return self._get_messages(
            "SELECT * FROM messages WHERE kind = 'public' "
            "ORDER BY julianday(timestamp) DESC, id DESC LIMIT ?",
            (limit,),
        )

    def get_private_messages(
        self, user_id: str, peer_id: str, limit: int = 500
    ) -> list[dict[str, Any]]:
        return self._get_messages(
            """
            SELECT * FROM messages
            WHERE kind IN ('private', 'file')
              AND ((sender_id = ? AND recipient_id = ?)
                OR (sender_id = ? AND recipient_id = ?))
            ORDER BY julianday(timestamp) DESC, id DESC LIMIT ?
            """,
            (user_id, peer_id, peer_id, user_id, limit),
        )

    def _get_messages(
        self, query: str, parameters: tuple[Any, ...]
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in reversed(rows)]
