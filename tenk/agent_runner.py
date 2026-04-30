"""
tenk.agent_runner
單一 agent 呼叫的 async 包裝。

差異於上游版本：
- 改用 utils.ai_client.get_ai_client() 共用 AsyncAnthropic 實例
- system prompt 走 prompt caching（cached_system）
- 移除自家 config.json，模型與 max_tokens 來自 bot Config 與 _SKILL_MAX_TOKENS
- usage / context log 寫到 Config.TENK_OUTPUT_DIR
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from config import Config
from utils.ai_client import cached_system, get_ai_client

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

    model = model or Config.ANTHROPIC_MODEL
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
    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=cached_system(system_prompt),
        messages=[{"role": "user", "content": user_content}],
        timeout=120,
    )
    raw = response.content[0].text.strip()
    usage = response.usage

    skill_ver = _get_skill_version(skill_name)
    _save_context(label, system_prompt, user_content, raw, usage, skill_ver)

    return _parse_json_loose(raw, skill_name)


def _save_context(label, system, user_content, response_text, usage, skill_version="unknown") -> None:
    """Persist request/response + append usage line. Failures are non-fatal."""
    try:
        log_dir = Path(Config.TENK_OUTPUT_DIR) / "contexts"
        log_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_label = re.sub(r"[^\w\-.]", "_", label)

        in_tokens = getattr(usage, "input_tokens", 0) or 0
        out_tokens = getattr(usage, "output_tokens", 0) or 0

        req_path = log_dir / f"{ts}_{safe_label}_request.md"
        req_path.write_text(
            f"# Request: {label}\n"
            f"Time: {datetime.now().isoformat()}\n"
            f"Tokens: in={in_tokens}, out={out_tokens}\n\n"
            f"## System\n\n{system}\n\n"
            f"## User\n\n{user_content}\n",
            encoding="utf-8",
        )

        resp_path = log_dir / f"{ts}_{safe_label}_response.md"
        resp_path.write_text(
            f"# Response: {label}\n"
            f"Time: {datetime.now().isoformat()}\n"
            f"Tokens: in={in_tokens}, out={out_tokens}\n\n"
            f"## Raw Output\n\n{response_text}\n",
            encoding="utf-8",
        )

        cost = in_tokens * 3e-6 + out_tokens * 15e-6
        entry = {
            "ts": datetime.now().isoformat(),
            "label": label,
            "version": skill_version,
            "in": in_tokens,
            "out": out_tokens,
            "cost": cost,
            "request_file": req_path.name,
            "response_file": resp_path.name,
        }
        with open(Path(Config.TENK_OUTPUT_DIR) / "usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning(f"[tenk] context log 寫入失敗（不中斷流程）: {exc}")
