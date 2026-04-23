"""
內部人交易模組
使用 Finnhub API 取得近期內部人（董事/高管）買賣紀錄。
靈感來自 ai-hedge-fund 的 insider trading signals。
"""

import asyncio
from datetime import datetime, timedelta, timezone

import finnhub

from config import Config
from utils.retry import retry_async_call

_finnhub_client: finnhub.Client | None = None


def _get_client() -> finnhub.Client:
    global _finnhub_client
    if _finnhub_client is None:
        _finnhub_client = finnhub.Client(api_key=Config.FINNHUB_API_KEY)
    return _finnhub_client


async def fetch_insider_transactions(ticker: str) -> dict:
    """
    抓取近 90 天內部人交易紀錄。

    Returns:
        dict: 包含內部人買入/賣出統計與重要交易明細
    """
    try:
        client = _get_client()
        ticker = ticker.upper()

        now = datetime.now(timezone.utc)
        from_date = (now - timedelta(days=90)).strftime("%Y-%m-%d")
        to_date = now.strftime("%Y-%m-%d")

        data = await retry_async_call(
            asyncio.to_thread,
            lambda: client.stock_insider_transactions(ticker, from_date, to_date),
            source_name="Finnhub_insider",
        )

        if not data or not isinstance(data, dict):
            return {
                "source": "Finnhub_insider",
                "ticker": ticker,
                "error": "無內部人交易數據",
            }

        transactions = data.get("data", [])
        if not transactions:
            return {
                "source": "Finnhub_insider",
                "ticker": ticker,
                "total_transactions": 0,
                "net_sentiment": "neutral",
                "summary": "近 90 天無內部人交易",
            }

        buy_count = 0
        sell_count = 0
        buy_value = 0.0
        sell_value = 0.0
        notable = []

        for tx in transactions[:50]:
            change = tx.get("change", 0) or 0
            price = tx.get("transactionPrice", 0) or 0
            value = abs(change * price)
            code = tx.get("transactionCode", "")

            if code in ("P", "A"):
                buy_count += 1
                buy_value += value
            elif code == "S":
                sell_count += 1
                sell_value += value

            if value > 100_000 and len(notable) < 5:
                notable.append({
                    "name": tx.get("name", "N/A"),
                    "type": "買入" if code in ("P", "A") else "賣出",
                    "shares": abs(change),
                    "value_usd": round(value, 0),
                    "date": tx.get("transactionDate", "N/A"),
                })

        if buy_value > sell_value * 1.5:
            net_sentiment = "bullish"
        elif sell_value > buy_value * 1.5:
            net_sentiment = "bearish"
        else:
            net_sentiment = "neutral"

        return {
            "source": "Finnhub_insider",
            "ticker": ticker,
            "total_transactions": buy_count + sell_count,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "buy_value": round(buy_value, 0),
            "sell_value": round(sell_value, 0),
            "net_sentiment": net_sentiment,
            "notable_transactions": notable,
        }

    except Exception as e:
        return {
            "source": "Finnhub_insider",
            "error": f"內部人交易數據錯誤: {str(e)}",
        }
