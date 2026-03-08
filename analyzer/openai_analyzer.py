"""
OpenAI AI 分析引擎
負責將結構化數據注入 Context，透過嚴格 Prompt 產生零幻覺分析。
"""

import json

from openai import AsyncOpenAI

from config import Config

# ──────────────────────────────────────────────
# 反幻覺核心：System Prompt
# ──────────────────────────────────────────────
SYSTEM_PROMPT = """你是一個嚴格的財務審核分析師。你的工作規則如下：

1. 你只能根據我提供的 [Context Data] 進行分析與推論。
2. 嚴禁發明、猜測、或引用任何不在 Context 中的數字、事件或新聞。
3. 若某項數據標記為 "N/A"、"Data Missing" 或 "error"，你必須明確在報告中說明「該項數據缺失，無法分析」。不得自行填補任何缺失數據。
4. 你的分析必須包含以下四個段落：
   a) 📈 基本面摘要：根據 yfinance 數據分析公司財務狀況
   b) 📊 技術面摘要：根據 TradingView 指標分析市場動能
   c) 📰 近期新聞影響：根據 Tavily 新聞評估市場情緒
   d) 🎯 綜合評估：給出整體觀點與風險提示
5. 在綜合評估中，必須標註「⚠️ 此為基於有限數據的觀點，不構成投資建議」。
6. 分析中引用的每一個數字都必須能在 Context Data 中找到對應來源。
7. 使用繁體中文回答。
8. 保持專業、客觀、簡潔的語調。"""


def _build_context(
    finnhub_data: dict,
    yfinance_data: dict,
    tavily_data: dict,
    tradingview_data: dict,
) -> str:
    """
    將四個數據源組裝為結構化 Context 字串。
    使用 JSON 格式確保數據完整性，避免格式化過程中遺失資訊。
    """
    context_parts = [
        "=== 即時股價數據 (來源: Finnhub) ===",
        json.dumps(finnhub_data, ensure_ascii=False, indent=2),
        "",
        "=== 基本面數據 (來源: yfinance) ===",
        json.dumps(yfinance_data, ensure_ascii=False, indent=2),
        "",
        "=== 最新新聞 (來源: Tavily) ===",
        json.dumps(tavily_data, ensure_ascii=False, indent=2),
        "",
        "=== 技術指標 (來源: TradingView) ===",
        json.dumps(tradingview_data, ensure_ascii=False, indent=2),
    ]
    return "\n".join(context_parts)


async def analyze_stock(
    ticker: str,
    finnhub_data: dict,
    yfinance_data: dict,
    tavily_data: dict,
    tradingview_data: dict,
) -> str:
    """
    使用 OpenAI GPT 分析股票數據。

    Args:
        ticker: 股票代碼
        finnhub_data: Finnhub 即時報價
        yfinance_data: yfinance 基本面
        tavily_data: Tavily 新聞
        tradingview_data: TradingView 技術指標

    Returns:
        str: AI 生成的分析文本
    """
    try:
        client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)

        context = _build_context(
            finnhub_data, yfinance_data, tavily_data, tradingview_data
        )

        user_prompt = f"""請根據以下 Context Data 對 {ticker.upper()} 進行全面分析。

嚴格遵守規則：只使用 Context 中的真實數據，禁止發明任何數字或事件。

[Context Data]
{context}

請開始你的分析報告："""

        response = await client.chat.completions.create(
            model=Config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=2000,
        )

        return response.choices[0].message.content

    except Exception as e:
        return f"❌ AI 分析引擎錯誤: {str(e)}\n\n請檢查 OpenAI API Key 是否有效。"
