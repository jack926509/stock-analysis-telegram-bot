"""
AI 市場分析師
使用 Claude 生成每日黃金市場分析摘要。
"""

import json
import logging
import re

import anthropic

from forex_trading.config import ForexConfig

logger = logging.getLogger(__name__)

_anthropic_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=ForexConfig.ANTHROPIC_API_KEY)
    return _anthropic_client


SYSTEM_PROMPT = """你是一位資深黃金市場分析師，專精 XAUUSD 外匯交易。
根據提供的數據生成簡潔、可操作的每日市場分析。

規則：
- 只引用提供的數據，不發明任何數字或事件
- 分析控制在 500 字以內
- 使用純文字，不用 Markdown 標記符號（不用 * _ ` #）
- 包含具體價格位作為支撐/壓力
- 評論 DXY 相關性及其影響
- 提及重要新聞催化劑
- 使用繁體中文

格式：
黃金(XAUUSD) 每日市場分析

當前概況
[價格、趨勢方向、主要技術指標狀態]

關鍵技術位
[支撐位、壓力位、均線位置]

DXY 相關性
[美元指數狀態、與黃金的相關性分析]

市場情緒與催化劑
[新聞要點、影響評估]

今日展望
[多空偏向、關鍵觀察點、建議操作方向]

風險提示
此為基於有限數據的分析觀點，不構成投資建議。"""


async def generate_daily_analysis(
    quote: dict | None,
    tv_data: dict | None,
    gold_indicators: dict | None,
    dxy_data: dict | None,
    news_data: dict | None,
    portfolio_status: dict | None = None,
) -> str:
    """
    生成每日市場分析。

    Args:
        quote: 即時報價
        tv_data: TradingView 多時間框架分析
        gold_indicators: 自行計算的指標數據
        dxy_data: DXY 相關數據
        news_data: 新聞情緒數據
        portfolio_status: 當前持倉狀態（可選）

    Returns:
        str: 市場分析文字
    """
    try:
        client = _get_client()

        context_parts = []

        if quote:
            context_parts.append(f"=== 即時報價 ===\n{json.dumps(quote, ensure_ascii=False, indent=2)}")

        if tv_data:
            context_parts.append(f"=== TradingView 技術分析 ===\n{json.dumps(tv_data, ensure_ascii=False, indent=2)}")

        if gold_indicators:
            context_parts.append(f"=== 技術指標 ===\n{json.dumps(gold_indicators, ensure_ascii=False, indent=2)}")

        if dxy_data:
            context_parts.append(f"=== DXY 數據 ===\n{json.dumps(dxy_data, ensure_ascii=False, indent=2)}")

        if news_data:
            context_parts.append(f"=== 市場新聞 ===\n{json.dumps(news_data, ensure_ascii=False, indent=2)}")

        if portfolio_status:
            context_parts.append(f"=== 持倉狀態 ===\n{json.dumps(portfolio_status, ensure_ascii=False, indent=2)}")

        context = "\n\n".join(context_parts)

        user_prompt = f"""請根據以下數據生成黃金(XAUUSD)每日市場分析。
嚴格遵守規則：只使用提供的真實數據，不發明任何數字或事件。

[Context Data]
{context}

請開始分析："""

        response = await client.messages.create(
            model=ForexConfig.ANTHROPIC_MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.3,
            timeout=60,
        )

        analysis = response.content[0].text

        # 清理可能的 Markdown 標記
        analysis = _clean_markdown(analysis)

        return analysis

    except Exception as e:
        logger.error(f"AI 市場分析生成失敗: {e}")
        return f"市場分析生成失敗: {str(e)}"


def _clean_markdown(text: str) -> str:
    """清理 AI 回傳中的 Markdown 標記。"""
    if not text:
        return text
    text = text.replace("**", "")
    text = text.replace("###", "")
    text = text.replace("##", "")
    text = text.replace("# ", "")
    text = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'\1', text)
    text = text.replace("`", "")
    return text
