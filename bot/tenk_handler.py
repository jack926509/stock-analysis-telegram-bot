"""
/tenk Slack handler — 10-K / 10-Q 深度分析背景任務。

設計重點：
- Slash command 立即 ack，pipeline 在 asyncio.create_task 背景跑
- 進度透過 chat_update 持續更新（throttle 1.5s）
- 全 bot 同時最多 1 件 tenk 任務（獨立 semaphore，避免 SEC EDGAR 阻擋）
- 每用戶每日次數限制 + 半年內報告快取（DB 記錄）
- 30 分鐘整體逾時保護
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from slack_sdk.web.async_client import AsyncWebClient

from config import Config
from utils.database import (
    tenk_get_cached_report,
    tenk_get_daily_count,
    tenk_increment_daily,
    tenk_save_report,
)
from utils.slack_formatter import (
    actions,
    button,
    context,
    divider,
    escape_mrkdwn,
    header,
    html_to_mrkdwn,
    mrkdwn_to_blocks,
    section,
)

logger = logging.getLogger(__name__)

# 全 bot 同時最多 1 件 tenk（pipeline 重 + 防止 SEC EDGAR 阻擋）
_tenk_semaphore = asyncio.Semaphore(1)
_inflight_users: set[str] = set()

_TICKER_PATTERN = re.compile(r"^[A-Z0-9]{1,5}$")
_QUARTER_PATTERN = re.compile(r"^Q[1-3]$", re.IGNORECASE)


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


def _validate_ticker(ticker: str) -> bool:
    return bool(_TICKER_PATTERN.match(ticker))


def _default_year() -> int:
    """10-K 通常 1-4 月才出，當前年-1 較保險。"""
    return datetime.utcnow().year - 1


def parse_tenk_args(args_text: str) -> tuple[str | None, int, str | None, str | None]:
    """解析 /tenk 參數字串。回傳 (ticker, year, quarter, error)。"""
    parts = (args_text or "").strip().split()
    if not parts:
        return None, 0, None, "usage"

    ticker = parts[0].upper()
    if not _validate_ticker(ticker):
        return None, 0, None, "❌ 無效的股票代碼"

    year = _default_year()
    quarter: str | None = None
    if len(parts) >= 2:
        try:
            year = int(parts[1])
        except ValueError:
            return None, 0, None, "❌ 年份格式錯誤，例如：2024"
        if year < 2000 or year > datetime.utcnow().year:
            return None, 0, None, "❌ 年份超出合理範圍"

    if len(parts) >= 3:
        q = parts[2].upper()
        if not _QUARTER_PATTERN.match(q):
            return None, 0, None, "❌ 季度格式應為 Q1 / Q2 / Q3"
        quarter = q

    return ticker, year, quarter, None


async def dispatch_tenk_analysis(
    *,
    client: AsyncWebClient,
    channel: str,
    user_id: str,
    ticker: str,
    year: int | None = None,
    quarter: str | None = None,
) -> None:
    """共用入口：/tenk 與 [📋 10-K 深度] 按鈕都走這裡。"""
    if not Config.TENK_ENABLED:
        await client.chat_postMessage(channel=channel, text="ℹ️ 10-K 深度分析功能目前未啟用")
        return
    if not _validate_ticker(ticker):
        await client.chat_postMessage(channel=channel, text="❌ 無效的股票代碼")
        return

    if year is None:
        year = _default_year()
    filing_type = "10-Q" if quarter else "10-K"

    # Atomic check-and-add：在進入 await 之前同步搶占 inflight 標記
    if user_id in _inflight_users:
        await client.chat_postMessage(
            channel=channel,
            text="⏳ 你已有一個 10-K 分析在進行中，請等它完成再開新的",
        )
        return
    _inflight_users.add(user_id)

    background_owns_flag = False
    try:
        used = await tenk_get_daily_count(user_id)
        if used >= Config.TENK_DAILY_LIMIT:
            await client.chat_postMessage(
                channel=channel,
                text=(
                    f"⚠️ 今日 10-K 深度分析已用完（{used}/{Config.TENK_DAILY_LIMIT}），"
                    "明日 UTC 00:00 重置"
                ),
            )
            return

        cached = await tenk_get_cached_report(
            ticker, year, filing_type, quarter, Config.TENK_REPORT_TTL_DAYS,
        )
        if cached:
            await _send_cached(client, channel, ticker, year, filing_type, quarter, cached)
            return

        label = filing_type + (f" {quarter}" if quarter else "")
        loading = await client.chat_postMessage(
            channel=channel,
            text=f"🔍 {ticker} {year} {label} 深度分析啟動",
            blocks=[
                header(f"🔍 {ticker} {year} {label} 深度分析啟動"),
                section(
                    "預估 *8–15 分鐘*，完成後會在本對話推送。\n"
                    "你可以繼續用其他指令（/report、/news 等）。"
                ),
            ],
        )
        loading_ts = loading.get("ts")

        asyncio.create_task(
            _run_tenk_background(
                client=client,
                channel=channel,
                user_id=user_id,
                ticker=ticker,
                year=year,
                filing_type=filing_type,
                quarter=quarter,
                loading_ts=loading_ts,
            )
        )
        background_owns_flag = True
    finally:
        if not background_owns_flag:
            _inflight_users.discard(user_id)


async def get_tenk_quota(user_id: str) -> tuple[int, int]:
    """回傳 (used, limit) — 給 [📋 10-K] callback 顯示配額。"""
    used = await tenk_get_daily_count(user_id)
    return used, Config.TENK_DAILY_LIMIT


async def _send_cached(client, channel, ticker, year, filing_type, quarter, cached) -> None:
    age = "—"
    try:
        created = datetime.fromisoformat(cached["created_at"].replace("Z", "+00:00"))
        delta = datetime.utcnow() - created.replace(tzinfo=None)
        age = f"{delta.days} 天前"
    except Exception:
        pass

    label = filing_type + (f" {quarter}" if quarter else "")
    summary_mrkdwn = html_to_mrkdwn(cached["summary"])

    await client.chat_postMessage(
        channel=channel,
        text=f"⚡ {ticker} {year} {label} 快取命中 ({age})",
        blocks=[
            header(f"📋 {ticker} {year} {label}"),
            context([f":zap: 快取 *{age}*（半年內不重跑）"]),
            divider(),
            *mrkdwn_to_blocks(summary_mrkdwn),
        ],
    )
    md_path = Path(cached["report_md_path"])
    if md_path.exists():
        await client.files_upload_v2(
            channel=channel,
            file=str(md_path),
            filename=md_path.name,
            title=f"{ticker} {year} {label} 完整報告",
            initial_comment=f"📑 完整 markdown 報告：*{ticker} {year} {label}*",
        )


async def _run_tenk_background(
    *, client: AsyncWebClient, channel: str, user_id: str,
    ticker: str, year: int, filing_type: str, quarter: str | None,
    loading_ts: str | None,
) -> None:
    """asyncio.create_task 跑這個。失敗會 catch 並回友善訊息。"""
    label = filing_type + (f" {quarter}" if quarter else "")

    if _tenk_semaphore.locked() and loading_ts:
        await _safe_update(
            client, channel, loading_ts,
            text=f"⏳ {ticker} {year} {label} — 排隊等待中…",
            blocks=[
                header(f"⏳ {ticker} {year} {label}"),
                section("目前有其他 10-K 分析進行中，*排隊等待…*"),
            ],
        )

    async with _tenk_semaphore:
        try:
            last_edit = {"ts": 0.0}

            async def _progress(stage: str, detail: str | None = None) -> None:
                now = time.time()
                if now - last_edit["ts"] < 1.5:
                    return
                last_edit["ts"] = now
                if not loading_ts:
                    return
                title = _STAGE_LABELS.get(stage, stage)
                blocks = [
                    header(f"⏳ {ticker} {year} {label}"),
                    section(f"*{title}*"),
                ]
                if detail:
                    blocks.append(context([f"_{escape_mrkdwn(detail)}_"]))
                await _safe_update(
                    client, channel, loading_ts,
                    text=f"{title} — {ticker} {year} {label}",
                    blocks=blocks,
                )

            # lazy import：避免 bot 啟動就載入重依賴
            from tenk.pipeline import run_tenk_analysis

            result = await asyncio.wait_for(
                run_tenk_analysis(
                    ticker, year,
                    filing_type=filing_type, quarter=quarter,
                    progress=_progress,
                ),
                timeout=Config.TENK_PIPELINE_TIMEOUT,
            )

            await tenk_save_report(
                ticker, year, filing_type, quarter,
                str(result["report_md"]),
                str(result["raw_json"]),
                result["summary"],
            )
            new_count = await tenk_increment_daily(user_id)
            logger.info(
                "tenk_done",
                extra={"ticker": ticker, "year": year, "label": label,
                       "user": user_id, "daily": f"{new_count}/{Config.TENK_DAILY_LIMIT}"}
            )

            if loading_ts:
                try:
                    await client.chat_delete(channel=channel, ts=loading_ts)
                except Exception as e:
                    logger.debug(f"[tenk] {ticker} loading delete failed: {e}")

            summary_mrkdwn = html_to_mrkdwn(result["summary"])
            await client.chat_postMessage(
                channel=channel,
                text=f"✅ {ticker} {year} {label} 深度分析完成",
                blocks=[
                    header(f"✅ {ticker} {year} {label} 深度分析完成"),
                    *mrkdwn_to_blocks(summary_mrkdwn),
                    context([f":bar_chart: 今日已使用 *{new_count}/{Config.TENK_DAILY_LIMIT}*"]),
                ],
            )

            md_path: Path = result["report_md"]
            if md_path.exists():
                await client.files_upload_v2(
                    channel=channel,
                    file=str(md_path),
                    filename=md_path.name,
                    title=f"{ticker} {year} {label} 完整報告",
                    initial_comment=f"📑 *{ticker} {year} {label}* 完整 markdown 報告",
                )

        except asyncio.TimeoutError:
            logger.error(f"[tenk] {ticker} {year} {label} 逾時")
            await _safe_update(
                client, channel, loading_ts,
                text=f"❌ {ticker} {year} 分析逾時",
                blocks=[
                    header(f"❌ {ticker} {year} 分析逾時"),
                    section("超過 30 分鐘，請稍後重試"),
                ],
            )
        except FileNotFoundError as exc:
            await _safe_update(
                client, channel, loading_ts,
                text=f"❌ 找不到 {ticker} {year} {label}",
                blocks=[
                    header(f"❌ 找不到 {ticker} {year} {label}"),
                    section(f"_{escape_mrkdwn(str(exc))}_"),
                    context(["可能尚未公布，或年份/季度不正確"]),
                ],
            )
        except Exception as exc:
            logger.error(f"[tenk] {ticker} 失敗: {exc}", exc_info=True)
            await _safe_update(
                client, channel, loading_ts,
                text=f"❌ {ticker} 分析失敗",
                blocks=[
                    header(f"❌ {ticker} 分析失敗"),
                    section(f"`{escape_mrkdwn(str(exc)[:200])}`"),
                ],
            )
        finally:
            _inflight_users.discard(user_id)


async def _safe_update(
    client: AsyncWebClient,
    channel: str,
    ts: str | None,
    *,
    text: str,
    blocks: list[dict] | None = None,
) -> None:
    if not ts:
        return
    try:
        await client.chat_update(channel=channel, ts=ts, text=text, blocks=blocks)
    except Exception as e:
        logger.debug(f"[tenk] safe_update failed: {e}")


# 給 slack_bot.py 重複使用
__all__ = [
    "dispatch_tenk_analysis",
    "get_tenk_quota",
    "parse_tenk_args",
    "actions",
    "button",
]
