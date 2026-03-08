"""
Tavily 新聞搜尋模組
使用 Tavily API 搜尋與股票相關的最新真實新聞。
"""

import asyncio

from tavily import TavilyClient

from config import Config


async def fetch_tavily_news(ticker: str) -> dict:
    """
    非同步搜尋股票相關新聞。

    Args:
        ticker: 股票代碼（如 AAPL）

    Returns:
        dict: 包含 AI 摘要和新聞列表
    """
    try:
        client = TavilyClient(api_key=Config.TAVILY_API_KEY)

        query = f"{ticker} stock latest news analysis"

        response = await asyncio.to_thread(
            client.search,
            query=query,
            search_depth="advanced",
            include_answer=True,
            max_results=5,
        )

        # 解析新聞結果
        news_items = []
        for result in response.get("results", []):
            news_items.append(
                {
                    "title": result.get("title", "N/A"),
                    "url": result.get("url", "N/A"),
                    "content": (
                        result.get("content", "N/A")[:200] + "..."
                        if result.get("content") and len(result.get("content", "")) > 200
                        else result.get("content", "N/A")
                    ),
                }
            )

        return {
            "source": "Tavily",
            "ticker": ticker.upper(),
            "ai_summary": response.get("answer", "無法取得新聞摘要"),
            "news_count": len(news_items),
            "news": news_items,
        }

    except Exception as e:
        return {
            "source": "Tavily",
            "error": f"Tavily 新聞搜尋錯誤: {str(e)}",
            "ai_summary": "News data unavailable",
            "news": [],
        }
