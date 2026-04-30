"""
tenk.orchestrator — async 版多 phase pipeline。

改寫重點：
- ThreadPoolExecutor → asyncio.gather（單一 event loop）
- 共用 utils.ai_client（含 prompt cache）
- 新增 progress_callback(stage: str, detail: str | None) 給 bot 推進度
- 保留所有 phase / eval loop / checkpoint 行為
"""

import asyncio
import json
import logging
from typing import Awaitable, Callable

from .agent_runner import run_agent
from .eval_runner import eval_all, get_failed_tasks
from .pipeline_state import PipelineState
from .report_writer import save_report

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, str | None], Awaitable[None]] | None

MAX_RETRIES = 2

PHASE1_TASKS = [
    {"task_id": "business",   "skill": "business_analysis",
     "input_keys": ["item1_current", "item1_prior"]},
    {"task_id": "competitor_mapping", "skill": "competitor_mapping",
     "input_keys": ["item1_current", "item1_prior"]},
    {"task_id": "risk",       "skill": "risk_analysis",
     "input_keys": ["item1a_current", "item1a_prior"]},
    {"task_id": "mdna",       "skill": "mdna_analysis",
     "input_keys": ["item7_current", "item7_prior"]},
    {"task_id": "governance", "skill": "governance_analysis",
     "input_keys": ["partiii_current"]},
    {"task_id": "fn_revenue",      "skill": "footnotes_revenue",
     "input_keys": ["fn_revenue"]},
    {"task_id": "fn_segment",      "skill": "footnotes_segment",
     "input_keys": ["fn_segment"]},
    {"task_id": "fn_receivables",  "skill": "footnotes_receivables",
     "input_keys": ["fn_receivables"]},
    {"task_id": "fn_assets",       "skill": "footnotes_assets",
     "input_keys": ["fn_assets"]},
    {"task_id": "fn_risk",         "skill": "footnotes_risk",
     "input_keys": ["fn_risk"]},
    {"task_id": "fn_pension",      "skill": "footnotes_pension",
     "input_keys": ["fn_pension"]},
    {"task_id": "fn_compensation", "skill": "footnotes_compensation",
     "input_keys": ["fn_compensation"]},
    {"task_id": "fn_tax",          "skill": "footnotes_tax",
     "input_keys": ["fn_tax"]},
    {"task_id": "terms_glossary",  "skill": "terms_glossary",
     "input_keys": ["all_sections_md"]},
]

FOOTNOTES_TASK_IDS = [
    "fn_revenue", "fn_segment", "fn_receivables", "fn_assets",
    "fn_risk", "fn_pension", "fn_compensation", "fn_tax",
]

KEY_MAP = {
    "item1_current":         "current_section",
    "item1_prior":           "prior_section",
    "item1_prior_as_current": "current_section",
    "item1a_current":   "current_section",
    "item1a_prior":     "prior_section",
    "item7_current":    "current_section",
    "item7_prior":      "prior_section",
    "partiii_current":  "current_section",
    "fn_revenue":       "current_section",
    "fn_segment":       "current_section",
    "fn_receivables":   "current_section",
    "fn_assets":        "current_section",
    "fn_risk":          "current_section",
    "fn_pension":       "current_section",
    "fn_compensation":  "current_section",
    "fn_tax":           "current_section",
    "fn_combined":      "current_section",
    "all_sections_md":  "all_sections_md",
}


async def _emit(progress: ProgressCallback, stage: str, detail: str | None = None) -> None:
    if progress is None:
        return
    try:
        await progress(stage, detail)
    except Exception as exc:
        logger.warning(f"[tenk] progress callback raised: {exc}")


async def _run_task(task, sections, state, step_prefix, hint=""):
    step_key = f"{step_prefix}.{task['task_id']}"
    cached = state.get_result(step_key)
    if cached is not None:
        logger.info(f"  ✓ {task['task_id']}（快取）")
        return task["task_id"], cached

    state.mark_running(step_key)
    inputs = {
        KEY_MAP[k]: sections.get(k)
        for k in task["input_keys"]
        if k in KEY_MAP
    }
    inputs.update(task.get("extra_inputs", {}))
    if hint:
        inputs["retry_hint"] = hint
    result = await run_agent(
        "analyst_agent", task["skill"], inputs, task_label=step_key
    )
    state.mark_done(step_key, result)
    logger.info(f"  ✓ {task['task_id']}")
    return task["task_id"], result


async def _run_parallel(tasks, sections, state, step_prefix, hints=None, *, concurrency=5):
    hints = hints or {}
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(task):
        async with sem:
            return await _run_task(task, sections, state, step_prefix, hints.get(task["task_id"], ""))

    coros = [_bounded(t) for t in tasks]
    pairs = await asyncio.gather(*coros)
    return dict(pairs)


def _collect_footnotes_summary(results) -> dict:
    if "fn_combined" in results:
        val = results["fn_combined"]
        if isinstance(val, dict) and "error" not in val:
            return {"fn_combined": val}
        logger.warning("fn_combined 結果無效，下游 footnotes summary 將為空")
        return {}
    summary = {}
    for tid in FOOTNOTES_TASK_IDS:
        if tid in results and isinstance(results[tid], dict):
            summary[tid] = results[tid]
    return summary


async def _run_financial(sections, footnotes_summary, state, step_key, hint=""):
    cached = state.get_result(step_key)
    if cached is not None:
        logger.info("  ✓ financial（快取）")
        return "financial", cached

    state.mark_running(step_key)
    inputs = {
        "xbrl_json":         sections.get("xbrl_data", ""),
        "fs_md":             sections.get("item8_fs", ""),
        "footnotes_summary": json.dumps(footnotes_summary, ensure_ascii=False),
    }
    if hint:
        inputs["retry_hint"] = hint
    result = await run_agent(
        "analyst_agent", "financial_analysis", inputs, task_label=step_key
    )
    state.mark_done(step_key, result)
    logger.info("  ✓ financial")
    return "financial", result


async def _run_supply_chain(sections, state, step_key, hint=""):
    cached = state.get_result(step_key)
    if cached is not None:
        logger.info("  ✓ supply_chain（快取）")
        return "supply_chain", cached

    state.mark_running(step_key)
    inputs = {
        "item1_current":           sections.get("item1_current", ""),
        "item1a_current":          sections.get("item1a_current", ""),
        "item7_current":           sections.get("item7_current", ""),
        "item8_footnotes_current": sections.get("item8_footnotes_current", ""),
        "item1_prior":             sections.get("item1_prior", ""),
        "item1a_prior":            sections.get("item1a_prior", ""),
    }
    if hint:
        inputs["retry_hint"] = hint
    result = await run_agent(
        "analyst_agent", "supply_chain_analysis", inputs, task_label=step_key
    )
    state.mark_done(step_key, result)
    logger.info("  ✓ supply_chain")
    return "supply_chain", result


async def run_pipeline(
    ticker, sections, prior_sections=None, state=None,
    filing_type="10-K", quarter=None,
    progress: ProgressCallback = None,
):
    if state is None:
        state = PipelineState(
            ticker, sections.get("_year", 0),
            prior_sections.get("_year") if prior_sections else None,
            filing_type=filing_type, quarter=quarter,
        )

    if prior_sections:
        sections["item1_prior"] = prior_sections.get("item1_current", "")
        sections["item1a_prior"] = prior_sections.get("item1a_current", "")
        sections["item7_prior"] = prior_sections.get("item7_current", "")
        sections["item1_prior_as_current"] = prior_sections.get("item1_current", "")

    phase1_tasks = PHASE1_TASKS
    if filing_type == "10-Q":
        skip_tasks = {"governance", "business", "risk"}
        if quarter in ("Q2", "Q3"):
            skip_tasks |= {"terms_glossary", "competitor_mapping"}
            logger.info("[Phase 1] terms_glossary / competitor_mapping skipped (Q2/Q3 10-Q)")
        phase1_tasks = [t for t in PHASE1_TASKS if t["task_id"] not in skip_tasks]
        phase1_tasks = [t for t in phase1_tasks if not t["task_id"].startswith("fn_")]
        phase1_tasks.append({
            "task_id": "fn_combined",
            "skill": "footnotes_combined",
            "input_keys": ["fn_combined"],
        })
        phase1_tasks = [
            {**t, "extra_inputs": {"filing_type": filing_type, "quarter": quarter or ""}}
            if t["task_id"] == "mdna" else t
            for t in phase1_tasks
        ]
        if quarter == "Q1":
            q1_prior_year = prior_sections.get("_year") if prior_sections else None
            if q1_prior_year is None:
                phase1_tasks = [t for t in phase1_tasks if t["task_id"] != "competitor_mapping"]
                logger.info("[Phase 1] competitor_mapping skipped (Q1 missing prior year)")
            else:
                phase1_tasks = [
                    {**t, "input_keys": ["item1_prior_as_current"],
                           "extra_inputs": {"mode": "q1_vs_10k", "prior_year": str(q1_prior_year)}}
                    if t["task_id"] == "competitor_mapping" else t
                    for t in phase1_tasks
                ]

    if len(sections.get("fn_pension", "")) < 500:
        phase1_tasks = [t for t in phase1_tasks if t["task_id"] != "fn_pension"]

    fn_count = sum(1 for t in phase1_tasks if t["task_id"].startswith("fn_"))
    run_supply = (filing_type == "10-K") or (filing_type == "10-Q" and quarter == "Q1")
    await _emit(progress, "phase1", f"並行分析 {len(phase1_tasks)} 維度（含 footnotes x{fn_count}）")
    logger.info(f"[Phase 1] {len(phase1_tasks)} 個 task，supply_chain={run_supply}")

    sem = asyncio.Semaphore(6)

    async def _phase1_one(task):
        async with sem:
            return await _run_task(task, sections, state, "phase1", "")

    coros = [_phase1_one(t) for t in phase1_tasks]
    if run_supply:
        async def _sc():
            async with sem:
                return await _run_supply_chain(sections, state, "phase1.supply_chain")
        coros.append(_sc())

    pairs = await asyncio.gather(*coros)
    results = dict(pairs)

    # Phase 2a: financial
    await _emit(progress, "phase2a", "三表交叉驗證")
    fn_summary = _collect_footnotes_summary(results)
    _, fin = await _run_financial(sections, fn_summary, state, "phase2.financial")
    results["financial"] = fin

    # Phase 2b: segment_trend
    if filing_type == "10-Q":
        results["segment_trend"] = {
            "insufficient_data": True,
            "_skipped_reason": "10-Q lacks business_summary",
        }
    else:
        await _emit(progress, "phase2b", "部門營收結構")
        st_key = "phase2b.segment_trend"
        cached_st = state.get_result(st_key)
        if cached_st is not None:
            results["segment_trend"] = cached_st
        else:
            state.mark_running(st_key)
            results["segment_trend"] = await run_agent(
                "analyst_agent", "segment_trend", {
                    "xbrl_json":        sections.get("xbrl_data", ""),
                    "business_summary": json.dumps(results.get("business", {}), ensure_ascii=False),
                    "mdna_summary":     json.dumps(results.get("mdna", {}), ensure_ascii=False),
                },
                task_label=st_key,
            )
            state.mark_done(st_key, results["segment_trend"])

    # Phase 2c: three_statement_cross
    await _emit(progress, "phase2c", "三表訊號")
    tsc_key = "phase2c.three_statement_cross"
    cached_tsc = state.get_result(tsc_key)
    if cached_tsc is not None:
        results["three_statement_cross"] = cached_tsc
    else:
        state.mark_running(tsc_key)
        results["three_statement_cross"] = await run_agent(
            "analyst_agent", "three_statement_cross", {
                "financial_summary": json.dumps(results.get("financial", {}), ensure_ascii=False),
                "xbrl_json":         sections.get("xbrl_data", ""),
            },
            task_label=tsc_key,
        )
        state.mark_done(tsc_key, results["three_statement_cross"])

    # Phase 3: unusual_operations
    await _emit(progress, "phase3", "不尋常操作識別")
    uo_key = "phase3.unusual_operations"
    cached_uo = state.get_result(uo_key)
    if cached_uo is not None:
        results["unusual_operations"] = cached_uo
    else:
        state.mark_running(uo_key)
        fn_summary = _collect_footnotes_summary(results)
        results["unusual_operations"] = await run_agent(
            "analyst_agent", "unusual_operations", {
                "footnotes_summary": json.dumps(fn_summary, ensure_ascii=False),
                "financial_summary": json.dumps(results.get("financial", {}), ensure_ascii=False),
                "item8_footnotes_md": sections.get("item8_footnotes_md", ""),
                "business_summary": json.dumps(results.get("business", {}), ensure_ascii=False),
            },
            task_label=uo_key,
        )
        state.mark_done(uo_key, results["unusual_operations"])

    # Phase 3b: rerate (parallel)
    await _emit(progress, "phase3b", "評價趨勢三條件判斷")
    rerate_tasks = [
        ("quality", "rerate_quality", "phase3b.rerate_quality",
         {"three_statement_summary": json.dumps(
             results.get("three_statement_cross", {}), ensure_ascii=False)}),
        ("narrative", "rerate_narrative", "phase3b.rerate_narrative",
         {"mdna_summary": json.dumps(results.get("mdna", {}), ensure_ascii=False)}),
    ]
    if filing_type != "10-Q":
        rerate_tasks.insert(0, (
            "structure", "rerate_structure", "phase3b.rerate_structure",
            {"segment_summary": json.dumps(
                results.get("segment_trend", {}), ensure_ascii=False)},
        ))

    async def _rerate_one(label, skill, step_key, inputs):
        cached = state.get_result(step_key)
        if cached is not None:
            return label, cached
        state.mark_running(step_key)
        res = await run_agent(
            "analyst_agent", skill, inputs, task_label=step_key
        )
        state.mark_done(step_key, res)
        return label, res

    rerate_pairs = await asyncio.gather(
        *[_rerate_one(*t) for t in rerate_tasks]
    )
    rerate_results = dict(rerate_pairs)

    results["rerate_signal"] = {
        "structure_changing": rerate_results.get("structure", {}),
        "quality_changing": rerate_results.get("quality", {}),
        "narrative_changing": rerate_results.get("narrative", {}),
        "insufficient_data": any(
            r.get("insufficient_data", False)
            for r in rerate_results.values()
            if isinstance(r, dict)
        ),
    }

    # Eval loop
    eval_results = {}
    for attempt in range(1, MAX_RETRIES + 1):
        eval_step = f"eval_round{attempt}"
        all_cached = all(
            state.is_done(f"{eval_step}.{t['task_id']}")
            for t in phase1_tasks
        ) and state.is_done(f"{eval_step}.financial") and (
            not run_supply or state.is_done(f"{eval_step}.supply_chain")
        )

        if all_cached:
            eval_results = {}
            for t in phase1_tasks:
                ev = state.get_result(f"{eval_step}.{t['task_id']}")
                if ev:
                    eval_results[t["task_id"]] = ev
            fin_ev = state.get_result(f"{eval_step}.financial")
            if fin_ev:
                eval_results["financial"] = fin_ev
            if run_supply:
                sc_ev = state.get_result(f"{eval_step}.supply_chain")
                if sc_ev:
                    eval_results["supply_chain"] = sc_ev
        else:
            await _emit(progress, "eval", f"品質評估 round {attempt}/{MAX_RETRIES}")
            eval_results = await eval_all(
                results, sections,
                filing_type=filing_type, quarter=quarter,
            )
            for tid, ev in eval_results.items():
                state.mark_eval(f"{eval_step}.{tid}", ev)

        failed = get_failed_tasks(eval_results)
        if not failed:
            logger.info("    eval 全部通過")
            break
        if attempt == MAX_RETRIES:
            for f in failed:
                results[f["task_id"]]["low_confidence"] = True
            logger.info(f"    {len(failed)} 個任務標記為 low_confidence")
            break

        retry_prefix = f"retry{attempt}"
        p1_failed = [f for f in failed if f["task_id"] not in ("financial", "supply_chain")]
        fin_failed = next((f for f in failed if f["task_id"] == "financial"), None)
        sc_failed = next((f for f in failed if f["task_id"] == "supply_chain"), None)

        if p1_failed:
            retry_tasks = [
                t for t in phase1_tasks
                if t["task_id"] in {f["task_id"] for f in p1_failed}
            ]
            hints = {f["task_id"]: f["retry_hint"] for f in p1_failed}
            retried = await _run_parallel(retry_tasks, sections, state, retry_prefix, hints)
            results.update(retried)
        if fin_failed:
            fn_summary = _collect_footnotes_summary(results)
            _, fin = await _run_financial(
                sections, fn_summary,
                state, f"{retry_prefix}.financial",
                hint=fin_failed["retry_hint"],
            )
            results["financial"] = fin
        if sc_failed:
            _, sc = await _run_supply_chain(
                sections, state, f"{retry_prefix}.supply_chain",
                hint=sc_failed["retry_hint"],
            )
            results["supply_chain"] = sc

    # Prior year analysis
    prior_results = None
    if prior_sections:
        await _emit(progress, "prior", "前期同維度分析")
        prior_results = await _run_parallel(phase1_tasks, prior_sections, state, "prior.phase1")
        prior_fn_summary = _collect_footnotes_summary(prior_results)
        _, prior_fin = await _run_financial(
            prior_sections, prior_fn_summary,
            state, "prior.phase2.financial",
        )
        prior_results["financial"] = prior_fin

    # Synthesis
    await _emit(progress, "synthesis", "跨年度比對 + 綜合判斷")
    synth_cross_key = "synthesis.cross_year"
    cached_cross = state.get_result(synth_cross_key)
    if cached_cross is not None:
        comparator = cached_cross
    else:
        state.mark_running(synth_cross_key)
        comparator = await run_agent(
            "analyst_agent", "cross_year_compare",
            {
                "current_analysis": json.dumps(results, ensure_ascii=False),
                "prior_analysis": json.dumps(prior_results, ensure_ascii=False)
                if prior_results else None,
            },
            task_label=synth_cross_key,
        )
        state.mark_done(synth_cross_key, comparator)

    synth_insight_key = "synthesis.insight"
    cached_insight = state.get_result(synth_insight_key)
    if cached_insight is not None:
        insight = cached_insight
    else:
        state.mark_running(synth_insight_key)
        slim_results = dict(results)
        for trim_key in ("segment_trend", "three_statement_cross", "rerate_signal"):
            val = slim_results.get(trim_key)
            if not isinstance(val, dict):
                continue
            if trim_key == "rerate_signal":
                slim_results[trim_key] = {
                    "structure_changing": val.get("structure_changing"),
                    "quality_changing": val.get("quality_changing"),
                    "narrative_changing": val.get("narrative_changing"),
                }
            elif trim_key == "segment_trend":
                slim_results[trim_key] = {
                    "structural_shift": val.get("structural_shift"),
                    "shift_description": val.get("shift_description"),
                    "rerating_candidate_structure": val.get("rerating_candidate_structure"),
                }
            elif trim_key == "three_statement_cross":
                slim_results[trim_key] = {
                    "dominant_signal": val.get("dominant_signal"),
                    "rerating_candidate_quality": val.get("rerating_candidate_quality"),
                    "overall_signals": val.get("overall_signals"),
                }
        insight = await run_agent(
            "analyst_agent", "insight_synthesis",
            {
                "analysis_results": json.dumps(slim_results, ensure_ascii=False),
                "comparator_result": json.dumps(comparator, ensure_ascii=False),
            },
            task_label=synth_insight_key,
        )
        state.mark_done(synth_insight_key, insight)

    # Phase 5: completeness_check
    await _emit(progress, "phase5", "品質評分")
    cc_key = "phase5.completeness"
    cached_cc = state.get_result(cc_key)
    if cached_cc is not None:
        completeness = cached_cc
    else:
        state.mark_running(cc_key)
        eval_summary = {
            tid: {"total": r.get("total", 0), "pass": r.get("pass", False)}
            for tid, r in eval_results.items()
        }
        completeness = await run_agent(
            "analyst_agent", "completeness_check", {
                "all_results": json.dumps(
                    {**results, "comparator": comparator, "insight": insight},
                    ensure_ascii=False,
                ),
                "eval_summary": json.dumps(eval_summary, ensure_ascii=False),
            },
            task_label=cc_key,
        )
        state.mark_done(cc_key, completeness)

    synthesis = {
        "comparator": comparator,
        "insight": insight,
        "completeness": completeness,
    }

    xbrl_metrics_raw = (
        json.loads(sections["xbrl_data"]) if sections.get("xbrl_data") else {}
    )
    await _emit(progress, "report", "產出報告")
    report_path = save_report(
        ticker, results, eval_results, synthesis,
        quarterly=sections.get("_quarterly", []),
        filing_type=filing_type, quarter=quarter,
        xbrl_metrics=xbrl_metrics_raw,
        prior_year=prior_sections.get("_year") if prior_sections else None,
    )

    return {
        "report_path": report_path,
        "results": results,
        "synthesis": synthesis,
        "eval": eval_results,
    }
