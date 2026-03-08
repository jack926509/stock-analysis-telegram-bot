"""
報告格式化工具（三角色優化版）
- 分析師：新增成交量、Beta、成長率展示
- 前端：精簡結構、Markdown 安全處理
- 後端：穩定的格式化流程
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
            return " 盤整"
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
        position = max(0, min(1, position))  # clamp 0-1
        filled = int(position * 10)
        bar = "▓" * filled + "░" * (10 - filled)
        pct = position * 100
        return f"  52W位置: [{bar}] {pct:.0f}%"
    except (ValueError, TypeError):
        return ""


def _recommendation_display(rec: str) -> str:
    """將技術建議轉換為中文。"""
    mapping = {
        "STRONG_BUY": "🟢 強力買入",
        "BUY": "🟢 買入",
        "NEUTRAL": "🟡 中性",
        "SELL": "🔴 賣出",
        "STRONG_SELL": "🔴 強力賣出",
    }
    return mapping.get(rec, f"⚪ {rec}")


def _data_quality_score(finnhub, yfinance, tavily, tradingview):
    """計算數據完整度指標。"""
    total = 4
    available = 0
    sources = []

    for name, data in [("Finnhub", finnhub), ("yfinance", yfinance),
                        ("Tavily", tavily), ("TradingView", tradingview)]:
        if "error" not in data:
            available += 1
            sources.append(f"✅{name}")
        else:
            sources.append(f"❌{name}")

    bar = "●" * available + "○" * (total - available)
    return f"[{bar}] {available}/{total}", sources


def _volume_analysis(yfinance_data: dict) -> str:
    """量能分析視覺化。"""
    volume = yfinance_data.get("volume", "N/A")
    avg_volume = yfinance_data.get("avg_volume", "N/A")

    if volume == "N/A" or avg_volume == "N/A":
        return ""

    lines = []
    lines.append("  成交量: " + volume)
    lines.append("  平均量: " + avg_volume)

    # 嘗試計算量比
    try:
        # 把 format 過的值轉回數字
        def _parse(v):
            v = str(v).replace(",", "")
            if v.endswith("B"):
                return float(v[:-1]) * 1e9
            elif v.endswith("M"):
                return float(v[:-1]) * 1e6
            elif v.endswith("K"):
                return float(v[:-1]) * 1e3
            return float(v)

        vol = _parse(volume)
        avg = _parse(avg_volume)
        if avg > 0:
            ratio = vol / avg
            if ratio >= 1.5:
                lines.append(f"  量比: {ratio:.1f}x ⬆️ 明顯放量")
            elif ratio >= 1.2:
                lines.append(f"  量比: {ratio:.1f}x ↗️ 溫和放量")
            elif ratio <= 0.5:
                lines.append(f"  量比: {ratio:.1f}x ⬇️ 明顯縮量")
            elif ratio <= 0.8:
                lines.append(f"  量比: {ratio:.1f}x ↘️ 溫和縮量")
            else:
                lines.append(f"  量比: {ratio:.1f}x ➡️ 正常")
    except (ValueError, TypeError):
        pass

    return "\n".join(lines)


def format_report(
    ticker: str,
    finnhub_data: dict,
    yfinance_data: dict,
    tavily_data: dict,
    tradingview_data: dict,
    ai_analysis: str,
) -> str:
    """
    組裝完整的分析報告（三角色優化版）。
    """
    report_parts = []

    # ══ 標題 ══
    company_name = yfinance_data.get("company_name", ticker.upper())
    if company_name == "N/A":
        company_name = ticker.upper()

    sector = yfinance_data.get("sector", "")
    industry = yfinance_data.get("industry", "")
    sector_line = f"{sector} | {industry}" if sector not in ("N/A", "") and industry not in ("N/A", "") else ""

    report_parts.append(f"📊 *{ticker.upper()} — {company_name}*")
    if sector_line:
        report_parts.append(sector_line)

    # 數據品質
    quality_score, quality_sources = _data_quality_score(
        finnhub_data, yfinance_data, tavily_data, tradingview_data
    )
    report_parts.append(f"🔋 數據: {quality_score} {' '.join(quality_sources)}")
    report_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # ══ 即時報價 ══
    if "error" not in finnhub_data:
        price = finnhub_data.get("current_price", "N/A")
        change_pct = finnhub_data.get("change_percent", "N/A")
        change = finnhub_data.get("change", "N/A")

        if isinstance(change_pct, (int, float)):
            if change_pct >= 0:
                chg = f"🟢 +{change_pct}% (+${abs(change) if isinstance(change, (int, float)) else 'N/A'})"
            else:
                chg = f"🔴 {change_pct}% (${change})"
        else:
            chg = ""

        report_parts.append(f"💰 現價: ${_format_number(price)}  {chg}")
        report_parts.append(
            f"  高/低: ${_format_number(finnhub_data.get('high'))} / "
            f"${_format_number(finnhub_data.get('low'))}  "
            f"前收: ${_format_number(finnhub_data.get('previous_close'))}"
        )

        # 52 週位置
        if "error" not in yfinance_data:
            pos_bar = _price_position_bar(
                price, yfinance_data.get("52w_low"), yfinance_data.get("52w_high")
            )
            if pos_bar:
                report_parts.append(pos_bar)
    else:
        report_parts.append(f"💰 ⚠️ {finnhub_data['error']}")

    # ══ 基本面 ══
    report_parts.append("")
    report_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    report_parts.append("📈 *基本面*")

    if "error" not in yfinance_data:
        report_parts.append(f"  市值: {yfinance_data.get('market_cap', 'N/A')}  "
                           f"Beta: {_safe_value(yfinance_data.get('beta'))}")

        # 估值
        pe = yfinance_data.get("pe_ratio", "N/A")
        fpe = yfinance_data.get("forward_pe", "N/A")
        pe_hint = ""
        try:
            if pe != "N/A" and fpe != "N/A":
                pe_hint = " 📉成長預期" if float(fpe) < float(pe) else " 📈獲利放緩"
        except (ValueError, TypeError):
            pass

        report_parts.append(f"  PE: {_format_number(pe)}  "
                           f"Forward PE: {_format_number(fpe)}{pe_hint}")
        report_parts.append(f"  EPS: {_safe_value(yfinance_data.get('eps'), prefix='$')}  "
                           f"PEG: {_safe_value(yfinance_data.get('peg_ratio'))}")
        report_parts.append(f"  殖利率: {yfinance_data.get('dividend_yield', 'N/A')}  "
                           f"利潤率: {yfinance_data.get('profit_margin', 'N/A')}")

        # 成長指標
        rev_growth = yfinance_data.get("revenue_growth", "N/A")
        earn_growth = yfinance_data.get("earnings_growth", "N/A")
        if rev_growth != "N/A" or earn_growth != "N/A":
            report_parts.append(f"  營收成長: {rev_growth}  盈餘成長: {earn_growth}")

        # 價格區間
        report_parts.append(f"  52W: ${_format_number(yfinance_data.get('52w_low'))} ~ "
                           f"${_format_number(yfinance_data.get('52w_high'))}")
        report_parts.append(f"  50MA: ${_format_number(yfinance_data.get('50d_avg'))}  "
                           f"200MA: ${_format_number(yfinance_data.get('200d_avg'))}")
    else:
        report_parts.append(f"  ⚠️ {yfinance_data['error']}")

    # ══ 量能 ══
    if "error" not in yfinance_data:
        vol_analysis = _volume_analysis(yfinance_data)
        if vol_analysis:
            report_parts.append("")
            report_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
            report_parts.append("📦 *量能分析*")
            report_parts.append(vol_analysis)

    # ══ 技術面 ══
    report_parts.append("")
    report_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    report_parts.append("🔍 *技術面信號*")

    if "error" not in tradingview_data:
        rec = tradingview_data.get("recommendation", "N/A")
        buy = tradingview_data.get("buy_signals", 0)
        sell = tradingview_data.get("sell_signals", 0)
        neutral = tradingview_data.get("neutral_signals", 0)

        report_parts.append(f"  建議: {_recommendation_display(rec)}")

        # 信號比例
        try:
            total_signals = int(buy) + int(sell) + int(neutral)
            if total_signals > 0:
                buy_bar = "🟢" * int(int(buy) / total_signals * 10)
                sell_bar = "🔴" * int(int(sell) / total_signals * 10)
                neutral_bar = "🟡" * (10 - len(buy_bar) - len(sell_bar))
                report_parts.append(f"  {buy_bar}{neutral_bar}{sell_bar} 買{buy}/中{neutral}/賣{sell}")
        except (ValueError, TypeError):
            pass

        # 動能指標（精簡排版）
        rsi = tradingview_data.get("rsi_14", "N/A")
        adx = tradingview_data.get("adx", "N/A")
        report_parts.append(f"  RSI: {rsi}{_rsi_label(rsi)}  ADX: {adx}{_adx_label(adx)}")
        report_parts.append(f"  MACD: {tradingview_data.get('macd', 'N/A')}  "
                           f"Signal: {tradingview_data.get('macd_signal', 'N/A')}")

        # 均線
        report_parts.append(f"  EMA20: ${_format_number(tradingview_data.get('ema_20'))}  "
                           f"SMA50: ${_format_number(tradingview_data.get('sma_50'))}  "
                           f"SMA200: ${_format_number(tradingview_data.get('sma_200'))}")

        # 子類別建議
        ma_rec = tradingview_data.get("moving_averages", {}).get("recommendation", "N/A")
        osc_rec = tradingview_data.get("oscillators", {}).get("recommendation", "N/A")
        if ma_rec != "N/A" or osc_rec != "N/A":
            report_parts.append(f"  均線: {_recommendation_display(ma_rec)}  "
                               f"震盪: {_recommendation_display(osc_rec)}")
    else:
        report_parts.append(f"  ⚠️ {tradingview_data['error']}")

    # ══ 新聞 ══
    report_parts.append("")
    report_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    report_parts.append("📰 *新聞*")

    if "error" not in tavily_data:
        ai_summary = tavily_data.get("ai_summary", "")
        if ai_summary and ai_summary != "無法取得新聞摘要":
            # 截短摘要
            summary = ai_summary[:200] + "..." if len(ai_summary) > 200 else ai_summary
            report_parts.append(f"  {summary}")
            report_parts.append("")

        if tavily_data.get("news"):
            for i, news in enumerate(tavily_data["news"][:3], 1):
                title = news.get("title", "N/A")
                url = news.get("url", "#")
                report_parts.append(f"  {i}. [{title}]({url})")
        else:
            report_parts.append("  暫無相關新聞")
    else:
        report_parts.append(f"  ⚠️ {tavily_data.get('error', '新聞不可用')}")

    # ══ AI 分析 ══
    report_parts.append("")
    report_parts.append("══════════════════════════")
    report_parts.append("🤖 *AI 深度分析*")
    report_parts.append("══════════════════════════")
    report_parts.append("")
    report_parts.append(ai_analysis)

    # ══ 尾部 ══
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report_parts.append("")
    report_parts.append("══════════════════════════")
    report_parts.append("⚠️ 本報告僅供參考研究，不構成投資建議。")
    report_parts.append(f"數據來源: Finnhub | yfinance | Tavily | TradingView")
    report_parts.append(f"📅 {now} | 🛡️ Zero-Hallucination Engine")

    return "\n".join(report_parts)
