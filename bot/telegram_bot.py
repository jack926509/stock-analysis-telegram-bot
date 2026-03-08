"""
Telegram Bot 介面模組
處理使用者指令，串接數據抓取與 AI 分析流程。
"""

import asyncio
import logging
import traceback

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
        "👋 歡迎使用零幻覺美股分析 Bot!\n"
        "\n"
        "我會基於真實數據為你分析美股，嚴格排除 AI 幻覺。\n"
        "\n"
        "📌 使用方式：\n"
        "  /report AAPL - 分析 Apple 股票\n"
        "  /report TSLA - 分析 Tesla 股票\n"
        "  /report MSFT - 分析 Microsoft 股票\n"
        "\n"
        "🔍 數據來源：\n"
        "  ├ Finnhub - 即時報價\n"
        "  ├ yfinance - 基本面數據\n"
        "  ├ Tavily - 真實新聞\n"
        "  └ TradingView - 技術指標\n"
        "\n"
        "🤖 分析引擎：OpenAI GPT\n"
        "🛡️ 反幻覺機制：所有分析僅基於真實數據"
    )
    await update.message.reply_text(welcome_msg)


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    處理 /report [TICKER] 指令。
    核心流程：並行抓取數據 → AI 分析 → 格式化報告 → 回傳。
    """
    # 驗證參數
    if not context.args or len(context.args) == 0:
        await update.message.reply_text(
            "❌ 請提供股票代碼，例如：/report AAPL"
        )
        return

    ticker = context.args[0].upper()

    # 驗證代碼格式（只允許英文字母，1-5 個字元）
    if not ticker.isalpha() or len(ticker) > 5:
        await update.message.reply_text(
            "❌ 無效的股票代碼。請使用英文字母代碼，例如：/report AAPL"
        )
        return

    # 發送載入訊息
    loading_msg = await update.message.reply_text(
        f"⏳ 正在分析 {ticker}，請稍候...\n\n"
        f"📡 正在並行抓取四個數據源..."
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
        try:
            await loading_msg.edit_text(
                f"⏳ 正在分析 {ticker}，請稍候...\n\n"
                f"✅ 數據抓取完成\n"
                f"🤖 正在進行 AI 深度分析..."
            )
        except Exception:
            pass  # 編輯載入訊息失敗不影響主流程

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
        try:
            await loading_msg.delete()
        except Exception:
            pass

        # Telegram 訊息有 4096 字元限制，需要分段發送
        # 先嘗試用 Markdown 發送，失敗則用純文字
        chunks = _split_message(report, 4096)
        for chunk in chunks:
            try:
                await update.message.reply_text(
                    chunk,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                )
            except Exception as md_error:
                logger.warning(f"Markdown 發送失敗，改用純文字: {md_error}")
                # Markdown 解析失敗時，用純文字發送
                clean_text = chunk.replace("*", "").replace("_", "").replace("`", "")
                await update.message.reply_text(
                    clean_text,
                    disable_web_page_preview=True,
                )

        logger.info(f"{ticker} 報告已發送")

    except Exception as e:
        logger.error(f"分析 {ticker} 時發生錯誤: {e}", exc_info=True)
        error_detail = traceback.format_exc()
        logger.error(f"完整錯誤: {error_detail}")
        try:
            await loading_msg.edit_text(
                f"❌ 分析 {ticker} 時發生錯誤：\n{str(e)}\n\n請稍後再試。"
            )
        except Exception:
            await update.message.reply_text(
                f"❌ 分析 {ticker} 時發生錯誤：\n{str(e)}\n\n請稍後再試。"
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


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """全局錯誤處理器，捕獲所有未處理的異常。"""
    logger.error(f"發生未處理的異常: {context.error}", exc_info=context.error)

    if update and isinstance(update, Update) and update.message:
        try:
            await update.message.reply_text(
                "❌ 抱歉，發生了未預期的錯誤。請稍後再試。"
            )
        except Exception:
            pass


def create_bot_application() -> Application:
    """建立並設定 Telegram Bot Application。"""
    app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()

    # 註冊指令
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("report", report_command))

    # 註冊全局錯誤處理
    app.add_error_handler(error_handler)

    return app
