"""
Slack Bot 主模組（Socket Mode + Block Kit）

設計重點：
- AsyncApp + AsyncSocketModeHandler，部署不需公開 URL
- Slash commands：/report /tenk /chart /compare /watchlist /scan /news
  /watch /unwatch /stats /cancel /help
- Block Kit 按鈕走 block_actions（action_id 命名規範：<verb>:<ticker>[:<arg>]）
- 所有命令 3 秒內 ack()；耗時工作丟 asyncio.create_task 背景跑
- 訊息分段：第一則為「結論先行卡」（會響通知），後續走 thread 靜默通知
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from config import Config
from fetchers.analyst_fetcher import fetch_analyst_data
from fetchers.earnings_surprise_fetcher import fetch_earnings_surprises
from fetchers.finnhub_fetcher import fetch_finnhub_quote
from fetchers.fmp_fetcher import fetch_fmp_batch_prices, fetch_fmp_fundamentals
from fetchers.history_fetcher import fetch_history_analysis
from fetchers.insider_fetcher import fetch_insider_transactions
from fetchers.macro_fetcher import fetch_macro_data
from fetchers.peer_fetcher import fetch_peer_comparison
from fetchers.stooq_fetcher import fetch_stooq_history
from fetchers.tavily_fetcher import fetch_tavily_news
from fetchers.tradingview_fetcher import fetch_tradingview_analysis
from analyzer.llm_analyzer import analyze_stock
from utils.cache import LRUCache, news_cache, raw_cache, report_cache
from utils.chart import generate_chart
from utils.database import (
    add_to_watchlist,
    get_user_stats,
    get_watchlist,
    record_query,
    remove_from_watchlist,
)
from utils.formatter import format_report
from utils.rate_limiter import rate_limiter
from utils.signals import compute_signals
from utils.slack_api import post_message
from utils.slack_formatter import (
    actions,
    button,
    chunk_mrkdwn,
    context,
    divider,
    escape_mrkdwn,
    fallback_text,
    header,
    html_to_mrkdwn,
    mrkdwn_to_blocks,
    section,
    split_blocks_into_messages,
)
from bot.tenk_handler import (
    dispatch_tenk_analysis,
    get_tenk_quota,
    parse_tenk_args,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════
# 全域狀態
# ══════════════════════════════════════════

_analysis_semaphore = asyncio.Semaphore(Config.ANALYSIS_CONCURRENCY)
# (channel, user) → asyncio.Task；/cancel 用
_running_tasks: dict[tuple[str, str], asyncio.Task] = {}
# 全域同時排隊上限（防 DoS / OOM）；超過此值直接拒絕新請求
_MAX_INFLIGHT_TASKS = max(20, Config.ANALYSIS_CONCURRENCY * 10)


def _purge_done_tasks() -> None:
    """清掉 dict 中已結束的 task，避免無限堆積。"""
    for key in [k for k, t in _running_tasks.items() if t.done()]:
        _running_tasks.pop(key, None)

# 超時
FETCH_TIMEOUT = 45
EXTENDED_FETCH_TIMEOUT = 60
AI_TIMEOUT = 90

# Ticker 規則：1–5 個英數字，但至少包含 1 個字母（避免 "12345" 這種純數字）
_TICKER_PATTERN = re.compile(r"^(?=[A-Z0-9]{1,5}$)[A-Z0-9]*[A-Z][A-Z0-9]*$")
_TICKER_HINT = "需 1–5 個英數字且至少含 1 個字母，例如 `AAPL`"


def _validate_ticker(ticker: str) -> bool:
    return bool(_TICKER_PATTERN.match(ticker))


def _split_args(text: str | None) -> list[str]:
    """slash command 內文拆參數（多空白容錯）。"""
    if not text:
        return []
    return [t for t in text.strip().split() if t]


def _invalid_ticker_msg(ticker: str | None = None) -> str:
    if ticker:
        return f"❌ 無效的股票代碼：`{escape_mrkdwn(ticker)}`\n{_TICKER_HINT}"
    return f"❌ 無效的股票代碼\n{_TICKER_HINT}"


# ══════════════════════════════════════════
# 共用工具
# ══════════════════════════════════════════


def _ensure_dict(result, source_name: str) -> dict:
    if isinstance(result, Exception):
        logger.warning(f"[{source_name}] fetcher 異常: {result}")
        return {"source": source_name, "error": f"{source_name} 錯誤: {str(result)}"}
    if isinstance(result, dict):
        return result
    return {"source": source_name, "error": f"{source_name} 回傳格式異常"}


def _pos_52w(price, year_high, year_low) -> float | None:
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
    try:
        if not volume or not avg_volume:
            return None
        return float(volume) / float(avg_volume)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def _fmt_mcap(mcap) -> str:
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
    if pos is None:
        return ""
    try:
        filled = max(0, min(length, round(float(pos) / 100 * length)))
    except (ValueError, TypeError):
        return ""
    return "▌" * filled + "░" * (length - filled)


def _earnings_days(earnings_str) -> int | None:
    if not earnings_str or not isinstance(earnings_str, str) or len(earnings_str) < 10:
        return None
    try:
        target = datetime.strptime(earnings_str[:10], "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        days = (target - today).days
        return days if 0 <= days <= 7 else None
    except (ValueError, TypeError):
        return None


# sparkline cache（30 分鐘 TTL，LRU 500）
_SPARK_CHARS = "▁▂▃▄▅▆▇█"
_sparkline_cache = LRUCache(ttl=1800, max_entries=500)


def _sparkline(closes: list[float]) -> str:
    if not closes or len(closes) < 2:
        return ""
    lo, hi = min(closes), max(closes)
    if hi == lo:
        return "▄" * len(closes)
    rng = hi - lo
    last = len(_SPARK_CHARS) - 1
    return "".join(
        _SPARK_CHARS[max(0, min(last, int((c - lo) / rng * last)))] for c in closes
    )


async def _get_sparkline(ticker: str, points: int = 7) -> str:
    cached = _sparkline_cache.get(ticker)
    if cached is not None:
        return cached  # 可能是 "" 表示已知無資料
    try:
        rows = await fetch_stooq_history(ticker, days=points + 3)
    except Exception as e:
        logger.debug(f"[sparkline] {ticker} history fetch failed: {e}")
        rows = None
    if not rows:
        _sparkline_cache.set(ticker, "")
        return ""
    closes = [r["close"] for r in rows[-points:]]
    spark = _sparkline(closes)
    _sparkline_cache.set(ticker, spark)
    return spark


# /watchlist 結果快取（120s，避免短時重複敲指令吃光 FMP 配額）
_watchlist_cache = LRUCache(ttl=120, max_entries=200)


def _wl_cache_key(key: tuple) -> str:
    """tuple → str（LRUCache 用 str key）。"""
    return "|".join(map(str, key))


def _wl_cache_get(key: tuple) -> dict | None:
    return _watchlist_cache.get(_wl_cache_key(key))  # type: ignore[return-value]


def _wl_cache_set(key: tuple, data: dict) -> None:
    _watchlist_cache.set(_wl_cache_key(key), data)


def _wl_cache_age(key: tuple) -> int | None:
    return _watchlist_cache.get_age(_wl_cache_key(key))


# ══════════════════════════════════════════
# Slack 訊息發送輔助
# ══════════════════════════════════════════


async def _send_report(
    client: AsyncWebClient,
    channel: str,
    html_report: str,
    *,
    ticker: str,
    watched: bool = False,
) -> None:
    """把 HTML 報告轉成 Block Kit 多則訊息發送。

    結構：
      msg 1: header + 結論先行 + 互動按鈕（會響通知）
      msg 2..N: thread 內附加 body section
    """
    mrkdwn = html_to_mrkdwn(html_report)
    sep_idx = mrkdwn.find("━" * 26)
    if 0 < sep_idx <= 1500:
        lead = mrkdwn[:sep_idx].rstrip()
        body = mrkdwn[sep_idx + 26:].lstrip("\n")
    else:
        lead = mrkdwn[:1500]
        body = mrkdwn[1500:]

    # 第一行做 header
    lines = lead.splitlines() if lead else [ticker]
    head_text = re.sub(r"^[^\w]*", "", lines[0]).replace("*", "").strip() or ticker.upper()
    rest_lead = "\n".join(lines[1:]).strip()

    first_blocks: list[dict] = [header(head_text[:150])]
    if rest_lead:
        first_blocks.extend(mrkdwn_to_blocks(rest_lead))
    first_blocks.append(divider())
    first_blocks.append(_report_actions_block(ticker, watched=watched))

    first_text = fallback_text(first_blocks)
    head_resp = await post_message(
        client, channel,
        text=first_text,
        blocks=first_blocks,
    )
    thread_ts = head_resp.get("ts")

    # 後續訊息切片，全部 thread 中 + 靜音；用 retry helper 並間隔 ~100ms 緩解 rate-limit
    body_blocks = mrkdwn_to_blocks(body) if body else []
    chunks = [b for b in split_blocks_into_messages(body_blocks) if b]
    for i, blocks in enumerate(chunks):
        if i > 0:
            await asyncio.sleep(0.1)
        await post_message(
            client, channel,
            text=fallback_text(blocks),
            blocks=blocks,
            thread_ts=thread_ts,
        )


_SEC_EDGAR_TPL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={t}"
    "&type=10-K&dateb=&owner=include&count=40"
)


def _report_actions_block(ticker: str, watched: bool = False) -> dict:
    btns = [
        button("📈 K 線", f"chart:{ticker}:60", value=f"{ticker}:60"),
        button("📚 SEC", f"sec:{ticker}", value=ticker, url=_SEC_EDGAR_TPL.format(t=ticker)),
        button("📋 10-K 深度", f"tenk_confirm:{ticker}", value=ticker),
        button("🔄 重新分析", f"refresh:{ticker}", value=ticker),
    ]
    if not watched:
        btns.append(button("⭐ 加入自選", f"watch:{ticker}", value=ticker, style="primary"))
    return actions(btns, block_id=f"report_actions:{ticker}")


# ══════════════════════════════════════════
# Slash command handlers
# ══════════════════════════════════════════


def register_handlers(app: AsyncApp) -> None:
    """把所有 slash command / block_actions 註冊到 AsyncApp。"""

    # ── /start /help ──
    @app.command("/help")
    async def cmd_help(ack, respond, command):
        await ack()
        await respond(blocks=_help_blocks(), text="🧭 指令手冊", response_type="ephemeral")

    @app.command("/start")
    async def cmd_start(ack, respond, command):
        await ack()
        await respond(blocks=_welcome_blocks(), text="📊 美股深度分析 Bot",
                      response_type="ephemeral")

    # ── /report ──
    @app.command("/report")
    async def cmd_report(ack, command, client, respond):
        await ack()
        args = _split_args(command.get("text"))
        if not args:
            await respond(text="❌ 請提供股票代碼\n例如：`/report AAPL`",
                          response_type="ephemeral")
            return
        ticker = args[0].upper()
        if not _validate_ticker(ticker):
            await respond(text=_invalid_ticker_msg(ticker), response_type="ephemeral")
            return

        channel = command["channel_id"]
        user_id = command["user_id"]

        if not rate_limiter.is_allowed(user_id):
            wait = rate_limiter.retry_after(user_id)
            await respond(
                text=f"⏰ 請求過於頻繁，{wait} 秒後再試（每分鐘上限 "
                     f"{Config.RATE_LIMIT_PER_MINUTE} 次）",
                response_type="ephemeral",
            )
            return

        asyncio.create_task(_dispatch_analysis(client, channel, user_id, ticker))

    # ── /tenk ──
    @app.command("/tenk")
    async def cmd_tenk(ack, command, client, respond):
        await ack()
        if not Config.TENK_ENABLED:
            await respond(text="ℹ️ 10-K 深度分析功能目前未啟用",
                          response_type="ephemeral")
            return

        ticker, year, quarter, err = parse_tenk_args(command.get("text", ""))
        if err == "usage":
            await respond(text=(
                "❌ 用法：`/tenk AAPL`\n"
                "  指定年份：`/tenk AAPL 2024`\n"
                "  指定季度：`/tenk AAPL 2025 Q1`"
            ), response_type="ephemeral")
            return
        if err:
            await respond(text=err, response_type="ephemeral")
            return

        await dispatch_tenk_analysis(
            client=client,
            channel=command["channel_id"],
            user_id=command["user_id"],
            ticker=ticker,
            year=year,
            quarter=quarter,
        )

    # ── /chart ──
    @app.command("/chart")
    async def cmd_chart(ack, command, client, respond):
        await ack()
        args = _split_args(command.get("text"))
        if not args:
            await respond(text="❌ 請提供股票代碼，例如：`/chart AAPL`",
                          response_type="ephemeral")
            return
        ticker = args[0].upper()
        if not _validate_ticker(ticker):
            await respond(text=_invalid_ticker_msg(ticker), response_type="ephemeral")
            return

        user_id = command["user_id"]
        if not rate_limiter.is_allowed(user_id):
            wait = rate_limiter.retry_after(user_id)
            await respond(text=f"⏰ 請求過於頻繁，{wait} 秒後再試",
                          response_type="ephemeral")
            return

        asyncio.create_task(_send_chart(client, command["channel_id"], ticker, 60))

    # ── /compare ──
    @app.command("/compare")
    async def cmd_compare(ack, command, client, respond):
        await ack()
        args = _split_args(command.get("text"))
        if len(args) < 2:
            await respond(
                text="❌ 請提供至少 2 檔股票代碼，例如：`/compare AAPL MSFT NVDA`",
                response_type="ephemeral",
            )
            return
        tickers = [t.upper() for t in args[:5]]
        invalid = [t for t in tickers if not _validate_ticker(t)]
        if invalid:
            await respond(text=_invalid_ticker_msg(", ".join(invalid)),
                          response_type="ephemeral")
            return

        user_id = command["user_id"]
        if not rate_limiter.is_allowed(user_id):
            wait = rate_limiter.retry_after(user_id)
            await respond(text=f"⏰ 請求過於頻繁，{wait} 秒後再試",
                          response_type="ephemeral")
            return

        asyncio.create_task(_run_compare(client, command["channel_id"], tickers))

    # ── /news ──
    @app.command("/news")
    async def cmd_news(ack, command, client, respond):
        await ack()
        args = _split_args(command.get("text"))
        if not args:
            await respond(text="❌ 請提供股票代碼，例如：`/news AAPL`",
                          response_type="ephemeral")
            return
        ticker = args[0].upper()
        if not _validate_ticker(ticker):
            await respond(text=_invalid_ticker_msg(ticker), response_type="ephemeral")
            return

        user_id = command["user_id"]
        if not rate_limiter.is_allowed(user_id):
            wait = rate_limiter.retry_after(user_id)
            await respond(text=f"⏰ 請求過於頻繁，{wait} 秒後再試",
                          response_type="ephemeral")
            return

        asyncio.create_task(_send_news(client, command["channel_id"], ticker))

    # ── /watchlist ──
    @app.command("/watchlist")
    async def cmd_watchlist(ack, command, client, respond):
        await ack()
        asyncio.create_task(_send_watchlist(
            client, command["channel_id"], command["user_id"], force_refresh=False
        ))

    # ── /scan ──
    @app.command("/scan")
    async def cmd_scan(ack, command, client, respond):
        await ack()
        asyncio.create_task(_run_scan(
            client, command["channel_id"], command["user_id"], page=0
        ))

    # ── /watch ──
    @app.command("/watch")
    async def cmd_watch(ack, command, respond):
        await ack()
        args = _split_args(command.get("text"))
        if not args:
            await respond(text="❌ 請提供股票代碼，例如：`/watch AAPL`",
                          response_type="ephemeral")
            return
        ticker = args[0].upper()
        if not _validate_ticker(ticker):
            await respond(text=_invalid_ticker_msg(ticker), response_type="ephemeral")
            return

        added = await add_to_watchlist(command["user_id"], ticker)
        if added:
            await respond(
                text=f"✅ `{ticker}` 已加入自選股；用 `/watchlist` 看清單，"
                     f"或 `/report {ticker}` 直接分析",
                response_type="in_channel",
            )
        else:
            await respond(text=f"ℹ️ `{ticker}` 已在清單中",
                          response_type="ephemeral")

    # ── /unwatch ──
    @app.command("/unwatch")
    async def cmd_unwatch(ack, command, client, respond):
        await ack()
        args = _split_args(command.get("text"))
        if not args:
            await respond(text="❌ 請提供股票代碼，例如：`/unwatch AAPL`",
                          response_type="ephemeral")
            return
        ticker = args[0].upper()
        user_id = command["user_id"]

        tickers = await get_watchlist(user_id)
        if ticker not in tickers:
            await respond(text=f"ℹ️ `{ticker}` 不在清單中",
                          response_type="ephemeral")
            return

        await client.chat_postEphemeral(
            channel=command["channel_id"],
            user=user_id,
            text=f"⚠️ 確定要從自選股移除 `{ticker}`？",
            blocks=[
                section(f"⚠️ 確定要從自選股移除 `{ticker}`？"),
                actions([
                    button("✅ 確認移除", f"unwatch_yes:{ticker}", value=ticker,
                           style="danger"),
                    button("取消", "unwatch_no", value=ticker),
                ], block_id=f"unwatch_confirm:{ticker}"),
            ],
        )

    # ── /stats ──
    @app.command("/stats")
    async def cmd_stats(ack, command, client, respond):
        await ack()
        asyncio.create_task(_send_stats(
            client, command["channel_id"], command["user_id"]
        ))

    # ── /cancel ──
    @app.command("/cancel")
    async def cmd_cancel(ack, command, respond):
        await ack()
        key = (command["channel_id"], command["user_id"])
        task = _running_tasks.get(key)
        if not task or task.done():
            await respond(text="ℹ️ 目前沒有進行中的分析",
                          response_type="ephemeral")
            return
        task.cancel()
        await respond(text="⏹️ 已送出取消請求", response_type="ephemeral")

    # ══════════════════════════════
    # Block actions（按鈕回呼）
    # ══════════════════════════════

    @app.action(re.compile(r"^watch:[A-Z0-9]+$"))
    async def act_watch(ack, body, client):
        await ack()
        ticker = body["actions"][0]["value"]
        user_id = body["user"]["id"]
        channel = body["channel"]["id"]
        added = await add_to_watchlist(user_id, ticker)
        if added:
            await client.chat_postEphemeral(
                channel=channel, user=user_id,
                text=f"✅ `{ticker}` 已加入自選股",
            )
            # 更新原訊息按鈕：隱藏「加入自選」
            try:
                ts = body["message"]["ts"]
                blocks = body["message"]["blocks"]
                new_blocks = _replace_actions_block(blocks, ticker, watched=True)
                await client.chat_update(
                    channel=channel, ts=ts,
                    text=body["message"].get("text", ticker),
                    blocks=new_blocks,
                )
            except Exception as e:
                logger.debug(f"[watch action] update message failed: {e}")
        else:
            await client.chat_postEphemeral(
                channel=channel, user=user_id,
                text=f"ℹ️ `{ticker}` 已在清單中",
            )

    @app.action(re.compile(r"^chart:[A-Z0-9]+:\d+$"))
    async def act_chart(ack, body, client):
        await ack()
        action = body["actions"][0]
        action_id = action["action_id"]
        _, ticker, days_str = action_id.split(":")
        try:
            days = int(days_str)
        except ValueError:
            days = 60
        user_id = body["user"]["id"]
        if not rate_limiter.is_allowed(user_id):
            wait = rate_limiter.retry_after(user_id)
            await client.chat_postEphemeral(
                channel=body["channel"]["id"], user=user_id,
                text=f"⏰ 請 {wait} 秒後再試",
            )
            return
        await _send_chart(client, body["channel"]["id"], ticker, days)

    @app.action(re.compile(r"^report:[A-Z0-9]+$"))
    async def act_report(ack, body, client):
        await ack()
        ticker = body["actions"][0]["value"]
        user_id = body["user"]["id"]
        channel = body["channel"]["id"]
        if not rate_limiter.is_allowed(user_id):
            wait = rate_limiter.retry_after(user_id)
            await client.chat_postEphemeral(
                channel=channel, user=user_id,
                text=f"⏰ 請 {wait} 秒後再試",
            )
            return
        asyncio.create_task(_dispatch_analysis(client, channel, user_id, ticker))

    @app.action(re.compile(r"^refresh:[A-Z0-9]+$"))
    async def act_refresh(ack, body, client):
        await ack()
        ticker = body["actions"][0]["value"]
        user_id = body["user"]["id"]
        channel = body["channel"]["id"]
        if not rate_limiter.is_allowed(user_id):
            wait = rate_limiter.retry_after(user_id)
            await client.chat_postEphemeral(
                channel=channel, user=user_id,
                text=f"⏰ 請 {wait} 秒後再試",
            )
            return
        report_cache.invalidate(ticker)
        raw_cache.invalidate(f"{ticker}:base")
        asyncio.create_task(_dispatch_analysis(client, channel, user_id, ticker))

    @app.action(re.compile(r"^tenk_confirm:[A-Z0-9]+$"))
    async def act_tenk_confirm(ack, body, client):
        await ack()
        ticker = body["actions"][0]["value"]
        user_id = body["user"]["id"]
        channel = body["channel"]["id"]
        if not Config.TENK_ENABLED:
            await client.chat_postEphemeral(
                channel=channel, user=user_id,
                text="ℹ️ 10-K 功能未啟用",
            )
            return
        try:
            used, limit = await get_tenk_quota(user_id)
        except Exception:
            used, limit = 0, Config.TENK_DAILY_LIMIT
        remaining = max(0, limit - used)
        await client.chat_postEphemeral(
            channel=channel, user=user_id,
            text=f"⚠️ {ticker} 10-K 深度分析",
            blocks=[
                section(
                    f"⚠️ *{ticker} 10-K 深度分析*\n"
                    f"預估 *8–15 分鐘*，完成後主動推送\n"
                    f"今日剩餘 *{remaining}/{limit}* 次，半年內已分析過會走快取"
                ),
                actions([
                    button("✅ 啟動", f"tenk_run:{ticker}", value=ticker, style="primary"),
                    button("取消", "tenk_no", value=ticker),
                ], block_id=f"tenk_confirm_actions:{ticker}"),
            ],
        )

    @app.action(re.compile(r"^tenk_run:[A-Z0-9]+$"))
    async def act_tenk_run(ack, body, client):
        await ack()
        ticker = body["actions"][0]["value"]
        await dispatch_tenk_analysis(
            client=client,
            channel=body["channel"]["id"],
            user_id=body["user"]["id"],
            ticker=ticker,
        )

    @app.action("tenk_no")
    async def act_tenk_no(ack, body, client):
        await ack()
        await client.chat_postEphemeral(
            channel=body["channel"]["id"], user=body["user"]["id"],
            text="已取消 10-K 分析",
        )

    @app.action(re.compile(r"^unwatch_yes:[A-Z0-9]+$"))
    async def act_unwatch_yes(ack, body, client):
        await ack()
        ticker = body["actions"][0]["value"]
        user_id = body["user"]["id"]
        removed = await remove_from_watchlist(user_id, ticker)
        msg = f"✅ 已從自選股移除 `{ticker}`" if removed else f"ℹ️ `{ticker}` 已不在清單中"
        await client.chat_postEphemeral(
            channel=body["channel"]["id"], user=user_id, text=msg,
        )

    @app.action("unwatch_no")
    async def act_unwatch_no(ack, body, client):
        await ack()
        await client.chat_postEphemeral(
            channel=body["channel"]["id"], user=body["user"]["id"],
            text="已取消移除",
        )

    @app.action(re.compile(r"^wl_refresh$"))
    async def act_wl_refresh(ack, body, client):
        await ack()
        await _send_watchlist(
            client, body["channel"]["id"], body["user"]["id"], force_refresh=True
        )

    @app.action(re.compile(r"^scanall$"))
    async def act_scanall(ack, body, client):
        await ack()
        await _run_scan(client, body["channel"]["id"], body["user"]["id"], page=0)

    @app.action(re.compile(r"^scan_page:\d+$"))
    async def act_scan_page(ack, body, client):
        await ack()
        try:
            page = int(body["actions"][0]["value"])
        except (KeyError, ValueError):
            page = 0
        await _run_scan(client, body["channel"]["id"], body["user"]["id"], page=page)

    @app.action(re.compile(r"^sec:[A-Z0-9]+$"))
    async def act_sec(ack):
        await ack()  # button 的 url 屬性會直接開新分頁，這裡只 ack

    # 通用 fallback：未匹配 action（避免 Bolt warn）
    @app.action(re.compile(r".*"))
    async def act_fallback(ack):
        await ack()

    # ── app_mention / DM 文字 ──
    @app.event("app_mention")
    async def evt_mention(event, client):
        text = (event.get("text") or "").strip()
        # 移除開頭的 <@BOT_ID>
        text = re.sub(r"^<@[A-Z0-9]+>\s*", "", text).strip()
        await _handle_free_text(client, event["channel"], event["user"], text)

    @app.event("message")
    async def evt_message(event, client):
        # 只處理 DM（im）且非 bot 訊息
        if event.get("channel_type") != "im":
            return
        if event.get("bot_id") or event.get("subtype"):
            return
        text = (event.get("text") or "").strip()
        await _handle_free_text(client, event["channel"], event["user"], text)


# ══════════════════════════════════════════
# 分析流程（給 slash command 與 button 共用）
# ══════════════════════════════════════════


async def _dispatch_analysis(
    client: AsyncWebClient, channel: str, user_id: str, ticker: str,
) -> None:
    """共用入口：/report 與按鈕都走這裡。"""

    # 記錄查詢
    try:
        await record_query(user_id, ticker)
    except Exception:
        pass

    try:
        from utils.health import increment_request_count
        increment_request_count()
    except Exception:
        pass

    # 報告快取命中：直接送
    cached_report = report_cache.get(ticker)
    if cached_report:
        age = report_cache.get_age(ticker) or 0
        await client.chat_postMessage(
            channel=channel,
            text=f"⚡ {ticker} 快取 {age}s",
            blocks=[context([f":zap: 快取 *{age}s*"])],
        )
        await _send_report(client, channel, cached_report, ticker=ticker)
        return

    # 並發控制
    _purge_done_tasks()
    if len(_running_tasks) >= _MAX_INFLIGHT_TASKS:
        await post_message(
            client, channel,
            text=(
                f"🛑 系統排隊已滿（{_MAX_INFLIGHT_TASKS} 筆），請稍候 1-2 分鐘再試 "
                f"`/report {ticker}`"
            ),
        )
        return
    if _analysis_semaphore.locked():
        await post_message(
            client, channel,
            text=(
                f"⏳ 系統繁忙中，目前有 {Config.ANALYSIS_CONCURRENCY} 筆分析在跑，"
                f"稍後重試 `/report {ticker}`"
            ),
        )
        return

    key = (channel, user_id)
    existing = _running_tasks.get(key)
    if existing and not existing.done():
        await post_message(
            client, channel,
            text="⏳ 你已有一筆分析在跑，用 `/cancel` 中止後再重試",
        )
        return

    async def _run() -> None:
        async with _analysis_semaphore:
            await _execute_analysis(client, channel, ticker)

    task = asyncio.create_task(_run())
    _running_tasks[key] = task
    try:
        await task
    except asyncio.CancelledError:
        await client.chat_postMessage(
            channel=channel,
            text=f"⏹️ 已取消 *{ticker}* 分析",
        )
    finally:
        _running_tasks.pop(key, None)


async def _execute_analysis(client: AsyncWebClient, channel: str, ticker: str) -> None:
    """執行完整分析流程（受 semaphore 控制並發）。"""

    loading = await client.chat_postMessage(
        channel=channel,
        text=f"⏳ 分析 {ticker}…",
        blocks=[
            header(f"⏳ 分析 {ticker}…"),
            section(
                f"`{_progress_bar(10)}` *10%*\n"
                "📡 即時報價 + 基本面 + 技術指標"
            ),
        ],
    )
    loading_ts = loading.get("ts")

    async def _update_progress(percent: int, body: str) -> None:
        try:
            await client.chat_update(
                channel=channel, ts=loading_ts,
                text=f"分析 {ticker} {percent}%",
                blocks=[
                    header(f"⏳ 分析 {ticker}…"),
                    section(f"`{_progress_bar(percent)}` *{percent}%*\n{body}"),
                ],
            )
        except Exception:
            pass

    try:
        # ── Step 1: 基礎數據 ──
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
            raw_cache.set(f"{ticker}:base",
                          (finnhub_data, fundamentals_data, tradingview_data))

        await _update_progress(
            35,
            "✅ 即時報價 / 基本面 / 技術指標\n"
            "⏳ 擴展數據（同業 / 歷史 / 分析師 / 內部人 / 宏觀）",
        )

        # ── Step 2: 擴展數據 ──
        sector = fundamentals_data.get("sector", "")
        industry = fundamentals_data.get("industry", "")
        company_name = fundamentals_data.get("company_name", "")

        extended_tasks = [fetch_tavily_news(ticker, company_name)]
        labels = ["Tavily"]
        if Config.HISTORY_ENABLED:
            extended_tasks.append(fetch_history_analysis(ticker))
            labels.append("History")
        if Config.PEER_COMPARISON_ENABLED and sector and sector != "N/A":
            extended_tasks.append(fetch_peer_comparison(ticker, sector, industry))
            labels.append("Peer")
        extended_tasks.append(fetch_analyst_data(ticker)); labels.append("Analyst")
        extended_tasks.append(fetch_insider_transactions(ticker)); labels.append("Insider")
        extended_tasks.append(fetch_earnings_surprises(ticker)); labels.append("Earnings")
        extended_tasks.append(fetch_macro_data()); labels.append("Macro")

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

        ext_map = {
            labels[i]: _ensure_dict(ext_results[i], labels[i])
            for i in range(len(labels))
        }
        tavily_data = ext_map.get("Tavily", {})
        history_data = ext_map.get("History", {})
        peer_data = ext_map.get("Peer", {})
        analyst_data = ext_map.get("Analyst", {})
        insider_data = ext_map.get("Insider", {})
        earnings_data = ext_map.get("Earnings", {})
        macro_data = ext_map.get("Macro", {})

        # ── Step 3: 12 維度量化信號 ──
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
            1 for d in [history_data, peer_data, analyst_data, insider_data,
                        earnings_data, macro_data]
            if d and "error" not in d
        )
        await _update_progress(
            70,
            f"✅ 完成 {total_sources} 源數據抓取\n"
            f"🧮 量化信號：{signals_data.get('consensus', 'N/A')}\n"
            "🤖 AI 四觀點深度分析中",
        )

        # ── Step 4: AI 分析 ──
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

        # ── Step 5: 組裝報告 ──
        report = format_report(
            ticker, finnhub_data, fundamentals_data, tavily_data,
            tradingview_data, ai_analysis, history_data, peer_data,
            signals_data=signals_data,
            analyst_data=analyst_data,
            insider_data=insider_data,
            earnings_data=earnings_data,
            macro_data=macro_data,
        )
        report_cache.set(ticker, report)

        # 刪 loading
        try:
            await client.chat_delete(channel=channel, ts=loading_ts)
        except Exception:
            pass

        # 先送 K 線圖
        if chart_buf:
            try:
                await client.files_upload_v2(
                    channel=channel,
                    file=chart_buf.getvalue(),
                    filename=f"{ticker}_60d.png",
                    title=f"{ticker} 60 日 K 線圖",
                    initial_comment=f"📈 *{ticker}* — 60 日 K 線圖（MA5 / MA20 / MA60）",
                )
            except Exception as chart_err:
                logger.warning(f"[{ticker}] K 線圖上傳失敗: {chart_err}")

        # 送報告
        await _send_report(client, channel, report, ticker=ticker)
        logger.info(f"[{ticker}] 報告已發送")

    except asyncio.TimeoutError:
        logger.error(f"[{ticker}] 數據抓取整體超時")
        await _safe_update(client, channel, loading_ts,
                           text=f"❌ {ticker} 分析逾時",
                           blocks=[
                               header(f"❌ {ticker} 分析逾時"),
                               section(f"數據源回應過慢，稍後重試 `/report {ticker}`"),
                           ])
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"[{ticker}] 分析失敗: {e}", exc_info=True)
        await _safe_update(client, channel, loading_ts,
                           text=f"❌ {ticker} 分析失敗",
                           blocks=[
                               header(f"❌ {ticker} 分析失敗"),
                               section(f"資料源暫時不可用，30 秒後重試 `/report {ticker}`"),
                           ])


def _progress_bar(percent: int, length: int = 10) -> str:
    p = max(0, min(100, percent))
    filled = round(p / 100 * length)
    return "▓" * filled + "░" * (length - filled)


async def _safe_update(client, channel, ts, *, text, blocks=None):
    if not ts:
        await client.chat_postMessage(channel=channel, text=text, blocks=blocks)
        return
    try:
        await client.chat_update(channel=channel, ts=ts, text=text, blocks=blocks)
    except Exception:
        try:
            await client.chat_postMessage(channel=channel, text=text, blocks=blocks)
        except Exception:
            pass


# ══════════════════════════════════════════
# /chart /news /compare /watchlist /scan /stats
# ══════════════════════════════════════════


async def _send_chart(client: AsyncWebClient, channel: str, ticker: str, days: int) -> None:
    chart_buf = await generate_chart(ticker, days=days)
    if not chart_buf:
        await client.chat_postMessage(
            channel=channel,
            text=f"❌ 無法生成 {ticker} 的 K 線圖，稍後再試",
        )
        return
    period_label = "1 年" if days >= 252 else f"{days} 日"
    await client.files_upload_v2(
        channel=channel,
        file=chart_buf.getvalue(),
        filename=f"{ticker}_{days}d.png",
        title=f"{ticker} {period_label} K 線圖",
        initial_comment=f"📈 *{ticker}* — {period_label} K 線圖（MA5 / MA20 / MA60）",
    )
    # 額外送一則互動按鈕（K 線下方）
    await client.chat_postMessage(
        channel=channel,
        text=f"{ticker} 互動",
        blocks=[
            actions([
                button(f"{'• ' if days == 30 else ''}30D", f"chart:{ticker}:30"),
                button(f"{'• ' if days == 60 else ''}60D", f"chart:{ticker}:60"),
                button(f"{'• ' if days == 90 else ''}90D", f"chart:{ticker}:90"),
                button(f"{'• ' if days >= 252 else ''}1Y", f"chart:{ticker}:252"),
            ], block_id=f"chart_periods:{ticker}"),
            actions([
                button("📊 完整分析", f"report:{ticker}", value=ticker, style="primary"),
                button("⭐ 加入自選", f"watch:{ticker}", value=ticker),
            ], block_id=f"chart_followups:{ticker}"),
        ],
    )


async def _send_news(client: AsyncWebClient, channel: str, ticker: str) -> None:
    cache_key = f"news:{ticker}"
    cached = news_cache.get(cache_key)
    used_cache = cached is not None
    if used_cache:
        data = cached
    else:
        data = await fetch_tavily_news(ticker)
        if data and "error" not in data and data.get("news"):
            news_cache.set(cache_key, data)

    blocks = _format_news_blocks(ticker, data, used_cache, news_cache.get_age(cache_key))
    await client.chat_postMessage(
        channel=channel,
        text=f"📰 {ticker} 近 7 日新聞",
        blocks=blocks,
        unfurl_links=False,
        unfurl_media=False,
    )


def _format_news_blocks(ticker: str, data: dict, used_cache: bool, age: int | None) -> list[dict]:
    if not data or "error" in data or not data.get("news"):
        err = (data or {}).get("error", "")
        hint = "可能是代碼較冷或近 7 日無新聞" if not err else escape_mrkdwn(err)
        return [
            header(f"📰 {ticker} 近 7 日無新聞"),
            section(f"{hint}\n試試 `/report {ticker}` 看完整分析"),
        ]

    items = data.get("news", [])
    summary = (data.get("ai_summary") or "").strip()
    tag = f":zap: 快取 {age}s" if used_cache and age is not None else ":zap: 即時"

    blocks: list[dict] = [
        header(f"📰 {ticker} 近 7 日新聞 {len(items)} 則"),
        context([tag]),
    ]
    if summary and summary.lower() not in {"news data unavailable", "n/a"}:
        if len(summary) > 320:
            summary = summary[:317] + "…"
        blocks.append(section(f"💡 {escape_mrkdwn(summary)}"))

    body_lines = []
    for i, item in enumerate(items, start=1):
        title = (item.get("title") or "（無標題）").strip()
        url = item.get("url") or ""
        if url and url != "N/A":
            body_lines.append(f"{i}. <{url}|{escape_mrkdwn(title)}>")
        else:
            body_lines.append(f"{i}. {escape_mrkdwn(title)}")
    body = "\n".join(body_lines)
    if body:
        for chunk in chunk_mrkdwn(body):
            blocks.append(section(chunk))

    blocks.append(actions([
        button("📊 完整分析", f"report:{ticker}", value=ticker, style="primary"),
        button("⭐ 加入自選", f"watch:{ticker}", value=ticker),
    ], block_id=f"news_actions:{ticker}"))
    return blocks


async def _run_compare(client: AsyncWebClient, channel: str, tickers: list[str]) -> None:
    loading = await client.chat_postMessage(
        channel=channel, text=f"⚖️ 比較 {len(tickers)} 檔…",
    )
    loading_ts = loading.get("ts")

    prices = await fetch_fmp_batch_prices(tickers)
    try:
        ta_results = await asyncio.wait_for(
            asyncio.gather(
                *[fetch_tradingview_analysis(t) for t in tickers],
                return_exceptions=True,
            ),
            timeout=20,
        )
    except asyncio.TimeoutError:
        ta_results = [{}] * len(tickers)

    rec_map = {
        "STRONG_BUY": "🟢 強買", "BUY": "🟢 買入",
        "NEUTRAL": "🟡 中性", "SELL": "🔴 賣出", "STRONG_SELL": "🔴 強賣",
    }

    lines = ["*⚖️ 個股對比*", ""]
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
        rsi_str = "N/A"
        if isinstance(rsi, (int, float)):
            rsi_str = f"{rsi:.0f}"
            if rsi > 70: rsi_str += " ⚠️超買"
            elif rsi < 30: rsi_str += " ⚠️超賣"
        mcap = quote.get("market_cap")
        cap_str = _fmt_mcap(mcap)
        lines.append(f"*{t}* — {price_str}  {arrow}{sign}{chg_pct:.2f}%")
        lines.append(f"  建議: {rec_str}  |  RSI: {rsi_str}" + (f"  |  市值: {cap_str}" if cap_str else ""))
        lines.append("")

    blocks: list[dict] = [section("\n".join(lines))]
    btn_rows = []
    row: list[dict] = []
    for t in tickers:
        row.append(button(f"📊 {t}", f"report:{t}", value=t))
        if len(row) == 3:
            btn_rows.append(row); row = []
    if row:
        btn_rows.append(row)
    for i, r in enumerate(btn_rows):
        blocks.append(actions(r, block_id=f"compare_row_{i}"))

    if loading_ts:
        try:
            await client.chat_update(
                channel=channel, ts=loading_ts,
                text=f"⚖️ 對比 {' / '.join(tickers)}",
                blocks=blocks,
            )
            return
        except Exception:
            pass
    await client.chat_postMessage(
        channel=channel, text=f"⚖️ 對比 {' / '.join(tickers)}", blocks=blocks,
    )


async def _send_watchlist(
    client: AsyncWebClient, channel: str, user_id: str, force_refresh: bool = False,
) -> None:
    tickers = await get_watchlist(user_id)
    if not tickers:
        await client.chat_postMessage(
            channel=channel,
            text="📋 自選股清單是空的",
            blocks=[
                section(
                    "📋 *你的自選股清單是空的*\n"
                    "`/watch AAPL` 加入第一檔\n"
                    "`/report AAPL` 直接分析（不需先加入）"
                ),
            ],
        )
        return

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

    spark_results = await asyncio.gather(
        *[_get_sparkline(t) for t in tickers], return_exceptions=True,
    )
    spark_map = {t: (s if isinstance(s, str) else "") for t, s in zip(tickers, spark_results)}

    valid, invalid = [], []
    for t in tickers:
        q = prices.get(t, {})
        if q.get("price") is not None:
            valid.append({
                "ticker": t,
                "price": q["price"],
                "chg_pct": q.get("change_pct") or 0,
                "pos52": _pos_52w(q.get("price"), q.get("year_high"), q.get("year_low")),
                "vol_ratio": _vol_ratio(q.get("volume"), q.get("avg_volume")),
                "earn_days": _earnings_days(q.get("earnings_announcement")),
            })
        else:
            invalid.append(t)

    valid.sort(key=lambda r: r["chg_pct"], reverse=True)

    ups = sum(1 for r in valid if r["chg_pct"] > 0)
    downs = sum(1 for r in valid if r["chg_pct"] < 0)
    flats = len(valid) - ups - downs
    avg_pct = sum(r["chg_pct"] for r in valid) / len(valid) if valid else 0

    summary_line = (
        f"*自選股* ({len(tickers)} 檔)  🟢{ups} 漲 / 🔴{downs} 跌"
        + (f" / ⚪{flats} 平" if flats else "")
        + f"  平均 {'+' if avg_pct >= 0 else ''}{avg_pct:.2f}%"
    )
    age_tag = f":zap: 快取 {_wl_cache_age(cache_key) or 0}s" if used_cache else ":zap: 即時"

    blocks: list[dict] = [
        header("📋 自選股儀表板"),
        section(summary_line),
        context([age_tag]),
    ]

    if valid:
        top, bot = valid[0], valid[-1]
        highlights = []
        if top["chg_pct"] > 0:
            highlights.append(f"👑 最強 *{top['ticker']}* +{top['chg_pct']:.2f}%")
        if bot["chg_pct"] < 0:
            highlights.append(f"⚠️ 最弱 *{bot['ticker']}* {bot['chg_pct']:.2f}%")
        if highlights:
            blocks.append(section("  ".join(highlights)))

    earn_alerts = [r for r in valid if r["earn_days"] is not None]
    if earn_alerts:
        earn_alerts.sort(key=lambda r: r["earn_days"])
        bits = [f"*{r['ticker']}* ({r['earn_days']}天)" for r in earn_alerts[:5]]
        blocks.append(section(f"📅 *財報臨近*：{' · '.join(bits)}"))

    blocks.append(divider())

    body_lines = []
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
        spark = spark_map.get(r["ticker"], "")
        spark_str = f" {spark}" if spark else ""
        tag_str = f"  {'  '.join(tags)}" if tags else ""
        body_lines.append(
            f"{arrow} *{r['ticker']}*{spark_str}  ${r['price']:.2f}  "
            f"{sign}{r['chg_pct']:.2f}%{tag_str}"
        )

    if invalid:
        body_lines.append("")
        body_lines.append(f"❓ *無法取得報價* ({len(invalid)} 檔)")
        for t in invalid:
            body_lines.append(f"  ⚪ *{t}*  ➜ `/report {t}` 嘗試完整分析")

    body = "\n".join(body_lines)
    for chunk in chunk_mrkdwn(body):
        blocks.append(section(chunk))

    blocks.append(actions([
        button("🔄 強刷", "wl_refresh"),
        button("📊 批次快掃", "scanall"),
    ], block_id="wl_actions"))

    await client.chat_postMessage(
        channel=channel,
        text=f"📋 自選股 {len(tickers)} 檔",
        blocks=blocks,
    )


_SCAN_PAGE_SIZE = 10


async def _run_scan(
    client: AsyncWebClient, channel: str, user_id: str, page: int = 0,
) -> None:
    all_tickers = await get_watchlist(user_id)
    if not all_tickers:
        await client.chat_postMessage(
            channel=channel,
            text="📋 自選股清單是空的",
            blocks=[section("📋 自選股清單是空的，`/watch AAPL` 加入第一檔")],
        )
        return

    total = len(all_tickers)
    pages = max(1, (total + _SCAN_PAGE_SIZE - 1) // _SCAN_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * _SCAN_PAGE_SIZE
    end = start + _SCAN_PAGE_SIZE
    tickers = all_tickers[start:end]
    has_prev = page > 0
    has_next = end < total

    loading_text = (
        f"🔍 掃描第 {page + 1}/{pages} 頁（{len(tickers)} 檔）…" if pages > 1
        else f"🔍 掃描 {len(tickers)} 檔自選股…"
    )
    loading = await client.chat_postMessage(channel=channel, text=loading_text)
    loading_ts = loading.get("ts")

    prices = await fetch_fmp_batch_prices(tickers)
    ta_timeout = False
    try:
        ta_results = await asyncio.wait_for(
            asyncio.gather(*[fetch_tradingview_analysis(t) for t in tickers],
                           return_exceptions=True),
            timeout=20,
        )
    except asyncio.TimeoutError:
        ta_results = [{}] * len(tickers)
        ta_timeout = True

    rec_map = {
        "STRONG_BUY": "強買", "BUY": "買入",
        "NEUTRAL": "中性", "SELL": "賣出", "STRONG_SELL": "強賣",
    }

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

    def _alerts(r) -> list[str]:
        out = []
        if r["rsi"] is not None:
            if r["rsi"] > 70: out.append(f"RSI {r['rsi']:.0f} 超買")
            elif r["rsi"] < 30: out.append(f"RSI {r['rsi']:.0f} 超賣")
        if r["rec"] == "STRONG_BUY": out.append("TV 強買")
        elif r["rec"] == "STRONG_SELL": out.append("TV 強賣")
        if r["pos52"] is not None:
            if r["pos52"] >= 95: out.append(f"近 52w 高 ({r['pos52']:.0f}%)")
            elif r["pos52"] <= 5: out.append(f"近 52w 低 ({r['pos52']:.0f}%)")
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
    avg_pct = sum(r["chg_pct"] for r in valid) / len(valid) if valid else 0

    header_line = (
        f"*自選股批次快掃* ({len(valid)}/{len(tickers)})  "
        f"🟢{len(ups)} 漲 / 🔴{len(downs)} 跌"
        + (f" / ⚪{len(flats)} 平" if flats else "")
        + f"  平均 {'+' if avg_pct >= 0 else ''}{avg_pct:.2f}%"
    )

    def _row_line(r) -> str:
        arrow = "🟢" if r["chg_pct"] >= 0 else "🔴"
        sign = "+" if r["chg_pct"] >= 0 else ""
        bits = []
        if r["pos52"] is not None:
            bits.append(f"52w:{_pos52_bar(r['pos52'], 6)} {r['pos52']:.0f}%")
        if r["rsi"] is not None:
            bits.append(f"RSI:{r['rsi']:.0f}")
        if r["rec_zh"] and r["rec_zh"] != "N/A":
            bits.append(f"TV:{r['rec_zh']}")
        cap = _fmt_mcap(r["market_cap"])
        if cap: bits.append(cap)
        if r["vol_ratio"] and r["vol_ratio"] >= 1.5:
            bits.append(f"🔥{r['vol_ratio']:.1f}x")
        if r["earn_days"] is not None:
            bits.append(f"📅{r['earn_days']}天")
        head = f"{arrow} *{r['ticker']}* ${r['price']:.2f} {sign}{r['chg_pct']:.2f}%"
        return head + ("\n  " + " | ".join(bits) if bits else "") + f"  ➜ `/report {r['ticker']}`"

    sections_text = [f"📊 {header_line}"]
    if ta_timeout:
        sections_text.append("_⚠️ TradingView 超時，技術面顯示為 N/A_")

    if alerted_rows:
        sections_text.append("")
        sections_text.append("⚠️ *警示*")
        alerted_rows.sort(key=lambda x: abs(x[0]["chg_pct"]), reverse=True)
        for r, alerts in alerted_rows:
            sections_text.append(_row_line(r))
            sections_text.append(f"  ⚠️ {' · '.join(alerts)}")

    remaining_ups = [r for r in ups if r["ticker"] not in alerted_ids]
    if remaining_ups:
        sections_text.append("")
        sections_text.append(f"🟢 *上漲 ({len(remaining_ups)})*")
        for r in remaining_ups:
            sections_text.append(_row_line(r))

    remaining_downs = [r for r in downs if r["ticker"] not in alerted_ids]
    if remaining_downs:
        sections_text.append("")
        sections_text.append(f"🔴 *下跌 ({len(remaining_downs)})*")
        for r in remaining_downs:
            sections_text.append(_row_line(r))

    remaining_flats = [r for r in flats if r["ticker"] not in alerted_ids]
    if remaining_flats:
        sections_text.append("")
        sections_text.append("⚪ *持平*")
        for r in remaining_flats:
            sections_text.append(_row_line(r))

    if invalid:
        sections_text.append("")
        sections_text.append(f"❓ *無法取得報價* ({len(invalid)} 檔)")
        for r in invalid:
            sections_text.append(f"  ⚪ *{r['ticker']}*  ➜ `/report {r['ticker']}`")

    footer = (
        f"\n第 {page + 1}/{pages} 頁  |  共 {total} 檔  |  ➜ `/report` 看完整分析"
        if pages > 1 else
        f"\n共 {total} 檔  |  ➜ `/report` 看完整分析"
    )
    sections_text.append(footer)

    body = "\n".join(sections_text)
    blocks: list[dict] = [section(chunk) for chunk in chunk_mrkdwn(body)]

    nav_btns = []
    if has_prev:
        nav_btns.append(button("⬅️ 前 10", f"scan_page:{page - 1}", value=str(page - 1)))
    if has_next:
        nav_btns.append(button("➡️ 後 10", f"scan_page:{page + 1}", value=str(page + 1)))
    nav_btns.append(button("🔄 強刷", f"scan_page:{page}", value=str(page)))
    nav_btns.append(button("📋 清單", "wl_refresh"))
    blocks.append(actions(nav_btns, block_id="scan_nav"))

    if loading_ts:
        try:
            await client.chat_update(
                channel=channel, ts=loading_ts,
                text=f"📊 批次快掃 ({len(tickers)} 檔)",
                blocks=blocks,
            )
            return
        except Exception:
            pass
    await client.chat_postMessage(
        channel=channel, text=f"📊 批次快掃 ({len(tickers)} 檔)", blocks=blocks,
    )


async def _send_stats(client: AsyncWebClient, channel: str, user_id: str) -> None:
    try:
        stats = await get_user_stats(user_id)
    except Exception as e:
        logger.warning(f"[stats] 讀 DB 失敗 user={user_id}: {e}")
        await client.chat_postMessage(channel=channel, text="❌ 讀取統計失敗，稍後再試")
        return

    try:
        tenk_used, tenk_limit = await get_tenk_quota(user_id)
    except Exception:
        tenk_used, tenk_limit = 0, Config.TENK_DAILY_LIMIT

    today = stats.get("today_count", 0)
    month = stats.get("month_count", 0)
    wl = stats.get("watchlist_count", 0)
    top = stats.get("top_tickers", []) or []

    today_text = (
        f"*今日*\n"
        f"完整分析　{today} 次\n"
        f"10-K 配額　{tenk_used} / {tenk_limit} 次"
    )
    month_text = (
        f"*本月*\n"
        f"完整分析　*{month} 次*\n"
        f"自選股　　{wl} 檔"
    )

    blocks: list[dict] = [
        header("📊 你的使用紀錄"),
        section(today_text),
        section(month_text),
    ]

    if top:
        top_lines = [
            f"{i}. `{t}`　{cnt} 次" for i, (t, cnt) in enumerate(top, start=1)
        ]
        blocks.append(section("*最常查 Top 5（本月）*\n" + "\n".join(top_lines)))
    else:
        blocks.append(section("本月還沒有查詢紀錄，試試 `/report AAPL`"))

    first_seen = stats.get("first_seen")
    if first_seen:
        try:
            d = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
            since_days = max(1, (datetime.now(timezone.utc) - d).days)
            blocks.append(context([f"_使用滿 *{since_days}* 天_"]))
        except Exception:
            pass

    blocks.append(actions([
        button("📋 看自選股", "wl_refresh"),
        button("🔍 批次掃", "scanall"),
    ], block_id="stats_actions"))

    await client.chat_postMessage(
        channel=channel,
        text=f"📊 {today} 今日 / {month} 本月",
        blocks=blocks,
    )


# ══════════════════════════════════════════
# 自由文字（@mention / DM）→ 引導使用指令
# ══════════════════════════════════════════


async def _handle_free_text(client: AsyncWebClient, channel: str, user_id: str, text: str) -> None:
    text_upper = text.upper().strip()
    if _validate_ticker(text_upper):
        await client.chat_postMessage(
            channel=channel,
            text=f"💡 看起來像股票代碼 {text_upper}",
            blocks=[
                section(
                    f"💡 看起來像股票代碼 `{text_upper}`，要做什麼？\n"
                    f"• `/report {text_upper}` 完整分析（60 秒）\n"
                    f"• `/chart {text_upper}` K 線圖（秒回）\n"
                    f"• `/watch {text_upper}` 加入自選"
                ),
                actions([
                    button("📊 完整分析", f"report:{text_upper}", value=text_upper, style="primary"),
                    button("📈 K 線", f"chart:{text_upper}:60", value=f"{text_upper}:60"),
                    button("⭐ 加入自選", f"watch:{text_upper}", value=text_upper),
                ], block_id=f"freetext_actions:{text_upper}"),
            ],
        )
    else:
        await client.chat_postMessage(
            channel=channel,
            text="🤖 試試 /help 看完整指令",
            blocks=[section("🤖 試試 `/help` 看完整指令，或 `/report AAPL` 直接分析")],
        )


# ══════════════════════════════════════════
# Welcome / Help blocks
# ══════════════════════════════════════════


def _welcome_blocks() -> list[dict]:
    return [
        header("📊 美股深度分析 Bot"),
        section(
            "12 維度量化信號 + OpenAI 四觀點 + SEC 10-K 全文解析\n\n"
            "*四種分析深度*\n"
            "• `/report AAPL` — 60 秒完整報告\n"
            "• `/tenk AAPL` — 10-K 年報深度（5–15 分鐘）\n"
            "• `/news AAPL` — 近 7 日新聞（秒回）\n"
            "• `/chart AAPL` — K 線圖秒回\n\n"
            "*自選股*\n"
            "• `/watch AAPL` 加入  · `/watchlist` 看清單  · `/scan` 批次掃"
        ),
        actions([
            button("🔥 試試 AAPL", "report:AAPL", value="AAPL", style="primary"),
            button("📋 看清單", "wl_refresh"),
            button("📊 我的統計", "stats_show"),
            button("🧭 完整指令", "help_show"),
        ], block_id="welcome_actions"),
    ]


def _help_blocks() -> list[dict]:
    return [
        header("🧭 指令手冊"),
        section(
            "*🔍 分析*\n"
            "• `/report TICKER` — 完整深度分析（12 信號 + AI）\n"
            "• `/tenk TICKER [年] [Q1|Q2|Q3]` — 10-K / 10-Q（5–15 分鐘，每日 3 次）\n"
            "• `/news TICKER` — 近 7 日新聞（秒回）\n"
            "• `/chart TICKER` — 60 日 K 線圖（秒回）\n"
            "• `/compare T1 T2 …` — 並排對比 2–5 檔"
        ),
        section(
            "*📋 自選股*\n"
            "• `/watchlist` — 即時報價儀表板\n"
            "• `/scan` — 批次快掃\n"
            "• `/watch TICKER` 加入 · `/unwatch TICKER` 移除"
        ),
        section(
            "*🧭 其他*\n"
            "• `/stats` — 個人使用統計\n"
            "• `/cancel` — 中止進行中的分析\n"
            "• `/help` — 本手冊"
        ),
        context([
            "📡 資料：Finnhub · FMP · Stooq · TradingView · Tavily · SEC EDGAR",
            "🛡️ 反幻覺：所有 AI 分析皆需數據佐證，缺失即標 N/A",
        ]),
    ]


def _replace_actions_block(blocks: list[dict], ticker: str, watched: bool) -> list[dict]:
    """更新 report 訊息的 actions block（隱藏「加入自選」按鈕）。"""
    out = []
    for b in blocks:
        if b.get("type") == "actions" and b.get("block_id") == f"report_actions:{ticker}":
            out.append(_report_actions_block(ticker, watched=watched))
        else:
            out.append(b)
    return out


# ══════════════════════════════════════════
# 額外 actions：歡迎卡片裡的 help_show / stats_show
# ══════════════════════════════════════════


def register_welcome_actions(app: AsyncApp) -> None:
    @app.action("help_show")
    async def _help_show(ack, body, client):
        await ack()
        await client.chat_postEphemeral(
            channel=body["channel"]["id"], user=body["user"]["id"],
            text="🧭 指令手冊", blocks=_help_blocks(),
        )

    @app.action("stats_show")
    async def _stats_show(ack, body, client):
        await ack()
        await _send_stats(client, body["channel"]["id"], body["user"]["id"])


# ══════════════════════════════════════════
# 公開：建立 App
# ══════════════════════════════════════════


def create_slack_app() -> AsyncApp:
    """建立並設定 Slack AsyncApp（Socket Mode 用）。"""
    app = AsyncApp(
        token=Config.SLACK_BOT_TOKEN,
        # Socket Mode 不需簽章驗證；HTTP 模式才需 signing_secret
        signing_secret=Config.SLACK_SIGNING_SECRET or None,
    )

    register_handlers(app)
    register_welcome_actions(app)

    @app.error
    async def global_error_handler(error, body, logger_):
        logger.error(f"未處理的 Slack 異常: {error}", exc_info=error)

    return app
