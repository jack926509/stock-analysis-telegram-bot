"""
Telegram Bot 介面模組（三角色優化版）
- 後端：超時控制、並發限制、return_exceptions、結構化錯誤處理
- 前端：Markdown 安全發送、智能分段
- 分析師：Tavily 搜尋加入公司全名
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

# ── 後端優化：並發控制 ──
# 限制同時處理的分析請求數量，避免 API rate limit
_analysis_semaphore = asyncio.Semaphore(3)

# ── 後端優化：每個 fetcher 的超時時間（秒）──
FETCH_TIMEOUT = 30
AI_TIMEOUT = 90


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /start 指令"""
    welcome_msg = (
        "👋 歡迎使用零幻覺美股分析 Bot!\n"
        "\n"
        "我會基於真實數據為你分析美股，嚴格排除 AI 幻覺。\n"
        "\n"
        "📌 使用方式：\n"
        "  /report AAPL - 分析 Apple\n"
        "  /report TSLA - 分析 Tesla\n"
        "  /report NVDA - 分析 NVIDIA\n"
        "\n"
        "🔍 數據來源：\n"
        "  Finnhub (即時報價) | yfinance (基本面)\n"
        "  Tavily (新聞) | TradingView (技術指標)\n"
        "\n"
        "🤖 分析引擎：OpenAI GPT\n"
        "🛡️ 所有分析僅基於真實數據，零幻覺"
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

    # 驗證代碼格式
    if not ticker.isalpha() or len(ticker) > 5:
        await update.message.reply_text(
            "❌ 無效的股票代碼。請使用英文字母代碼，例如：/report AAPL"
        )
        return

    # ── 後端優化：並發控制 ──
    if _analysis_semaphore.locked():
        await update.message.reply_text(
            f"⏳ 系統繁忙中，目前有多筆分析正在處理。\n"
            f"請稍候片刻再重新查詢 /report {ticker}"
        )
        return

    async with _analysis_semaphore:
        await _execute_analysis(update, ticker)


async def _execute_analysis(update: Update, ticker: str) -> None:
    """執行完整分析流程（被 semaphore 控制並發）。"""

    loading_msg = await update.message.reply_text(
        f"⏳ 正在分析 {ticker}...\n📡 並行抓取 4 個數據源中..."
    )

    try:
        # ── Step 1: 並行抓取數據（後端優化：帶超時 + return_exceptions）──
        logger.info(f"[{ticker}] 開始並行抓取數據...")

        results = await asyncio.wait_for(
            asyncio.gather(
                fetch_finnhub_quote(ticker),
                fetch_yfinance_fundamentals(ticker),
                fetch_tradingview_analysis(ticker),
                return_exceptions=True,  # 後端優化：單一失敗不影響其他
            ),
            timeout=FETCH_TIMEOUT,
        )

        # 處理可能的異常結果
        finnhub_data = _ensure_dict(results[0], "Finnhub")
        yfinance_data = _ensure_dict(results[1], "yfinance")
        tradingview_data = _ensure_dict(results[2], "TradingView")

        logger.info(f"[{ticker}] 基礎數據抓取完成")

        # Tavily 單獨處理：使用公司全名提高搜尋精確度（分析師優化）
        company_name = yfinance_data.get("company_name", "")
        try:
            tavily_data = await asyncio.wait_for(
                fetch_tavily_news(ticker, company_name),
                timeout=FETCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            tavily_data = {"source": "Tavily", "error": "新聞搜尋超時", "news": []}
        except Exception as e:
            tavily_data = {"source": "Tavily", "error": str(e), "news": []}

        logger.info(f"[{ticker}] 所有數據抓取完成，開始 AI 分析...")

        # 更新載入訊息
        try:
            await loading_msg.edit_text(
                f"⏳ 正在分析 {ticker}...\n"
                f"✅ 數據抓取完成\n"
                f"🤖 AI 深度分析中..."
            )
        except Exception:
            pass

        # ── Step 2: AI 分析（帶超時）──
        try:
            ai_analysis = await asyncio.wait_for(
                analyze_stock(
                    ticker, finnhub_data, yfinance_data, tavily_data, tradingview_data
                ),
                timeout=AI_TIMEOUT,
            )
        except asyncio.TimeoutError:
            ai_analysis = "❌ AI 分析超時（超過 90 秒），請稍後重試。以下為原始數據供參考。"

        logger.info(f"[{ticker}] AI 分析完成，組裝報告...")

        # ── Step 3: 格式化報告 ──
        report = format_report(
            ticker, finnhub_data, yfinance_data, tavily_data,
            tradingview_data, ai_analysis,
        )

        # ── Step 4: 發送報告 ──
        try:
            await loading_msg.delete()
        except Exception:
            pass

        await _send_report(update, report)
        logger.info(f"[{ticker}] 報告已發送")

    except asyncio.TimeoutError:
        logger.error(f"[{ticker}] 數據抓取整體超時")
        try:
            await loading_msg.edit_text(
                f"❌ 分析 {ticker} 超時，數據源回應過慢。\n"
                f"請稍後再試：/report {ticker}"
            )
        except Exception:
            await update.message.reply_text(f"❌ 分析 {ticker} 超時，請稍後再試。")

    except Exception as e:
        logger.error(f"[{ticker}] 分析失敗: {e}", exc_info=True)
        error_msg = f"❌ 分析 {ticker} 時發生錯誤：\n{str(e)[:200]}\n\n請稍後再試。"
        try:
            await loading_msg.edit_text(error_msg)
        except Exception:
            try:
                await update.message.reply_text(error_msg)
            except Exception:
                pass


def _ensure_dict(result, source_name: str) -> dict:
    """
    確保 gather 的結果是 dict。
    如果是 Exception（return_exceptions=True 時），轉為錯誤 dict。
    """
    if isinstance(result, Exception):
        logger.warning(f"[{source_name}] fetcher 異常: {result}")
        return {"source": source_name, "error": f"{source_name} 錯誤: {str(result)}"}
    if isinstance(result, dict):
        return result
    return {"source": source_name, "error": f"{source_name} 回傳格式異常"}


async def _send_report(update: Update, report: str) -> None:
    """
    安全發送報告。先嘗試 Markdown，失敗用純文字。
    分段時確保不會切到 Markdown 標記中間。
    """
    chunks = _split_message(report, 4096)

    for chunk in chunks:
        try:
            await update.message.reply_text(
                chunk,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
        except Exception as md_err:
            logger.warning(f"Markdown 發送失敗: {md_err}")
            # 清理所有 Markdown 標記後用純文字發送
            clean = chunk.replace("*", "").replace("_", "").replace("`", "")
            try:
                await update.message.reply_text(
                    clean, disable_web_page_preview=True
                )
            except Exception as txt_err:
                logger.error(f"純文字發送也失敗: {txt_err}")


def _split_message(text: str, max_length: int = 4096) -> list[str]:
    """
    將過長的訊息分割成多段。
    優先在段落分隔線處切割，避免破壞格式。
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        # 優先在分隔線處切割
        split_pos = text.rfind("━━━━", 0, max_length)
        if split_pos == -1:
            split_pos = text.rfind("══════", 0, max_length)
        if split_pos == -1:
            split_pos = text.rfind("\n\n", 0, max_length)
        if split_pos == -1:
            split_pos = text.rfind("\n", 0, max_length)
        if split_pos == -1:
            split_pos = max_length

        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")

    return chunks


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """全局錯誤處理器。"""
    logger.error(f"未處理的異常: {context.error}", exc_info=context.error)

    if update and isinstance(update, Update) and update.message:
        try:
            await update.message.reply_text(
                "❌ 系統發生未預期的錯誤，請稍後再試。"
            )
        except Exception:
            pass


def create_bot_application() -> Application:
    """建立並設定 Telegram Bot Application。"""
    app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("report", report_command))
    app.add_error_handler(error_handler)

    return app
