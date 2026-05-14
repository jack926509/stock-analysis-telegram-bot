"""
歷史 OHLCV 抓取（Yahoo Finance v8 chart API、無 API key、免費）

注意：本檔案歷史名稱仍為 stooq_fetcher，因 Stooq 於 2026-05 起改為
captcha-based apikey 制（伺服器無法自動申請），已遷移到 Yahoo v8 chart。
公開函式名 fetch_stooq_history 保留以維持呼叫端零修改。
"""

import asyncio
import logging
import random
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

# Yahoo 429 後 60s 內全 ticker 都 fail-fast，避免雪崩
_RATE_LIMIT_COOLDOWN = 60.0
_rate_limited_until: float = 0.0

# 模組層級共用 client（連線池 + keep-alive）
_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()

_YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
_YAHOO_URL_TMPL = "https://{host}/v8/finance/chart/{symbol}"
# Yahoo 會 block 自我宣告為 bot 的 UA，必須假裝成真實瀏覽器
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://finance.yahoo.com",
    "Referer": "https://finance.yahoo.com/",
}


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


async def _get_client() -> httpx.AsyncClient:
    """共用 httpx client（含連線池）。"""
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = httpx.AsyncClient(
                    timeout=15,
                    headers=_HEADERS,
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                )
    return _client


async def fetch_stooq_history(ticker: str, days: int = 252) -> list[dict] | None:
    """
    從 Yahoo Finance v8 chart 抓日 K 線。返回舊->新時序：
    [{date, open, high, low, close, volume}, ...]
    遇 429 後 60s 內 fail-fast，避免雪崩。
    """
    global _rate_limited_until

    # 速率限制冷卻中 → 直接 fail-fast
    if _rate_limited_until > time.monotonic():
        logger.debug(
            f"[Yahoo] {ticker} 跳過：rate-limit 冷卻中 "
            f"（剩 {_rate_limited_until - time.monotonic():.0f}s）"
        )
        return None

    symbol = _to_yahoo_symbol(ticker)
    rng = _pick_range(days)
    params = {"range": rng, "interval": "1d"}

    resp = None
    last_err: str | None = None
    last_status: int | None = None
    client = await _get_client()
    for host in _YAHOO_HOSTS:
        url = _YAHOO_URL_TMPL.format(host=host, symbol=symbol)
        try:
            resp = await client.get(url, params=params)
        except Exception as e:
            last_err = f"連線失敗 ({host}): {e}"
            resp = None
            continue
        if resp.status_code == 200:
            break
        last_status = resp.status_code
        last_err = f"HTTP {resp.status_code} ({host})"
        resp = None
        # 429 不重試下一個 host，浪費配額
        if last_status == 429:
            break

    if resp is None:
        if last_status == 429:
            # 設冷卻時間，避免雪崩；用 debug 而非 warning 降噪
            _rate_limited_until = time.monotonic() + _RATE_LIMIT_COOLDOWN + random.uniform(0, 10)
            logger.warning(
                f"[Yahoo] 觸發 429 rate-limit，未來 ~{int(_RATE_LIMIT_COOLDOWN)}s 內跳過 Yahoo 抓取"
            )
        else:
            logger.warning(f"[Yahoo] {ticker} 取得歷史失敗：{last_err}")
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
