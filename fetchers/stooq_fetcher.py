"""
Stooq 歷史 OHLCV 抓取（無 API key、免費）
取代 FMP Premium 的 historical-price-eod/full。
"""

import io
import logging

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

_STOOQ_URL = "https://stooq.com/q/d/l/"


def _to_stooq_symbol(ticker: str) -> str:
    """AAPL → aapl.us；BRK.B → brk-b.us；^VIX → ^vix。"""
    t = ticker.lower()
    if t.startswith("^"):
        return t
    return f"{t.replace('.', '-')}.us"


async def fetch_stooq_history(ticker: str, days: int = 252) -> list[dict] | None:
    """
    從 Stooq 抓日 K 線。返回舊→新時序：
    [{date, open, high, low, close, volume}, ...]
    """
    symbol = _to_stooq_symbol(ticker)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(_STOOQ_URL, params={"s": symbol, "i": "d"})
    except Exception as e:
        logger.warning(f"[Stooq] {ticker} 連線失敗: {e}")
        return None

    if resp.status_code != 200 or not resp.text or resp.text.lstrip().startswith("No data"):
        logger.warning(f"[Stooq] {ticker} 無資料: status={resp.status_code}")
        return None

    try:
        df = pd.read_csv(io.StringIO(resp.text))
    except Exception as e:
        logger.warning(f"[Stooq] {ticker} CSV 解析失敗: {e}")
        return None

    if df.empty or "Close" not in df.columns:
        return None

    df = df.dropna(subset=["Close"]).tail(days)
    return [
        {
            "date": str(row["Date"]),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]) if not pd.isna(row.get("Volume")) else 0.0,
        }
        for _, row in df.iterrows()
    ]
