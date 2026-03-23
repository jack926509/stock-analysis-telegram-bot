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


def _trend_arrows(price, ema20, sma50, sma200) -> str:
    """生成均線趨勢排列判斷。"""
    try:
        p = float(price)
        e20 = float(ema20)
        s50 = float(sma50)
        s200 = float(sma200)
    except (ValueError, TypeError):
        return ""

    if p > e20 > s50 > s200:
        return "🟢 多頭排列"
    elif p < e20 < s50 < s200:
        return "🔴 空頭排列"
    elif p > s200:
        return "🟡 偏多整理"
    else:
        return "🟡 偏空整理"


def _quick_summary(finnhub_data, yfinance_data, tradingview_data) -> str:
    """生成快速摘要區塊，讓讀者 3 秒掌握重點。"""
    parts = []

    # 漲跌
    change_pct = finnhub_data.get("change_percent", "N/A")
    if isinstance(change_pct, (int, float)):
        if change_pct >= 0:
            parts.append(f"🟢 +{change_pct}%")
        else:
            parts.append(f"🔴 {change_pct}%")

    # 技術建議
    rec = tradingview_data.get("recommendation", "N/A")
    if rec != "N/A":
        parts.append(_recommendation_display(rec))

    # RSI 狀態
    rsi = tradingview_data.get("rsi_14", "N/A")
    if rsi != "N/A":
        try:
            rsi_val = float(rsi)
            parts.append(f"RSI {rsi_val:.0f}{_rsi_label(rsi)}")
        except (ValueError, TypeError):
            pass

    # 均線趨勢
    price = finnhub_data.get("current_price", "N/A")
    ema20 = tradingview_data.get("ema_20", "N/A")
    sma50 = tradingview_data.get("sma_50", "N/A")
    sma200 = tradingview_data.get("sma_200", "N/A")
    trend = _trend_arrows(price, ema20, sma50, sma200)
    if trend:
        parts.append(trend)

    if not parts:
        return ""
    return " | ".join(parts)


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


def _format_return(val) -> str:
    """格式化報酬率。"""
    if val == "N/A" or val is None:
        return "N/A"
    try:
        v = float(val)
        emoji = "🟢" if v >= 0 else "🔴"
        return f"{emoji} {v:+.2f}%"
    except (ValueError, TypeError):
        return "N/A"


def _format_sr_section(history_data: dict) -> list[str]:
    """格式化支撐壓力位區塊。"""
    sr = history_data.get("support_resistance", {})
    if not sr:
        return []

    lines = []
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🎯 *支撐壓力位*")

    if "resistance_20d" in sr:
        lines.append(f"  短期壓力: ${_format_number(sr['resistance_20d'])}  "
                     f"支撐: ${_format_number(sr['support_20d'])}")
    if "resistance_60d" in sr:
        lines.append(f"  中期壓力: ${_format_number(sr['resistance_60d'])}  "
                     f"支撐: ${_format_number(sr['support_60d'])}")

    # 動態均線參考
    parts = []
    if "sma20" in sr:
        parts.append(f"SMA20(${_format_number(sr['sma20'])})={sr['sma20_position']}")
    if "sma50" in sr:
        parts.append(f"SMA50(${_format_number(sr['sma50'])})={sr['sma50_position']}")
    if parts:
        lines.append(f"  動態: {' | '.join(parts)}")

    return lines


def _format_history_section(history_data: dict) -> list[str]:
    """格式化歷史回測區塊。"""
    if not history_data or "error" in history_data:
        return []

    lines = []
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📉 *歷史回測*")

    # 區間報酬率
    ret_7d = _format_return(history_data.get("return_7d"))
    ret_30d = _format_return(history_data.get("return_30d"))
    ret_60d = _format_return(history_data.get("return_60d"))
    ret_90d = _format_return(history_data.get("return_90d"))

    lines.append(f"  7日: {ret_7d}  30日: {ret_30d}")
    lines.append(f"  60日: {ret_60d}  90日: {ret_90d}")

    # 波動率
    vol = history_data.get("volatility_30d", "N/A")
    if vol != "N/A":
        vol_label = "⚠️偏高" if float(vol) > 40 else ("中等" if float(vol) > 20 else "低")
        lines.append(f"  30日年化波動: {vol}% ({vol_label})")

    # 相對強弱 vs SPY
    alpha_30 = history_data.get("alpha_vs_spy_30d", "N/A")
    alpha_90 = history_data.get("alpha_vs_spy_90d", "N/A")
    if alpha_30 != "N/A" or alpha_90 != "N/A":
        lines.append("  vs SPY:")
        if alpha_30 != "N/A":
            spy_30 = history_data.get("spy_return_30d", "N/A")
            emoji_30 = "🟢" if float(alpha_30) >= 0 else "🔴"
            lines.append(f"    30日 Alpha: {emoji_30} {alpha_30:+.2f}%  (SPY: {spy_30}%)")
        if alpha_90 != "N/A":
            spy_90 = history_data.get("spy_return_90d", "N/A")
            emoji_90 = "🟢" if float(alpha_90) >= 0 else "🔴"
            lines.append(f"    90日 Alpha: {emoji_90} {alpha_90:+.2f}%  (SPY: {spy_90}%)")

    return lines


def _format_peer_section(peer_data: dict, yfinance_data: dict) -> list[str]:
    """格式化同業比較區塊。"""
    if not peer_data or "error" in peer_data:
        return []

    lines = []
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🏢 *同業比較*")

    peers_str = " ".join(peer_data.get("peers", []))
    lines.append(f"  比較對象: {peers_str}")

    # 與同業平均對比
    avg_pe = peer_data.get("sector_avg_pe", "N/A")
    my_pe = yfinance_data.get("pe_ratio", "N/A")
    if avg_pe != "N/A" and my_pe != "N/A":
        try:
            diff = ((float(my_pe) / float(avg_pe)) - 1) * 100
            hint = "偏高" if diff > 10 else ("偏低" if diff < -10 else "接近")
            lines.append(f"  PE: {_format_number(my_pe)} vs 同業 {_format_number(avg_pe)} ({hint})")
        except (ValueError, TypeError):
            pass

    avg_margin = peer_data.get("sector_avg_profit_margin", "N/A")
    my_margin = yfinance_data.get("profit_margin", "N/A")
    if avg_margin != "N/A" and my_margin != "N/A":
        try:
            avg_pct = f"{float(avg_margin) * 100:.2f}%"
            lines.append(f"  利潤率: {my_margin} vs 同業 {avg_pct}")
        except (ValueError, TypeError):
            pass

    avg_growth = peer_data.get("sector_avg_revenue_growth", "N/A")
    my_growth = yfinance_data.get("revenue_growth", "N/A")
    if avg_growth != "N/A" and my_growth != "N/A":
        try:
            avg_pct = f"{float(avg_growth) * 100:.2f}%"
            lines.append(f"  營收成長: {my_growth} vs 同業 {avg_pct}")
        except (ValueError, TypeError):
            pass

    return lines


def format_report(
    ticker: str,
    finnhub_data: dict,
    yfinance_data: dict,
    tavily_data: dict,
    tradingview_data: dict,
    ai_analysis: str,
    history_data: dict | None = None,
    peer_data: dict | None = None,
) -> str:
    """
    組裝完整的分析報告（三角色優化版 v3）。
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

    # 快速摘要（3 秒掌握重點）
    if "error" not in finnhub_data and "error" not in tradingview_data:
        summary = _quick_summary(finnhub_data, yfinance_data, tradingview_data)
        if summary:
            report_parts.append(f"⚡ {summary}")

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
                # Forward PE < Trailing PE → 市場預期未來獲利成長（EPS↑ 使 PE↓）
                pe_hint = " 📈成長預期" if float(fpe) < float(pe) else " 📉成長放緩"
        except (ValueError, TypeError):
            pass

        report_parts.append(f"  PE: {_format_number(pe)}  "
                           f"Forward PE: {_format_number(fpe)}{pe_hint}")
        report_parts.append(f"  EPS: {_safe_value(yfinance_data.get('eps'), prefix='$')}  "
                           f"PEG: {_safe_value(yfinance_data.get('peg_ratio'))}")
        report_parts.append(f"  殖利率: {yfinance_data.get('dividend_yield', 'N/A')}  "
                           f"利潤率: {yfinance_data.get('profit_margin', 'N/A')}")

        # 獲利能力
        roe = yfinance_data.get("roe", "N/A")
        roa = yfinance_data.get("roa", "N/A")
        op_margin = yfinance_data.get("operating_margin", "N/A")
        if roe != "N/A" or roa != "N/A":
            report_parts.append(f"  ROE: {roe}  ROA: {roa}  營業利潤率: {op_margin}")

        # 成長指標
        rev_growth = yfinance_data.get("revenue_growth", "N/A")
        earn_growth = yfinance_data.get("earnings_growth", "N/A")
        if rev_growth != "N/A" or earn_growth != "N/A":
            report_parts.append(f"  營收成長: {rev_growth}  盈餘成長: {earn_growth}")

        # 現金流與財務健康
        fcf = yfinance_data.get("free_cash_flow", "N/A")
        de = yfinance_data.get("debt_to_equity", "N/A")
        if fcf != "N/A" or de != "N/A":
            cr = yfinance_data.get("current_ratio", "N/A")
            report_parts.append(f"  FCF: {fcf}  D/E: {_safe_value(de)}  流動比率: {_safe_value(cr)}")

        # 估值補充
        ev_ebitda = yfinance_data.get("ev_to_ebitda", "N/A")
        pb = yfinance_data.get("price_to_book", "N/A")
        ps = yfinance_data.get("price_to_sales", "N/A")
        if ev_ebitda != "N/A" or pb != "N/A":
            report_parts.append(f"  EV/EBITDA: {_safe_value(ev_ebitda)}  P/B: {_safe_value(pb)}  P/S: {_safe_value(ps)}")

        # 籌碼面
        short_ratio = yfinance_data.get("short_ratio", "N/A")
        inst_pct = yfinance_data.get("held_pct_institutions", "N/A")
        if short_ratio != "N/A" or inst_pct != "N/A":
            report_parts.append(f"  空頭比率: {_safe_value(short_ratio)}  "
                               f"機構持股: {inst_pct}")

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

        # 均線 + 趨勢排列
        ema20 = tradingview_data.get('ema_20')
        sma50 = tradingview_data.get('sma_50')
        sma200 = tradingview_data.get('sma_200')
        report_parts.append(f"  EMA20: ${_format_number(ema20)}  "
                           f"SMA50: ${_format_number(sma50)}  "
                           f"SMA200: ${_format_number(sma200)}")

        # 均線趨勢判斷
        price = finnhub_data.get("current_price", "N/A") if "error" not in finnhub_data else "N/A"
        trend = _trend_arrows(price, ema20, sma50, sma200)
        if trend:
            report_parts.append(f"  趨勢: {trend}")

        # 布林通道
        bb_upper = tradingview_data.get("bb_upper", "N/A")
        bb_lower = tradingview_data.get("bb_lower", "N/A")
        if bb_upper != "N/A" and bb_lower != "N/A":
            report_parts.append(f"  布林: ${_format_number(bb_upper)} ~ ${_format_number(bb_lower)}")

        # 子類別建議
        ma_rec = tradingview_data.get("moving_averages", {}).get("recommendation", "N/A")
        osc_rec = tradingview_data.get("oscillators", {}).get("recommendation", "N/A")
        if ma_rec != "N/A" or osc_rec != "N/A":
            report_parts.append(f"  均線: {_recommendation_display(ma_rec)}  "
                               f"震盪: {_recommendation_display(osc_rec)}")
    else:
        report_parts.append(f"  ⚠️ {tradingview_data['error']}")

    # ══ 歷史回測 ══
    if history_data:
        report_parts.extend(_format_history_section(history_data))
        report_parts.extend(_format_sr_section(history_data))

    # ══ 同業比較 ══
    if peer_data:
        report_parts.extend(_format_peer_section(peer_data, yfinance_data))

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
