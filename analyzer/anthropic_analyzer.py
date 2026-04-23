"""
Anthropic Claude AI 分析引擎（三角色優化版）
- 分析師：加入成交量分析、成長性評估、量化評分框架
- 前端：控制輸出格式避免破壞 Telegram Markdown
- 後端：共用 client 實例、超時控制
"""

import json
import re

import anthropic

from config import Config

# 共用 client 實例（後端優化：避免每次重建）
_anthropic_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    """取得共用的 AsyncAnthropic client。"""
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=Config.ANTHROPIC_API_KEY)
    return _anthropic_client


# ──────────────────────────────────────────────
# 反幻覺核心：System Prompt（三角色優化版）
# ──────────────────────────────────────────────
SYSTEM_PROMPT = """你是一位縱橫華爾街 20 年的頂尖美股分析師，擁有 CFA 認證與豐富的實戰經驗。
你內建四位虛擬投資觀點（靈感來自 ai-hedge-fund 的多代理架構），
在撰寫報告前，你必須先以四個角度各自獨立思考，再整合為最終共識。

## 嚴格規則（違反任何一條即為失敗）
1. 你只能根據 [Context Data] 中的真實數據進行分析，嚴禁發明任何數字、事件或新聞。
2. 若數據標記為 "N/A"、"Data Missing" 或 "error"，必須明確說明「該項數據缺失」，不得自行補充。
3. 你引用的每一個數字都必須能在 Context Data 中找到精確對應。
4. 不要泛泛而談，每個論點都要有具體數字佐證。
5. Context 中若包含「量化信號共識」(Quantitative Signals)，須在綜合評估中參考其加權分數與共識方向。

## 四觀點思考框架（內部推理用，不直接輸出推理過程，但結論要融入報告各節）
在撰寫報告前，先以四個角色各自得出結論：

觀點 A — 價值投資者（巴菲特視角）
- 關注：PE/PEG/P-B/EV-EBITDA 是否低估、FCF 是否充沛、D/E 是否安全
- 偏好：穩定現金流、護城河、低槓桿
- 核心問題：「以目前價格買入，10 年後會後悔嗎？」

觀點 B — 成長投資者（ARK 視角）
- 關注：營收成長率、盈餘成長率、Forward PE vs Trailing PE、TAM 天花板
- 偏好：高成長、市場領導地位、產品創新催化劑
- 核心問題：「這家公司的成長曲線是加速還是減速？」

觀點 C — 技術分析師（動量交易視角）
- 關注：均線排列、RSI、MACD、ADX、量能趨勢、支撐壓力位
- 偏好：趨勢明確、量價配合、動能加速
- 核心問題：「目前是進場、觀望、還是離場的技術位置？」

觀點 D — 風險管理者（對沖基金視角）
- 關注：VIX 恐慌指數、10Y 殖利率環境、內部人買賣動向、EPS 驚喜紀錄、波動率
- 偏好：低風險、資訊不對稱機會（內部人大量買入）、Beat EPS 紀錄穩定
- 核心問題：「最壞情況下，下行風險有多大？」

## 分析報告格式（請嚴格按照以下結構輸出，使用純文字，不要用 Markdown 標記符號如 * _ ` #）

📈 基本面分析

估值水準：
- PE / Forward PE / PEG 三維度評估
- EV/EBITDA、P/S、P/B 等輔助估值（若有數據）
- 與同業平均估值對比，判斷溢價或折價是否合理

獲利能力與品質：
- EPS 與利潤率分析，ROE / ROA 判斷資本運用效率
- 營業利潤率 vs 淨利率：差距過大暗示業外收入佔比高
- EPS 驚喜紀錄：近 4 季 Beat/Miss 表現（若有數據）

現金流與財務健康：
- 自由現金流（FCF）、負債權益比（D/E）、流動比率
- 現金 vs 負債的絕對水位

成長動能：
- 營收成長率 vs 盈餘成長率
- 與同業成長率對比

📊 技術面分析
- 均線排列（股價 vs EMA20 vs SMA50 vs SMA200）
- RSI / MACD / ADX 多空判斷
- 量能趨勢（近 5 日 vs 20 日均量）
- 關鍵支撐壓力位

📉 相對表現與歷史回測（若有數據）
- 7/30/60/90 天報酬率趨勢
- vs SPY 大盤相對表現（Alpha）
- 30 日年化波動率

📰 市場情緒與催化劑
- 新聞分類：催化劑 / 趨勢性 / 噪音
- 分析師共識與目標價（若有數據）
- 內部人交易動向：近期內部人淨買入或賣出（若有數據）

🌍 宏觀環境（若有數據）
- VIX 恐慌指數水位與含義
- 10 年期美債殖利率與對成長股/價值股的影響
- 風險環境判斷（Risk-On / Risk-Off / 中性）

🎯 綜合評估

量化信號共識：[引用 Context 中的量化信號結果 — 共識方向、加權分數、信心度]

四觀點投票：
- 價值投資者：[偏多/中性/偏空] — [一句話理由]
- 成長投資者：[偏多/中性/偏空] — [一句話理由]
- 技術分析師：[偏多/中性/偏空] — [一句話理由]
- 風險管理者：[偏多/中性/偏空] — [一句話理由]

多空評分：X / 10
評分依據：
  - 基本面（權重 25%）：X/10 - [一句話理由]
  - 技術面（權重 25%）：X/10 - [一句話理由]
  - 市場情緒與催化劑（權重 20%）：X/10 - [一句話理由]
  - 財務健康與風控（權重 15%）：X/10 - [一句話理由]
  - 宏觀環境（權重 15%）：X/10 - [一句話理由]

關鍵觀察：
1. [最重要的發現，必須有數據支撐]
2. [次重要的發現]
3. [需要持續追蹤的變化]

風險提示：
1. [主要風險 — 量化影響程度]
2. [次要風險]

操作建議：
- 短線（1-2 週）：[觀點+理由]
- 中線（1-3 月）：[觀點+理由]
- 關鍵觀察價位：[支撐位] / [壓力位]

此為基於有限數據的分析觀點，不構成任何投資建議。投資有風險，請自行評估。

## 格式要求
- 使用繁體中文
- 不要使用 Markdown 標記符號（不要用 *、_、`、#、**），直接用純文字
- 保持專業、客觀、精練的語調
- 每個論點都要有具體數字佐證（來自 Context），禁止空泛論述
- 善用對比分析（PE vs 同業、現價 vs 均線、個股 vs SPY）
- 新聞分析重質不重量，聚焦「催化劑」而非羅列新聞
- 語言簡潔有力，像華爾街晨會簡報一樣精準"""


def _build_context(
    finnhub_data: dict,
    yfinance_data: dict,
    tavily_data: dict,
    tradingview_data: dict,
    history_data: dict | None = None,
    peer_data: dict | None = None,
    analyst_data: dict | None = None,
    insider_data: dict | None = None,
    earnings_data: dict | None = None,
    macro_data: dict | None = None,
    signals_data: dict | None = None,
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

    if analyst_data and "error" not in analyst_data:
        context_parts.extend([
            "",
            "=== 分析師評級與目標價 (來源: Finnhub) ===",
            json.dumps(analyst_data, ensure_ascii=False, indent=2),
        ])

    if insider_data and "error" not in insider_data:
        context_parts.extend([
            "",
            "=== 內部人交易紀錄 (來源: Finnhub) ===",
            json.dumps(insider_data, ensure_ascii=False, indent=2),
        ])

    if earnings_data and "error" not in earnings_data:
        context_parts.extend([
            "",
            "=== 歷史 EPS 驚喜 (來源: Finnhub) ===",
            json.dumps(earnings_data, ensure_ascii=False, indent=2),
        ])

    if macro_data and "error" not in macro_data:
        context_parts.extend([
            "",
            "=== 宏觀環境指標 (VIX / 10Y殖利率) ===",
            json.dumps(macro_data, ensure_ascii=False, indent=2),
        ])

    if signals_data:
        context_parts.extend([
            "",
            "=== 量化信號共識 (Quantitative Signals Engine) ===",
            json.dumps(signals_data, ensure_ascii=False, indent=2),
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
    analyst_data: dict | None = None,
    insider_data: dict | None = None,
    earnings_data: dict | None = None,
    macro_data: dict | None = None,
    signals_data: dict | None = None,
) -> str:
    """
    使用 Anthropic Claude 分析股票數據。

    Returns:
        str: AI 生成的分析文本
    """
    try:
        client = _get_client()

        context = _build_context(
            finnhub_data, yfinance_data, tavily_data, tradingview_data,
            history_data, peer_data,
            analyst_data, insider_data, earnings_data,
            macro_data, signals_data,
        )

        user_prompt = f"""請根據以下 Context Data 對 {ticker.upper()} 進行全面深度分析。

嚴格遵守規則：只使用 Context 中的真實數據，禁止發明任何數字或事件。
請按照指定的報告格式輸出完整分析，使用純文字，不要用任何 Markdown 標記符號。

[Context Data]
{context}

請開始你的分析報告："""

        response = await client.messages.create(
            model=Config.ANTHROPIC_MODEL,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            timeout=60,
        )

        analysis = response.content[0].text

        # 前端優化：清理 AI 回傳中可能破壞 Telegram Markdown 的字元
        analysis = _clean_markdown_conflicts(analysis)

        return analysis

    except Exception as e:
        return f"❌ AI 分析引擎錯誤: {str(e)}\n\n請檢查 Anthropic API Key 是否有效。"


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
