"""
/tenk Telegram handler — 10-K / 10-Q 深度分析背景任務。

設計重點：
- 立即回覆「啟動中」訊息，pipeline 在 asyncio.create_task 背景跑，不擋其他指令
- 進度透過 edit_message_text 持續更新
- 全 bot 同時最多 1 件 tenk 任務（獨立 semaphore）
- 每用戶每日次數限制 + 半年內報告快取（DB 記錄）
- 30 分鐘整體逾時保護
"""

import asyncio
import html
import logging
import re
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import Config
from utils.database import (
    tenk_get_cached_report,
    tenk_save_report,
    tenk_get_daily_count,
    tenk_increment_daily,
)

logger = logging.getLogger(__name__)

# 全 bot 同時最多 1 件 tenk（pipeline 重 + 防止 SEC EDGAR 阻擋）
_tenk_semaphore = asyncio.Semaphore(1)
_inflight_users: set[int] = set()

_TICKER_PATTERN = re.compile(r"^[A-Z0-9]{1,5}$")
_QUARTER_PATTERN = re.compile(r"^Q[1-3]$", re.IGNORECASE)


# ────────────────────────────────────────────
# 進度文案
# ────────────────────────────────────────────

_STAGE_LABELS = {
    "fetch":          "📡 下載 SEC 財務數據",
    "sections":       "📄 抓主文件並切章節",
    "prior_sections": "📄 抓前期作為對照",
    "phase1":         "🧠 Phase 1/5：並行分析多維度",
    "phase2a":        "🧠 Phase 2/5：財務交叉驗證",
    "phase2b":        "🧠 Phase 2/5：部門結構",
    "phase2c":        "🧠 Phase 2/5：三表訊號",
    "phase3":         "🧠 Phase 3/5：不尋常操作",
    "phase3b":        "🧠 Phase 3/5：評價趨勢三條件",
    "eval":           "🧠 Phase 4/5：品質評估",
    "prior":          "🧠 Phase 4/5：前期同維度",
    "synthesis":      "🧠 Phase 4/5：跨年度綜合判斷",
    "phase5":         "🧠 Phase 5/5：報告品質評分",
    "report":         "📝 產出報告",
}


def _h(value) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


def _validate_ticker(ticker: str) -> bool:
    return bool(_TICKER_PATTERN.match(ticker))


def _default_year() -> int:
    """10-K 通常 1-4 月才出，當前年-1 較保險。"""
    return datetime.utcnow().year - 1


# ────────────────────────────────────────────
# /tenk 指令
# ────────────────────────────────────────────


async def tenk_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tenk TICKER [YEAR] [Q1|Q2|Q3]"""
    if not Config.TENK_ENABLED:
        await update.message.reply_text("ℹ️ 10-K 深度分析功能目前未啟用")
        return

    if not context.args:
        await update.message.reply_text(
            "❌ 用法：<code>/tenk AAPL</code>\n"
            "  指定年份：<code>/tenk AAPL 2024</code>\n"
            "  指定季度：<code>/tenk AAPL 2025 Q1</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    args = list(context.args)
    ticker = args[0].upper()
    if not _validate_ticker(ticker):
        await update.message.reply_text("❌ 無效的股票代碼")
        return

    year = _default_year()
    quarter = None

    if len(args) >= 2:
        try:
            year = int(args[1])
        except ValueError:
            await update.message.reply_text("❌ 年份格式錯誤，例如：2024")
            return
        if year < 2000 or year > datetime.utcnow().year:
            await update.message.reply_text("❌ 年份超出合理範圍")
            return

    if len(args) >= 3:
        q = args[2].upper()
        if not _QUARTER_PATTERN.match(q):
            await update.message.reply_text("❌ 季度格式應為 Q1 / Q2 / Q3")
            return
        quarter = q

    await dispatch_tenk_analysis(
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
        ticker=ticker,
        bot=context.bot,
        year=year,
        quarter=quarter,
    )


async def dispatch_tenk_analysis(
    *,
    chat_id: int,
    user_id: int,
    ticker: str,
    bot,
    year: int | None = None,
    quarter: str | None = None,
) -> None:
    """共用入口：/tenk 指令與 [📋 10K] 按鈕都走這裡。"""
    if not Config.TENK_ENABLED:
        await bot.send_message(chat_id=chat_id, text="ℹ️ 10-K 深度分析功能目前未啟用")
        return

    if not _validate_ticker(ticker):
        await bot.send_message(chat_id=chat_id, text="❌ 無效的股票代碼")
        return

    if year is None:
        year = _default_year()
    filing_type = "10-Q" if quarter else "10-K"

    if user_id in _inflight_users:
        await bot.send_message(
            chat_id=chat_id,
            text="⏳ 你已經有一個 10-K 分析在進行中，請等它完成再開新的",
        )
        return

    used = await tenk_get_daily_count(user_id)
    if used >= Config.TENK_DAILY_LIMIT:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"⚠️ 今日 10-K 深度分析已用完（{used}/{Config.TENK_DAILY_LIMIT}）\n"
                f"明日 UTC 00:00 重置"
            ),
        )
        return

    cached = await tenk_get_cached_report(
        ticker, year, filing_type, quarter, Config.TENK_REPORT_TTL_DAYS,
    )
    if cached:
        await _send_cached_via_bot(bot, chat_id, ticker, year, filing_type, quarter, cached)
        return

    label = filing_type + (f" {quarter}" if quarter else "")
    loading = await bot.send_message(
        chat_id=chat_id,
        text=(
            f"🔍 <b>{_h(ticker)} {_h(year)} {_h(label)}</b> 深度分析啟動\n"
            f"預估 8-15 分鐘，完成後會主動推送\n"
            f"你可以繼續用其他指令"
        ),
        parse_mode=ParseMode.HTML,
    )

    _inflight_users.add(user_id)
    asyncio.create_task(
        _run_tenk_background(
            chat_id=chat_id,
            user_id=user_id,
            ticker=ticker,
            year=year,
            filing_type=filing_type,
            quarter=quarter,
            loading_message=loading,
            bot=bot,
        )
    )


async def get_tenk_quota(user_id: int) -> tuple[int, int]:
    """回傳 (used, limit) — 給 [📋 10K] callback 顯示配額。"""
    used = await tenk_get_daily_count(user_id)
    return used, Config.TENK_DAILY_LIMIT


async def _send_cached_via_bot(bot, chat_id, ticker, year, filing_type, quarter, cached) -> None:
    """callback / dispatch 走這條（純 bot+chat_id 版）。"""
    age = "—"
    try:
        created = datetime.fromisoformat(cached["created_at"].replace("Z", "+00:00"))
        delta = datetime.utcnow() - created.replace(tzinfo=None)
        age = f"{delta.days} 天前"
    except Exception:
        pass

    await bot.send_message(
        chat_id=chat_id,
        text=f"⚡ 快取 ({age}，半年內不重跑)",
        disable_notification=True,
    )
    await bot.send_message(
        chat_id=chat_id,
        text=cached["summary"],
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    md_path = Path(cached["report_md_path"])
    if md_path.exists():
        await bot.send_document(
            chat_id=chat_id,
            document=md_path.open("rb"),
            filename=md_path.name,
            caption=f"📑 {ticker} {year} {filing_type}{(' ' + quarter) if quarter else ''} 完整報告",
        )




# ────────────────────────────────────────────
# 背景執行
# ────────────────────────────────────────────


async def _run_tenk_background(
    *, chat_id, user_id, ticker, year, filing_type, quarter, loading_message, bot,
) -> None:
    """asyncio.create_task 跑這個。失敗會 catch 並回友善訊息。"""
    label = filing_type + (f" {quarter}" if quarter else "")

    if _tenk_semaphore.locked():
        try:
            await loading_message.edit_text(
                f"⏳ <b>{_h(ticker)} {_h(year)} {_h(label)}</b>\n"
                f"目前有其他 10-K 分析進行中，排隊等待…",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    async with _tenk_semaphore:
        try:
            # 進度 callback：以 throttle 機制更新 loading message，避免 hit Telegram rate
            last_edit = {"ts": 0.0}

            async def _progress(stage: str, detail: str | None = None) -> None:
                import time
                now = time.time()
                if now - last_edit["ts"] < 1.5:  # 至少 1.5s 才更新一次
                    return
                last_edit["ts"] = now
                title = _STAGE_LABELS.get(stage, stage)
                text = (
                    f"⏳ <b>{_h(ticker)} {_h(year)} {_h(label)}</b>\n"
                    f"{title}"
                )
                if detail:
                    text += f"\n<i>{_h(detail)}</i>"
                try:
                    await loading_message.edit_text(text, parse_mode=ParseMode.HTML)
                except Exception:
                    pass

            # 真正執行（lazy import 避免 bot 啟動就載入重依賴）
            from tenk.pipeline import run_tenk_analysis

            result = await asyncio.wait_for(
                run_tenk_analysis(
                    ticker, year,
                    filing_type=filing_type, quarter=quarter,
                    progress=_progress,
                ),
                timeout=Config.TENK_PIPELINE_TIMEOUT,
            )

            # 寫快取與計數
            await tenk_save_report(
                ticker, year, filing_type, quarter,
                str(result["report_md"]),
                str(result["raw_json"]),
                result["summary"],
            )
            new_count = await tenk_increment_daily(user_id)
            logger.info(
                f"[tenk] {ticker} {year} {label} 完成 user={user_id} "
                f"daily={new_count}/{Config.TENK_DAILY_LIMIT}"
            )

            # 收尾：刪 loading、推摘要、推 md 檔
            try:
                await loading_message.delete()
            except Exception:
                pass

            await bot.send_message(
                chat_id=chat_id,
                text=result["summary"],
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

            md_path: Path = result["report_md"]
            if md_path.exists():
                await bot.send_document(
                    chat_id=chat_id,
                    document=md_path.open("rb"),
                    filename=md_path.name,
                    caption=(
                        f"📑 {ticker} {year} {label} 完整報告（markdown）\n"
                        f"今日已使用 {new_count}/{Config.TENK_DAILY_LIMIT}"
                    ),
                )

        except asyncio.TimeoutError:
            logger.error(f"[tenk] {ticker} {year} {label} 逾時")
            await _safe_edit(
                loading_message,
                f"❌ <b>{_h(ticker)} {_h(year)}</b> 分析逾時（超過 30 分鐘）\n請稍後重試",
            )

        except FileNotFoundError as exc:
            await _safe_edit(
                loading_message,
                f"❌ 找不到 <b>{_h(ticker)} {_h(year)} {_h(label)}</b>\n"
                f"<i>{_h(str(exc))}</i>\n"
                f"可能尚未公布，或年份/季度不正確",
            )

        except Exception as exc:
            logger.error(f"[tenk] {ticker} 失敗: {exc}", exc_info=True)
            await _safe_edit(
                loading_message,
                f"❌ <b>{_h(ticker)}</b> 分析失敗\n<code>{_h(str(exc)[:200])}</code>",
            )

        finally:
            _inflight_users.discard(user_id)


async def _safe_edit(message, text: str) -> None:
    try:
        await message.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception:
        pass
