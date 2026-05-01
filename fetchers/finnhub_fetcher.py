"""
Finnhub 即時股價數據抓取模組
使用 Finnhub API 取得即時報價與基本面指標。
"""

import asyncio
import logging
from datetime import datetime, timezone

import finnhub

from config import Config
from utils.retry import retry_async_call

logger = logging.getLogger(__name__)

# 後端優化：共用 client 實例，避免每次請求重建
_finnhub_client: finnhub.Client | None = None


def _get_client() -> finnhub.Client:
    """取得共用的 Finnhub client。"""
    global _finnhub_client
    if _finnhub_client is None:
        _finnhub_client = finnhub.Client(api_key=Config.FINNHUB_API_KEY)
    return _finnhub_client


async def fetch_finnhub_quote(ticker: str) -> dict:
    """
    非同步抓取 Finnhub 即時股價。

    Args:
        ticker: 股票代碼（如 AAPL）

    Returns:
        dict: 包含即時股價數據，缺失值標記為 "N/A"
    """
    try:
        client = _get_client()
        quote = await retry_async_call(
            asyncio.to_thread, client.quote, ticker.upper(),
            source_name="Finnhub",
        )

        if not quote or quote.get("c", 0) == 0:
            return {
                "source": "Finnhub",
                "error": f"無法取得 {ticker.upper()} 的即時報價（可能為無效代碼或非交易時段）",
            }

        current_price = quote.get("c")
        previous_close = quote.get("pc")

        # 計算漲跌幅
        if current_price and previous_close and previous_close != 0:
            change = current_price - previous_close
            change_pct = (change / previous_close) * 100
        else:
            change = "N/A"
            change_pct = "N/A"

        return {
            "source": "Finnhub",
            "ticker": ticker.upper(),
            "current_price": current_price if current_price else "N/A",
            "open": quote.get("o") if quote.get("o") else "N/A",
            "high": quote.get("h") if quote.get("h") else "N/A",
            "low": quote.get("l") if quote.get("l") else "N/A",
            "previous_close": previous_close if previous_close else "N/A",
            "change": round(change, 4) if isinstance(change, (int, float)) else "N/A",
            "change_percent": (
                round(change_pct, 2) if isinstance(change_pct, (int, float)) else "N/A"
            ),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }

    except Exception as e:
        return {
            "source": "Finnhub",
            "error": f"Finnhub API 錯誤: {str(e)}",
        }


async def fetch_finnhub_metrics(ticker: str) -> dict:
    """
    Finnhub 免費版基本面指標（補 FMP Premium key-metrics-ttm 缺口）。
    回傳 metric 子物件；失敗回 {}。
    """
    if not Config.FINNHUB_API_KEY:
        return {}
    try:
        client = _get_client()
        data = await asyncio.to_thread(client.company_basic_financials, ticker.upper(), "all")
        return data.get("metric", {}) if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"[Finnhub] basic_financials {ticker} 失敗: {e}")
        return {}
