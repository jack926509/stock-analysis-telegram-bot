"""
Postgres 資料庫管理模組（asyncpg）

設計：
- 全程 async：asyncpg.Pool，無 asyncio.to_thread 包裝
- 連線池：startup 時建立、shutdown 時關閉；Pool 內建並發控制
- Schema migration：startup 時 `CREATE TABLE IF NOT EXISTS`（idempotent）
- 移除 SQLite 時代的全域 _db_lock（Postgres MVCC + asyncpg pool 已處理併發）
- 時區：所有時間欄位用 TIMESTAMPTZ；Python 端統一 datetime(tz=UTC)

公共 API（與 SQLite 版完全相同）：
- 自選股：add_to_watchlist / remove_from_watchlist / get_watchlist
- 查詢歷史：record_query / get_user_query_count / get_user_stats
- tenk：tenk_get_cached_report / tenk_save_report
        tenk_get_daily_count / tenk_increment_daily

額外：
- init_db()：呼叫一次以建立 pool + 跑 schema migration
- close_db()：graceful shutdown 時關 pool
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import asyncpg

from config import Config

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS watchlist (
    user_id  TEXT NOT NULL,
    ticker   TEXT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, ticker)
);

CREATE TABLE IF NOT EXISTS query_history (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    queried_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_query_history_user_time
    ON query_history (user_id, queried_at DESC);

CREATE TABLE IF NOT EXISTS tenk_reports (
    ticker          TEXT NOT NULL,
    year            INTEGER NOT NULL,
    filing_type     TEXT NOT NULL,
    quarter         TEXT,
    report_md_path  TEXT NOT NULL,
    raw_json_path   TEXT NOT NULL,
    summary         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- 唯一性透過 COALESCE-based unique index 處理（見 init_db），
    -- 不能放在表內 PK，因為 Postgres PK 欄位強制 NOT NULL，
    -- 而 10-K（無季度）必須允許 quarter IS NULL。
);

CREATE TABLE IF NOT EXISTS tenk_usage (
    user_id  TEXT NOT NULL,
    day      DATE NOT NULL,
    count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
"""


async def init_db() -> asyncpg.Pool:
    """建立連線池並執行 schema migration。startup 時呼叫一次。"""
    global _pool
    if _pool is not None:
        return _pool

    if not Config.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL 未設定。Zeabur 請接 Postgres add-on，"
            "或在 .env 設定 postgresql://user:pass@host:5432/dbname"
        )

    _pool = await asyncpg.create_pool(
        dsn=Config.DATABASE_URL,
        min_size=Config.DB_POOL_MIN,
        max_size=Config.DB_POOL_MAX,
        timeout=Config.DB_POOL_TIMEOUT,
        command_timeout=30,
    )
    async with _pool.acquire() as conn:
        # tenk_reports 的 PK 含 nullable quarter；Postgres 在 PK 中對 NULL
        # 不會視為相等。改用 COALESCE-based unique index 處理。
        await conn.execute(_SCHEMA_SQL)
        # 補強：把 quarter 的 NULL 視為 '' 來做唯一性
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_tenk_reports_pk "
            "ON tenk_reports (ticker, year, filing_type, COALESCE(quarter, ''))"
        )
    logger.info("✅ Postgres 連線池已就緒並完成 schema migration")
    return _pool


async def close_db() -> None:
    """關閉連線池。graceful shutdown 時呼叫。"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("🗄️  Postgres 連線池已關閉")


def _require_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool 未初始化，請先在啟動時 await init_db()")
    return _pool


# ──────────────────────────────────────────────
# tenk 報告快取 + 每日次數
# ──────────────────────────────────────────────


async def tenk_get_cached_report(
    ticker: str, year: int, filing_type: str, quarter: str | None,
    ttl_days: int,
) -> dict | None:
    """查詢 ttl_days 內是否有可重用報告。回傳 dict 或 None。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    pool = _require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT report_md_path, raw_json_path, summary, created_at
              FROM tenk_reports
             WHERE ticker = $1 AND year = $2 AND filing_type = $3
               AND COALESCE(quarter, '') = COALESCE($4, '')
               AND created_at > $5
            """,
            ticker.upper(), year, filing_type, quarter, cutoff,
        )
    if not row:
        return None
    return {
        "report_md_path": row["report_md_path"],
        "raw_json_path":  row["raw_json_path"],
        "summary":        row["summary"],
        # 用 isoformat 保持與舊版 API 一致（呼叫端會 fromisoformat）
        "created_at":     row["created_at"].isoformat(),
    }


async def tenk_save_report(
    ticker: str, year: int, filing_type: str, quarter: str | None,
    report_md_path: str, raw_json_path: str, summary: str,
) -> None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        # 用 COALESCE-based unique index 做 upsert（quarter 可能為 NULL）
        await conn.execute(
            """
            INSERT INTO tenk_reports
                (ticker, year, filing_type, quarter,
                 report_md_path, raw_json_path, summary, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (ticker, year, filing_type, COALESCE(quarter, ''))
            DO UPDATE SET
                report_md_path = EXCLUDED.report_md_path,
                raw_json_path  = EXCLUDED.raw_json_path,
                summary        = EXCLUDED.summary,
                created_at     = EXCLUDED.created_at
            """,
            ticker.upper(), year, filing_type, quarter,
            report_md_path, raw_json_path, summary,
        )


async def tenk_get_daily_count(user_id: str) -> int:
    """取得使用者今日（UTC）的 /tenk 使用次數。"""
    pool = _require_pool()
    today = datetime.now(timezone.utc).date()
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT count FROM tenk_usage WHERE user_id = $1 AND day = $2",
            user_id, today,
        )
    return val or 0


async def tenk_increment_daily(user_id: str) -> int:
    """+1 並回傳新計數（原子操作）。"""
    pool = _require_pool()
    today = datetime.now(timezone.utc).date()
    async with pool.acquire() as conn:
        new_count = await conn.fetchval(
            """
            INSERT INTO tenk_usage (user_id, day, count)
            VALUES ($1, $2, 1)
            ON CONFLICT (user_id, day)
            DO UPDATE SET count = tenk_usage.count + 1
            RETURNING count
            """,
            user_id, today,
        )
    return int(new_count)


# ──────────────────────────────────────────────
# 自選股
# ──────────────────────────────────────────────


async def add_to_watchlist(user_id: str, ticker: str) -> bool:
    """新增股票到自選股清單。回傳 True 表示新增成功（False 表示已存在）。"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        # ON CONFLICT DO NOTHING 後，asyncpg 的 status string 為 'INSERT 0 N'
        # N=1 表示真的插入；N=0 表示衝突未動。改用 RETURNING 判斷更穩。
        row = await conn.fetchrow(
            """
            INSERT INTO watchlist (user_id, ticker, added_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (user_id, ticker) DO NOTHING
            RETURNING 1
            """,
            user_id, ticker.upper(),
        )
    return row is not None


async def remove_from_watchlist(user_id: str, ticker: str) -> bool:
    """從自選股清單移除。回傳是否真的有東西被刪。"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            DELETE FROM watchlist
             WHERE user_id = $1 AND ticker = $2
            RETURNING 1
            """,
            user_id, ticker.upper(),
        )
    return row is not None


async def get_watchlist(user_id: str) -> list[str]:
    """取得使用者的自選股清單（依加入時間排序）。"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT ticker FROM watchlist WHERE user_id = $1 ORDER BY added_at",
            user_id,
        )
    return [r["ticker"] for r in rows]


# ──────────────────────────────────────────────
# 查詢歷史
# ──────────────────────────────────────────────


async def record_query(user_id: str, ticker: str) -> None:
    """記錄查詢歷史。"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO query_history (user_id, ticker, queried_at) "
            "VALUES ($1, $2, NOW())",
            user_id, ticker.upper(),
        )


async def get_user_query_count(user_id: str, window_seconds: int = 60) -> int:
    """取得使用者在時間窗口內的查詢次數（rate limiting 輔助）。"""
    pool = _require_pool()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT COUNT(*) FROM query_history "
            "WHERE user_id = $1 AND queried_at > $2",
            user_id, cutoff,
        )
    return int(val or 0)


async def get_user_stats(user_id: str) -> dict:
    """單次撈出 /stats 需要的全部數據（今日/本月查詢、Top 5、自選股數、首次出現）。

    用一條 transaction 把多個 SELECT 串成一致性快照；五個查詢都走相同
    連線、相同 snapshot。
    """
    pool = _require_pool()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            today_count = await conn.fetchval(
                "SELECT COUNT(*) FROM query_history "
                "WHERE user_id = $1 AND queried_at >= $2",
                user_id, today_start,
            )
            month_count = await conn.fetchval(
                "SELECT COUNT(*) FROM query_history "
                "WHERE user_id = $1 AND queried_at >= $2",
                user_id, month_start,
            )
            top_rows = await conn.fetch(
                """
                SELECT ticker, COUNT(*)::int AS cnt
                  FROM query_history
                 WHERE user_id = $1 AND queried_at >= $2
                 GROUP BY ticker
                 ORDER BY cnt DESC
                 LIMIT 5
                """,
                user_id, month_start,
            )
            wl_count = await conn.fetchval(
                "SELECT COUNT(*) FROM watchlist WHERE user_id = $1",
                user_id,
            )
            first_seen = await conn.fetchval(
                "SELECT MIN(queried_at) FROM query_history WHERE user_id = $1",
                user_id,
            )

    return {
        "today_count":     int(today_count or 0),
        "month_count":     int(month_count or 0),
        "top_tickers":     [(r["ticker"], int(r["cnt"])) for r in top_rows],
        "watchlist_count": int(wl_count or 0),
        # 保持與舊 API 一致：回傳 ISO 字串
        "first_seen":      first_seen.isoformat() if first_seen else None,
    }
