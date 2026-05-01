"""
分析師評級模組
使用 Finnhub recommendation_trends 取得分析師推薦趨勢。
（price_target 是 Finnhub 付費端點，已移除）
"""

import asyncio

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
    """抓取分析師推薦趨勢（共識評級、買入/賣出/持有數量）。"""
    try:
        client = _get_client()
        ticker = ticker.upper()

        rec_data = await retry_async_call(
            asyncio.to_thread, client.recommendation_trends, ticker,
            source_name="Finnhub_analyst_rec",
        )

        result = {
            "source": "Finnhub_analyst",
            "ticker": ticker,
            "target_high": "N/A",
            "target_low": "N/A",
            "target_mean": "N/A",
            "target_median": "N/A",
        }

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

        return result

    except Exception as e:
        return {
            "source": "Finnhub_analyst",
            "error": f"分析師數據錯誤: {str(e)}",
        }
