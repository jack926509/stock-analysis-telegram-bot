"""
歷史 EPS 驚喜模組
使用 Finnhub API 取得近 4 季 EPS 實際值 vs 預估值。
靈感來自 ai-hedge-fund 的 fundamentals agent。
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


async def fetch_earnings_surprises(ticker: str) -> dict:
    """
    抓取近 4 季 EPS 驚喜數據。

    Returns:
        dict: 包含各季 EPS 實際/預估/驚喜百分比
    """
    try:
        client = _get_client()
        ticker = ticker.upper()

        data = await retry_async_call(
            asyncio.to_thread,
            lambda: client.company_earnings(ticker, limit=4),
            source_name="Finnhub_earnings",
        )

        if not data or not isinstance(data, list) or len(data) == 0:
            return {
                "source": "Finnhub_earnings",
                "ticker": ticker,
                "error": "無 EPS 驚喜數據",
            }

        quarters = []
        beat_count = 0
        miss_count = 0

        for q in data:
            actual = q.get("actual")
            estimate = q.get("estimate")
            surprise_pct = q.get("surprisePercent")
            period = q.get("period", "N/A")

            if actual is not None and estimate is not None:
                if surprise_pct is None and estimate != 0:
                    surprise_pct = ((actual - estimate) / abs(estimate)) * 100

                if surprise_pct is not None:
                    if surprise_pct > 0:
                        beat_count += 1
                    elif surprise_pct < 0:
                        miss_count += 1

                quarters.append({
                    "period": period,
                    "actual": actual,
                    "estimate": estimate,
                    "surprise_pct": round(surprise_pct, 2) if surprise_pct is not None else "N/A",
                })

        total = len(quarters)
        if total > 0:
            if beat_count == total:
                track_record = "excellent"
            elif beat_count >= total * 0.75:
                track_record = "good"
            elif miss_count >= total * 0.5:
                track_record = "poor"
            else:
                track_record = "mixed"
        else:
            track_record = "N/A"

        return {
            "source": "Finnhub_earnings",
            "ticker": ticker,
            "quarters": quarters,
            "beat_count": beat_count,
            "miss_count": miss_count,
            "total_quarters": total,
            "track_record": track_record,
        }

    except Exception as e:
        return {
            "source": "Finnhub_earnings",
            "error": f"EPS 驚喜數據錯誤: {str(e)}",
        }
