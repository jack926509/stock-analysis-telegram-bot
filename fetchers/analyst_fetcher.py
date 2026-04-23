"""
分析師評級與目標價模組
使用 Finnhub API 取得分析師推薦趨勢與目標價。
靈感來自 ai-hedge-fund 的 sentiment agent。
"""

import asyncio
from datetime import datetime, timezone

import finnhub

from config import Config
from utils.retry import retry_async_call

_finnhub_client: finnhub.Client | None = None


def _get_client() -> finnhub.Client:
    global _finnhub_client
    if _finnhub_client is None:
        _finnhub_client = finnhub.Client(api_key=Config.FINNHUB_API_KEY)
    return _finnhub_client


async def fetch_analyst_data(ticker: str) -> dict:
    """
    抓取分析師推薦趨勢與目標價。

    Returns:
        dict: 包含共識評級、買入/賣出/持有數量、目標價
    """
    try:
        client = _get_client()
        ticker = ticker.upper()

        rec_task = retry_async_call(
            asyncio.to_thread, client.recommendation_trends, ticker,
            source_name="Finnhub_analyst_rec",
        )
        pt_task = retry_async_call(
            asyncio.to_thread, client.price_target, ticker,
            source_name="Finnhub_price_target",
        )

        rec_data, pt_data = await asyncio.gather(rec_task, pt_task, return_exceptions=True)

        result = {
            "source": "Finnhub_analyst",
            "ticker": ticker,
        }

        # 分析師推薦趨勢（取最近一期）
        if isinstance(rec_data, list) and len(rec_data) > 0:
            latest = rec_data[0]
            strong_buy = latest.get("strongBuy", 0)
            buy = latest.get("buy", 0)
            hold = latest.get("hold", 0)
            sell = latest.get("sell", 0)
            strong_sell = latest.get("strongSell", 0)
            total = strong_buy + buy + hold + sell + strong_sell

            result["strong_buy"] = strong_buy
            result["buy"] = buy
            result["hold"] = hold
            result["sell"] = sell
            result["strong_sell"] = strong_sell
            result["total_analysts"] = total
            result["period"] = latest.get("period", "N/A")

            if total > 0:
                bull = strong_buy + buy
                bear = sell + strong_sell
                if bull > total * 0.6:
                    result["consensus"] = "strongBuy" if strong_buy > buy else "buy"
                elif bear > total * 0.4:
                    result["consensus"] = "strongSell" if strong_sell > sell else "sell"
                else:
                    result["consensus"] = "hold"
            else:
                result["consensus"] = "N/A"
        else:
            result["consensus"] = "N/A"
            result["total_analysts"] = 0

        # 目標價
        if isinstance(pt_data, dict) and "targetMean" in pt_data:
            result["target_high"] = pt_data.get("targetHigh", "N/A")
            result["target_low"] = pt_data.get("targetLow", "N/A")
            result["target_mean"] = pt_data.get("targetMean", "N/A")
            result["target_median"] = pt_data.get("targetMedian", "N/A")
            result["last_updated"] = pt_data.get("lastUpdated", "N/A")
        else:
            result["target_high"] = "N/A"
            result["target_low"] = "N/A"
            result["target_mean"] = "N/A"
            result["target_median"] = "N/A"

        return result

    except Exception as e:
        return {
            "source": "Finnhub_analyst",
            "error": f"分析師數據錯誤: {str(e)}",
        }
