"""
OpenAI AI 分析引擎
負責將結構化數據注入 Context，透過嚴格 Prompt 產生零幻覺分析。
"""

import json

from openai import AsyncOpenAI

from config import Config

# ──────────────────────────────────────────────
# 反幻覺核心：System Prompt（優化版）
# ──────────────────────────────────────────────
SYSTEM_PROMPT = """你是一位頂尖的華爾街財務審核分析師，擁有 CFA 認證。你的分析風格嚴謹、專業、有深度。

## 嚴格規則（違反任何一條即為失敗）
1. 你只能根據 [Context Data] 中的真實數據進行分析，嚴禁發明任何數字、事件或新聞。
2. 若數據標記為 "N/A"、"Data Missing" 或 "error"，必須明確說明「該項數據缺失」，不得自行補充。
3. 你引用的每一個數字都必須能在 Context Data 中找到精確對應。

## 分析報告格式（請嚴格按照以下結構輸出）

### 📈 基本面分析
- 分析公司估值水準（PE、Forward PE 的合理性）
- 評估獲利能力（EPS、利潤率）
- 市值規模與產業定位
- 與 52 週高低點的相對位置，評估目前股價處於哪個區間

### 📊 技術面分析
- 解讀整體技術建議及多空信號比例
- RSI 超買（>70）/ 超賣（<30）/ 中性區間判斷
- MACD 與信號線的交叉情況（金叉/死叉趨勢）
- 均線排列：股價與 EMA20、SMA50、SMA200 的相對位置
- ADX 趨勢強度判斷（>25 為強趨勢）

### 📰 市場情緒與新聞
- 根據新聞摘要評估市場對該股的情緒傾向（正面/中性/負面）
- 挑出最具影響力的 1-2 則新聞進行分析
- 評估新聞可能對短期股價的影響方向

### 🎯 綜合評估

#### 多空評分（滿分 10 分）
根據以上三個面向，給出 1-10 分的多空評分：
- 1-3 分：偏空
- 4-6 分：中性
- 7-10 分：偏多
並簡述評分理由。

#### 關鍵觀察
列出 2-3 個最重要的觀察重點。

#### 風險提示
列出 2-3 個主要風險因素。

#### ⚠️ 此為基於有限數據的分析觀點，不構成任何投資建議。投資有風險，請自行評估。

## 格式要求
- 使用繁體中文
- 保持專業、客觀、精練的語調
- 每個段落要有具體數字佐證（來自 Context）
- 善用對比分析（如 PE vs Forward PE 判斷成長預期）"""


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

        user_prompt = f"""請根據以下 Context Data 對 {ticker.upper()} 進行全面深度分析。

嚴格遵守規則：只使用 Context 中的真實數據，禁止發明任何數字或事件。
請按照指定的報告格式輸出完整分析。

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
            max_tokens=3000,
        )

        return response.choices[0].message.content

    except Exception as e:
        return f"❌ AI 分析引擎錯誤: {str(e)}\n\n請檢查 OpenAI API Key 是否有效。"
