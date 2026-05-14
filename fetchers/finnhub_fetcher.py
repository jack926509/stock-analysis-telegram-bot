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

# finnhub-python 內部 requests session 沒有暴露 timeout 參數，
# 由呼叫端用 asyncio.wait_for 包住強制上限，避免阻塞 event loop。
_FINNHUB_CALL_TIMEOUT = 15.0


def _get_client() -> finnhub.Client:
    """取得共用的 Finnhub client。"""
    global _finnhub_client
    if _finnhub_client is None:
        _finnhub_client = finnhub.Client(api_key=Config.FINNHUB_API_KEY)
    return _finnhub_client


def _sanitize(msg: str) -> str:
    """從錯誤訊息中過濾掉 API key。"""
    key = Config.FINNHUB_API_KEY
    if key and key in msg:
        return msg.replace(key, "***")
    return msg


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
        quote = await asyncio.wait_for(
            retry_async_call(
                asyncio.to_thread, client.quote, ticker.upper(),
                source_name="Finnhub",
            ),
            timeout=_FINNHUB_CALL_TIMEOUT,
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

    except asyncio.TimeoutError:
        logger.warning(f"[Finnhub] {ticker} quote 逾時（{_FINNHUB_CALL_TIMEOUT}s）")
        return {
            "source": "Finnhub",
            "error": f"Finnhub API 逾時（>{int(_FINNHUB_CALL_TIMEOUT)}s）",
        }
    except Exception as e:
        safe_msg = _sanitize(str(e))
        logger.warning(f"[Finnhub] {ticker} quote 失敗: {safe_msg}")
        return {
            "source": "Finnhub",
            "error": f"Finnhub API 錯誤: {safe_msg}",
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
        data = await asyncio.wait_for(
            asyncio.to_thread(client.company_basic_financials, ticker.upper(), "all"),
            timeout=_FINNHUB_CALL_TIMEOUT,
        )
        return data.get("metric", {}) if isinstance(data, dict) else {}
    except asyncio.TimeoutError:
        logger.warning(f"[Finnhub] basic_financials {ticker} 逾時（{_FINNHUB_CALL_TIMEOUT}s）")
        return {}
    except Exception as e:
        logger.warning(f"[Finnhub] basic_financials {ticker} 失敗: {_sanitize(str(e))}")
        return {}
