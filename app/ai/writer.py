"""
Newsletter AI 撰寫模組
根據規劃結果與市場數據，生成完整的美股日報。
"""

import json
import logging

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import Config
from app.ai.exceptions import AIGenerationError

logger = logging.getLogger("newsletter")

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=Config.ANTHROPIC_API_KEY)
    return _client


WRITER_SYSTEM = """你是一位專業的美股市場日報撰寫者，擁有華爾街 20 年經驗。
你的日報風格：精準、有洞察力、數據驅動、語言精練。

## 日報格式（使用純文字，不用 Markdown 標記符號）

📊 美股日報 — {日期}

🌐 市場總覽
[一段精練的市場總結，包含主要指數表現]

🔥 今日焦點
[2-3 個最重要的市場主題，每個主題 2-3 句話]

📈 重點個股
[漲跌幅最大或最受關注的個股，說明原因]

⚠️ 風險關注
[當前需要注意的風險因素]

🎯 明日展望
[基於今日數據對明日的簡短展望]

## 規則
1. 所有數字必須來自提供的數據
2. 語言使用繁體中文
3. 每個論點都要有具體數字佐證
4. 不要用 Markdown 標記符號（不要用 *、_、`、#、**）
5. 控制在 800 字以內
6. 像華爾街晨會簡報一樣精準"""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(AIGenerationError),
    before_sleep=lambda retry_state: logger.warning(
        f"AI writing failed: {retry_state.outcome.exception()}"
    ),
)
async def write_newsletter(plan: dict, market_data: dict) -> str:
    """
    根據規劃結果撰寫日報。

    Args:
        plan: AI 規劃結果
        market_data: 市場數據

    Returns:
        str: 完整日報文字
    """
    try:
        client = _get_client()

        user_prompt = f"""請根據以下規劃與市場數據，撰寫今日美股日報。

[Newsletter Plan]
{json.dumps(plan, ensure_ascii=False, indent=2)}

[Market Data]
{json.dumps(market_data, ensure_ascii=False, indent=2)}

請撰寫完整日報："""

        response = await client.messages.create(
            model=Config.ANTHROPIC_MODEL,
            max_tokens=3000,
            system=WRITER_SYSTEM,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            timeout=90,
        )

        newsletter = response.content[0].text
        logger.info(f"日報撰寫完成，共 {len(newsletter)} 字")
        return newsletter

    except anthropic.APIError as e:
        raise AIGenerationError(f"Anthropic API error: {e}") from e
    except Exception as e:
        raise AIGenerationError(f"Writing error: {e}") from e
