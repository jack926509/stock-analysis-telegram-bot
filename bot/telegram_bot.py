"""
Telegram Bot 介面模組（v5）
- HTML parse mode（取代 legacy Markdown，對特殊字元更寬容）
- 共用 _run_analysis(chat_id, user_id, ticker, bot)，移除 fake-update hack
- 新增 /help /compare /chart 指令 + setMyCommands 選單
- 輸入檢查、分析途中的 typing ChatAction、快取回應不發通知
"""

import asyncio
import html
import logging
import re

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from telegram.constants import ChatAction, ParseMode

from config import Config
from fetchers.finnhub_fetcher import fetch_finnhub_quote
from fetchers.fmp_fetcher import fetch_fmp_fundamentals, fetch_fmp_batch_prices
from fetchers.yfinance_fetcher import fetch_yfinance_fundamentals
from fetchers.tavily_fetcher import fetch_tavily_news
from fetchers.tradingview_fetcher import fetch_tradingview_analysis
from fetchers.history_fetcher import fetch_history_analysis
from fetchers.peer_fetcher import fetch_peer_comparison
from fetchers.analyst_fetcher import fetch_analyst_data
from fetchers.insider_fetcher import fetch_insider_transactions
from fetchers.earnings_surprise_fetcher import fetch_earnings_surprises
from fetchers.macro_fetcher import fetch_macro_data
from analyzer.anthropic_analyzer import analyze_stock
from utils.formatter import format_report
from utils.cache import raw_cache, report_cache
from utils.chart import generate_chart
from utils.signals import compute_signals
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
FETCH_TIMEOUT = 45
EXTENDED_FETCH_TIMEOUT = 60  # yfinance 重試最多 21s + 其他數據源
AI_TIMEOUT = 90

# ── Ticker 驗證：允許字母與數字（ETF / share class 支援）──
_TICKER_PATTERN = re.compile(r"^[A-Z0-9]{1,5}$")


def _esc(value) -> str:
    """HTML escape helper for bot-side composed messages."""
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


def _validate_ticker(ticker: str) -> bool:
    """Validate ticker symbol (letters + digits, 1–5 chars)."""
    return bool(_TICKER_PATTERN.match(ticker))


# ══════════════════════════════════════════
# 指令處理器
# ══════════════════════════════════════════


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /start 指令"""
    welcome_msg = (
        "👋 歡迎使用美股分析 Bot!\n"
        "\n"
        "📌 指令：\n"
        "  /report AAPL — 完整深度分析報告\n"
        "  /chart AAPL — 僅 60 日 K 線圖\n"
        "  /compare AAPL MSFT NVDA — 多股橫向對比\n"
        "  /watchlist — 自選股清單（即時報價）\n"
        "  /scan — 自選股批次快掃總覽\n"
        "  /watch AAPL  /unwatch AAPL — 新增 / 移除\n"
        "  /help — 詳細指令說明\n"
        "\n"
        "🔍 FMP + yfinance 雙源基本面\n"
        "🧮 12 維度量化信號引擎\n"
        "🏦 分析師·內部人·EPS 驚喜\n"
        "🌍 VIX·殖利率 宏觀環境\n"
        "🤖 Claude 四觀點深度分析\n"
        "📊 所有分析皆基於真實數據，原始數據與 AI 分析同步展示"
    )
    await update.message.reply_text(welcome_msg)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /help 指令 — 詳細指令與用法說明。"""
    msg = (
        "<b>🧭 指令手冊</b>\n\n"
        "<b>/report &lt;TICKER&gt;</b>\n"
        "  產生完整深度分析報告，包含 12 維度量化信號 + Claude AI 分析。\n"
        "  範例：<code>/report AAPL</code>\n\n"
        "<b>/chart &lt;TICKER&gt;</b>\n"
        "  只發送 60 日 K 線圖（MA5/MA20/MA60），不跑 AI 分析，秒回。\n"
        "  範例：<code>/chart TSLA</code>\n\n"
        "<b>/compare &lt;TICKER1&gt; &lt;TICKER2&gt; ...</b>\n"
        "  並排對比 2–5 檔個股的即時價、建議、RSI、市值。\n"
        "  範例：<code>/compare AAPL MSFT NVDA</code>\n\n"
        "<b>/watchlist</b>\n"
        "  顯示自選股即時報價。\n\n"
        "<b>/scan</b>\n"
        "  批次快掃自選股（報價 + 技術面共識 + RSI）。\n\n"
        "<b>/watch &lt;TICKER&gt;</b> / <b>/unwatch &lt;TICKER&gt;</b>\n"
        "  新增 / 移除自選股。\n\n"
        "📡 資料來源：Finnhub · FMP · yfinance · TradingView · Tavily\n"
        "🛡️ 反幻覺：所有 AI 分析皆需數據佐證，缺失即標 N/A。"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /report [TICKER] 指令。"""
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

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # ── Rate Limiting ──
    if not rate_limiter.is_allowed(user_id):
        wait = rate_limiter.retry_after(user_id)
        await update.message.reply_text(
            f"⏰ 請求過於頻繁，請 {wait} 秒後再試。\n"
            f"（每分鐘最多 {Config.RATE_LIMIT_PER_MINUTE} 次查詢）"
        )
        return

    await _dispatch_analysis(chat_id, user_id, ticker, context.bot)


async def _dispatch_analysis(chat_id: int, user_id: int, ticker: str, bot) -> None:
    """Common entry-point shared by /report command and InlineKeyboard callbacks."""

    # ── 記錄查詢 ──
    try:
        await record_query(user_id, ticker)
    except Exception:
        pass

    # ── 健康計數 ──
    try:
        from utils.health import increment_request_count
        increment_request_count()
    except Exception:
        pass

    # ── 報告快取檢查 ──
    cached_report = report_cache.get(ticker)
    if cached_report:
        age = report_cache.get_age(ticker) or 0
        await bot.send_message(
            chat_id=chat_id,
            text=f"⚡ 使用 {age} 秒前的快取結果",
            disable_notification=True,
        )
        keyboard = _build_report_keyboard(ticker)
        await _send_report(chat_id, bot, cached_report, reply_markup=keyboard)
        return

    # ── 並發控制 ──
    if _analysis_semaphore.locked():
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"⏳ 系統繁忙中，目前有 3 筆分析正在處理。\n"
                f"請稍候片刻再重新查詢 /report {ticker}"
            ),
        )
        return

    async with _analysis_semaphore:
        await _execute_analysis(chat_id, ticker, bot)


async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /watchlist 指令：顯示自選股清單 + 即時報價。"""
    user_id = update.effective_user.id
    tickers = await get_watchlist(user_id)

    if not tickers:
        await update.message.reply_text(
            "📋 你的自選股清單是空的。\n\n"
            "使用 /watch AAPL 加入股票\n"
            "或 /report AAPL 直接分析"
        )
        return

    loading = await update.message.reply_text("📋 正在載入自選股即時報價...")

    prices = await fetch_fmp_batch_prices(tickers)

    lines = ["📋 <b>自選股清單</b>  ─  即時報價", ""]
    for i, t in enumerate(tickers, 1):
        quote = prices.get(t, {})
        if quote and quote.get("price") is not None:
            price = quote["price"]
            chg = quote.get("change", 0) or 0
            chg_pct = quote.get("change_pct", 0) or 0
            arrow = "🟢" if chg >= 0 else "🔴"
            sign = "+" if chg >= 0 else ""
            lines.append(
                f"  {i}. <b>{_esc(t)}</b>  ${price:.2f}  "
                f"{arrow}{sign}{chg:.2f} ({sign}{chg_pct:.2f}%)"
            )
        else:
            lines.append(f"  {i}. <b>{_esc(t)}</b>  報價載入中...")

    lines.append(f"\n共 {len(tickers)} 檔  |  /scan 批次快掃")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 批次快掃", callback_data="scanall"),
            InlineKeyboardButton("📋 管理清單", callback_data="manage_wl"),
        ]
    ])

    await loading.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


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


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /scan 指令：批次快掃自選股（報價 + 技術面 + 關鍵指標）。"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    await _run_scan(chat_id, user_id, context.bot)


async def _run_scan(chat_id: int, user_id: int, bot) -> None:
    tickers = await get_watchlist(user_id)

    if not tickers:
        await bot.send_message(
            chat_id=chat_id,
            text="📋 自選股清單是空的。\n使用 /watch AAPL 加入股票",
        )
        return

    truncated = len(tickers) > 10
    if truncated:
        tickers = tickers[:10]

    loading = await bot.send_message(
        chat_id=chat_id,
        text=f"🔍 正在掃描 {len(tickers)} 檔自選股...",
    )

    prices = await fetch_fmp_batch_prices(tickers)

    ta_tasks = [fetch_tradingview_analysis(t) for t in tickers]
    try:
        ta_results = await asyncio.wait_for(
            asyncio.gather(*ta_tasks, return_exceptions=True),
            timeout=20,
        )
    except asyncio.TimeoutError:
        ta_results = [{}] * len(tickers)

    lines = ["📊 <b>自選股批次快掃</b>", ""]

    for i, t in enumerate(tickers):
        quote = prices.get(t, {})
        ta = _ensure_dict(ta_results[i], "TA") if i < len(ta_results) else {}

        price_str = f"${quote['price']:.2f}" if quote.get("price") else "N/A"
        chg = quote.get("change", 0) or 0
        chg_pct = quote.get("change_pct", 0) or 0
        arrow = "🟢" if chg >= 0 else "🔴"
        sign = "+" if chg >= 0 else ""

        summary_val = ta.get("summary", {})
        rec = summary_val.get("RECOMMENDATION", "N/A") if isinstance(summary_val, dict) else "N/A"
        if rec == "N/A":
            rec = ta.get("recommendation", "N/A")

        rsi_val = ta.get("rsi_14", ta.get("rsi", "N/A"))
        if isinstance(rsi_val, (int, float)):
            if rsi_val > 70:
                rsi_tag = "超買"
            elif rsi_val < 30:
                rsi_tag = "超賣"
            else:
                rsi_tag = f"{rsi_val:.0f}"
        else:
            rsi_tag = "N/A"

        rec_map = {
            "STRONG_BUY": "強買", "BUY": "買入",
            "NEUTRAL": "中性", "SELL": "賣出", "STRONG_SELL": "強賣",
        }
        rec_zh = rec_map.get(rec, rec)

        mcap = quote.get("market_cap")
        cap_str = ""
        if mcap and isinstance(mcap, (int, float)):
            if mcap >= 1e12:
                cap_str = f"{mcap / 1e12:.1f}T"
            elif mcap >= 1e9:
                cap_str = f"{mcap / 1e9:.0f}B"
            else:
                cap_str = f"{mcap / 1e6:.0f}M"

        lines.append(f"<b>{_esc(t)}</b> {price_str} {arrow}{sign}{chg_pct:.1f}%")
        detail_parts = []
        if cap_str:
            detail_parts.append(cap_str)
        detail_parts.append(f"TV:{_esc(rec_zh)}")
        detail_parts.append(f"RSI:{_esc(rsi_tag)}")
        lines.append(f"  {' | '.join(detail_parts)}  ➜ /report {_esc(t)}")
        lines.append("")

    footer = f"共 {len(tickers)} 檔  |  點選 /report 查看完整分析"
    if truncated:
        footer = "⚠️ 清單超過 10 檔，僅顯示前 10  |  " + footer
    lines.append(footer)

    await loading.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /chart [TICKER]：僅回傳 60 日 K 線圖，不做 AI 分析。"""
    if not context.args:
        await update.message.reply_text("❌ 請提供股票代碼，例如：/chart AAPL")
        return

    ticker = context.args[0].upper()
    if not _validate_ticker(ticker):
        await update.message.reply_text("❌ 無效的股票代碼")
        return

    user_id = update.effective_user.id
    if not rate_limiter.is_allowed(user_id):
        wait = rate_limiter.retry_after(user_id)
        await update.message.reply_text(f"⏰ 請 {wait} 秒後再試")
        return

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)

    chart_buf = await generate_chart(ticker)
    if not chart_buf:
        await update.message.reply_text(
            f"❌ 無法生成 {ticker} 的 K 線圖（資料源可能暫時不可用）"
        )
        return

    await update.message.reply_photo(
        photo=chart_buf,
        caption=f"📈 {ticker} — 60 日 K 線圖（MA5/MA20/MA60）",
    )


async def compare_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /compare TICKER1 TICKER2 ...：並排比較 2-5 檔個股。"""
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ 請提供至少 2 檔股票代碼，例如：/compare AAPL MSFT NVDA"
        )
        return

    tickers = [t.upper() for t in context.args[:5]]
    invalid = [t for t in tickers if not _validate_ticker(t)]
    if invalid:
        await update.message.reply_text(f"❌ 無效的股票代碼：{', '.join(invalid)}")
        return

    user_id = update.effective_user.id
    if not rate_limiter.is_allowed(user_id):
        wait = rate_limiter.retry_after(user_id)
        await update.message.reply_text(f"⏰ 請 {wait} 秒後再試")
        return

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)

    loading = await update.message.reply_text(
        f"⚖️ 正在並排比較 {len(tickers)} 檔..."
    )

    prices = await fetch_fmp_batch_prices(tickers)
    ta_tasks = [fetch_tradingview_analysis(t) for t in tickers]
    try:
        ta_results = await asyncio.wait_for(
            asyncio.gather(*ta_tasks, return_exceptions=True),
            timeout=20,
        )
    except asyncio.TimeoutError:
        ta_results = [{}] * len(tickers)

    lines = ["⚖️ <b>個股對比</b>", ""]
    rec_map = {
        "STRONG_BUY": "🟢 強買", "BUY": "🟢 買入",
        "NEUTRAL": "🟡 中性", "SELL": "🔴 賣出", "STRONG_SELL": "🔴 強賣",
    }

    for i, t in enumerate(tickers):
        quote = prices.get(t, {})
        ta = _ensure_dict(ta_results[i], "TA") if i < len(ta_results) else {}

        price = quote.get("price")
        price_str = f"${price:.2f}" if isinstance(price, (int, float)) else "N/A"
        chg_pct = quote.get("change_pct", 0) or 0
        arrow = "🟢" if chg_pct >= 0 else "🔴"
        sign = "+" if chg_pct >= 0 else ""

        rec = ta.get("recommendation", "N/A")
        rec_str = rec_map.get(rec, f"⚪ {rec}")

        rsi = ta.get("rsi_14", "N/A")
        if isinstance(rsi, (int, float)):
            rsi_str = f"{rsi:.0f}"
            if rsi > 70:
                rsi_str += " ⚠️超買"
            elif rsi < 30:
                rsi_str += " ⚠️超賣"
        else:
            rsi_str = "N/A"

        mcap = quote.get("market_cap")
        cap_str = ""
        if mcap and isinstance(mcap, (int, float)):
            if mcap >= 1e12:
                cap_str = f"{mcap / 1e12:.2f}T"
            elif mcap >= 1e9:
                cap_str = f"{mcap / 1e9:.1f}B"
            else:
                cap_str = f"{mcap / 1e6:.0f}M"

        lines.append(f"<b>{_esc(t)}</b> — {price_str} {arrow}{sign}{chg_pct:.2f}%")
        lines.append(f"  建議: {rec_str}  |  RSI: {_esc(rsi_str)}")
        if cap_str:
            lines.append(f"  市值: {cap_str}")
        lines.append("")

    lines.append("➜ /report TICKER 查看單檔完整分析")
    await loading.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ══════════════════════════════════════════
# 核心分析流程
# ══════════════════════════════════════════


async def _execute_analysis(chat_id: int, ticker: str, bot) -> None:
    """執行完整分析流程（被 semaphore 控制並發）。"""

    loading_msg = await bot.send_message(
        chat_id=chat_id,
        text=f"⏳ 正在分析 {ticker}...\n📡 並行抓取 10+ 數據源中...",
    )

    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)

        # ── Step 1: 並行抓取基礎數據（帶 raw 快取）──
        cached_raw = raw_cache.get(f"{ticker}:base")
        if cached_raw:
            finnhub_data, yfinance_data, tradingview_data = cached_raw
            logger.info(f"[{ticker}] 使用 raw cache 基礎數據")
        else:
            logger.info(f"[{ticker}] 開始並行抓取數據...")
            results = await asyncio.wait_for(
                asyncio.gather(
                    fetch_finnhub_quote(ticker),
                    fetch_fmp_fundamentals(ticker),
                    fetch_tradingview_analysis(ticker),
                    return_exceptions=True,
                ),
                timeout=FETCH_TIMEOUT,
            )
            finnhub_data = _ensure_dict(results[0], "Finnhub")
            fmp_data = _ensure_dict(results[1], "FMP")
            tradingview_data = _ensure_dict(results[2], "TradingView")

            # FMP primary → yfinance fallback
            if "error" in fmp_data:
                logger.warning(f"[{ticker}] FMP 失敗，切換 yfinance 備援")
                try:
                    yfinance_data = await asyncio.wait_for(
                        fetch_yfinance_fundamentals(ticker),
                        timeout=30,
                    )
                    yfinance_data = _ensure_dict(yfinance_data, "yfinance")
                except Exception as yf_err:
                    logger.error(f"[{ticker}] yfinance 備援也失敗: {yf_err}")
                    yfinance_data = fmp_data
            else:
                yfinance_data = fmp_data

            raw_cache.set(f"{ticker}:base", (finnhub_data, yfinance_data, tradingview_data))
            logger.info(f"[{ticker}] 基礎數據抓取完成 (源: {yfinance_data.get('source', '?')})")

        # ── Step 1.5: 擴展數據（Tavily + 歷史 + 同業 + 分析師 + 內部人 + EPS + 宏觀）──
        company_name = yfinance_data.get("company_name", "")
        sector = yfinance_data.get("sector", "")
        industry = yfinance_data.get("industry", "")

        extended_tasks = [fetch_tavily_news(ticker, company_name)]
        task_labels = ["Tavily"]

        if Config.HISTORY_ENABLED:
            extended_tasks.append(fetch_history_analysis(ticker))
            task_labels.append("History")

        if Config.PEER_COMPARISON_ENABLED and sector and sector != "N/A":
            extended_tasks.append(fetch_peer_comparison(ticker, sector, industry))
            task_labels.append("Peer")

        extended_tasks.append(fetch_analyst_data(ticker))
        task_labels.append("Analyst")
        extended_tasks.append(fetch_insider_transactions(ticker))
        task_labels.append("Insider")
        extended_tasks.append(fetch_earnings_surprises(ticker))
        task_labels.append("Earnings")
        extended_tasks.append(fetch_macro_data())
        task_labels.append("Macro")

        chart_task = generate_chart(ticker)

        try:
            ext_results, chart_buf = await asyncio.wait_for(
                asyncio.gather(
                    asyncio.gather(*extended_tasks, return_exceptions=True),
                    chart_task,
                ),
                timeout=EXTENDED_FETCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            ext_results = [{"source": "extended", "error": "擴展數據超時"}] * len(extended_tasks)
            chart_buf = None

        ext_map = {}
        for i, label in enumerate(task_labels):
            ext_map[label] = _ensure_dict(ext_results[i], label) if i < len(ext_results) else {}

        tavily_data = ext_map.get("Tavily", {})
        history_data = ext_map.get("History", {})
        peer_data = ext_map.get("Peer", {})
        analyst_data = ext_map.get("Analyst", {})
        insider_data = ext_map.get("Insider", {})
        earnings_data = ext_map.get("Earnings", {})
        macro_data = ext_map.get("Macro", {})

        # ── Step 1.6: 12 維度量化信號共識 ──
        signals_data = compute_signals(
            finnhub_data=finnhub_data,
            yfinance_data=yfinance_data,
            tradingview_data=tradingview_data,
            history_data=history_data if history_data and "error" not in history_data else None,
            peer_data=peer_data if peer_data and "error" not in peer_data else None,
            analyst_data=analyst_data if analyst_data and "error" not in analyst_data else None,
            insider_data=insider_data if insider_data and "error" not in insider_data else None,
            earnings_data=earnings_data if earnings_data and "error" not in earnings_data else None,
            macro_data=macro_data if macro_data and "error" not in macro_data else None,
        )

        total_sources = 6 + sum(
            1 for d in [history_data, peer_data, analyst_data, insider_data, earnings_data, macro_data]
            if d and "error" not in d
        )
        logger.info(f"[{ticker}] 所有數據抓取完成（{total_sources} 源），開始 AI 分析...")

        try:
            await loading_msg.edit_text(
                f"⏳ 正在分析 {ticker}...\n"
                f"✅ 數據抓取完成（{total_sources} 源）\n"
                f"🧮 量化信號：{signals_data.get('consensus', 'N/A')}\n"
                f"🤖 AI 四觀點深度分析中..."
            )
        except Exception:
            pass

        await bot.send_chat_action(chat_id, ChatAction.TYPING)

        # ── Step 2: AI 分析 ──
        try:
            ai_analysis = await asyncio.wait_for(
                analyze_stock(
                    ticker, finnhub_data, yfinance_data, tavily_data,
                    tradingview_data, history_data, peer_data,
                    analyst_data, insider_data, earnings_data,
                    macro_data, signals_data,
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
            signals_data=signals_data,
            analyst_data=analyst_data,
            insider_data=insider_data,
            earnings_data=earnings_data,
            macro_data=macro_data,
        )

        # ── Step 4: 快取並發送 ──
        report_cache.set(ticker, report)

        try:
            await loading_msg.delete()
        except Exception:
            pass

        if chart_buf:
            try:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=chart_buf,
                    caption=f"📈 {ticker.upper()} — 60 日 K 線圖（MA5/MA20/MA60）",
                )
            except Exception as chart_err:
                logger.warning(f"[{ticker}] K 線圖發送失敗: {chart_err}")

        keyboard = _build_report_keyboard(ticker)
        await _send_report(chat_id, bot, report, reply_markup=keyboard)
        logger.info(f"[{ticker}] 報告已發送")

    except asyncio.TimeoutError:
        logger.error(f"[{ticker}] 數據抓取整體超時")
        try:
            await loading_msg.edit_text(
                f"❌ 分析 {ticker} 超時，數據源回應過慢。\n"
                f"請稍後再試：/report {ticker}"
            )
        except Exception:
            await bot.send_message(chat_id=chat_id, text=f"❌ 分析 {ticker} 超時，請稍後再試。")

    except Exception as e:
        logger.error(f"[{ticker}] 分析失敗: {e}", exc_info=True)
        error_msg = f"❌ 分析 {ticker} 時發生錯誤：\n{str(e)[:200]}\n\n請稍後再試。"
        try:
            await loading_msg.edit_text(error_msg)
        except Exception:
            try:
                await bot.send_message(chat_id=chat_id, text=error_msg)
            except Exception:
                pass


# ══════════════════════════════════════════
# 工具函數
# ══════════════════════════════════════════


def _build_report_keyboard(ticker: str) -> InlineKeyboardMarkup:
    """建立報告下方的互動按鈕。"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⭐ 加入自選股", callback_data=f"watch:{ticker}"),
            InlineKeyboardButton("🔄 重新分析", callback_data=f"refresh:{ticker}"),
        ],
    ])


def _ensure_dict(result, source_name: str) -> dict:
    """確保 gather 的結果是 dict。"""
    if isinstance(result, Exception):
        logger.warning(f"[{source_name}] fetcher 異常: {result}")
        return {"source": source_name, "error": f"{source_name} 錯誤: {str(result)}"}
    if isinstance(result, dict):
        return result
    return {"source": source_name, "error": f"{source_name} 回傳格式異常"}


async def _send_report(chat_id: int, bot, report: str, reply_markup=None) -> None:
    """安全發送報告（HTML parse mode；失敗時退回純文字）。"""
    chunks = _split_message(report, 4096)

    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        markup = reply_markup if is_last else None
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=markup,
            )
        except Exception as html_err:
            logger.warning(f"HTML 發送失敗，降級純文字: {html_err}")
            # 移除 HTML 標籤後重發
            plain = re.sub(r"<[^>]+>", "", chunk)
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=plain,
                    disable_web_page_preview=True,
                    reply_markup=markup,
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
            split_pos = text.rfind("─ ─ ─", 0, max_length)
        if split_pos == -1:
            split_pos = text.rfind("\n\n", 0, max_length)
        if split_pos == -1:
            split_pos = text.rfind("\n", 0, max_length)
        if split_pos == -1:
            split_pos = max_length

        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")

    return chunks


async def _inline_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 InlineKeyboard 按鈕回調。"""
    query = update.callback_query
    data = query.data or ""
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    bot = context.bot

    if data.startswith("watch:"):
        ticker = data.split(":", 1)[1]
        added = await add_to_watchlist(user_id, ticker)
        if added:
            await query.answer(f"✅ {ticker} 已加入自選股")
            new_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 重新分析", callback_data=f"refresh:{ticker}")],
            ])
            try:
                await query.edit_message_reply_markup(reply_markup=new_keyboard)
            except Exception:
                pass
        else:
            await query.answer(f"ℹ️ {ticker} 已在自選股清單中", show_alert=True)

    elif data.startswith("refresh:"):
        ticker = data.split(":", 1)[1]
        if not rate_limiter.is_allowed(user_id):
            wait = rate_limiter.retry_after(user_id)
            await query.answer(f"⏰ 請 {wait} 秒後再試", show_alert=True)
            return
        await query.answer(f"🔄 正在重新分析 {ticker}...")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        report_cache.invalidate(ticker)
        raw_cache.invalidate(f"{ticker}:base")
        await _dispatch_analysis(chat_id, user_id, ticker, bot)

    elif data.startswith("report:"):
        ticker = data.split(":", 1)[1]
        if not rate_limiter.is_allowed(user_id):
            wait = rate_limiter.retry_after(user_id)
            await query.answer(f"⏰ 請 {wait} 秒後再試", show_alert=True)
            return
        await query.answer(f"📈 正在分析 {ticker}...")
        await _dispatch_analysis(chat_id, user_id, ticker, bot)

    elif data == "scanall":
        await query.answer("🔍 開始批次快掃...")
        await _run_scan(chat_id, user_id, bot)

    elif data == "manage_wl":
        tickers = await get_watchlist(user_id)
        if not tickers:
            await query.answer("清單為空", show_alert=True)
            return
        await query.answer()
        buttons = []
        for t in tickers:
            buttons.append([
                InlineKeyboardButton(f"📈 {t}", callback_data=f"report:{t}"),
                InlineKeyboardButton("❌ 移除", callback_data=f"unwatchcb:{t}"),
            ])
        buttons.append([InlineKeyboardButton("🔙 返回", callback_data="back_wl")])
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except Exception:
            pass

    elif data.startswith("unwatchcb:"):
        ticker = data.split(":", 1)[1]
        removed = await remove_from_watchlist(user_id, ticker)
        if removed:
            await query.answer(f"✅ 已移除 {ticker}")
            tickers = await get_watchlist(user_id)
            if tickers:
                buttons = []
                for t in tickers:
                    buttons.append([
                        InlineKeyboardButton(f"📈 {t}", callback_data=f"report:{t}"),
                        InlineKeyboardButton("❌ 移除", callback_data=f"unwatchcb:{t}"),
                    ])
                buttons.append([InlineKeyboardButton("🔙 返回", callback_data="back_wl")])
                try:
                    await query.edit_message_reply_markup(
                        reply_markup=InlineKeyboardMarkup(buttons)
                    )
                except Exception:
                    pass
            else:
                await query.edit_message_text("📋 自選股清單已清空。使用 /watch AAPL 加入股票")
        else:
            await query.answer(f"ℹ️ {ticker} 不在清單中", show_alert=True)

    elif data == "back_wl":
        await query.answer()
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 批次快掃", callback_data="scanall"),
                InlineKeyboardButton("📋 管理清單", callback_data="manage_wl"),
            ]
        ])
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except Exception:
            pass

    else:
        await query.answer()


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


async def setup_bot_commands(bot) -> None:
    """註冊 Telegram 指令選單，在聊天介面左下角提供指令建議。"""
    commands = [
        BotCommand("start", "查看歡迎訊息"),
        BotCommand("help", "指令手冊"),
        BotCommand("report", "完整深度分析報告（需股票代碼）"),
        BotCommand("chart", "僅 K 線圖（需股票代碼）"),
        BotCommand("compare", "多股對比（2-5 檔代碼）"),
        BotCommand("watchlist", "自選股清單與即時報價"),
        BotCommand("scan", "自選股批次快掃"),
        BotCommand("watch", "加入自選股"),
        BotCommand("unwatch", "移除自選股"),
    ]
    try:
        await bot.set_my_commands(commands)
        logger.info("✅ Bot 指令選單已註冊")
    except Exception as e:
        logger.warning(f"⚠️ 指令選單註冊失敗: {e}")


def create_bot_application() -> Application:
    """建立並設定 Telegram Bot Application。"""
    app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()

    # 分析指令
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("report", report_command))
    app.add_handler(CommandHandler("chart", chart_command))
    app.add_handler(CommandHandler("compare", compare_command))

    # 自選股指令
    app.add_handler(CommandHandler("watchlist", watchlist_command))
    app.add_handler(CommandHandler("scan", scan_command))
    app.add_handler(CommandHandler("watch", watch_command))
    app.add_handler(CommandHandler("unwatch", unwatch_command))

    # InlineKeyboard 回調
    app.add_handler(CallbackQueryHandler(_inline_button_handler))

    app.add_error_handler(error_handler)

    return app
