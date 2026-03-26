"""
Tavily 黃金市場新聞搜尋模組
搜尋 XAUUSD 相關的市場新聞與情緒。
"""

import asyncio
import logging

from tavily import TavilyClient

from forex_trading.config import ForexConfig

logger = logging.getLogger(__name__)

_tavily_client: TavilyClient | None = None


def _get_client() -> TavilyClient:
    """取得共用的 TavilyClient。"""
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=ForexConfig.TAVILY_API_KEY)
    return _tavily_client


async def fetch_gold_news() -> dict:
    """
    搜尋黃金/XAUUSD 市場新聞。

    Returns:
        dict: 包含 AI 摘要和新聞列表
    """
    try:
        client = _get_client()

        query = (
            "gold XAUUSD price forecast Federal Reserve dollar "
            "interest rate inflation safe haven"
        )

        response = await asyncio.to_thread(
            client.search,
            query=query,
            search_depth="advanced",
            include_answer=True,
            max_results=5,
            topic="news",
            days=3,
        )

        news_items = []
        for result in response.get("results", []):
            title = result.get("title", "N/A")
            title = title.replace("[", "(").replace("]", ")")
            if len(title) > 80:
                title = title[:77] + "..."

            news_items.append({
                "title": title,
                "url": result.get("url", ""),
                "content": (
                    result.get("content", "")[:200] + "..."
                    if result.get("content") and len(result.get("content", "")) > 200
                    else result.get("content", "")
                ),
            })

        return {
            "source": "Tavily",
            "ai_summary": response.get("answer", ""),
            "news_count": len(news_items),
            "news": news_items,
        }

    except Exception as e:
        logger.error(f"Tavily 黃金新聞搜尋錯誤: {e}")
        return {
            "source": "Tavily",
            "error": f"新聞搜尋錯誤: {str(e)}",
            "ai_summary": "",
            "news": [],
        }
