"""
XAUUSD 交易系統 SQLite 資料庫管理模組
管理交易信號、持倉、權益快照、回測結果、AI 選擇記錄。
"""

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone

from forex_trading.config import ForexConfig

logger = logging.getLogger(__name__)

_db_lock = asyncio.Lock()


def _get_connection() -> sqlite3.Connection:
    """取得資料庫連線並確保所有表存在。"""
    conn = sqlite3.connect(ForexConfig.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_name TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            take_profit REAL NOT NULL,
            confidence REAL NOT NULL,
            ai_selected INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            market_regime TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER REFERENCES signals(id),
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            current_price REAL,
            stop_loss REAL NOT NULL,
            take_profit REAL NOT NULL,
            lot_size REAL NOT NULL,
            risk_amount REAL NOT NULL,
            unrealized_pnl REAL DEFAULT 0,
            realized_pnl REAL,
            status TEXT NOT NULL DEFAULT 'open',
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            close_price REAL,
            close_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            equity REAL NOT NULL,
            cash REAL NOT NULL,
            unrealized_pnl REAL NOT NULL,
            open_positions INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_name TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            total_trades INTEGER,
            win_rate REAL,
            total_return REAL,
            annual_return REAL,
            max_drawdown REAL,
            sharpe_ratio REAL,
            profit_factor REAL,
            avg_hold_time_hours REAL,
            alpha_vs_buyhold REAL,
            run_at TEXT NOT NULL,
            details_json TEXT
        );

        CREATE TABLE IF NOT EXISTS ai_selections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            selected_strategies TEXT NOT NULL,
            confidence REAL,
            reasoning TEXT,
            market_regime TEXT,
            market_data_summary TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS market_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_text TEXT NOT NULL,
            market_data_json TEXT,
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ══════════════════════════════════════════
# 信號 CRUD
# ══════════════════════════════════════════

async def save_signal(
    strategy_name: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    confidence: float,
    ai_selected: bool = False,
    reason: str = "",
    market_regime: str = "",
) -> int:
    """儲存交易信號，回傳信號 ID。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    """INSERT INTO signals
                       (strategy_name, direction, entry_price, stop_loss, take_profit,
                        confidence, ai_selected, reason, market_regime, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (strategy_name, direction, entry_price, stop_loss, take_profit,
                     confidence, int(ai_selected), reason, market_regime, _now_iso()),
                )
                conn.commit()
                return cursor.lastrowid
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


async def update_signal_status(signal_id: int, status: str) -> None:
    """更新信號狀態。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                conn.execute(
                    "UPDATE signals SET status = ? WHERE id = ?",
                    (status, signal_id),
                )
                conn.commit()
            finally:
                conn.close()
        await asyncio.to_thread(_op)


async def get_recent_signals(limit: int = 20) -> list[dict]:
    """取得最近的信號。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


# ══════════════════════════════════════════
# 持倉 CRUD
# ══════════════════════════════════════════

async def open_position(
    signal_id: int,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    lot_size: float,
    risk_amount: float,
) -> int:
    """開設新持倉，回傳持倉 ID。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    """INSERT INTO positions
                       (signal_id, direction, entry_price, current_price,
                        stop_loss, take_profit, lot_size, risk_amount, opened_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (signal_id, direction, entry_price, entry_price,
                     stop_loss, take_profit, lot_size, risk_amount, _now_iso()),
                )
                conn.commit()
                return cursor.lastrowid
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


async def close_position(
    position_id: int,
    close_price: float,
    realized_pnl: float,
    close_reason: str,
) -> None:
    """關閉持倉。"""
    status = f"closed_{close_reason}"
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                conn.execute(
                    """UPDATE positions
                       SET status = ?, close_price = ?, realized_pnl = ?,
                           close_reason = ?, closed_at = ?, current_price = ?
                       WHERE id = ?""",
                    (status, close_price, realized_pnl, close_reason,
                     _now_iso(), close_price, position_id),
                )
                conn.commit()
            finally:
                conn.close()
        await asyncio.to_thread(_op)


async def update_position_price(position_id: int, current_price: float, unrealized_pnl: float) -> None:
    """更新持倉的當前價格和未實現損益。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                conn.execute(
                    "UPDATE positions SET current_price = ?, unrealized_pnl = ? WHERE id = ?",
                    (current_price, unrealized_pnl, position_id),
                )
                conn.commit()
            finally:
                conn.close()
        await asyncio.to_thread(_op)


async def get_open_positions() -> list[dict]:
    """取得所有未平倉部位。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    "SELECT * FROM positions WHERE status = 'open' ORDER BY opened_at DESC"
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


async def get_closed_positions(limit: int = 20) -> list[dict]:
    """取得最近已平倉部位。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    "SELECT * FROM positions WHERE status != 'open' ORDER BY closed_at DESC LIMIT ?",
                    (limit,),
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


async def get_strategy_performance(strategy_name: str, limit: int = 20) -> dict:
    """取得特定策略的績效統計。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    """SELECT p.* FROM positions p
                       JOIN signals s ON p.signal_id = s.id
                       WHERE s.strategy_name = ? AND p.status != 'open'
                       ORDER BY p.closed_at DESC LIMIT ?""",
                    (strategy_name, limit),
                )
                rows = [dict(row) for row in cursor.fetchall()]
                if not rows:
                    return {"strategy": strategy_name, "trades": 0, "win_rate": 0, "profit_factor": 0}

                wins = sum(1 for r in rows if (r.get("realized_pnl") or 0) > 0)
                total_profit = sum(r.get("realized_pnl", 0) for r in rows if (r.get("realized_pnl") or 0) > 0)
                total_loss = abs(sum(r.get("realized_pnl", 0) for r in rows if (r.get("realized_pnl") or 0) < 0))

                return {
                    "strategy": strategy_name,
                    "trades": len(rows),
                    "win_rate": (wins / len(rows) * 100) if rows else 0,
                    "profit_factor": (total_profit / total_loss) if total_loss > 0 else float("inf"),
                    "total_pnl": sum(r.get("realized_pnl", 0) for r in rows),
                }
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


# ══════════════════════════════════════════
# 權益快照
# ══════════════════════════════════════════

async def save_equity_snapshot(equity: float, cash: float, unrealized_pnl: float, open_positions: int) -> None:
    """儲存權益快照。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                conn.execute(
                    """INSERT INTO equity_snapshots
                       (timestamp, equity, cash, unrealized_pnl, open_positions)
                       VALUES (?, ?, ?, ?, ?)""",
                    (_now_iso(), equity, cash, unrealized_pnl, open_positions),
                )
                conn.commit()
            finally:
                conn.close()
        await asyncio.to_thread(_op)


async def get_equity_history(limit: int = 100) -> list[dict]:
    """取得權益歷史。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    "SELECT * FROM equity_snapshots ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


# ══════════════════════════════════════════
# 回測結果
# ══════════════════════════════════════════

async def save_backtest_result(
    strategy_name: str,
    timeframe: str,
    period_start: str,
    period_end: str,
    metrics: dict,
    details: list[dict] | None = None,
) -> int:
    """儲存回測結果。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    """INSERT INTO backtest_results
                       (strategy_name, timeframe, period_start, period_end,
                        total_trades, win_rate, total_return, annual_return,
                        max_drawdown, sharpe_ratio, profit_factor,
                        avg_hold_time_hours, alpha_vs_buyhold, run_at, details_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (strategy_name, timeframe, period_start, period_end,
                     metrics.get("total_trades", 0),
                     metrics.get("win_rate", 0),
                     metrics.get("total_return", 0),
                     metrics.get("annual_return", 0),
                     metrics.get("max_drawdown", 0),
                     metrics.get("sharpe_ratio", 0),
                     metrics.get("profit_factor", 0),
                     metrics.get("avg_hold_time_hours", 0),
                     metrics.get("alpha_vs_buyhold", 0),
                     _now_iso(),
                     json.dumps(details, ensure_ascii=False) if details else None),
                )
                conn.commit()
                return cursor.lastrowid
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


async def get_backtest_results(strategy_name: str | None = None, limit: int = 10) -> list[dict]:
    """取得回測結果。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                if strategy_name:
                    cursor = conn.execute(
                        "SELECT * FROM backtest_results WHERE strategy_name = ? ORDER BY run_at DESC LIMIT ?",
                        (strategy_name, limit),
                    )
                else:
                    cursor = conn.execute(
                        "SELECT * FROM backtest_results ORDER BY run_at DESC LIMIT ?",
                        (limit,),
                    )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


# ══════════════════════════════════════════
# AI 選擇記錄
# ══════════════════════════════════════════

async def save_ai_selection(
    selected_strategies: list[str],
    confidence: float,
    reasoning: str,
    market_regime: str,
    market_data_summary: dict | None = None,
) -> int:
    """儲存 AI 策略選擇記錄。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    """INSERT INTO ai_selections
                       (selected_strategies, confidence, reasoning, market_regime,
                        market_data_summary, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (json.dumps(selected_strategies),
                     confidence, reasoning, market_regime,
                     json.dumps(market_data_summary, ensure_ascii=False) if market_data_summary else None,
                     _now_iso()),
                )
                conn.commit()
                return cursor.lastrowid
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


# ══════════════════════════════════════════
# 市場分析快取
# ══════════════════════════════════════════

async def save_market_analysis(analysis_text: str, market_data: dict | None = None) -> int:
    """儲存市場分析。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    "INSERT INTO market_analyses (analysis_text, market_data_json, created_at) VALUES (?, ?, ?)",
                    (analysis_text,
                     json.dumps(market_data, ensure_ascii=False) if market_data else None,
                     _now_iso()),
                )
                conn.commit()
                return cursor.lastrowid
            finally:
                conn.close()
        return await asyncio.to_thread(_op)


async def get_latest_analysis() -> dict | None:
    """取得最新的市場分析。"""
    async with _db_lock:
        def _op():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    "SELECT * FROM market_analyses ORDER BY created_at DESC LIMIT 1"
                )
                row = cursor.fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        return await asyncio.to_thread(_op)
