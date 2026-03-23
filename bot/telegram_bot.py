"""
Telegram Bot 介面模組（三角色優化版 v3）
- 後端：超時控制、並發限制、return_exceptions、快取、Rate Limiting、查詢記錄
- 前端：Markdown 安全發送、智能分段、自選股清單
- 分析師：Tavily 搜尋加入公司全名、歷史回測、同業比較、支撐壓力位、ETF 支援
"""

import asyncio
import logging
import re
import time

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
from fetchers.history_fetcher import fetch_history_analysis
from fetchers.peer_fetcher import fetch_peer_comparison
from analyzer.anthropic_analyzer import analyze_stock
from utils.formatter import format_report
from utils.rate_limiter import rate_limiter
from utils.database import (
    add_to_watchlist,
    remove_from_watchlist,
    get_watchlist,
    record_query,
)

logger = logging.getLogger(__name__)

# ── 並發控制 ──
_analysis_semaphore = asyncio.Semaphore(3)

# ── 超時設定（秒）──
FETCH_TIMEOUT = 30
EXTENDED_FETCH_TIMEOUT = 45  # 含歷史 + 同業
AI_TIMEOUT = 90

# ── 快取 ──
_report_cache: dict[str, tuple[str, float]] = {}

# ── ETF 支援：允許數字的 ticker 驗證 ──
_TICKER_PATTERN = re.compile(r'^[A-Z]{1,5}$')
_ETF_PATTERN = re.compile(r'^[A-Z0-9]{1,5}$')


def _validate_ticker(ticker: str) -> bool:
    """驗證 ticker 格式。支援股票（純字母）和 ETF（字母+數字）。"""
    return bool(_ETF_PATTERN.match(ticker))


# ══════════════════════════════════════════
# 指令處理器
# ══════════════════════════════════════════


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /start 指令"""
    welcome_msg = (
        "👋 歡迎使用零幻覺美股分析 Bot!\n"
        "\n"
        "我會基於真實數據為你分析美股，嚴格排除 AI 幻覺。\n"
        "\n"
        "📌 分析指令：\n"
        "  /report AAPL — 分析 Apple\n"
        "  /report TSLA — 分析 Tesla\n"
        "  /report SPY  — 分析 ETF\n"
        "\n"
        "📋 自選股指令：\n"
        "  /watchlist       — 查看自選股清單\n"
        "  /watch AAPL      — 加入自選股\n"
        "  /unwatch AAPL    — 移除自選股\n"
        "\n"
        "🔍 數據來源：\n"
        "  Finnhub | yfinance | Tavily | TradingView\n"
        "\n"
        "🤖 分析引擎：Anthropic Claude\n"
        "🛡️ 所有分析僅基於真實數據，零幻覺"
    )
    await update.message.reply_text(welcome_msg)


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    處理 /report [TICKER] 指令。
    核心流程：Rate Limit → 快取 → 並行抓取數據 → AI 分析 → 格式化報告 → 回傳。
    """
    if not context.args:
        await update.message.reply_text(
            "❌ 請提供股票代碼，例如：/report AAPL"
        )
        return

    ticker = context.args[0].upper()

    if not _validate_ticker(ticker):
        await update.message.reply_text(
            "❌ 無效的股票代碼。請使用英文代碼（1-5 字），例如：/report AAPL"
        )
        return

    user_id = update.effective_user.id

    # ── Rate Limiting ──
    if not rate_limiter.is_allowed(user_id):
        wait = rate_limiter.retry_after(user_id)
        await update.message.reply_text(
            f"⏰ 請求過於頻繁，請 {wait} 秒後再試。\n"
            f"（每分鐘最多 {Config.RATE_LIMIT_PER_MINUTE} 次查詢）"
        )
        return

    # ── 記錄查詢 ──
    try:
        await record_query(user_id, ticker)
    except Exception:
        pass  # 記錄失敗不影響核心功能

    # ── 健康計數 ──
    try:
        from utils.health import increment_request_count
        increment_request_count()
    except Exception:
        pass

    # ── 快取檢查 ──
    cache_key = ticker
    cached = _report_cache.get(cache_key)
    if cached:
        report, cached_time = cached
        if time.time() - cached_time < Config.CACHE_TTL:
            age = int(time.time() - cached_time)
            await update.message.reply_text(f"⚡ 使用 {age} 秒前的快取結果")
            await _send_report(update, report)
            return

    # ── 並發控制 ──
    if _analysis_semaphore._value <= 0:
        await update.message.reply_text(
            f"⏳ 系統繁忙中，目前有多筆分析正在處理。\n"
            f"請稍候片刻再重新查詢 /report {ticker}"
        )
        return

    async with _analysis_semaphore:
        await _execute_analysis(update, ticker)


async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /watchlist 指令：顯示自選股清單。"""
    user_id = update.effective_user.id
    tickers = await get_watchlist(user_id)

    if not tickers:
        await update.message.reply_text(
            "📋 你的自選股清單是空的。\n\n"
            "使用 /watch AAPL 加入股票\n"
            "或 /report AAPL 直接分析"
        )
        return

    lines = ["📋 *你的自選股清單*", ""]
    for i, t in enumerate(tickers, 1):
        lines.append(f"  {i}. {t}  ➜ /report {t}")
    lines.append(f"\n共 {len(tickers)} 檔股票")
    lines.append("使用 /unwatch AAPL 移除")

    try:
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        await update.message.reply_text("\n".join(lines).replace("*", ""))


async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /watch [TICKER] 指令：加入自選股。"""
    if not context.args:
        await update.message.reply_text("❌ 請提供股票代碼，例如：/watch AAPL")
        return

    ticker = context.args[0].upper()
    if not _validate_ticker(ticker):
        await update.message.reply_text("❌ 無效的股票代碼")
        return

    user_id = update.effective_user.id
    added = await add_to_watchlist(user_id, ticker)

    if added:
        await update.message.reply_text(f"✅ 已將 {ticker} 加入自選股清單")
    else:
        await update.message.reply_text(f"ℹ️ {ticker} 已在自選股清單中")


async def unwatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /unwatch [TICKER] 指令：移除自選股。"""
    if not context.args:
        await update.message.reply_text("❌ 請提供股票代碼，例如：/unwatch AAPL")
        return

    ticker = context.args[0].upper()
    user_id = update.effective_user.id
    removed = await remove_from_watchlist(user_id, ticker)

    if removed:
        await update.message.reply_text(f"✅ 已將 {ticker} 從自選股清單移除")
    else:
        await update.message.reply_text(f"ℹ️ {ticker} 不在自選股清單中")


# ══════════════════════════════════════════
# 核心分析流程
# ══════════════════════════════════════════


async def _execute_analysis(update: Update, ticker: str) -> None:
    """執行完整分析流程（被 semaphore 控制並發）。"""

    loading_msg = await update.message.reply_text(
        f"⏳ 正在分析 {ticker}...\n📡 並行抓取 6 個數據源中..."
    )

    try:
        # ── Step 1: 並行抓取基礎數據 ──
        logger.info(f"[{ticker}] 開始並行抓取數據...")

        results = await asyncio.wait_for(
            asyncio.gather(
                fetch_finnhub_quote(ticker),
                fetch_yfinance_fundamentals(ticker),
                fetch_tradingview_analysis(ticker),
                return_exceptions=True,
            ),
            timeout=FETCH_TIMEOUT,
        )

        finnhub_data = _ensure_dict(results[0], "Finnhub")
        yfinance_data = _ensure_dict(results[1], "yfinance")
        tradingview_data = _ensure_dict(results[2], "TradingView")

        logger.info(f"[{ticker}] 基礎數據抓取完成")

        # ── Step 1.5: 並行抓取擴展數據（Tavily + 歷史 + 同業）──
        company_name = yfinance_data.get("company_name", "")
        sector = yfinance_data.get("sector", "")
        industry = yfinance_data.get("industry", "")

        extended_tasks = [fetch_tavily_news(ticker, company_name)]

        if Config.HISTORY_ENABLED:
            extended_tasks.append(fetch_history_analysis(ticker))

        if Config.PEER_COMPARISON_ENABLED and sector and sector != "N/A":
            extended_tasks.append(fetch_peer_comparison(ticker, sector, industry))

        try:
            ext_results = await asyncio.wait_for(
                asyncio.gather(*extended_tasks, return_exceptions=True),
                timeout=EXTENDED_FETCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            ext_results = [{"source": "extended", "error": "擴展數據超時"}] * len(extended_tasks)

        # 解析擴展結果
        tavily_data = _ensure_dict(ext_results[0], "Tavily")

        history_data = {}
        peer_data = {}
        idx = 1
        if Config.HISTORY_ENABLED:
            history_data = _ensure_dict(ext_results[idx], "History") if idx < len(ext_results) else {}
            idx += 1
        if Config.PEER_COMPARISON_ENABLED and sector and sector != "N/A":
            peer_data = _ensure_dict(ext_results[idx], "Peer") if idx < len(ext_results) else {}

        logger.info(f"[{ticker}] 所有數據抓取完成，開始 AI 分析...")

        # 更新載入訊息
        try:
            await loading_msg.edit_text(
                f"⏳ 正在分析 {ticker}...\n"
                f"✅ 數據抓取完成（6 源）\n"
                f"🤖 AI 深度分析中..."
            )
        except Exception:
            pass

        # ── Step 2: AI 分析 ──
        try:
            ai_analysis = await asyncio.wait_for(
                analyze_stock(
                    ticker, finnhub_data, yfinance_data, tavily_data,
                    tradingview_data, history_data, peer_data,
                ),
                timeout=AI_TIMEOUT,
            )
        except asyncio.TimeoutError:
            ai_analysis = "❌ AI 分析超時（超過 90 秒），請稍後重試。以下為原始數據供參考。"

        logger.info(f"[{ticker}] AI 分析完成，組裝報告...")

        # ── Step 3: 格式化報告 ──
        report = format_report(
            ticker, finnhub_data, yfinance_data, tavily_data,
            tradingview_data, ai_analysis, history_data, peer_data,
        )

        # ── Step 4: 快取並發送報告 ──
        _report_cache[ticker] = (report, time.time())

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


# ══════════════════════════════════════════
# 工具函數
# ══════════════════════════════════════════


def _ensure_dict(result, source_name: str) -> dict:
    """確保 gather 的結果是 dict。"""
    if isinstance(result, Exception):
        logger.warning(f"[{source_name}] fetcher 異常: {result}")
        return {"source": source_name, "error": f"{source_name} 錯誤: {str(result)}"}
    if isinstance(result, dict):
        return result
    return {"source": source_name, "error": f"{source_name} 回傳格式異常"}


async def _send_report(update: Update, report: str) -> None:
    """安全發送報告。先嘗試 Markdown，失敗用純文字。"""
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
            clean = chunk.replace("*", "").replace("_", "").replace("`", "")
            try:
                await update.message.reply_text(
                    clean, disable_web_page_preview=True
                )
            except Exception as txt_err:
                logger.error(f"純文字發送也失敗: {txt_err}")


def _split_message(text: str, max_length: int = 4096) -> list[str]:
    """將過長的訊息分割成多段。"""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

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

    # 分析指令
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("report", report_command))

    # 自選股指令
    app.add_handler(CommandHandler("watchlist", watchlist_command))
    app.add_handler(CommandHandler("watch", watch_command))
    app.add_handler(CommandHandler("unwatch", unwatch_command))

    app.add_error_handler(error_handler)

    return app
