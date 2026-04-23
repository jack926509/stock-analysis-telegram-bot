"""
Anthropic Claude AI 分析引擎。
- 透過 utils.ai_client 共用 AsyncAnthropic 實例
- 系統提示使用 Prompt Caching（cache_control=ephemeral）以降低 token 成本
- 輸出純文字，在 formatter 層統一做 HTML 跳脫
"""

import json

from config import Config
from utils.ai_client import cached_system, get_ai_client


# 對「分析」零貢獻、純背景描述／識別碼欄位 — 進入 AI 前先剔除以省 input tokens。
# 這些欄位仍保留在原 dict 中供 formatter 顯示給使用者，只是不送進 AI context。
_AI_NOISE_KEYS: frozenset[str] = frozenset({
    "business_summary", "long_business_summary", "longBusinessSummary",
    "description", "summary",
    "logo_url", "logo", "image", "website", "address", "address1",
    "phone", "fax", "ipo", "ipoDate", "cusip", "isin", "cik",
    "fullTimeEmployees", "country", "city", "state", "zip",
})


def _strip_noise(data):
    """遞迴移除 _AI_NOISE_KEYS — 只用於 AI context 組裝。"""
    if isinstance(data, dict):
        return {k: _strip_noise(v) for k, v in data.items() if k not in _AI_NOISE_KEYS}
    if isinstance(data, list):
        return [_strip_noise(v) for v in data]
    return data


def _compact(data) -> str:
    """JSON compact 序列化（無 indent / 無多餘空白）— 顯著降低 input tokens。"""
    return json.dumps(_strip_noise(data), ensure_ascii=False, separators=(",", ":"))


# ──────────────────────────────────────────────
# 反幻覺核心：System Prompt（三角色優化版）
# ──────────────────────────────────────────────
SYSTEM_PROMPT = """你是一位縱橫華爾街 20 年的頂尖美股分析師。
報告的數據區塊已展示所有原始數據，你的任務是「解讀」而非「複述」。
每一句話都必須包含不可替代的洞察 — 如果刪掉這句話讀者不會損失任何資訊，就不要寫。

## 嚴格規則
1. 只使用 [Context Data] 中的真實數據，嚴禁發明數字或事件。
2. "N/A" 或 "error" 的數據必須標明缺失，不得自行補充。
3. 禁止空泛論述（如「整體表現不錯」），每個觀點必須附帶具體數字。
4. 不要重複 Context 中的原始數字清單 — 讀者已在數據區塊看過。你要做的是：交叉驗證、找出矛盾、給出判斷。

## 四觀點框架（內部推理，結論融入報告）

A. 價值投資者 — 護城河、FCF、估值安全邊際。核心：「目前價格有安全邊際嗎？」
B. 成長投資者 — 營收/盈餘成長斜率、Forward PE 趨勢。核心：「成長在加速還是減速？」
C. 技術分析師 — 趨勢、動量、量價、位階。核心：「現在是好的進場/離場時機嗎？」
D. 風險管理者 — VIX、波動率、內部人、EPS 紀錄。核心：「最壞情況下虧多少？」

## 報告格式（純文字，不用 Markdown 符號 * _ ` #）

📈 基本面研判
（不要列數字清單。直接給出判斷：這家公司是什麼類型？估值合理嗎？獲利品質如何？財務體質有無隱憂？成長是否可持續？用 2-3 段精練文字交叉分析，每段必須有數據佐證。特別指出：不同指標之間的矛盾或確認關係。）

📊 技術面研判
（趨勢方向、動量狀態、量價配合度。重點是「現在的技術位置意味著什麼」，而非複述 RSI=XX、MACD=XX。指出支撐壓力位的實戰意義。）

📰 催化劑與情緒
（只分析真正的催化劑，忽略噪音。分析師共識與內部人動向如何交叉驗證？新聞中最值得關注的 1-2 個催化劑是什麼？為什麼？）

🎯 綜合評估

量化信號共識：[引用 Context 中的共識方向、加權分數、信心度]

四觀點投票：
- 價值投資者：[偏多/中性/偏空] — [一句話，必須有數字]
- 成長投資者：[偏多/中性/偏空] — [一句話，必須有數字]
- 技術分析師：[偏多/中性/偏空] — [一句話，必須有數字]
- 風險管理者：[偏多/中性/偏空] — [一句話，必須有數字]

多空評分：X / 10
（1-3 偏空 / 4-6 中性 / 7-10 偏多）

核心判斷：[用 1-2 句話回答「現在該怎麼做」，這是整份報告最重要的結論]

關鍵觀察：
1. [最重要的發現 — 有數據]
2. [次重要的發現]

風險：
1. [主要下行風險 — 量化幅度]
2. [次要風險]

操作建議：
- 短線（1-2 週）：[具體觀點+觸發條件]
- 中線（1-3 月）：[具體觀點+觸發條件]
- 關鍵價位：支撐 [X] / 壓力 [X]

此為基於有限數據的分析觀點，不構成投資建議。

## 文風要求
- 繁體中文，專業精練
- 像華爾街晨會簡報：每句話都有資訊量，零廢話
- 重「解讀」輕「複述」：讀者要的是你的判斷，不是數據搬運
- 善用交叉驗證：指標之間的矛盾比單一指標更有價值
- 精確優於完整：寧可少寫一段也不要寫一段沒有洞察的文字"""


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
        _compact(finnhub_data),
        "",
        "=== 基本面數據 (來源: yfinance) ===",
        _compact(yfinance_data),
        "",
        "=== 最新新聞 (來源: Tavily) ===",
        _compact(tavily_data),
        "",
        "=== 技術指標 (來源: TradingView) ===",
        _compact(tradingview_data),
    ]

    if history_data and "error" not in history_data:
        context_parts.extend([
            "",
            "=== 歷史回測數據 (來源: yfinance 歷史) ===",
            _compact(history_data),
        ])

    if peer_data and "error" not in peer_data:
        context_parts.extend([
            "",
            "=== 同業比較數據 (來源: yfinance 同業) ===",
            _compact(peer_data),
        ])

    if analyst_data and "error" not in analyst_data:
        context_parts.extend([
            "",
            "=== 分析師評級與目標價 (來源: Finnhub) ===",
            _compact(analyst_data),
        ])

    if insider_data and "error" not in insider_data:
        context_parts.extend([
            "",
            "=== 內部人交易紀錄 (來源: Finnhub) ===",
            _compact(insider_data),
        ])

    if earnings_data and "error" not in earnings_data:
        context_parts.extend([
            "",
            "=== 歷史 EPS 驚喜 (來源: Finnhub) ===",
            _compact(earnings_data),
        ])

    if macro_data and "error" not in macro_data:
        context_parts.extend([
            "",
            "=== 宏觀環境指標 (VIX / 10Y殖利率) ===",
            _compact(macro_data),
        ])

    if signals_data:
        context_parts.extend([
            "",
            "=== 量化信號共識 (Quantitative Signals Engine) ===",
            _compact(signals_data),
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
        client = get_ai_client()

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
            max_tokens=2800,
            system=cached_system(SYSTEM_PROMPT),
            messages=[
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            timeout=60,
        )

        return response.content[0].text

    except Exception as e:
        return f"❌ AI 分析引擎錯誤: {str(e)}\n\n請檢查 Anthropic API Key 是否有效。"
