"""
Newsletter AI 規劃模組
使用 Anthropic Claude 分析市場數據，規劃日報內容結構。

關鍵修正：使用 client.messages.create()（非 .parse()，.parse() 是 OpenAI 專屬方法）
"""

import json
import logging

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import Config
from app.ai.exceptions import AIGenerationError
from utils.ai_client import cached_system, get_ai_client

logger = logging.getLogger("newsletter")


PLANNER_SYSTEM = """你是一位資深美股市場策略分析師，負責規劃每日美股日報的內容結構。

根據提供的市場數據與新聞，你需要決定今日日報應涵蓋哪些主題。

請以 JSON 格式回傳規劃結果，結構如下：
{
  "market_overview": "一句話總結今日市場狀態",
  "key_themes": [
    {
      "title": "主題標題",
      "description": "為什麼這個主題重要（1-2句）",
      "related_tickers": ["相關股票代碼"],
      "sentiment": "bullish / bearish / neutral"
    }
  ],
  "top_movers": [
    {
      "ticker": "代碼",
      "reason": "漲跌原因（1句）"
    }
  ],
  "risk_alerts": ["需要關注的風險因素"],
  "recommended_focus": ["建議深度分析的 2-3 檔個股代碼"]
}

規則：
1. 只根據提供的數據規劃，不要發明數據
2. key_themes 最多 3 個，聚焦最重要的市場敘事
3. top_movers 最多 5 個
4. 回傳純 JSON，不要加任何其他文字或 Markdown 標記"""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(AIGenerationError),
    before_sleep=lambda retry_state: logger.warning(
        f"AI planning failed: {retry_state.outcome.exception()}"
    ),
)
async def plan_newsletter(market_data: dict) -> dict:
    """
    使用 AI 規劃日報內容結構。

    Args:
        market_data: 包含市場數據與新聞的 dict

    Returns:
        dict: 規劃結果（主題、重點個股、風險等）
    """
    try:
        client = get_ai_client()

        user_prompt = f"""請根據以下今日市場數據，規劃美股日報內容。

[Market Data]
{json.dumps(market_data, ensure_ascii=False, indent=2)}

請以 JSON 格式回傳規劃結果："""

        response = await client.messages.create(
            model=Config.ANTHROPIC_MODEL,
            max_tokens=2000,
            system=cached_system(PLANNER_SYSTEM),
            messages=[
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            timeout=60,
        )

        raw_text = response.content[0].text

        # 從回應中解析 JSON
        plan = _extract_json(raw_text)
        if plan is None:
            raise AIGenerationError(f"無法從 AI 回應中解析 JSON: {raw_text[:200]}")

        logger.info(f"AI 規劃完成：{len(plan.get('key_themes', []))} 個主題")
        return plan

    except AIGenerationError:
        raise
    except anthropic.APIError as e:
        raise AIGenerationError(f"Anthropic API error: {e}") from e
    except Exception as e:
        raise AIGenerationError(f"Planning error: {e}") from e


def _extract_json(text: str) -> dict | None:
    """從 AI 回應文字中提取 JSON。"""
    # 嘗試直接解析
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 嘗試從 ```json ... ``` 區塊提取
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        try:
            return json.loads(text[start:end].strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # 嘗試找到第一個 { 和最後一個 }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1:
        try:
            return json.loads(text[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            pass

    return None
