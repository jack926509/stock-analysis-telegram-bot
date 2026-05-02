"""
歷史 OHLCV 抓取（Yahoo Finance v8 chart API、無 API key、免費）

注意：本檔案歷史名稱仍為 stooq_fetcher，因 Stooq 於 2026-05 起改為
captcha-based apikey 制（伺服器無法自動申請），已遷移到 Yahoo v8 chart。
公開函式名 fetch_stooq_history 保留以維持呼叫端零修改。
"""

import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; stock-analysis-bot)"}


def _to_yahoo_symbol(ticker: str) -> str:
    """AAPL → AAPL；BRK.B → BRK-B；^VIX → ^VIX。"""
    t = ticker.upper()
    if t.startswith("^"):
        return t
    return t.replace(".", "-")


def _pick_range(days: int) -> str:
    """挑最小但 >= days 的 Yahoo range，避免抓過多。"""
    if days <= 22:
        return "1mo"
    if days <= 65:
        return "3mo"
    if days <= 130:
        return "6mo"
    if days <= 252:
        return "1y"
    if days <= 504:
        return "2y"
    return "5y"


async def fetch_stooq_history(ticker: str, days: int = 252) -> list[dict] | None:
    """
    從 Yahoo Finance v8 chart 抓日 K 線。返回舊->新時序：
    [{date, open, high, low, close, volume}, ...]
    """
    symbol = _to_yahoo_symbol(ticker)
    rng = _pick_range(days)
    try:
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
            resp = await client.get(
                _YAHOO_URL.format(symbol=symbol),
                params={"range": rng, "interval": "1d"},
            )
    except Exception as e:
        logger.warning(f"[Yahoo] {ticker} 連線失敗: {e}")
        return None

    if resp.status_code != 200:
        logger.warning(f"[Yahoo] {ticker} HTTP {resp.status_code}")
        return None

    try:
        data = resp.json()
    except Exception as e:
        logger.warning(f"[Yahoo] {ticker} JSON 解析失敗: {e}")
        return None

    chart = data.get("chart") or {}
    if chart.get("error"):
        logger.warning(f"[Yahoo] {ticker} API 錯誤: {chart['error']}")
        return None

    results = chart.get("result") or []
    if not results:
        return None

    r = results[0]
    timestamps = r.get("timestamp") or []
    quote_list = (r.get("indicators") or {}).get("quote") or []
    if not timestamps or not quote_list:
        return None

    q = quote_list[0]
    opens = q.get("open") or []
    highs = q.get("high") or []
    lows = q.get("low") or []
    closes = q.get("close") or []
    volumes = q.get("volume") or []

    rows: list[dict] = []
    for i, ts in enumerate(timestamps):
        c = closes[i] if i < len(closes) else None
        if c is None:
            continue
        rows.append({
            "date": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"),
            "open": float(opens[i]) if i < len(opens) and opens[i] is not None else float(c),
            "high": float(highs[i]) if i < len(highs) and highs[i] is not None else float(c),
            "low":  float(lows[i])  if i < len(lows)  and lows[i]  is not None else float(c),
            "close": float(c),
            "volume": float(volumes[i]) if i < len(volumes) and volumes[i] is not None else 0.0,
        })

    if not rows:
        return None
    return rows[-days:]
