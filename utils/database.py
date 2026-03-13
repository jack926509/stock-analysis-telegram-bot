"""
SQLite 資料庫管理模組
管理自選股清單與查詢歷史。
"""

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "bot_data.db")

_db_lock = asyncio.Lock()


def _get_connection() -> sqlite3.Connection:
    """取得資料庫連線並確保表存在。"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (user_id, ticker)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS query_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            queried_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


async def add_to_watchlist(user_id: int, ticker: str) -> bool:
    """新增股票到自選股清單。回傳是否新增成功（False 表示已存在）。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO watchlist (user_id, ticker, added_at) VALUES (?, ?, ?)",
                    (user_id, ticker.upper(), datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
                return conn.total_changes > 0
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


async def remove_from_watchlist(user_id: int, ticker: str) -> bool:
    """從自選股清單移除。回傳是否成功移除。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?",
                    (user_id, ticker.upper()),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


async def get_watchlist(user_id: int) -> list[str]:
    """取得使用者的自選股清單。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    "SELECT ticker FROM watchlist WHERE user_id = ? ORDER BY added_at",
                    (user_id,),
                )
                return [row[0] for row in cursor.fetchall()]
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


async def record_query(user_id: int, ticker: str) -> None:
    """記錄查詢歷史。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                conn.execute(
                    "INSERT INTO query_history (user_id, ticker, queried_at) VALUES (?, ?, ?)",
                    (user_id, ticker.upper(), datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
            finally:
                conn.close()
        await asyncio.to_thread(_op)


async def get_user_query_count(user_id: int, window_seconds: int = 60) -> int:
    """取得使用者在時間窗口內的查詢次數（用於 rate limiting）。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cutoff = datetime.now(timezone.utc).isoformat()
                cursor = conn.execute(
                    """SELECT COUNT(*) FROM query_history
                       WHERE user_id = ? AND queried_at > datetime(?, ?)""",
                    (user_id, cutoff, f"-{window_seconds} seconds"),
                )
                return cursor.fetchone()[0]
            finally:
                conn.close()
        return await asyncio.to_thread(_op)
