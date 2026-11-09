"""SQLite 資料庫存取。

機密資料（Token、API Key）不寫入此資料庫，一律存放在 keyring。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    scopes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    base_url TEXT,
    default_model TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    provider_id TEXT,
    model TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    events TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);

CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    """簡單的 SQLite 包裝；以鎖保護，讓多個 thread 可共用同一個連線。"""

    def __init__(self, path: Path | str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.Lock()
        with self.transaction() as cur:
            cur.executescript(SCHEMA)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.transaction() as cur:
            return [dict(r) for r in cur.execute(sql, params).fetchall()]

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.transaction() as cur:
            row = cur.execute(sql, params).fetchone()
            return dict(row) if row else None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """執行寫入，回傳影響筆數。"""
        with self.transaction() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    def insert(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """執行 INSERT，回傳 lastrowid。"""
        with self.transaction() as cur:
            cur.execute(sql, params)
            return int(cur.lastrowid or 0)

    # ---- 應用程式設定 ----

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.fetch_one("SELECT value FROM app_settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str | None) -> None:
        if value is None:
            self.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        else:
            self.execute(
                "INSERT INTO app_settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ---- 快取 ----

    def get_cache(self, key: str) -> tuple[Any, str] | None:
        row = self.fetch_one("SELECT value, updated_at FROM cache WHERE key = ?", (key,))
        if not row:
            return None
        return json.loads(row["value"]), row["updated_at"]

    def set_cache(self, key: str, value: Any) -> str:
        now = utc_now()
        self.execute(
            "INSERT INTO cache(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), now),
        )
        return now

    def close(self) -> None:
        self._conn.close()
