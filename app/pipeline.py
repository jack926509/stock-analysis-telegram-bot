"""
美股日報生成管線（Newsletter Pipeline）
流程：抓取市場數據 → AI 規劃 → AI 撰寫 → 輸出日報
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from config import Config
from fetchers.finnhub_fetcher import fetch_finnhub_quote
from fetchers.tavily_fetcher import fetch_tavily_news
from fetchers.fmp_fetcher import fetch_fmp_fundamentals
from fetchers.tradingview_fetcher import fetch_tradingview_analysis
from app.ai.planner import plan_newsletter
from app.ai.writer import write_newsletter
from app.ai.exceptions import AIGenerationError

logger = logging.getLogger("newsletter")

# 追蹤的主要指數 / 大盤 ETF
INDEX_TICKERS = ["SPY", "QQQ", "DIA", "IWM"]
# 熱門關注個股
FOCUS_TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "GOOGL", "AMZN", "META"]


async def run_newsletter_pipeline() -> str | None:
    """
    執行完整的日報生成管線。

    Returns:
        str: 生成的日報文字，失敗則返回 None
    """
    try:
        logger.info("📰 開始美股日報生成流程...")

        # ── Step 1: 並行抓取市場數據 ──
        logger.info("📡 抓取市場數據中...")
        market_data = await _fetch_market_data()
        logger.info("初步市場數據與新聞就緒，讓 AI 進行規劃...")

        # ── Step 2: AI 規劃日報結構 ──
        plan = await plan_newsletter(market_data)
        logger.info(f"✅ AI 規劃完成")

        # ── Step 3: 補充重點個股數據（根據規劃結果）──
        focus_tickers = plan.get("recommended_focus", [])
        if focus_tickers:
            logger.info(f"📊 補充重點個股數據: {focus_tickers}")
            extra_data = await _fetch_focus_stocks(focus_tickers)
            market_data["focus_stocks"] = extra_data

        # ── Step 4: AI 撰寫日報 ──
        logger.info("✍️ AI 撰寫日報中...")
        newsletter = await write_newsletter(plan, market_data)
        logger.info("✅ 美股日報生成完成")

        return newsletter

    except AIGenerationError as e:
        logger.error(f"❌ 美股日報生成流程發生未預期嚴重失敗: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ 美股日報生成流程發生未預期嚴重失敗: {e}", exc_info=True)
        return None


async def _fetch_market_data() -> dict:
    """並行抓取主要指數和熱門股的基礎數據 + 新聞。"""

    # 並行抓取所有指數報價
    index_tasks = [fetch_finnhub_quote(t) for t in INDEX_TICKERS]
    stock_tasks = [fetch_finnhub_quote(t) for t in FOCUS_TICKERS]
    news_task = fetch_tavily_news("US stock market", "US stock market today")

    all_results = await asyncio.gather(
        *index_tasks, *stock_tasks, news_task,
        return_exceptions=True,
    )

    # 解析結果
    index_quotes = {}
    for i, ticker in enumerate(INDEX_TICKERS):
        result = all_results[i]
        if isinstance(result, dict) and "error" not in result:
            index_quotes[ticker] = result

    stock_quotes = {}
    offset = len(INDEX_TICKERS)
    for i, ticker in enumerate(FOCUS_TICKERS):
        result = all_results[offset + i]
        if isinstance(result, dict) and "error" not in result:
            stock_quotes[ticker] = result

    news_result = all_results[-1]
    news_data = news_result if isinstance(news_result, dict) else {"error": str(news_result)}

    return {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "index_quotes": index_quotes,
        "stock_quotes": stock_quotes,
        "news": news_data,
    }


async def _fetch_focus_stocks(tickers: list[str]) -> dict:
    """抓取重點個股的基本面 + 技術面數據。"""
    tasks = []
    for t in tickers[:3]:  # 最多 3 檔避免過慢
        tasks.append(fetch_fmp_fundamentals(t))
        tasks.append(fetch_tradingview_analysis(t))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    focus_data = {}
    for i, ticker in enumerate(tickers[:3]):
        fundamental = results[i * 2]
        technical = results[i * 2 + 1]
        focus_data[ticker] = {
            "fundamental": fundamental if isinstance(fundamental, dict) else {"error": str(fundamental)},
            "technical": technical if isinstance(technical, dict) else {"error": str(technical)},
        }

    return focus_data
