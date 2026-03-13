"""
OpenAI AI 分析引擎（三角色優化版）
- 分析師：加入成交量分析、成長性評估、量化評分框架
- 前端：控制輸出格式避免破壞 Telegram Markdown
- 後端：共用 client 實例、超時控制
"""

import json
import re

from openai import AsyncOpenAI

from config import Config

# 共用 client 實例（後端優化：避免每次重建）
_openai_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """取得共用的 AsyncOpenAI client。"""
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)
    return _openai_client


# ──────────────────────────────────────────────
# 反幻覺核心：System Prompt（三角色優化版）
# ──────────────────────────────────────────────
SYSTEM_PROMPT = """你是一位縱橫華爾街 20 年的頂尖美股分析師，擁有 CFA 認證與豐富的實戰經驗。
你擅長從技術指標、基本面數據、籌碼結構、市場情緒等多維度進行交叉驗證分析。
你的分析風格嚴謹、專業、有深度，能用精練的語言傳達關鍵洞察。

## 嚴格規則（違反任何一條即為失敗）
1. 你只能根據 [Context Data] 中的真實數據進行分析，嚴禁發明任何數字、事件或新聞。
2. 若數據標記為 "N/A"、"Data Missing" 或 "error"，必須明確說明「該項數據缺失」，不得自行補充。
3. 你引用的每一個數字都必須能在 Context Data 中找到精確對應。

## 分析報告格式（請嚴格按照以下結構輸出，使用純文字，不要用 Markdown 標記符號如 * _ ` #）

📈 基本面分析
- 估值水準：分析 PE、Forward PE、PEG 的合理性
  - Forward PE < Trailing PE 代表市場預期獲利成長（正面）
  - Forward PE > Trailing PE 代表市場預期獲利放緩（警訊）
  - PEG < 1 為低估、PEG 1-2 合理、PEG > 2 偏高
- 獲利能力：EPS、利潤率分析
- 成長性：營收成長率與盈餘成長率的變化方向，兩者是否背離
- 市值規模與產業定位（大型股 vs 成長股的不同評估框架）
- 52 週高低點的相對位置，評估目前股價處於哪個區間
- 籌碼結構：若有空頭比率(Short Ratio)、機構持股等數據，分析其意義
- 同業比較：若有同業數據，比較 PE、利潤率、成長率與同業平均的差異

📊 技術面分析
- 解讀整體技術建議及多空信號比例
- RSI 判斷：>70 超買區（注意回調風險）、<30 超賣區（可能反彈機會）、30-70 中性
- MACD 與信號線：若 MACD > Signal 為金叉（偏多），反之為死叉（偏空）
- 均線排列：股價 vs EMA20 vs SMA50 vs SMA200，判斷多頭/空頭排列
  - 多頭排列：股價 > EMA20 > SMA50 > SMA200（強勢）
  - 空頭排列：股價 < EMA20 < SMA50 < SMA200（弱勢）
- ADX 趨勢強度：>25 有明確趨勢，<20 盤整震盪
- 布林通道：若有數據，判斷股價相對上下軌的位置
- ATR 波動率：評估近期波動風險
- 支撐壓力位：若有歷史數據，判斷關鍵支撐與壓力價位

📉 歷史回測（若有數據）
- 分析 7/30/60/90 天報酬率趨勢
- 近期波動率是否偏高
- 量能趨勢（近5日 vs 近20日均量）

📊 量能分析
- 分析當日成交量 vs 平均成交量，判斷量能是否放大或萎縮
- 量價配合判斷：放量上漲=健康、放量下跌=警訊、縮量上漲=動能不足、縮量下跌=賣壓減輕

📰 市場情緒與新聞
- 根據新聞摘要評估市場對該股的情緒傾向（正面/中性/負面）
- 挑出最具影響力的 1-2 則新聞重點
- 評估新聞可能對短期股價的影響方向

🎯 綜合評估

多空評分：X / 10
評分依據：
  - 基本面（權重 35%）：X/10 - [一句話理由]
  - 技術面（權重 30%）：X/10 - [一句話理由]
  - 量能（權重 15%）：X/10 - [一句話理由]
  - 市場情緒（權重 20%）：X/10 - [一句話理由]

評分標準：
  1-3 分 = 偏空（建議觀望或減碼）
  4-6 分 = 中性（持有，等待方向確認）
  7-10 分 = 偏多（可考慮分批布局）

關鍵觀察：
1. [最重要的觀察]
2. [次重要的觀察]
3. [需要關注的變化]

風險提示：
1. [主要風險因素]
2. [次要風險因素]

操作建議：
- 短線（1-2 週）：[觀點]
- 中線（1-3 月）：[觀點]

此為基於有限數據的分析觀點，不構成任何投資建議。投資有風險，請自行評估。

## 格式要求
- 使用繁體中文
- 不要使用 Markdown 標記符號（不要用 *、_、`、#、**），直接用純文字
- 保持專業、客觀、精練的語調
- 每個段落要有具體數字佐證（來自 Context）
- 善用對比分析（如 PE vs 同業平均、現價 vs 均線）
- 語言簡潔有力，避免冗長敘述"""


def _build_context(
    finnhub_data: dict,
    yfinance_data: dict,
    tavily_data: dict,
    tradingview_data: dict,
    history_data: dict | None = None,
    peer_data: dict | None = None,
) -> str:
    """
    將所有數據源組裝為結構化 Context 字串。
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

    if history_data and "error" not in history_data:
        context_parts.extend([
            "",
            "=== 歷史回測數據 (來源: yfinance 歷史) ===",
            json.dumps(history_data, ensure_ascii=False, indent=2),
        ])

    if peer_data and "error" not in peer_data:
        context_parts.extend([
            "",
            "=== 同業比較數據 (來源: yfinance 同業) ===",
            json.dumps(peer_data, ensure_ascii=False, indent=2),
        ])

    return "\n".join(context_parts)


async def analyze_stock(
    ticker: str,
    finnhub_data: dict,
    yfinance_data: dict,
    tavily_data: dict,
    tradingview_data: dict,
    history_data: dict | None = None,
    peer_data: dict | None = None,
) -> str:
    """
    使用 OpenAI GPT 分析股票數據。

    Args:
        ticker: 股票代碼
        finnhub_data: Finnhub 即時報價
        yfinance_data: yfinance 基本面
        tavily_data: Tavily 新聞
        tradingview_data: TradingView 技術指標
        history_data: 歷史回測數據
        peer_data: 同業比較數據

    Returns:
        str: AI 生成的分析文本
    """
    try:
        client = _get_client()

        context = _build_context(
            finnhub_data, yfinance_data, tavily_data, tradingview_data,
            history_data, peer_data,
        )

        user_prompt = f"""請根據以下 Context Data 對 {ticker.upper()} 進行全面深度分析。

嚴格遵守規則：只使用 Context 中的真實數據，禁止發明任何數字或事件。
請按照指定的報告格式輸出完整分析，使用純文字，不要用任何 Markdown 標記符號。

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
            timeout=60,  # 後端優化：60 秒超時
        )

        analysis = response.choices[0].message.content

        # 前端優化：清理 AI 回傳中可能破壞 Telegram Markdown 的字元
        analysis = _clean_markdown_conflicts(analysis)

        return analysis

    except Exception as e:
        return f"❌ AI 分析引擎錯誤: {str(e)}\n\n請檢查 OpenAI API Key 是否有效。"


def _clean_markdown_conflicts(text: str) -> str:
    """
    清理 AI 回傳文字中可能與 Telegram Markdown 衝突的字元。
    因為報告的標題區使用了 *bold*，AI 回傳中的 * 和 _ 需要移除。
    """
    if not text:
        return text
    # 移除 Markdown 標記，因為我們的 formatter 已經處理了格式
    text = text.replace("**", "")
    text = text.replace("###", "")
    text = text.replace("##", "")
    text = text.replace("# ", "")
    # 移除可能破壞 Telegram Markdown 的底線（保留數字間的底線如 52_week）
    # 處理 _斜體_ 格式
    text = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'\1', text)
    # 移除反引號
    text = text.replace("`", "")
    return text
