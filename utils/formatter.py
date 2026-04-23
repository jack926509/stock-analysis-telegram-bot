"""
報告格式化工具（v5 版面優化版）
針對 Telegram 手機介面重新設計：
- 合併為 8 大區塊，減少視覺雜訊
- 量化共識移至頂部作為「結論先行」
- 分析師/內部人/EPS 合併為「Smart Money」
- 歷史/宏觀合併為「環境與動量」
- 支撐壓力併入技術面
"""

from datetime import datetime, timezone


def _safe(value, prefix="", suffix="") -> str:
    if value == "N/A" or value is None:
        return "N/A"
    return f"{prefix}{value}{suffix}"


def _num(value, d=2) -> str:
    if value == "N/A" or value is None:
        return "N/A"
    try:
        n = float(value)
        return f"{n:,.{d}f}" if abs(n) >= 1000 else f"{n:.{d}f}"
    except (ValueError, TypeError):
        return str(value)


def _ret(val) -> str:
    if val == "N/A" or val is None:
        return "N/A"
    try:
        v = float(val)
        return f"{'🟢' if v >= 0 else '🔴'} {v:+.1f}%"
    except (ValueError, TypeError):
        return "N/A"


def _rsi_tag(rsi) -> str:
    if rsi == "N/A" or rsi is None:
        return ""
    try:
        v = float(rsi)
        if v >= 70: return " ⚠️超買"
        if v >= 60: return " 偏強"
        if v <= 30: return " ⚠️超賣"
        if v <= 40: return " 偏弱"
        return " 中性"
    except (ValueError, TypeError):
        return ""


def _rec_cn(rec: str) -> str:
    return {
        "STRONG_BUY": "🟢 強力買入", "BUY": "🟢 買入", "NEUTRAL": "🟡 中性",
        "SELL": "🔴 賣出", "STRONG_SELL": "🔴 強力賣出",
    }.get(rec, f"⚪ {rec}")


def _trend(price, ema20, sma50, sma200) -> str:
    try:
        p, e, s5, s2 = float(price), float(ema20), float(sma50), float(sma200)
    except (ValueError, TypeError):
        return ""
    if p > e > s5 > s2: return "🟢 多頭排列"
    if p < e < s5 < s2: return "🔴 空頭排列"
    return "🟡 偏多整理" if p > s2 else "🟡 偏空整理"


def _pos_bar(cur, lo52, hi52) -> str:
    try:
        c, lo, hi = float(cur), float(lo52), float(hi52)
        if hi == lo: return ""
        pos = max(0, min(1, (c - lo) / (hi - lo)))
        filled = int(pos * 10)
        return f"[{'▓' * filled}{'░' * (10 - filled)}] {pos * 100:.0f}%"
    except (ValueError, TypeError):
        return ""


def _earnings_countdown(ds: str) -> str:
    try:
        from datetime import date
        days = (date.fromisoformat(ds) - date.today()).days
        if days < 0: return ""
        if days == 0: return " (今天!)"
        if days <= 7: return f" (剩{days}天 ⚠️)"
        return f" (剩{days}天)"
    except (ValueError, TypeError):
        return ""


# ═══ 分隔線 ═══
DIV = "─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─"
DIV_BOLD = "━━━━━━━━━━━━━━━━━━━━━━━━━━"


def format_report(
    ticker: str,
    finnhub_data: dict,
    yfinance_data: dict,
    tavily_data: dict,
    tradingview_data: dict,
    ai_analysis: str,
    history_data: dict | None = None,
    peer_data: dict | None = None,
    signals_data: dict | None = None,
    analyst_data: dict | None = None,
    insider_data: dict | None = None,
    earnings_data: dict | None = None,
    macro_data: dict | None = None,
) -> str:
    L = []

    # ════════════════════════════════════════
    # 1. HEADER + VERDICT
    # ════════════════════════════════════════
    yf = yfinance_data
    fh = finnhub_data
    tv = tradingview_data

    name = yf.get("company_name", ticker.upper())
    if name == "N/A": name = ticker.upper()
    sector = yf.get("sector", "")
    industry = yf.get("industry", "")

    L.append(f"📊 *{ticker.upper()} — {name}*")
    if sector not in ("N/A", "") and industry not in ("N/A", ""):
        L.append(f"{sector} · {industry}")

    # Quick verdict line
    parts = []
    if "error" not in fh:
        cp = fh.get("change_percent", "N/A")
        if isinstance(cp, (int, float)):
            parts.append(f"{'🟢' if cp >= 0 else '🔴'} {cp:+.2f}%")
    if "error" not in tv:
        rec = tv.get("recommendation", "N/A")
        if rec != "N/A": parts.append(_rec_cn(rec))
        rsi = tv.get("rsi_14", "N/A")
        if rsi != "N/A":
            try: parts.append(f"RSI {float(rsi):.0f}{_rsi_tag(rsi)}")
            except: pass
    if parts:
        L.append(f"⚡ {' | '.join(parts)}")

    # Signals consensus banner (結論先行)
    if signals_data:
        con = signals_data.get("consensus", "N/A")
        sc = signals_data.get("weighted_score", 0)
        conf = signals_data.get("confidence", 0)
        bc = signals_data.get("bullish_count", 0)
        brc = signals_data.get("bearish_count", 0)
        nc = signals_data.get("neutral_count", 0)
        em = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(con, "⚪")
        L.append(f"🧮 {em} *{con}*  分數 {sc:+.3f}  信心 {conf}%  ({bc}多/{brc}空/{nc}中)")

    L.append(DIV_BOLD)

    # ════════════════════════════════════════
    # 2. PRICE
    # ════════════════════════════════════════
    if "error" not in fh:
        price = fh.get("current_price", "N/A")
        cp = fh.get("change_percent", "N/A")
        chg = fh.get("change", "N/A")

        chg_str = ""
        if isinstance(cp, (int, float)):
            sign = "+" if cp >= 0 else ""
            chg_str = f"  {'🟢' if cp >= 0 else '🔴'} {sign}{cp}%"
            if isinstance(chg, (int, float)):
                chg_str += f" ({sign}${abs(chg):.2f})"

        L.append(f"💰 ${_num(price)}{chg_str}")
        L.append(f"  高 ${_num(fh.get('high'))} / 低 ${_num(fh.get('low'))} / 前收 ${_num(fh.get('previous_close'))}")

        if "error" not in yf:
            bar = _pos_bar(price, yf.get("52w_low"), yf.get("52w_high"))
            if bar:
                L.append(f"  52W {bar}  (${_num(yf.get('52w_low'))}~${_num(yf.get('52w_high'))})")
    else:
        L.append(f"💰 ⚠️ {fh.get('error', '報價不可用')}")

    # ════════════════════════════════════════
    # 3. FUNDAMENTALS (精簡)
    # ════════════════════════════════════════
    L.append("")
    L.append(f"{DIV}")
    L.append("📈 *基本面*")

    if "error" not in yf:
        L.append(f"  市值 {yf.get('market_cap', 'N/A')}  Beta {_safe(yf.get('beta'))}")

        pe = yf.get("pe_ratio", "N/A")
        fpe = yf.get("forward_pe", "N/A")
        pe_hint = ""
        try:
            if pe != "N/A" and fpe != "N/A":
                pe_hint = " 📈" if float(fpe) < float(pe) else " 📉"
        except (ValueError, TypeError):
            pass
        L.append(f"  PE {_num(pe)} → FPE {_num(fpe)}{pe_hint}  PEG {_safe(yf.get('peg_ratio'))}")
        L.append(f"  EPS ${_safe(yf.get('eps'))}  殖利率 {yf.get('dividend_yield', 'N/A')}")

        # 獲利品質 (合併為一行)
        roe = yf.get("roe", "N/A")
        margin = yf.get("profit_margin", "N/A")
        op_m = yf.get("operating_margin", "N/A")
        if roe != "N/A" or margin != "N/A":
            L.append(f"  ROE {roe}  利潤率 {margin}  營業 {op_m}")

        # 成長 (合併為一行)
        rg = yf.get("revenue_growth", "N/A")
        eg = yf.get("earnings_growth", "N/A")
        if rg != "N/A" or eg != "N/A":
            L.append(f"  營收成長 {rg}  盈餘成長 {eg}")

        # 財務健康 (合併為一行)
        fcf = yf.get("free_cash_flow", "N/A")
        de = yf.get("debt_to_equity", "N/A")
        cr = yf.get("current_ratio", "N/A")
        if fcf != "N/A" or de != "N/A":
            L.append(f"  FCF {fcf}  D/E {_safe(de)}  流動 {_safe(cr)}")

        # 補充估值 (合併為一行)
        ev = yf.get("ev_to_ebitda", "N/A")
        pb = yf.get("price_to_book", "N/A")
        ps = yf.get("price_to_sales", "N/A")
        if ev != "N/A" or pb != "N/A":
            L.append(f"  EV/EBITDA {_safe(ev)}  P/B {_safe(pb)}  P/S {_safe(ps)}")

        # 財報日
        ed = yf.get("earnings_date", "N/A")
        if ed != "N/A":
            L.append(f"  📅 財報 {ed}{_earnings_countdown(ed)}")

        # 籌碼
        sr = yf.get("short_ratio", "N/A")
        inst = yf.get("held_pct_institutions", "N/A")
        if sr != "N/A" or inst != "N/A":
            L.append(f"  空頭比 {_safe(sr)}  機構持股 {inst}")

        # 同業 (內嵌，不再獨立區塊)
        if peer_data and "error" not in peer_data:
            avg_pe = peer_data.get("sector_avg_pe", "N/A")
            if avg_pe != "N/A" and pe != "N/A":
                try:
                    diff = ((float(pe) / float(avg_pe)) - 1) * 100
                    tag = "↑偏高" if diff > 10 else ("↓偏低" if diff < -10 else "≈接近")
                    L.append(f"  vs同業PE {_num(avg_pe)} ({tag})")
                except (ValueError, TypeError):
                    pass
    else:
        L.append(f"  ⚠️ {yf.get('error', '基本面不可用')}")

    # ════════════════════════════════════════
    # 4. TECHNICALS (含量能 + 支撐壓力)
    # ════════════════════════════════════════
    L.append("")
    L.append(f"{DIV}")
    L.append("📊 *技術面*")

    if "error" not in tv:
        rec = tv.get("recommendation", "N/A")
        buy = tv.get("buy_signals", 0)
        sell = tv.get("sell_signals", 0)
        neu = tv.get("neutral_signals", 0)

        L.append(f"  建議 {_rec_cn(rec)}")

        try:
            tot = int(buy) + int(sell) + int(neu)
            if tot > 0:
                bg = "🟢" * int(int(buy) / tot * 10)
                sg = "🔴" * int(int(sell) / tot * 10)
                ng = "🟡" * (10 - len(bg) - len(sg))
                L.append(f"  {bg}{ng}{sg}  買{buy}/中{neu}/賣{sell}")
        except (ValueError, TypeError):
            pass

        rsi = tv.get("rsi_14", "N/A")
        adx = tv.get("adx", "N/A")
        adx_tag = ""
        try:
            av = float(adx)
            adx_tag = " 強趨勢" if av >= 25 else (" 盤整" if av < 20 else " 弱趨勢")
        except (ValueError, TypeError):
            pass
        L.append(f"  RSI {rsi}{_rsi_tag(rsi)}  ADX {adx}{adx_tag}")

        macd = tv.get("macd", "N/A")
        macd_s = tv.get("macd_signal", "N/A")
        macd_tag = ""
        try:
            if macd != "N/A" and macd_s != "N/A":
                macd_tag = " 金叉" if float(macd) > float(macd_s) else " 死叉"
        except (ValueError, TypeError):
            pass
        L.append(f"  MACD {macd} / Signal {macd_s}{macd_tag}")

        # 均線 + 趨勢
        ema20 = tv.get("ema_20", "N/A")
        sma50 = tv.get("sma_50", "N/A")
        sma200 = tv.get("sma_200", "N/A")
        price = fh.get("current_price", "N/A") if "error" not in fh else "N/A"
        tr = _trend(price, ema20, sma50, sma200)
        if tr:
            L.append(f"  {tr}  EMA20 ${_num(ema20)} / SMA50 ${_num(sma50)} / SMA200 ${_num(sma200)}")
        else:
            L.append(f"  EMA20 ${_num(ema20)} / SMA50 ${_num(sma50)} / SMA200 ${_num(sma200)}")

        # 布林
        bbu = tv.get("bb_upper", "N/A")
        bbl = tv.get("bb_lower", "N/A")
        if bbu != "N/A" and bbl != "N/A":
            L.append(f"  布林 ${_num(bbl)}~${_num(bbu)}")

        # 量能 (內嵌)
        if "error" not in yf:
            vol = yf.get("volume", "N/A")
            avg_vol = yf.get("avg_volume", "N/A")
            if vol != "N/A" and avg_vol != "N/A":
                vol_tag = ""
                try:
                    def _pv(v):
                        v = str(v).replace(",", "")
                        for s, m in [("B", 1e9), ("M", 1e6), ("K", 1e3)]:
                            if v.endswith(s): return float(v[:-1]) * m
                        return float(v)
                    ratio = _pv(vol) / _pv(avg_vol)
                    if ratio >= 1.5: vol_tag = " ⬆️放量"
                    elif ratio >= 1.2: vol_tag = " ↗️溫和放量"
                    elif ratio <= 0.5: vol_tag = " ⬇️縮量"
                    elif ratio <= 0.8: vol_tag = " ↘️溫和縮量"
                    else: vol_tag = ""
                    vol_tag = f" ({ratio:.1f}x{vol_tag})" if vol_tag else f" ({ratio:.1f}x)"
                except (ValueError, TypeError):
                    pass
                L.append(f"  量能 {vol} / 均量 {avg_vol}{vol_tag}")

        # 支撐壓力 (內嵌)
        if history_data and "error" not in history_data:
            sr = history_data.get("support_resistance", {})
            if sr:
                parts = []
                if "support_20d" in sr:
                    parts.append(f"支撐${_num(sr['support_20d'])}")
                if "resistance_20d" in sr:
                    parts.append(f"壓力${_num(sr['resistance_20d'])}")
                if parts:
                    L.append(f"  關鍵位 {' / '.join(parts)}")
    else:
        L.append(f"  ⚠️ {tv.get('error', '技術面不可用')}")

    # ════════════════════════════════════════
    # 5. PERFORMANCE + MACRO (歷史表現 & 環境)
    # ════════════════════════════════════════
    has_hist = history_data and "error" not in history_data
    has_macro = macro_data and "error" not in macro_data

    if has_hist or has_macro:
        L.append("")
        L.append(f"{DIV}")
        L.append("📉 *表現 & 環境*")

        if has_hist:
            r7 = _ret(history_data.get("return_7d"))
            r30 = _ret(history_data.get("return_30d"))
            r90 = _ret(history_data.get("return_90d"))
            L.append(f"  7d {r7}  30d {r30}  90d {r90}")

            vol30 = history_data.get("volatility_30d", "N/A")
            if vol30 != "N/A":
                try:
                    vl = "⚠️高" if float(vol30) > 40 else ("中" if float(vol30) > 20 else "低")
                    L.append(f"  波動率 {vol30}%({vl})")
                except (ValueError, TypeError):
                    pass

            a30 = history_data.get("alpha_vs_spy_30d", "N/A")
            a90 = history_data.get("alpha_vs_spy_90d", "N/A")
            if a30 != "N/A":
                ae = "🟢" if float(a30) >= 0 else "🔴"
                spy30 = history_data.get("spy_return_30d", "N/A")
                alpha_line = f"  vs SPY {ae} 30d Alpha {a30:+.1f}%"
                if a90 != "N/A":
                    ae9 = "🟢" if float(a90) >= 0 else "🔴"
                    alpha_line += f"  {ae9} 90d {a90:+.1f}%"
                L.append(alpha_line)

            # 量能趨勢
            vt = history_data.get("volume_trend", "N/A")
            if vt != "N/A":
                try:
                    vtf = float(vt)
                    vt_tag = "↑放量" if vtf >= 1.2 else ("↓縮量" if vtf <= 0.8 else "→持平")
                    L.append(f"  5d/20d量比 {vtf:.2f} {vt_tag}")
                except (ValueError, TypeError):
                    pass

        if has_macro:
            vix = macro_data.get("vix", "N/A")
            us10y = macro_data.get("us10y", "N/A")
            risk = macro_data.get("risk_label", "")
            risk_env = macro_data.get("risk_environment", "N/A")
            macro_parts = []
            if vix != "N/A":
                macro_parts.append(f"VIX {vix}")
            if us10y != "N/A":
                macro_parts.append(f"10Y {us10y}%")
            if macro_parts:
                re = {"risk_on": "🟢", "risk_off": "🔴"}.get(risk_env, "🟡")
                L.append(f"  🌍 {' | '.join(macro_parts)}  {re} {risk}")

    # ════════════════════════════════════════
    # 6. SMART MONEY (分析師 + 內部人 + EPS)
    # ════════════════════════════════════════
    has_analyst = analyst_data and "error" not in analyst_data and analyst_data.get("total_analysts", 0) > 0
    has_insider = insider_data and "error" not in insider_data
    has_eps = earnings_data and "error" not in earnings_data and earnings_data.get("total_quarters", 0) > 0

    if has_analyst or has_insider or has_eps:
        L.append("")
        L.append(f"{DIV}")
        L.append("🏦 *Smart Money*")

        if has_analyst:
            con = analyst_data.get("consensus", "N/A")
            tot = analyst_data.get("total_analysts", 0)
            ae = {"strongBuy": "🟢", "buy": "🟢", "hold": "🟡", "sell": "🔴", "strongSell": "🔴"}.get(con, "⚪")
            sb = analyst_data.get("strong_buy", 0)
            b = analyst_data.get("buy", 0)
            h = analyst_data.get("hold", 0)
            s = analyst_data.get("sell", 0)
            ss = analyst_data.get("strong_sell", 0)
            L.append(f"  {ae} 分析師 {con} ({tot}位)  強買{sb}/買{b}/持{h}/賣{s}/強賣{ss}")

            tm = analyst_data.get("target_median", "N/A")
            tl = analyst_data.get("target_low", "N/A")
            th = analyst_data.get("target_high", "N/A")
            if tm != "N/A":
                # 計算 upside/downside
                upside_str = ""
                cur_price = fh.get("current_price", "N/A") if "error" not in fh else "N/A"
                if cur_price != "N/A" and tm != "N/A":
                    try:
                        upside = ((float(tm) - float(cur_price)) / float(cur_price)) * 100
                        upside_str = f" ({'🟢' if upside >= 0 else '🔴'} {upside:+.1f}%)"
                    except (ValueError, TypeError):
                        pass
                L.append(f"  🎯 目標 ${_num(tm)}{upside_str}  (${_num(tl)}~${_num(th)})")

        if has_insider:
            total_tx = insider_data.get("total_transactions", 0)
            if total_tx > 0:
                sent = insider_data.get("net_sentiment", "neutral")
                ie = {"bullish": "🟢", "bearish": "🔴"}.get(sent, "🟡")
                bc = insider_data.get("buy_count", 0)
                sc = insider_data.get("sell_count", 0)
                L.append(f"  {ie} 內部人 {sent}  買{bc}/賣{sc}筆(90天)")

                notable = insider_data.get("notable_transactions", [])
                if notable:
                    tx = notable[0]
                    L.append(f"    最大: {tx['type']} {tx['name']} ${_num(tx['value_usd'], 0)}")

        if has_eps:
            track = earnings_data.get("track_record", "N/A")
            beat = earnings_data.get("beat_count", 0)
            total_q = earnings_data.get("total_quarters", 0)
            te = {"excellent": "🟢", "good": "🟢", "poor": "🔴", "mixed": "🟡"}.get(track, "⚪")
            L.append(f"  {te} EPS紀錄 {track} (Beat {beat}/{total_q}季)")

            quarters = earnings_data.get("quarters", [])
            if quarters:
                q_parts = []
                for q in quarters[:4]:
                    sp = q.get("surprise_pct", "N/A")
                    if sp != "N/A":
                        q_parts.append(f"{'✓' if sp > 0 else '✗'}{sp:+.0f}%")
                if q_parts:
                    L.append(f"    近4季: {' | '.join(q_parts)}")

    # ════════════════════════════════════════
    # 7. SIGNALS DETAIL (量化信號明細)
    # ════════════════════════════════════════
    if signals_data:
        signals = signals_data.get("signals", [])
        if signals:
            L.append("")
            L.append(f"{DIV}")
            L.append("🧮 *量化信號明細*")
            for s in signals:
                se = {"bullish": "🟢", "bearish": "🔴"}.get(s.get("signal"), "🟡")
                reason = s.get("reason", "")
                if len(reason) > 40:
                    reason = reason[:37] + "..."
                L.append(f"  {se} {s['name']}: {reason}")

    # ════════════════════════════════════════
    # 8. NEWS
    # ════════════════════════════════════════
    L.append("")
    L.append(f"{DIV}")
    L.append("📰 *新聞*")

    if "error" not in tavily_data:
        ai_sum = tavily_data.get("ai_summary", "")
        if ai_sum and ai_sum != "無法取得新聞摘要":
            summary = ai_sum[:150] + "..." if len(ai_sum) > 150 else ai_sum
            L.append(f"  {summary}")

        news = tavily_data.get("news", [])
        if news:
            for i, n in enumerate(news[:3], 1):
                title = n.get("title", "N/A")
                url = n.get("url", "#")
                L.append(f"  {i}. [{title}]({url})")
        else:
            L.append("  暫無相關新聞")
    else:
        L.append(f"  ⚠️ {tavily_data.get('error', '新聞不可用')}")

    # ════════════════════════════════════════
    # 9. AI ANALYSIS
    # ════════════════════════════════════════
    L.append("")
    L.append(DIV_BOLD)
    L.append("🤖 *AI 四觀點深度分析*")
    L.append(DIV_BOLD)
    L.append("")
    L.append(ai_analysis)

    # ════════════════════════════════════════
    # FOOTER
    # ════════════════════════════════════════
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L.append("")
    L.append(DIV_BOLD)
    L.append("⚠️ 僅供參考研究，不構成投資建議。")
    L.append(f"📅 {now} | 🛡️ Zero-Hallucination Engine v4.2")

    return "\n".join(L)
