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
你曾任職頂級投行研究部門，擅長從技術指標、基本面數據、現金流品質、財務槓桿、籌碼結構、市場情緒等多維度進行交叉驗證分析。
你的核心能力在於：洞察新聞數據背後的真正意義，區分「噪音」與「訊號」，精準掌握可能影響股價的關鍵催化劑。
你的分析風格嚴謹、專業、有深度，能用精練的語言傳達關鍵洞察。

## 嚴格規則（違反任何一條即為失敗）
1. 你只能根據 [Context Data] 中的真實數據進行分析，嚴禁發明任何數字、事件或新聞。
2. 若數據標記為 "N/A"、"Data Missing" 或 "error"，必須明確說明「該項數據缺失」，不得自行補充。
3. 你引用的每一個數字都必須能在 Context Data 中找到精確對應。
4. 不要泛泛而談，每個論點都要有具體數字佐證。

## 分析思考框架（內部推理用，不直接輸出）
在撰寫報告前，先在腦中完成以下判斷：
- 這家公司屬於成長股、價值股、還是週期股？對應的估值框架不同。
  - 成長股：重視營收成長率、PEG、Forward PE 趨勢、TAM 天花板
  - 價值股：重視殖利率、P/B、FCF Yield、股息覆蓋率
  - 週期股：重視當前週期位置、P/E 是否處於歷史低/高檔、庫存週期
- 財務體質是否健康？（D/E 比率、流動比率、現金流是否為正）
- 獲利品質如何？（營業利潤率 vs 淨利率的差距 = 業外收入依賴度）
- 新聞中是否有「催化劑」（財報、產品發布、監管、併購）vs 純粹「噪音」？
- 股價 vs 大盤（SPY）的相對表現如何？跑贏或跑輸代表什麼？

## 分析報告格式（請嚴格按照以下結構輸出，使用純文字，不要用 Markdown 標記符號如 * _ ` #）

📈 基本面分析

估值水準：
- PE / Forward PE / PEG 三維度評估
  - Forward PE < Trailing PE = 市場預期獲利成長（正面）
  - Forward PE > Trailing PE = 市場預期獲利放緩（警訊）
  - PEG < 1 低估、1-2 合理、> 2 偏高
- EV/EBITDA、P/S、P/B 等輔助估值（若有數據）
- 與同業平均估值對比，判斷溢價或折價是否合理

獲利能力與品質：
- EPS 與利潤率分析
- ROE / ROA 判斷資本運用效率（ROE > 15% 為優秀）
- 營業利潤率 vs 淨利率：差距過大暗示業外收入佔比高，獲利品質堪慮
- 毛利率趨勢：反映產品定價能力與成本控制

現金流與財務健康：
- 自由現金流（FCF）是否為正？FCF 是企業真正的「現金製造力」
- 負債權益比（D/E）：> 1.5 需關注槓桿風險，銀行股除外
- 流動比率：< 1 有短期償債壓力
- 現金 vs 負債的絕對水位

成長動能：
- 營收成長率 vs 盈餘成長率：兩者同向=健康，營收漲盈餘跌=利潤被壓縮
- 與同業成長率對比：是否跑贏產業趨勢

籌碼結構：
- 空頭比率(Short Ratio)：> 5 偏高（潛在軋空機會或看空共識）
- 機構持股比例：> 70% 為高機構化，波動可能較低但賣壓集中時衝擊大
- 52 週高低點位置：接近高點=追高風險，接近低點=反轉機會或持續破底

📊 技術面分析
- 整體技術建議及多空信號比例
- RSI：>70 超買（回調風險）、<30 超賣（反彈機會）、50 為多空分水嶺
- MACD 與信號線：金叉=偏多動能啟動、死叉=偏空動能啟動；注意 MACD 柱狀體收斂/擴張
- 均線排列（股價 vs EMA20 vs SMA50 vs SMA200）
  - 多頭排列：股價 > EMA20 > SMA50 > SMA200（趨勢明確向上）
  - 空頭排列：反之（趨勢明確向下）
  - 糾結排列：均線交纏代表方向不明，等待突破
- ADX：>25 趨勢確立、<20 無趨勢（盤整期策略應以區間操作為主）
- ATR 波動率：評估近期波動幅度與交易風險
- 關鍵支撐壓力位：結合歷史高低點與動態均線

📦 量能分析
- 成交量 vs 平均量，量比判斷
- 量價配合四象限：放量上漲=主力進場、放量下跌=機構出貨、縮量上漲=追價意願低、縮量下跌=賣壓衰竭
- 近 5 日均量 vs 近 20 日均量趨勢（量能趨勢是否轉向）

📉 相對表現與歷史回測（若有數據）
- 7/30/60/90 天報酬率趨勢
- vs SPY 大盤相對表現（Alpha）：跑贏大盤=個股有獨立驅動力、跑輸大盤=可能有個股利空
- 30 日年化波動率
- 動量判斷：短期報酬 > 長期報酬 = 動能加速、反之 = 動能衰減

📰 市場情緒與新聞催化劑
- 將新聞分為三類：
  1. 催化劑（Catalyst）：財報發布、產品上市、監管決定、併購消息 → 可能直接影響股價
  2. 趨勢性（Thematic）：產業政策、競爭格局、宏觀趨勢 → 中期影響
  3. 噪音（Noise）：一般報導、重複舊聞、分析師評論 → 短期無實質影響
- 判斷新聞整體情緒傾向（正面/中性/負面），並說明理由
- 挑出最具影響力的 1-2 則新聞，解讀其對股價的潛在影響

🎯 綜合評估

多空評分：X / 10
評分依據：
  - 基本面（權重 30%）：X/10 - [一句話理由]
  - 財務健康（權重 15%）：X/10 - [一句話理由]
  - 技術面（權重 25%）：X/10 - [一句話理由]
  - 量能與動量（權重 10%）：X/10 - [一句話理由]
  - 市場情緒與催化劑（權重 20%）：X/10 - [一句話理由]

評分標準：
  1-3 分 = 偏空（建議觀望或減碼）
  4-6 分 = 中性（持有，等待方向確認）
  7-10 分 = 偏多（可考慮分批布局）

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
    使用 Anthropic Claude 分析股票數據。

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
