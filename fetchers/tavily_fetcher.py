"""
Tavily 新聞搜尋模組（優化版）
使用 Tavily API 搜尋與股票相關的最新真實新聞。
優化：加入公司全名提高搜尋精確度。
"""

import asyncio

from tavily import TavilyClient

from config import Config

# 後端優化：共用 client 實例
_tavily_client: TavilyClient | None = None


def _get_client() -> TavilyClient:
    """取得共用的 TavilyClient。"""
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=Config.TAVILY_API_KEY)
    return _tavily_client


async def fetch_tavily_news(ticker: str, company_name: str = "") -> dict:
    """
    非同步搜尋股票相關新聞。

    Args:
        ticker: 股票代碼（如 AAPL）
        company_name: 公司全名（如 Apple Inc.），可選，用於提高搜尋精確度

    Returns:
        dict: 包含 AI 摘要和新聞列表
    """
    try:
        client = _get_client()

        # 優化搜尋查詢：加入公司全名提高精確度
        if company_name and company_name != "N/A":
            query = f"{company_name} ({ticker}) stock latest news financial analysis"
        else:
            query = f"{ticker} stock latest news financial analysis"

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
            title = result.get("title", "N/A")
            # 清理標題中可能破壞 Markdown 的字元
            title = _sanitize_title(title)

            news_items.append(
                {
                    "title": title,
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


def _sanitize_title(title: str) -> str:
    """
    清理新聞標題中可能破壞 Telegram Markdown 語法的字元。
    """
    if not title:
        return "N/A"
    # 移除或替換可能破壞 Markdown URL 語法的字元
    title = title.replace("[", "(").replace("]", ")")
    # 限制標題長度
    if len(title) > 80:
        title = title[:77] + "..."
    return title
