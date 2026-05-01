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
import time
from datetime import datetime, timezone

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
from bot.tenk_handler import tenk_command

logger = logging.getLogger(__name__)

# ── 並發控制 ──
_analysis_semaphore = asyncio.Semaphore(3)

# ── 超時設定（秒）──
FETCH_TIMEOUT = 45
EXTENDED_FETCH_TIMEOUT = 60  # 多數據源並行 + 重試 buffer
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


# ── 錯誤訊息共用文案 ──
_TICKER_HINT = "需 1–5 個英文字母或數字，例如 <code>AAPL</code>"


def _usage_error(cmd: str, example: str) -> str:
    """缺參數時的統一用法提示。"""
    return f"❌ 請提供股票代碼\n例如：<code>/{cmd} {example}</code>"


def _invalid_ticker_error(tickers: str | None = None) -> str:
    """無效代碼的統一錯誤訊息。"""
    if tickers:
        return f"❌ 無效的股票代碼：<code>{_esc(tickers)}</code>\n{_TICKER_HINT}"
    return f"❌ 無效的股票代碼\n{_TICKER_HINT}"


def _pos_52w(price, year_high, year_low) -> float | None:
    """股價在 52 週區間的相對位置 0–100（貼近 52w 低為 0、貼近 52w 高為 100）。"""
    try:
        if price is None or year_high is None or year_low is None:
            return None
        p, h, l = float(price), float(year_high), float(year_low)
        if h <= l:
            return None
        return max(0.0, min(100.0, (p - l) / (h - l) * 100))
    except (ValueError, TypeError):
        return None


def _vol_ratio(volume, avg_volume) -> float | None:
    """成交量 / 平均量，用於判斷量能異常。"""
    try:
        if not volume or not avg_volume:
            return None
        return float(volume) / float(avg_volume)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def _fmt_mcap(mcap) -> str:
    """市值格式化（1.2T / 320B / 500M）。"""
    if not isinstance(mcap, (int, float)):
        return ""
    if mcap >= 1e12:
        return f"{mcap / 1e12:.1f}T"
    if mcap >= 1e9:
        return f"{mcap / 1e9:.0f}B"
    if mcap >= 1e6:
        return f"{mcap / 1e6:.0f}M"
    return f"{mcap:,.0f}"


def _pos52_bar(pos, length: int = 8) -> str:
    """52w 區間視覺化進度條（▌ 已填 / ░ 未填）。"""
    if pos is None:
        return ""
    try:
        filled = max(0, min(length, round(float(pos) / 100 * length)))
    except (ValueError, TypeError):
        return ""
    return "▌" * filled + "░" * (length - filled)


def _earnings_days(earnings_str) -> int | None:
    """距離財報的整日數（0–7 才回傳；今日=0、明日=1）。FMP 格式：'2025-01-30T21:00:00.000+0000'。"""
    if not earnings_str or not isinstance(earnings_str, str) or len(earnings_str) < 10:
        return None
    try:
        target = datetime.strptime(earnings_str[:10], "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        days = (target - today).days
        return days if 0 <= days <= 7 else None
    except (ValueError, TypeError):
        return None


# ── /watchlist 結果快取（避免短時重覆敲指令吃光 FMP 配額）──
_WL_CACHE_TTL = 120  # 秒
_WL_CACHE_MAX = 200
_watchlist_cache: dict[tuple, tuple[dict, float]] = {}


def _wl_cache_get(key: tuple) -> dict | None:
    entry = _watchlist_cache.get(key)
    if not entry:
        return None
    data, ts = entry
    if time.time() - ts > _WL_CACHE_TTL:
        _watchlist_cache.pop(key, None)
        return None
    return data


def _wl_cache_set(key: tuple, data: dict) -> None:
    _watchlist_cache[key] = (data, time.time())
    if len(_watchlist_cache) > _WL_CACHE_MAX:
        oldest_key = min(_watchlist_cache.items(), key=lambda kv: kv[1][1])[0]
        _watchlist_cache.pop(oldest_key, None)


def _wl_cache_age(key: tuple) -> int | None:
    entry = _watchlist_cache.get(key)
    if not entry:
        return None
    return int(time.time() - entry[1])


# ══════════════════════════════════════════
# 指令處理器
# ══════════════════════════════════════════


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /start 指令"""
    welcome_msg = (
        "📊 <b>美股深度分析 Bot</b>\n"
        "12 維度量化信號 + Claude AI 四觀點 + SEC 10-K 全文解析\n"
        "\n"
        "<b>三種分析深度</b>\n"
        "<code>/report AAPL</code> — 60 秒完整報告\n"
        "<code>/tenk AAPL</code> — 10-K 年報深度（5–15 分鐘）\n"
        "<code>/chart AAPL</code> — K 線圖秒回\n"
        "\n"
        "<b>自選股</b>\n"
        "<code>/watch AAPL</code> 加入 · <code>/watchlist</code> 看清單 · <code>/scan</code> 批次掃\n"
        "\n"
        "輸入 /help 看完整指令"
    )
    await update.message.reply_text(welcome_msg, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /help 指令 — 詳細指令與用法說明。"""
    msg = (
        "🧭 <b>指令手冊</b>\n"
        "\n"
        "<b>🔍 分析</b>\n"
        "<code>/report TICKER</code> — 完整深度分析（12 信號 + Claude AI）\n"
        "<code>/tenk TICKER [年] [Q1|Q2|Q3]</code> — 10-K / 10-Q 深度（5–15 分鐘，每日 3 次）\n"
        "<code>/chart TICKER</code> — 60 日 K 線圖（秒回，省 Claude 費用）\n"
        "<code>/compare T1 T2 …</code> — 並排對比 2–5 檔\n"
        "\n"
        "<b>📋 自選股</b>\n"
        "<code>/watchlist</code> — 即時報價儀表板（含警示・52w 位置）\n"
        "<code>/scan</code> — 批次快掃（含 RSI / 技術評級 / 警示分組）\n"
        "<code>/watch TICKER</code> 加入 · <code>/unwatch TICKER</code> 移除\n"
        "\n"
        "<b>🧭 其他</b>\n"
        "<code>/start</code> 歡迎訊息 · <code>/help</code> 本手冊\n"
        "\n"
        "📡 資料：Finnhub · FMP · TradingView · Tavily · SEC EDGAR\n"
        "🛡️ 反幻覺：所有 AI 分析皆需數據佐證，缺失即標 N/A"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /report [TICKER] 指令。"""
    if not context.args:
        await update.message.reply_text(_usage_error("report", "AAPL"), parse_mode=ParseMode.HTML)
        return

    ticker = context.args[0].upper()

    if not _validate_ticker(ticker):
        await update.message.reply_text(_invalid_ticker_error(ticker), parse_mode=ParseMode.HTML)
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # ── Rate Limiting ──
    if not rate_limiter.is_allowed(user_id):
        wait = rate_limiter.retry_after(user_id)
        await update.message.reply_text(
            f"⏰ 請求過於頻繁，{wait} 秒後再試\n"
            f"每分鐘上限 {Config.RATE_LIMIT_PER_MINUTE} 次"
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
                f"⏳ 系統繁忙中，目前有 3 筆分析在跑\n"
                f"稍後重試：<code>/report {ticker}</code>"
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    async with _analysis_semaphore:
        await _execute_analysis(chat_id, ticker, bot)


def _wl_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 強刷", callback_data="wl_refresh"),
            InlineKeyboardButton("📊 批次快掃", callback_data="scanall"),
            InlineKeyboardButton("📋 管理清單", callback_data="manage_wl"),
        ]
    ])


async def _render_watchlist_view(user_id: int, force_refresh: bool = False) -> tuple[str, InlineKeyboardMarkup | None, bool]:
    """組裝 /watchlist 顯示內容。回傳 (text, keyboard, used_cache)。"""
    tickers = await get_watchlist(user_id)
    if not tickers:
        return (
            "📋 你的自選股清單是空的\n\n"
            "<code>/watch AAPL</code> 加入第一檔\n"
            "<code>/report AAPL</code> 直接分析（不需先加入）"
        ), None, False

    cache_key = (user_id, tuple(tickers))
    used_cache = False
    if not force_refresh:
        cached = _wl_cache_get(cache_key)
        if cached is not None:
            prices = cached
            used_cache = True
        else:
            prices = await fetch_fmp_batch_prices(tickers)
            _wl_cache_set(cache_key, prices)
    else:
        prices = await fetch_fmp_batch_prices(tickers)
        _wl_cache_set(cache_key, prices)

    # ── 整理資料 ──
    valid, invalid = [], []
    for t in tickers:
        q = prices.get(t, {})
        if q.get("price") is not None:
            valid.append({
                "ticker": t,
                "price": q["price"],
                "chg": q.get("change") or 0,
                "chg_pct": q.get("change_pct") or 0,
                "pos52": _pos_52w(q.get("price"), q.get("year_high"), q.get("year_low")),
                "vol_ratio": _vol_ratio(q.get("volume"), q.get("avg_volume")),
                "earn_days": _earnings_days(q.get("earnings_announcement")),
            })
        else:
            invalid.append(t)

    # 依當日漲跌 % 由高到低排序（強勢在上）
    valid.sort(key=lambda r: r["chg_pct"], reverse=True)

    # ── 總覽列 ──
    ups = sum(1 for r in valid if r["chg_pct"] > 0)
    downs = sum(1 for r in valid if r["chg_pct"] < 0)
    flats = len(valid) - ups - downs
    avg_pct = sum(r["chg_pct"] for r in valid) / len(valid) if valid else 0

    summary = f"📋 <b>自選股</b> ({len(tickers)} 檔)  🟢{ups} 漲 / 🔴{downs} 跌"
    if flats:
        summary += f" / ⚪{flats} 平"
    summary += f"  平均 {'+' if avg_pct >= 0 else ''}{avg_pct:.2f}%"
    if used_cache:
        age = _wl_cache_age(cache_key) or 0
        summary += f"  ⚡{age}s 快取"
    lines = [summary]

    if valid:
        top, bot = valid[0], valid[-1]
        highlights = []
        if top["chg_pct"] > 0:
            highlights.append(f"👑 最強 <b>{_esc(top['ticker'])}</b> +{top['chg_pct']:.2f}%")
        if bot["chg_pct"] < 0:
            highlights.append(f"⚠️ 最弱 <b>{_esc(bot['ticker'])}</b> {bot['chg_pct']:.2f}%")
        if highlights:
            lines.append("  ".join(highlights))

    # 即將公佈財報的個股
    earn_alerts = [r for r in valid if r["earn_days"] is not None]
    if earn_alerts:
        earn_alerts.sort(key=lambda r: r["earn_days"])
        bits = [f"<b>{_esc(r['ticker'])}</b>({r['earn_days']}天)" for r in earn_alerts[:5]]
        lines.append(f"📅 財報臨近：{' · '.join(bits)}")
    lines.append("")

    # ── 各檔明細 ──
    for r in valid:
        arrow = "🟢" if r["chg_pct"] >= 0 else "🔴"
        sign = "+" if r["chg_pct"] >= 0 else ""
        tags = []
        if r["pos52"] is not None:
            bar = _pos52_bar(r["pos52"])
            marker = " 🔝" if r["pos52"] >= 95 else (" 🔻" if r["pos52"] <= 5 else "")
            tags.append(f"📍{bar} {r['pos52']:.0f}%{marker}")
        if r["vol_ratio"] and r["vol_ratio"] >= 1.5:
            tags.append(f"🔥{r['vol_ratio']:.1f}x量")
        if r["earn_days"] is not None:
            tags.append(f"📅{r['earn_days']}天")
        tag_str = f"  {'  '.join(tags)}" if tags else ""
        lines.append(
            f"{arrow} <b>{_esc(r['ticker'])}</b>  ${r['price']:.2f}  "
            f"{sign}{r['chg_pct']:.2f}%{tag_str}"
        )

    if invalid:
        lines.append("")
        lines.append(f"❓ <b>無法取得報價</b> ({len(invalid)} 檔，可能為非美股或代碼有誤)")
        for t in invalid:
            lines.append(f"  ⚪ <b>{_esc(t)}</b>  ➜ /report {_esc(t)} 嘗試完整分析")

    lines.append("\n/scan 批次快掃 (含 RSI / 技術評級)")

    return "\n".join(lines), _wl_keyboard(), used_cache


async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /watchlist 指令：顯示自選股清單 + 即時報價（含 120s 結果快取、視覺化 52w、財報臨近）。"""
    user_id = update.effective_user.id
    loading = await update.message.reply_text("📋 載入自選股…")
    text, keyboard, _ = await _render_watchlist_view(user_id, force_refresh=False)
    await loading.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /watch [TICKER] 指令：加入自選股。"""
    if not context.args:
        await update.message.reply_text(_usage_error("watch", "AAPL"), parse_mode=ParseMode.HTML)
        return

    ticker = context.args[0].upper()
    if not _validate_ticker(ticker):
        await update.message.reply_text(_invalid_ticker_error(ticker), parse_mode=ParseMode.HTML)
        return

    user_id = update.effective_user.id
    added = await add_to_watchlist(user_id, ticker)

    if added:
        await update.message.reply_text(
            f"✅ <code>{ticker}</code> 已加入自選股\n用 /watchlist 看清單，或 /report {ticker} 直接分析",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            f"ℹ️ <code>{ticker}</code> 已在清單中",
            parse_mode=ParseMode.HTML,
        )


async def unwatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /unwatch [TICKER] 指令：移除自選股。"""
    if not context.args:
        await update.message.reply_text(_usage_error("unwatch", "AAPL"), parse_mode=ParseMode.HTML)
        return

    ticker = context.args[0].upper()
    user_id = update.effective_user.id
    removed = await remove_from_watchlist(user_id, ticker)

    if removed:
        await update.message.reply_text(
            f"✅ 已從自選股移除 <code>{ticker}</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            f"ℹ️ <code>{ticker}</code> 不在清單中",
            parse_mode=ParseMode.HTML,
        )


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
            text="📋 自選股清單是空的\n用 <code>/watch AAPL</code> 加入第一檔",
            parse_mode=ParseMode.HTML,
        )
        return

    truncated = len(tickers) > 10
    if truncated:
        tickers = tickers[:10]

    loading = await bot.send_message(
        chat_id=chat_id,
        text=f"🔍 掃描 {len(tickers)} 檔自選股…",
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

    rec_map = {
        "STRONG_BUY": "強買", "BUY": "買入",
        "NEUTRAL": "中性", "SELL": "賣出", "STRONG_SELL": "強賣",
    }

    # ── 整理每檔資料 ──
    rows = []
    for i, t in enumerate(tickers):
        q = prices.get(t, {})
        ta = _ensure_dict(ta_results[i], "TA") if i < len(ta_results) else {}
        summary_val = ta.get("summary", {})
        rec = summary_val.get("RECOMMENDATION", "N/A") if isinstance(summary_val, dict) else "N/A"
        if rec == "N/A":
            rec = ta.get("recommendation", "N/A")
        rsi = ta.get("rsi_14", ta.get("rsi"))
        rsi = rsi if isinstance(rsi, (int, float)) else None
        rows.append({
            "ticker": t,
            "price": q.get("price"),
            "chg_pct": (q.get("change_pct") or 0) if q.get("price") is not None else None,
            "market_cap": q.get("market_cap"),
            "rec": rec,
            "rec_zh": rec_map.get(rec, rec),
            "rsi": rsi,
            "pos52": _pos_52w(q.get("price"), q.get("year_high"), q.get("year_low")),
            "vol_ratio": _vol_ratio(q.get("volume"), q.get("avg_volume")),
            "earn_days": _earnings_days(q.get("earnings_announcement")),
        })

    valid = [r for r in rows if r["price"] is not None]
    invalid = [r for r in rows if r["price"] is None]

    # ── 警示判定 ──
    def _alerts(r) -> list[str]:
        out = []
        if r["rsi"] is not None:
            if r["rsi"] > 70:
                out.append(f"RSI {r['rsi']:.0f} 超買")
            elif r["rsi"] < 30:
                out.append(f"RSI {r['rsi']:.0f} 超賣")
        if r["rec"] == "STRONG_BUY":
            out.append("TV 強買")
        elif r["rec"] == "STRONG_SELL":
            out.append("TV 強賣")
        if r["pos52"] is not None:
            if r["pos52"] >= 95:
                out.append(f"近 52w 高 ({r['pos52']:.0f}%)")
            elif r["pos52"] <= 5:
                out.append(f"近 52w 低 ({r['pos52']:.0f}%)")
        if r["vol_ratio"] and r["vol_ratio"] >= 2.0:
            out.append(f"量爆 {r['vol_ratio']:.1f}x")
        if r["earn_days"] is not None:
            out.append(f"📅 財報 {r['earn_days']} 天")
        return out

    alerted_ids = set()
    alerted_rows = []
    for r in valid:
        a = _alerts(r)
        if a:
            alerted_rows.append((r, a))
            alerted_ids.add(r["ticker"])

    ups = sorted((r for r in valid if r["chg_pct"] > 0), key=lambda r: r["chg_pct"], reverse=True)
    downs = sorted((r for r in valid if r["chg_pct"] < 0), key=lambda r: r["chg_pct"])
    flats = [r for r in valid if r["chg_pct"] == 0]

    # ── 總覽列 ──
    avg_pct = sum(r["chg_pct"] for r in valid) / len(valid) if valid else 0
    header = (
        f"📊 <b>自選股批次快掃</b> ({len(valid)}/{len(tickers)})  "
        f"🟢{len(ups)} 漲 / 🔴{len(downs)} 跌"
    )
    if flats:
        header += f" / ⚪{len(flats)} 平"
    header += f"  平均 {'+' if avg_pct >= 0 else ''}{avg_pct:.2f}%"
    lines = [header]

    def _row_line(r) -> str:
        arrow = "🟢" if r["chg_pct"] >= 0 else "🔴"
        sign = "+" if r["chg_pct"] >= 0 else ""
        bits = []
        if r["pos52"] is not None:
            bits.append(f"52w:{_pos52_bar(r['pos52'], 6)} {r['pos52']:.0f}%")
        if r["rsi"] is not None:
            bits.append(f"RSI:{r['rsi']:.0f}")
        if r["rec_zh"] and r["rec_zh"] != "N/A":
            bits.append(f"TV:{_esc(r['rec_zh'])}")
        cap = _fmt_mcap(r["market_cap"])
        if cap:
            bits.append(cap)
        if r["vol_ratio"] and r["vol_ratio"] >= 1.5:
            bits.append(f"🔥{r['vol_ratio']:.1f}x")
        if r["earn_days"] is not None:
            bits.append(f"📅{r['earn_days']}天")
        head = f"{arrow} <b>{_esc(r['ticker'])}</b> ${r['price']:.2f} {sign}{r['chg_pct']:.2f}%"
        return head + ("\n  " + " | ".join(bits) if bits else "") + f"  ➜ /report {_esc(r['ticker'])}"

    # ── 警示區 ──
    if alerted_rows:
        lines.append("")
        lines.append("🚨 <b>警示</b>")
        alerted_rows.sort(key=lambda x: abs(x[0]["chg_pct"]), reverse=True)
        for r, alerts in alerted_rows:
            lines.append(_row_line(r))
            lines.append(f"  ⚠️ {' · '.join(alerts)}")

    # ── 上漲（排除已在警示區）──
    remaining_ups = [r for r in ups if r["ticker"] not in alerted_ids]
    if remaining_ups:
        lines.append("")
        lines.append(f"🟢 <b>上漲 ({len(remaining_ups)})</b>")
        for r in remaining_ups:
            lines.append(_row_line(r))

    # ── 下跌（排除已在警示區）──
    remaining_downs = [r for r in downs if r["ticker"] not in alerted_ids]
    if remaining_downs:
        lines.append("")
        lines.append(f"🔴 <b>下跌 ({len(remaining_downs)})</b>")
        for r in remaining_downs:
            lines.append(_row_line(r))

    # ── 平盤（若有且未在警示區）──
    remaining_flats = [r for r in flats if r["ticker"] not in alerted_ids]
    if remaining_flats:
        lines.append("")
        lines.append("⚪ <b>持平</b>")
        for r in remaining_flats:
            lines.append(_row_line(r))

    # ── 抓取失敗 ──
    if invalid:
        lines.append("")
        lines.append(f"❓ <b>無法取得報價</b> ({len(invalid)} 檔，可能為非美股或代碼有誤)")
        for r in invalid:
            lines.append(f"  ⚪ <b>{_esc(r['ticker'])}</b>  ➜ /report {_esc(r['ticker'])} 嘗試完整分析")

    footer = f"\n共 {len(tickers)} 檔  |  ➜ /report 查看完整分析"
    if truncated:
        footer = "\n⚠️ 超過 10 檔，僅顯示前 10  |  ➜ /report 查看完整分析"
    lines.append(footer)

    await loading.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /chart [TICKER]：僅回傳 60 日 K 線圖，不做 AI 分析。"""
    if not context.args:
        await update.message.reply_text(_usage_error("chart", "AAPL"), parse_mode=ParseMode.HTML)
        return

    ticker = context.args[0].upper()
    if not _validate_ticker(ticker):
        await update.message.reply_text(_invalid_ticker_error(ticker), parse_mode=ParseMode.HTML)
        return

    user_id = update.effective_user.id
    if not rate_limiter.is_allowed(user_id):
        wait = rate_limiter.retry_after(user_id)
        await update.message.reply_text(f"⏰ 請求過於頻繁，{wait} 秒後再試")
        return

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)

    chart_buf = await generate_chart(ticker)
    if not chart_buf:
        await update.message.reply_text(
            f"❌ 無法生成 <code>{ticker}</code> 的 K 線圖\n資料源可能暫時不可用，稍後再試",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_photo(
        photo=chart_buf,
        caption=f"📈 {ticker} — 60 日 K 線圖（MA5 / MA20 / MA60）",
    )


async def compare_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /compare TICKER1 TICKER2 ...：並排比較 2-5 檔個股。"""
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ 請提供至少 2 檔股票代碼\n例如：<code>/compare AAPL MSFT NVDA</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    tickers = [t.upper() for t in context.args[:5]]
    invalid = [t for t in tickers if not _validate_ticker(t)]
    if invalid:
        await update.message.reply_text(
            _invalid_ticker_error(", ".join(invalid)),
            parse_mode=ParseMode.HTML,
        )
        return

    user_id = update.effective_user.id
    if not rate_limiter.is_allowed(user_id):
        wait = rate_limiter.retry_after(user_id)
        await update.message.reply_text(f"⏰ 請求過於頻繁，{wait} 秒後再試")
        return

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)

    loading = await update.message.reply_text(f"⚖️ 比較 {len(tickers)} 檔…")

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
        text=f"⏳ 分析 <b>{ticker}</b>…\n📡 並行抓取 10+ 數據源",
        parse_mode=ParseMode.HTML,
    )

    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)

        # ── Step 1: 並行抓取基礎數據（帶 raw 快取）──
        cached_raw = raw_cache.get(f"{ticker}:base")
        if cached_raw:
            finnhub_data, fundamentals_data, tradingview_data = cached_raw
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
            fundamentals_data = _ensure_dict(results[1], "FMP")
            tradingview_data = _ensure_dict(results[2], "TradingView")

            raw_cache.set(f"{ticker}:base", (finnhub_data, fundamentals_data, tradingview_data))
            logger.info(f"[{ticker}] 基礎數據抓取完成 (源: {fundamentals_data.get('source', '?')})")

        # ── Step 1.5: 擴展數據（Tavily + 歷史 + 同業 + 分析師 + 內部人 + EPS + 宏觀）──
        company_name = fundamentals_data.get("company_name", "")
        sector = fundamentals_data.get("sector", "")
        industry = fundamentals_data.get("industry", "")

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
            fundamentals_data=fundamentals_data,
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
                f"⏳ 分析 <b>{ticker}</b>…\n"
                f"✅ 數據抓取完成（{total_sources} 源）\n"
                f"🧮 量化信號：{signals_data.get('consensus', 'N/A')}\n"
                f"🤖 Claude 四觀點深度分析中",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        await bot.send_chat_action(chat_id, ChatAction.TYPING)

        # ── Step 2: AI 分析 ──
        try:
            ai_analysis = await asyncio.wait_for(
                analyze_stock(
                    ticker, finnhub_data, fundamentals_data, tavily_data,
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
            ticker, finnhub_data, fundamentals_data, tavily_data,
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
        timeout_msg = (
            f"❌ <b>{ticker}</b> 分析逾時\n"
            f"數據源回應過慢，稍後重試：<code>/report {ticker}</code>"
        )
        try:
            await loading_msg.edit_text(timeout_msg, parse_mode=ParseMode.HTML)
        except Exception:
            await bot.send_message(chat_id=chat_id, text=timeout_msg, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"[{ticker}] 分析失敗: {e}", exc_info=True)
        error_msg = (
            f"❌ <b>{ticker}</b> 分析失敗\n"
            f"<code>{_esc(str(e)[:200])}</code>\n"
            f"稍後重試：<code>/report {ticker}</code>"
        )
        try:
            await loading_msg.edit_text(error_msg, parse_mode=ParseMode.HTML)
        except Exception:
            try:
                await bot.send_message(chat_id=chat_id, text=error_msg, parse_mode=ParseMode.HTML)
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

    elif data == "wl_refresh":
        await query.answer("🔄 強刷中...")
        text, keyboard, _ = await _render_watchlist_view(user_id, force_refresh=True)
        try:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except Exception:
            pass

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
        try:
            await query.edit_message_reply_markup(reply_markup=_wl_keyboard())
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
                "❌ 系統發生錯誤，請稍後重試\n若持續發生，輸入 /help 查看其他指令"
            )
        except Exception:
            pass


async def setup_bot_commands(bot) -> None:
    """註冊 Telegram 指令選單，在聊天介面左下角提供指令建議。"""
    commands = [
        BotCommand("start", "查看歡迎訊息"),
        BotCommand("help", "指令手冊"),
        BotCommand("report", "完整深度分析報告（需股票代碼）"),
        BotCommand("tenk", "10-K 年報深度分析（5-15 分鐘）"),
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
    app.add_handler(CommandHandler("tenk", tenk_command))
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
