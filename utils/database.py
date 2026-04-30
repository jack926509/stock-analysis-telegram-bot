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
    # tenk 報告索引（用於半年快取查詢，避免重跑）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tenk_reports (
            ticker TEXT NOT NULL,
            year INTEGER NOT NULL,
            filing_type TEXT NOT NULL,
            quarter TEXT,
            report_md_path TEXT NOT NULL,
            raw_json_path TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (ticker, year, filing_type, quarter)
        )
    """)
    # tenk 每日次數計數
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tenk_usage (
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day)
        )
    """)
    conn.commit()
    return conn


# ──────────────────────────────────────────────
# tenk 報告快取 + 每日次數
# ──────────────────────────────────────────────


async def tenk_get_cached_report(
    ticker: str, year: int, filing_type: str, quarter: str | None,
    ttl_days: int,
) -> dict | None:
    """查詢 ttl_days 內是否有可重用報告。回傳 dict 或 None。"""
    from datetime import timedelta
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
                cursor = conn.execute(
                    """
                    SELECT report_md_path, raw_json_path, summary, created_at
                    FROM tenk_reports
                    WHERE ticker = ? AND year = ? AND filing_type = ?
                      AND COALESCE(quarter, '') = COALESCE(?, '')
                      AND created_at > ?
                    """,
                    (ticker.upper(), year, filing_type, quarter or "", cutoff),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return {
                    "report_md_path": row[0],
                    "raw_json_path": row[1],
                    "summary": row[2],
                    "created_at": row[3],
                }
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


async def tenk_save_report(
    ticker: str, year: int, filing_type: str, quarter: str | None,
    report_md_path: str, raw_json_path: str, summary: str,
) -> None:
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO tenk_reports
                    (ticker, year, filing_type, quarter,
                     report_md_path, raw_json_path, summary, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticker.upper(), year, filing_type, quarter,
                        report_md_path, raw_json_path, summary,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        await asyncio.to_thread(_op)


async def tenk_get_daily_count(user_id: int) -> int:
    """取得使用者今日（UTC）的 /tenk 使用次數。"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    "SELECT count FROM tenk_usage WHERE user_id = ? AND day = ?",
                    (user_id, today),
                )
                row = cursor.fetchone()
                return row[0] if row else 0
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


async def tenk_increment_daily(user_id: int) -> int:
    """+1 並回傳新計數。"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO tenk_usage (user_id, day, count) VALUES (?, ?, 1)
                    ON CONFLICT(user_id, day) DO UPDATE SET count = count + 1
                    """,
                    (user_id, today),
                )
                conn.commit()
                cursor = conn.execute(
                    "SELECT count FROM tenk_usage WHERE user_id = ? AND day = ?",
                    (user_id, today),
                )
                return cursor.fetchone()[0]
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


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
                cutoff = datetime.now(timezone.utc)
                from datetime import timedelta
                cutoff_str = (cutoff - timedelta(seconds=window_seconds)).isoformat()
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM query_history WHERE user_id = ? AND queried_at > ?",
                    (user_id, cutoff_str),
                )
                return cursor.fetchone()[0]
            finally:
                conn.close()
        return await asyncio.to_thread(_op)
