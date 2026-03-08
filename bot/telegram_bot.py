"""
Telegram Bot 介面模組
處理使用者指令，串接數據抓取與 AI 分析流程。
"""

import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

from config import Config
from fetchers.finnhub_fetcher import fetch_finnhub_quote
from fetchers.yfinance_fetcher import fetch_yfinance_fundamentals
from fetchers.tavily_fetcher import fetch_tavily_news
from fetchers.tradingview_fetcher import fetch_tradingview_analysis
from analyzer.openai_analyzer import analyze_stock
from utils.formatter import format_report

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /start 指令"""
    welcome_msg = (
        "👋 *歡迎使用零幻覺美股分析 Bot\\!*\n"
        "\n"
        "我會基於真實數據為你分析美股，嚴格排除 AI 幻覺。\n"
        "\n"
        "📌 *使用方式：*\n"
        "`/report AAPL` \\- 分析 Apple 股票\n"
        "`/report TSLA` \\- 分析 Tesla 股票\n"
        "`/report MSFT` \\- 分析 Microsoft 股票\n"
        "\n"
        "🔍 *數據來源：*\n"
        "├ Finnhub \\- 即時報價\n"
        "├ yfinance \\- 基本面數據\n"
        "├ Tavily \\- 真實新聞\n"
        "└ TradingView \\- 技術指標\n"
        "\n"
        "🤖 *分析引擎：* OpenAI GPT\n"
        "🛡️ *反幻覺機制：* 所有分析僅基於真實數據"
    )
    await update.message.reply_text(welcome_msg, parse_mode=ParseMode.MARKDOWN_V2)


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    處理 /report [TICKER] 指令。
    核心流程：並行抓取數據 → AI 分析 → 格式化報告 → 回傳。
    """
    # 驗證參數
    if not context.args or len(context.args) == 0:
        await update.message.reply_text(
            "❌ 請提供股票代碼，例如：`/report AAPL`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    ticker = context.args[0].upper()

    # 驗證代碼格式（只允許英文字母，1-5 個字元）
    if not ticker.isalpha() or len(ticker) > 5:
        await update.message.reply_text(
            "❌ 無效的股票代碼。請使用英文字母代碼，例如：`/report AAPL`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # 發送載入訊息
    loading_msg = await update.message.reply_text(
        f"⏳ 正在分析 *{ticker}*，請稍候...\n\n"
        f"📡 正在並行抓取四個數據源...",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    try:
        # ── Step 1: 並行抓取所有數據 ──
        logger.info(f"開始分析 {ticker}：並行抓取數據...")

        finnhub_data, yfinance_data, tavily_data, tradingview_data = (
            await asyncio.gather(
                fetch_finnhub_quote(ticker),
                fetch_yfinance_fundamentals(ticker),
                fetch_tavily_news(ticker),
                fetch_tradingview_analysis(ticker),
            )
        )

        logger.info(f"{ticker} 數據抓取完成，開始 AI 分析...")

        # 更新載入訊息
        await loading_msg.edit_text(
            f"⏳ 正在分析 *{ticker}*，請稍候...\n\n"
            f"✅ 數據抓取完成\n"
            f"🤖 正在進行 AI 深度分析...",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        # ── Step 2: AI 分析 ──
        ai_analysis = await analyze_stock(
            ticker, finnhub_data, yfinance_data, tavily_data, tradingview_data
        )

        logger.info(f"{ticker} AI 分析完成，組裝報告...")

        # ── Step 3: 格式化報告 ──
        report = format_report(
            ticker,
            finnhub_data,
            yfinance_data,
            tavily_data,
            tradingview_data,
            ai_analysis,
        )

        # ── Step 4: 發送報告 ──
        # 刪除載入訊息
        await loading_msg.delete()

        # Telegram 訊息有 4096 字元限制，需要分段發送
        if len(report) <= 4096:
            await update.message.reply_text(
                report, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True
            )
        else:
            # 分段發送
            chunks = _split_message(report, 4096)
            for chunk in chunks:
                await update.message.reply_text(
                    chunk, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True
                )

        logger.info(f"{ticker} 報告已發送")

    except Exception as e:
        logger.error(f"分析 {ticker} 時發生錯誤: {e}", exc_info=True)
        await loading_msg.edit_text(
            f"❌ 分析 {ticker} 時發生錯誤：\n`{str(e)}`\n\n請稍後再試。",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


def _split_message(text: str, max_length: int = 4096) -> list[str]:
    """將過長的訊息分割成多段。"""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        # 在最大長度內找到最後一個換行符
        split_pos = text.rfind("\n", 0, max_length)
        if split_pos == -1:
            split_pos = max_length

        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")

    return chunks


def create_bot_application() -> Application:
    """建立並設定 Telegram Bot Application。"""
    app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()

    # 註冊指令
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("report", report_command))

    return app
