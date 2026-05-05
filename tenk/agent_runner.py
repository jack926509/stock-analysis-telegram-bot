"""
tenk.agent_runner
單一 agent 呼叫的 async 包裝。

設計：
- 透過 utils.ai_client.get_ai_client() 共用 AsyncOpenAI 實例（OpenAI 官方 API）
- 長 system prompt（agent + skill markdown 通常 ≥1024 tokens）由 OpenAI 自動
  prompt caching；命中時 cached input tokens 享 50% 折扣
- 模型與 max_tokens 來自 bot Config 與 _SKILL_MAX_TOKENS
- usage / context log 寫到 Config.TENK_OUTPUT_DIR
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from config import Config
from utils.ai_client import extract_text, extract_usage, get_ai_client, system_message

from . import PACKAGE_DIR

logger = logging.getLogger(__name__)

# 各 skill 的 max_tokens 上限（與上游 config.example.json 對齊）
_SKILL_MAX_TOKENS = {
    "risk_analysis": 8192,
    "mdna_analysis": 8192,
    "cross_year_compare": 8192,
    "insight_synthesis": 8192,
}
_DEFAULT_MAX_TOKENS = 4096

# 純結構化萃取或分類，不需深度推理 → 用 mini 系列省錢（成本約 1/15）
_MINI_SKILLS = frozenset({
    "footnotes_assets",
    "footnotes_compensation",
    "footnotes_pension",
    "footnotes_receivables",
    "footnotes_revenue",
    "footnotes_risk",
    "footnotes_segment",
    "footnotes_tax",
    "competitor_mapping",
    "governance_analysis",
    "terms_glossary",
    "section_splitter",
    "completeness_check",
    "rerate_quality",
    "rerate_narrative",
    "rerate_structure",
})

_dry_run = False
_MOCK_OUTPUTS: dict | None = None


def set_dry_run(enabled: bool) -> None:
    global _dry_run
    _dry_run = enabled


def _load_mock() -> dict:
    global _MOCK_OUTPUTS
    if _MOCK_OUTPUTS is None:
        _MOCK_OUTPUTS = json.loads(
            (PACKAGE_DIR / "mock_outputs.json").read_text(encoding="utf-8")
        )
    return _MOCK_OUTPUTS


def truncate_with_notice(text: str, max_chars: int) -> str:
    """Truncate long text with explicit notice so the agent knows it's incomplete."""
    if not text or len(text) <= max_chars:
        return text or ""
    return (
        text[:max_chars]
        + f"\n\n[截斷提示：以上為前 {max_chars} 字，"
        + f"原始文件共 {len(text)} 字，仍有後續內容未包含。"
        + "請勿將此視為文件結尾，結論應反映資料可能不完整。]"
    )


def _get_skill_version(skill_name: str) -> str:
    skill_path = PACKAGE_DIR / "skills" / f"{skill_name}.md"
    if not skill_path.exists():
        return "unknown"
    header = skill_path.read_text(encoding="utf-8")[:200]
    m = re.search(r"skill_version:\s*([\d.]+)", header)
    return m.group(1) if m else "unknown"


def _parse_json_loose(raw: str, skill_name: str) -> dict:
    """Strip ```json fences, then try to extract first complete JSON object."""
    raw_clean = re.sub(r"^```(?:json)?\s*", "", raw)
    raw_clean = re.sub(r"\s*```$", "", raw_clean)
    try:
        return json.loads(raw_clean)
    except json.JSONDecodeError:
        m = re.search(r"\{", raw_clean)
        if m:
            depth = 0
            for i, ch in enumerate(raw_clean[m.start():], start=m.start()):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(raw_clean[m.start():i + 1])
                        except json.JSONDecodeError:
                            break
        return {
            "error": "JSON parse failed",
            "raw": raw_clean[:500],
            "skill": skill_name,
        }


async def run_agent(
    agent_name: str,
    skill_name: str,
    inputs: dict,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    task_label: str | None = None,
) -> dict:
    """
    Async 版本：單一 skill 呼叫。回傳已解析的 JSON dict。
    解析失敗時回傳 {"error": ..., "raw": ...}，由上層 eval / synthesis 決定如何處理。
    """
    label = task_label or skill_name

    if _dry_run:
        mock = _load_mock().get(skill_name, {"insufficient_data": False})
        logger.info(f"[tenk dry-run] {label}")
        return json.loads(json.dumps(mock))  # deep copy

    if model is None:
        model = (
            Config.OPENAI_PLANNER_MODEL
            if skill_name in _MINI_SKILLS
            else Config.OPENAI_MODEL
        )
    max_tokens = max_tokens or _SKILL_MAX_TOKENS.get(skill_name, _DEFAULT_MAX_TOKENS)

    agent_md = (PACKAGE_DIR / "agents" / f"{agent_name}.md").read_text(encoding="utf-8")
    skill_md = (PACKAGE_DIR / "skills" / f"{skill_name}.md").read_text(encoding="utf-8")
    system_prompt = f"{agent_md}\n\n---\n\n[SKILL]\n{skill_md}"

    parts = ["[INPUT]"]
    for k, v in inputs.items():
        if v is not None:
            parts.append(f"\n## {k}\n{v}")
    user_content = "\n".join(parts)

    client = get_ai_client()
    response = await client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            system_message(system_prompt),
            {"role": "user", "content": user_content},
        ],
        timeout=120,
    )
    raw = extract_text(response)
    in_tokens, out_tokens, cached_tokens = extract_usage(response)

    skill_ver = _get_skill_version(skill_name)
    _save_context(
        label, system_prompt, user_content, raw,
        in_tokens, out_tokens, cached_tokens, skill_ver, model,
    )

    return _parse_json_loose(raw, skill_name)


# OpenAI 公開定價（$/token）— 命中 prompt cache 的 input tokens 享 50% 折扣。
# 來源：https://openai.com/api/pricing/
# 若使用其他模型（o1, gpt-4-turbo…），caller 可改 _OPENAI_PRICING 或交給預設值。
_OPENAI_PRICING: dict[str, tuple[float, float]] = {
    # model_substring : (input $/token, output $/token)
    "gpt-4o-mini":  (0.15e-6,  0.60e-6),
    "gpt-4o":       (2.50e-6, 10.00e-6),
    "o1-mini":      (1.10e-6,  4.40e-6),
    "o1":          (15.00e-6, 60.00e-6),
    "gpt-4-turbo": (10.00e-6, 30.00e-6),
}
_DEFAULT_RATES = (2.50e-6, 10.00e-6)  # 未匹配時保守估（gpt-4o 價）


def _resolve_rates(model: str) -> tuple[float, float]:
    """以子字串包含關係匹配最具體的模型費率（mini 優先於 4o）。"""
    name = (model or "").lower()
    # 排序：較長的 key 先匹配（避免 gpt-4o 搶在 gpt-4o-mini 之前）
    for key in sorted(_OPENAI_PRICING.keys(), key=len, reverse=True):
        if key in name:
            return _OPENAI_PRICING[key]
    return _DEFAULT_RATES


def _save_context(
    label, system, user_content, response_text,
    in_tokens, out_tokens, cached_tokens=0,
    skill_version="unknown", model="",
) -> None:
    """Persist request/response + append usage line. Failures are non-fatal."""
    try:
        log_dir = Path(Config.TENK_OUTPUT_DIR) / "contexts"
        log_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_label = re.sub(r"[^\w\-.]", "_", label)

        token_line = f"Tokens: in={in_tokens}, out={out_tokens}"
        if cached_tokens:
            token_line += f", cached={cached_tokens}"

        req_path = log_dir / f"{ts}_{safe_label}_request.md"
        req_path.write_text(
            f"# Request: {label}\n"
            f"Time: {datetime.now().isoformat()}\n"
            f"Model: {model}\n"
            f"{token_line}\n\n"
            f"## System\n\n{system}\n\n"
            f"## User\n\n{user_content}\n",
            encoding="utf-8",
        )

        resp_path = log_dir / f"{ts}_{safe_label}_response.md"
        resp_path.write_text(
            f"# Response: {label}\n"
            f"Time: {datetime.now().isoformat()}\n"
            f"Model: {model}\n"
            f"{token_line}\n\n"
            f"## Raw Output\n\n{response_text}\n",
            encoding="utf-8",
        )

        in_rate, out_rate = _resolve_rates(model)
        # cached input tokens 享 50% 折扣
        billable_in = max(in_tokens - cached_tokens, 0)
        cost = billable_in * in_rate + cached_tokens * (in_rate * 0.5) + out_tokens * out_rate
        entry = {
            "ts": datetime.now().isoformat(),
            "label": label,
            "version": skill_version,
            "model": model,
            "in": in_tokens,
            "out": out_tokens,
            "cached_in": cached_tokens,
            "cost": cost,
            "request_file": req_path.name,
            "response_file": resp_path.name,
        }
        with open(Path(Config.TENK_OUTPUT_DIR) / "usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning(f"[tenk] context log 寫入失敗（不中斷流程）: {exc}")
