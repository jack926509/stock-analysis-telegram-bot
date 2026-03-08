"""
報告格式化工具（優化版）
將原始數據和 AI 分析組裝成專業級的 Telegram Markdown 報告。
"""

from datetime import datetime, timezone


def _safe_value(value, prefix="", suffix="") -> str:
    """安全格式化數值，N/A 不加前後綴。"""
    if value == "N/A" or value is None:
        return "N/A"
    return f"{prefix}{value}{suffix}"


def _format_number(value, decimals=2) -> str:
    """格式化數字，加入千分位。"""
    if value == "N/A" or value is None:
        return "N/A"
    try:
        num = float(value)
        if abs(num) >= 1000:
            return f"{num:,.{decimals}f}"
        return f"{num:.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)


def _rsi_label(rsi) -> str:
    """根據 RSI 值給予解讀標籤。"""
    if rsi == "N/A" or rsi is None:
        return ""
    try:
        val = float(rsi)
        if val >= 70:
            return " ⚠️超買"
        elif val >= 60:
            return " 偏強"
        elif val <= 30:
            return " ⚠️超賣"
        elif val <= 40:
            return " 偏弱"
        else:
            return " 中性"
    except (ValueError, TypeError):
        return ""


def _adx_label(adx) -> str:
    """根據 ADX 值給予趨勢強度標籤。"""
    if adx == "N/A" or adx is None:
        return ""
    try:
        val = float(adx)
        if val >= 50:
            return " 極強趨勢"
        elif val >= 25:
            return " 強趨勢"
        elif val >= 20:
            return " 弱趨勢"
        else:
            return " 無趨勢"
    except (ValueError, TypeError):
        return ""


def _price_position_bar(current, low_52w, high_52w) -> str:
    """生成 52 週價格位置視覺化長條。"""
    try:
        cur = float(current)
        lo = float(low_52w)
        hi = float(high_52w)
        if hi == lo:
            return ""
        position = (cur - lo) / (hi - lo)
        filled = int(position * 10)
        bar = "▓" * filled + "░" * (10 - filled)
        pct = position * 100
        return f"  52W位置: [{bar}] {pct:.0f}%"
    except (ValueError, TypeError):
        return ""


def _recommendation_display(rec: str) -> str:
    """將技術建議轉換為中文顯示。"""
    mapping = {
        "STRONG_BUY": "🟢 強力買入",
        "BUY": "🟢 買入",
        "NEUTRAL": "🟡 中性",
        "SELL": "🔴 賣出",
        "STRONG_SELL": "🔴 強力賣出",
    }
    return mapping.get(rec, f"⚪ {rec}")


def _data_quality_score(finnhub, yfinance, tavily, tradingview) -> str:
    """計算數據完整度指標。"""
    total = 4
    available = 0
    sources = []

    if "error" not in finnhub:
        available += 1
        sources.append("✅ Finnhub")
    else:
        sources.append("❌ Finnhub")

    if "error" not in yfinance:
        available += 1
        sources.append("✅ yfinance")
    else:
        sources.append("❌ yfinance")

    if "error" not in tavily:
        available += 1
        sources.append("✅ Tavily")
    else:
        sources.append("❌ Tavily")

    if "error" not in tradingview:
        available += 1
        sources.append("✅ TradingView")
    else:
        sources.append("❌ TradingView")

    bar = "●" * available + "○" * (total - available)
    return f"[{bar}] {available}/{total}", sources


def format_report(
    ticker: str,
    finnhub_data: dict,
    yfinance_data: dict,
    tavily_data: dict,
    tradingview_data: dict,
    ai_analysis: str,
) -> str:
    """
    組裝完整的分析報告（優化版）。

    同時展示原始數據與 AI 分析，讓使用者可交叉驗證（反幻覺最後防線）。
    """
    report_parts = []

    # ══════════════════════════════
    # 標題區
    # ══════════════════════════════
    company_name = yfinance_data.get("company_name", ticker.upper())
    if company_name == "N/A":
        company_name = ticker.upper()

    sector = yfinance_data.get("sector", "")
    industry = yfinance_data.get("industry", "")
    sector_display = f"{sector} • {industry}" if sector != "N/A" and industry != "N/A" else ""

    report_parts.append(f"📊 *{ticker.upper()} — {company_name}*")
    if sector_display:
        report_parts.append(f"_{sector_display}_")
    report_parts.append("══════════════════════════")

    # ══════════════════════════════
    # 數據品質指標
    # ══════════════════════════════
    quality_score, quality_sources = _data_quality_score(
        finnhub_data, yfinance_data, tavily_data, tradingview_data
    )
    report_parts.append(f"🔋 數據完整度: {quality_score}")
    report_parts.append(f"  {' | '.join(quality_sources)}")

    # ══════════════════════════════
    # 即時報價儀表板
    # ══════════════════════════════
    report_parts.append("")
    report_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    report_parts.append("💰 *即時報價儀表板*")
    report_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if "error" not in finnhub_data:
        price = finnhub_data.get("current_price", "N/A")
        change = finnhub_data.get("change", "N/A")
        change_pct = finnhub_data.get("change_percent", "N/A")

        # 漲跌顯示
        if isinstance(change_pct, (int, float)):
            if change_pct >= 0:
                arrow = "🟢 ▲"
                change_display = f"{arrow} +{change_pct}% (+${abs(change) if isinstance(change, (int, float)) else 'N/A'})"
            else:
                arrow = "🔴 ▼"
                change_display = f"{arrow} {change_pct}% (${change})"
        else:
            change_display = "N/A"

        report_parts.append(f"  💵 現價: *${_format_number(price)}*  {change_display}")
        report_parts.append(
            f"  📈 盤中高: ${_format_number(finnhub_data.get('high'))}  "
            f"📉 盤中低: ${_format_number(finnhub_data.get('low'))}"
        )
        report_parts.append(f"  ⏮ 前收盤: ${_format_number(finnhub_data.get('previous_close'))}")

        # 52 週位置視覺化
        if "error" not in yfinance_data:
            pos_bar = _price_position_bar(
                price, yfinance_data.get("52w_low"), yfinance_data.get("52w_high")
            )
            if pos_bar:
                report_parts.append(pos_bar)
    else:
        report_parts.append(f"  ⚠️ {finnhub_data['error']}")

    # ══════════════════════════════
    # 基本面數據
    # ══════════════════════════════
    report_parts.append("")
    report_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    report_parts.append("📈 *基本面數據*")
    report_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if "error" not in yfinance_data:
        report_parts.append(f"  🏢 市值: *{yfinance_data.get('market_cap', 'N/A')}*")
        report_parts.append("")

        # 估值指標
        pe = yfinance_data.get("pe_ratio", "N/A")
        fpe = yfinance_data.get("forward_pe", "N/A")
        pe_comparison = ""
        try:
            if pe != "N/A" and fpe != "N/A":
                if float(fpe) < float(pe):
                    pe_comparison = " 📉 獲利預期成長"
                else:
                    pe_comparison = " 📈 獲利預期下滑"
        except (ValueError, TypeError):
            pass

        report_parts.append("  *估值指標*")
        report_parts.append(f"  ├ 本益比 (TTM): {_format_number(pe)}")
        report_parts.append(f"  ├ 預估本益比: {_format_number(fpe)}{pe_comparison}")
        report_parts.append(f"  └ EPS: {_safe_value(yfinance_data.get('eps'), prefix='$')}")
        report_parts.append("")

        # 收益指標
        report_parts.append("  *收益指標*")
        report_parts.append(f"  ├ 殖利率: {yfinance_data.get('dividend_yield', 'N/A')}")
        report_parts.append(f"  └ 利潤率: {yfinance_data.get('profit_margin', 'N/A')}")
        report_parts.append("")

        # 價格區間
        report_parts.append("  *52 週價格區間*")
        report_parts.append(
            f"  ├ 52 週高: ${_format_number(yfinance_data.get('52w_high'))}"
        )
        report_parts.append(
            f"  ├ 52 週低: ${_format_number(yfinance_data.get('52w_low'))}"
        )
        report_parts.append(f"  ├ 50 日均線: ${_format_number(yfinance_data.get('50d_avg'))}")
        report_parts.append(f"  └ 200 日均線: ${_format_number(yfinance_data.get('200d_avg'))}")
    else:
        report_parts.append(f"  ⚠️ {yfinance_data['error']}")

    # ══════════════════════════════
    # 技術面信號
    # ══════════════════════════════
    report_parts.append("")
    report_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    report_parts.append("🔍 *技術面信號*")
    report_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if "error" not in tradingview_data:
        rec = tradingview_data.get("recommendation", "N/A")
        buy = tradingview_data.get("buy_signals", 0)
        sell = tradingview_data.get("sell_signals", 0)
        neutral = tradingview_data.get("neutral_signals", 0)

        report_parts.append(f"  🎯 整體建議: *{_recommendation_display(rec)}*")
        report_parts.append("")

        # 信號比例視覺化
        try:
            total_signals = int(buy) + int(sell) + int(neutral)
            if total_signals > 0:
                buy_bar = "🟢" * int(int(buy) / total_signals * 10)
                sell_bar = "🔴" * int(int(sell) / total_signals * 10)
                neutral_bar = "🟡" * (10 - len(buy_bar) - len(sell_bar))
                report_parts.append(f"  信號分佈: {buy_bar}{neutral_bar}{sell_bar}")
                report_parts.append(f"  買入 {buy} | 中性 {neutral} | 賣出 {sell}")
        except (ValueError, TypeError):
            report_parts.append(f"  信號: 買{buy} / 中性{neutral} / 賣{sell}")

        report_parts.append("")

        # 動能指標
        rsi = tradingview_data.get("rsi_14", "N/A")
        adx = tradingview_data.get("adx", "N/A")

        report_parts.append("  *動能指標*")
        report_parts.append(f"  ├ RSI(14): {rsi}{_rsi_label(rsi)}")
        report_parts.append(f"  ├ MACD: {tradingview_data.get('macd', 'N/A')}")
        report_parts.append(f"  ├ MACD Signal: {tradingview_data.get('macd_signal', 'N/A')}")
        report_parts.append(f"  ├ ADX: {adx}{_adx_label(adx)}")
        report_parts.append(f"  └ Stoch %K: {tradingview_data.get('stoch_k', 'N/A')}")
        report_parts.append("")

        # 均線數據
        report_parts.append("  *均線數據*")
        report_parts.append(f"  ├ EMA20: ${_format_number(tradingview_data.get('ema_20'))}")
        report_parts.append(f"  ├ SMA50: ${_format_number(tradingview_data.get('sma_50'))}")
        report_parts.append(f"  └ SMA200: ${_format_number(tradingview_data.get('sma_200'))}")

        # 子類別建議
        ma_rec = tradingview_data.get("moving_averages", {}).get("recommendation", "N/A")
        osc_rec = tradingview_data.get("oscillators", {}).get("recommendation", "N/A")
        if ma_rec != "N/A" or osc_rec != "N/A":
            report_parts.append("")
            report_parts.append(f"  均線建議: {_recommendation_display(ma_rec)}")
            report_parts.append(f"  震盪指標: {_recommendation_display(osc_rec)}")
    else:
        report_parts.append(f"  ⚠️ {tradingview_data['error']}")

    # ══════════════════════════════
    # 近期新聞
    # ══════════════════════════════
    report_parts.append("")
    report_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    report_parts.append("📰 *近期新聞摘要*")
    report_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if "error" not in tavily_data:
        # AI 新聞摘要
        ai_summary = tavily_data.get("ai_summary", "")
        if ai_summary and ai_summary != "無法取得新聞摘要":
            report_parts.append(f"  💡 _{ai_summary[:300]}_")
            report_parts.append("")

        # 新聞列表
        if tavily_data.get("news"):
            for i, news in enumerate(tavily_data["news"][:5], 1):
                title = news.get("title", "N/A")
                url = news.get("url", "#")
                report_parts.append(f"  {i}. [{title}]({url})")
        else:
            report_parts.append("  暫無相關新聞")
    else:
        report_parts.append(f"  ⚠️ {tavily_data.get('error', '新聞數據不可用')}")

    # ══════════════════════════════
    # AI 深度分析
    # ══════════════════════════════
    report_parts.append("")
    report_parts.append("══════════════════════════")
    report_parts.append("🤖 *AI 深度分析報告*")
    report_parts.append("══════════════════════════")
    report_parts.append("")
    report_parts.append(ai_analysis)

    # ══════════════════════════════
    # 尾部
    # ══════════════════════════════
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report_parts.append("")
    report_parts.append("══════════════════════════")
    report_parts.append("⚠️ _免責聲明：本報告僅供參考研究，不構成任何投資建議。_")
    report_parts.append("_投資有風險，歷史數據不代表未來表現。_")
    report_parts.append(
        "_數據來源: Finnhub • yfinance • Tavily • TradingView_"
    )
    report_parts.append(f"📅 _報告生成: {now}_")
    report_parts.append("🛡️ _Powered by Zero-Hallucination Engine_")

    return "\n".join(report_parts)
