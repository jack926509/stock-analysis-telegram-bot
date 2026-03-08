"""
報告格式化工具
將原始數據和 AI 分析組裝成美觀的 Telegram Markdown 報告。
"""

from datetime import datetime, timezone


def _safe_value(value, prefix="", suffix="") -> str:
    """安全格式化數值，N/A 不加前後綴。"""
    if value == "N/A" or value is None:
        return "N/A"
    return f"{prefix}{value}{suffix}"


def format_report(
    ticker: str,
    finnhub_data: dict,
    yfinance_data: dict,
    tavily_data: dict,
    tradingview_data: dict,
    ai_analysis: str,
) -> str:
    """
    組裝完整的分析報告。

    同時展示原始數據與 AI 分析，讓使用者可交叉驗證（反幻覺最後防線）。
    """
    report_parts = []

    # ── 標題 ──
    company_name = yfinance_data.get("company_name", ticker.upper())
    if company_name == "N/A":
        company_name = ticker.upper()
    report_parts.append(f"📊 *{ticker.upper()} — {company_name}*")
    report_parts.append("━" * 24)

    # ── 即時報價區 ──
    if "error" not in finnhub_data:
        price = finnhub_data.get("current_price", "N/A")
        change = finnhub_data.get("change", "N/A")
        change_pct = finnhub_data.get("change_percent", "N/A")

        # 漲跌 emoji
        if isinstance(change_pct, (int, float)):
            arrow = "🟢 +" if change_pct >= 0 else "🔴 "
            change_display = f"{arrow}{change_pct}%"
        else:
            change_display = "N/A"

        report_parts.append("")
        report_parts.append("💰 *即時報價*")
        report_parts.append(f"├ 當前價格: ${price}")
        report_parts.append(f"├ 漲跌幅: {change_display}")
        report_parts.append(
            f"├ 盤中高/低: ${finnhub_data.get('high', 'N/A')} / ${finnhub_data.get('low', 'N/A')}"
        )
        report_parts.append(
            f"└ 前收盤: ${finnhub_data.get('previous_close', 'N/A')}"
        )
    else:
        report_parts.append("")
        report_parts.append(f"💰 *即時報價*: ⚠️ {finnhub_data['error']}")

    # ── 基本面區 ──
    if "error" not in yfinance_data:
        report_parts.append("")
        report_parts.append("📈 *基本面概覽*")
        report_parts.append(f"├ 產業: {yfinance_data.get('sector', 'N/A')} / {yfinance_data.get('industry', 'N/A')}")
        report_parts.append(f"├ 市值: {yfinance_data.get('market_cap', 'N/A')}")
        report_parts.append(f"├ 本益比 (TTM): {_safe_value(yfinance_data.get('pe_ratio'))}")
        report_parts.append(f"├ 預估本益比: {_safe_value(yfinance_data.get('forward_pe'))}")
        report_parts.append(f"├ EPS: {_safe_value(yfinance_data.get('eps'), prefix='$')}")
        report_parts.append(f"├ 殖利率: {yfinance_data.get('dividend_yield', 'N/A')}")
        report_parts.append(f"├ 利潤率: {yfinance_data.get('profit_margin', 'N/A')}")
        report_parts.append(
            f"└ 52 週高/低: ${yfinance_data.get('52w_high', 'N/A')} / ${yfinance_data.get('52w_low', 'N/A')}"
        )
    else:
        report_parts.append("")
        report_parts.append(f"📈 *基本面概覽*: ⚠️ {yfinance_data['error']}")

    # ── 技術面區 ──
    if "error" not in tradingview_data:
        rec = tradingview_data.get("recommendation", "N/A")
        rec_emoji = {"STRONG_BUY": "🟢", "BUY": "🟢", "NEUTRAL": "🟡", "SELL": "🔴", "STRONG_SELL": "🔴"}.get(rec, "⚪")

        report_parts.append("")
        report_parts.append("🔍 *技術面信號*")
        report_parts.append(f"├ 整體建議: {rec_emoji} {rec}")
        report_parts.append(f"├ RSI(14): {tradingview_data.get('rsi_14', 'N/A')}")
        report_parts.append(f"├ MACD: {tradingview_data.get('macd', 'N/A')}")
        report_parts.append(f"├ EMA20: {tradingview_data.get('ema_20', 'N/A')}")
        report_parts.append(f"├ SMA50: {tradingview_data.get('sma_50', 'N/A')}")
        report_parts.append(f"├ ADX: {tradingview_data.get('adx', 'N/A')}")
        report_parts.append(
            f"└ 信號統計: 買{tradingview_data.get('buy_signals', 'N/A')} / "
            f"中性{tradingview_data.get('neutral_signals', 'N/A')} / "
            f"賣{tradingview_data.get('sell_signals', 'N/A')}"
        )
    else:
        report_parts.append("")
        report_parts.append(f"🔍 *技術面信號*: ⚠️ {tradingview_data['error']}")

    # ── 新聞區 ──
    if "error" not in tavily_data and tavily_data.get("news"):
        report_parts.append("")
        report_parts.append("📰 *近期新聞*")
        for i, news in enumerate(tavily_data["news"][:3], 1):
            title = news.get("title", "N/A")
            url = news.get("url", "#")
            report_parts.append(f"  {i}\\. [{title}]({url})")
    elif "error" in tavily_data:
        report_parts.append("")
        report_parts.append(f"📰 *近期新聞*: ⚠️ {tavily_data['error']}")

    # ── AI 分析區 ──
    report_parts.append("")
    report_parts.append("━" * 24)
    report_parts.append("🤖 *AI 深度分析*")
    report_parts.append("━" * 24)
    report_parts.append("")
    report_parts.append(ai_analysis)

    # ── 免責聲明 ──
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report_parts.append("")
    report_parts.append("━" * 24)
    report_parts.append("⚠️ _免責聲明：本報告僅供參考，不構成投資建議。_")
    report_parts.append(
        "_數據來源: Finnhub, yfinance, Tavily, TradingView_"
    )
    report_parts.append(f"📅 _報告生成時間: {now}_")

    return "\n".join(report_parts)
