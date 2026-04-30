"""
tenk.pipeline — 對外的高階入口。

bot 端只需 import 這個模組：
    from tenk.pipeline import run_tenk_analysis

封裝原 main.py 的 build_sections / _determine_prior 邏輯，回傳
{"report_md": Path, "raw_json": Path, "summary": str}。
"""

import asyncio
import json
import logging
from pathlib import Path

from .agent_runner import truncate_with_notice
from .data_fetcher import (
    download_filing,
    extract_key_metrics,
    extract_quarterly_metrics,
    get_xbrl_facts,
)
from .doc_converter import convert_to_markdown
from .orchestrator import run_pipeline
from .pipeline_state import PipelineState
from .report_writer import build_telegram_summary
from .section_splitter import (
    extract_footnotes,
    extract_fs_tables,
    split_footnotes,
    split_sections,
    validate_sections,
)

logger = logging.getLogger(__name__)


def _determine_prior(year: int, filing_type: str, quarter: str | None,
                     override: int | None = None):
    """Decide which period to compare against. Same rules as upstream main.py."""
    if filing_type == "10-K":
        return (override or year - 1), "10-K", None
    if quarter == "Q1":
        return (override or year - 1), "10-K", None
    if quarter == "Q2":
        return (override or year), "10-Q", "Q1"
    if quarter == "Q3":
        return (override or year), "10-Q", "Q2"
    return (override or year - 1), "10-K", None


async def _build_sections(
    ticker: str,
    year: int,
    filing_type: str = "10-K",
    quarter: str | None = None,
) -> dict:
    """Download filing, convert to markdown, split sections (LLM fallback if needed)."""
    # I/O bound (HTTP + file write) — push to thread to avoid blocking event loop
    doc_path: Path = await asyncio.to_thread(
        download_filing, ticker, year, filing_type, quarter
    )
    md_text: str = await asyncio.to_thread(convert_to_markdown, doc_path)

    raw = await split_sections(md_text, filing_type=filing_type)
    warnings = validate_sections(raw, filing_type=filing_type)
    for w in warnings:
        logger.warning(f"[tenk:section_splitter] {w}")

    if filing_type == "10-Q":
        item_fs = raw.get("item1", "")
        result = {
            "item1_current": "",
            "item1a_current": raw.get("item1a", ""),
            "item7_current": raw.get("item2", ""),
            "item8_fs": extract_fs_tables(item_fs),
            "partiii_current": "",
            "fn_combined": extract_footnotes(item_fs),
            "_split_warnings": warnings,
            "_year": year,
        }
    else:
        item8 = raw.get("item8", "")
        item_fs = item8
        fn_subs = split_footnotes(item8)
        result = {
            "item1_current": raw.get("item1", ""),
            "item1a_current": raw.get("item1a", ""),
            "item7_current": raw.get("item7", ""),
            "item8_fs": extract_fs_tables(item8),
            "partiii_current": (
                raw.get("item10", "")
                + "\n"
                + raw.get("item11", "")
                + "\n"
                + raw.get("item13", "")
            ),
            "_split_warnings": warnings,
            "_year": year,
        }
        for fn_key, fn_text in fn_subs.items():
            result[fn_key] = fn_text

    all_sections_md = (
        truncate_with_notice(result.get("item1_current", ""), 2000) + "\n\n"
        + truncate_with_notice(result.get("item1a_current", ""), 3000) + "\n\n"
        + truncate_with_notice(result.get("item7_current", ""), 4000) + "\n\n"
        + truncate_with_notice(extract_footnotes(item_fs), 3000)
    )
    result["all_sections_md"] = all_sections_md
    result["item8_footnotes_md"] = truncate_with_notice(
        extract_footnotes(item_fs), 8000
    )
    result["item8_footnotes_current"] = truncate_with_notice(
        extract_footnotes(item8 if filing_type != "10-Q" else item_fs),
        12000,
    )
    return result


async def run_tenk_analysis(
    ticker: str,
    year: int,
    *,
    filing_type: str = "10-K",
    quarter: str | None = None,
    prior_year: int | None = None,
    progress=None,
) -> dict:
    """
    跑完整 10-K / 10-Q pipeline。

    progress: optional async callable(stage: str, detail: str | None)
    回傳 dict: {report_md, raw_json, summary, ticker, year, filing_type, quarter}
    """
    ticker = ticker.upper()
    prior_y, prior_ft, prior_q = _determine_prior(year, filing_type, quarter, prior_year)

    state = PipelineState(
        ticker, year, prior_y,
        filing_type=filing_type, quarter=quarter,
        prior_filing_type=prior_ft, prior_quarter=prior_q,
    )

    # 進度：抓 XBRL（同步、I/O，丟 to_thread）
    if progress:
        await progress("fetch", "下載 SEC EDGAR XBRL")
    xbrl_facts = await asyncio.to_thread(get_xbrl_facts, ticker)
    xbrl_json = json.dumps(
        extract_key_metrics(xbrl_facts, filing_type=filing_type),
        ensure_ascii=False,
    )
    quarterly = extract_quarterly_metrics(xbrl_facts, num_quarters=5)

    # 進度：抓財報主文件 + 切章節
    if progress:
        await progress("sections", f"抓 {filing_type} 主文件並切章節")
    sections = await _build_sections(ticker, year, filing_type, quarter)
    sections["xbrl_data"] = xbrl_json
    sections["_quarterly"] = quarterly

    if progress:
        await progress("prior_sections", f"抓前期 {prior_ft}")
    prior_sections = await _build_sections(
        ticker, prior_y, filing_type=prior_ft, quarter=prior_q
    )
    prior_sections["xbrl_data"] = xbrl_json

    # 跑 pipeline
    pipeline_out = await run_pipeline(
        ticker, sections, prior_sections, state=state,
        filing_type=filing_type, quarter=quarter,
        progress=progress,
    )

    report_md_path: Path = pipeline_out["report_path"]
    raw_json_path = report_md_path.with_name(report_md_path.name.replace("_report.md", "_raw.json"))

    summary = build_telegram_summary(
        ticker,
        pipeline_out["results"],
        pipeline_out["synthesis"],
        filing_type=filing_type,
        quarter=quarter,
    )

    return {
        "ticker": ticker,
        "year": year,
        "filing_type": filing_type,
        "quarter": quarter,
        "report_md": report_md_path,
        "raw_json": raw_json_path,
        "summary": summary,
    }
