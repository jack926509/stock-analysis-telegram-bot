"""
量化信號引擎（靈感來自 ai-hedge-fund）
純 Python 規則引擎，不需 LLM。
根據基本面、技術面、動量、波動率計算結構化多空信號。
"""


def _signal(name: str, value: str, score: float, reason: str) -> dict:
    return {"name": name, "signal": value, "score": round(score, 2), "reason": reason}


# ═══════════════════════════════════════════
# 基本面信號
# ═══════════════════════════════════════════

def _profitability_signal(yf: dict) -> dict:
    pts = 0
    reasons = []

    roe = yf.get("roe", "N/A")
    if roe != "N/A":
        try:
            v = float(str(roe).replace("%", ""))
            if v > 15:
                pts += 1
                reasons.append(f"ROE {v:.1f}%>15%")
            elif v < 5:
                pts -= 1
                reasons.append(f"ROE {v:.1f}%<5%")
        except (ValueError, TypeError):
            pass

    margin = yf.get("profit_margin", "N/A")
    if margin != "N/A":
        try:
            v = float(str(margin).replace("%", ""))
            if v > 20:
                pts += 1
                reasons.append(f"利潤率{v:.1f}%>20%")
            elif v < 5:
                pts -= 1
                reasons.append(f"利潤率{v:.1f}%<5%")
        except (ValueError, TypeError):
            pass

    op_margin = yf.get("operating_margin", "N/A")
    if op_margin != "N/A":
        try:
            v = float(str(op_margin).replace("%", ""))
            if v > 15:
                pts += 1
                reasons.append(f"營業利潤率{v:.1f}%>15%")
        except (ValueError, TypeError):
            pass

    if pts >= 2:
        return _signal("獲利能力", "bullish", 1.0, " | ".join(reasons))
    elif pts <= -1:
        return _signal("獲利能力", "bearish", -1.0, " | ".join(reasons))
    return _signal("獲利能力", "neutral", 0.0, " | ".join(reasons) or "數據不足")


def _growth_signal(yf: dict) -> dict:
    pts = 0
    reasons = []

    for key, label in [("revenue_growth", "營收成長"), ("earnings_growth", "盈餘成長")]:
        val = yf.get(key, "N/A")
        if val != "N/A":
            try:
                v = float(str(val).replace("%", ""))
                if v > 10:
                    pts += 1
                    reasons.append(f"{label}{v:.1f}%>10%")
                elif v < -5:
                    pts -= 1
                    reasons.append(f"{label}{v:.1f}%<-5%")
            except (ValueError, TypeError):
                pass

    if pts >= 2:
        return _signal("成長動能", "bullish", 1.0, " | ".join(reasons))
    elif pts <= -1:
        return _signal("成長動能", "bearish", -1.0, " | ".join(reasons))
    return _signal("成長動能", "neutral", 0.0, " | ".join(reasons) or "數據不足")


def _health_signal(yf: dict) -> dict:
    pts = 0
    reasons = []

    de = yf.get("debt_to_equity", "N/A")
    if de != "N/A":
        try:
            v = float(de)
            if v < 50:
                pts += 1
                reasons.append(f"D/E {v:.1f}<50")
            elif v > 150:
                pts -= 1
                reasons.append(f"D/E {v:.1f}>150")
        except (ValueError, TypeError):
            pass

    cr = yf.get("current_ratio", "N/A")
    if cr != "N/A":
        try:
            v = float(cr)
            if v > 1.5:
                pts += 1
                reasons.append(f"流動比率{v:.1f}>1.5")
            elif v < 1.0:
                pts -= 1
                reasons.append(f"流動比率{v:.1f}<1")
        except (ValueError, TypeError):
            pass

    fcf = yf.get("free_cash_flow", "N/A")
    if fcf != "N/A":
        try:
            v = _parse_number(fcf)
            if v and v > 0:
                pts += 1
                reasons.append("FCF為正")
            elif v and v < 0:
                pts -= 1
                reasons.append("FCF為負")
        except (ValueError, TypeError):
            pass

    if pts >= 2:
        return _signal("財務健康", "bullish", 1.0, " | ".join(reasons))
    elif pts <= -1:
        return _signal("財務健康", "bearish", -1.0, " | ".join(reasons))
    return _signal("財務健康", "neutral", 0.0, " | ".join(reasons) or "數據不足")


def _valuation_signal(yf: dict, peer: dict | None) -> dict:
    pts = 0
    reasons = []

    pe = yf.get("pe_ratio", "N/A")
    fpe = yf.get("forward_pe", "N/A")
    if pe != "N/A":
        try:
            v = float(pe)
            if v < 15:
                pts += 1
                reasons.append(f"PE {v:.1f}<15")
            elif v > 35:
                pts -= 1
                reasons.append(f"PE {v:.1f}>35")
        except (ValueError, TypeError):
            pass

    if fpe != "N/A" and pe != "N/A":
        try:
            fp, tp = float(fpe), float(pe)
            if fp < tp:
                pts += 1
                reasons.append("Forward PE<Trailing PE(成長預期)")
        except (ValueError, TypeError):
            pass

    peg = yf.get("peg_ratio", "N/A")
    if peg != "N/A":
        try:
            v = float(peg)
            if v < 1:
                pts += 1
                reasons.append(f"PEG {v:.2f}<1(低估)")
            elif v > 2:
                pts -= 1
                reasons.append(f"PEG {v:.2f}>2(偏貴)")
        except (ValueError, TypeError):
            pass

    if peer and "error" not in peer:
        avg_pe = peer.get("sector_avg_pe", "N/A")
        if avg_pe != "N/A" and pe != "N/A":
            try:
                my_pe, sec_pe = float(pe), float(avg_pe)
                if my_pe < sec_pe * 0.8:
                    pts += 1
                    reasons.append(f"PE低於同業20%+")
                elif my_pe > sec_pe * 1.3:
                    pts -= 1
                    reasons.append(f"PE高於同業30%+")
            except (ValueError, TypeError):
                pass

    if pts >= 2:
        return _signal("估值", "bullish", 1.0, " | ".join(reasons))
    elif pts <= -1:
        return _signal("估值", "bearish", -1.0, " | ".join(reasons))
    return _signal("估值", "neutral", 0.0, " | ".join(reasons) or "數據不足")


# ═══════════════════════════════════════════
# 技術面信號
# ═══════════════════════════════════════════

def _trend_signal(tv: dict, fh: dict) -> dict:
    pts = 0
    reasons = []

    try:
        price = float(fh.get("current_price", 0))
        ema20 = float(tv.get("ema_20", 0))
        sma50 = float(tv.get("sma_50", 0))
        sma200 = float(tv.get("sma_200", 0))

        if price and ema20 and sma50 and sma200:
            if price > ema20 > sma50 > sma200:
                pts += 2
                reasons.append("完美多頭排列")
            elif price < ema20 < sma50 < sma200:
                pts -= 2
                reasons.append("完美空頭排列")
            elif price > sma200:
                pts += 1
                reasons.append("站穩200MA上方")
            else:
                pts -= 1
                reasons.append("跌破200MA")
    except (ValueError, TypeError):
        pass

    adx = tv.get("adx", "N/A")
    if adx != "N/A":
        try:
            v = float(adx)
            if v > 25:
                reasons.append(f"ADX {v:.0f}趨勢確立")
        except (ValueError, TypeError):
            pass

    if pts >= 2:
        return _signal("趨勢", "bullish", 1.0, " | ".join(reasons))
    elif pts <= -1:
        return _signal("趨勢", "bearish", -1.0, " | ".join(reasons))
    return _signal("趨勢", "neutral", 0.0, " | ".join(reasons) or "數據不足")


def _momentum_signal(tv: dict, hist: dict | None) -> dict:
    pts = 0
    reasons = []

    rsi = tv.get("rsi_14", "N/A")
    if rsi != "N/A":
        try:
            v = float(rsi)
            if v > 70:
                pts -= 1
                reasons.append(f"RSI {v:.0f} 超買")
            elif v < 30:
                pts += 1
                reasons.append(f"RSI {v:.0f} 超賣(反彈機會)")
            elif 50 < v < 65:
                pts += 1
                reasons.append(f"RSI {v:.0f} 偏多")
        except (ValueError, TypeError):
            pass

    macd = tv.get("macd", "N/A")
    macd_sig = tv.get("macd_signal", "N/A")
    if macd != "N/A" and macd_sig != "N/A":
        try:
            m, s = float(macd), float(macd_sig)
            if m > s:
                pts += 1
                reasons.append("MACD金叉")
            else:
                pts -= 1
                reasons.append("MACD死叉")
        except (ValueError, TypeError):
            pass

    if hist and "error" not in hist:
        r7 = hist.get("return_7d", "N/A")
        r30 = hist.get("return_30d", "N/A")
        if r7 != "N/A" and r30 != "N/A":
            try:
                r7v, r30v = float(r7), float(r30)
                if r7v > 0 and r7v > r30v:
                    pts += 1
                    reasons.append("短期動能加速")
                elif r7v < 0 and r7v < r30v:
                    pts -= 1
                    reasons.append("短期動能衰減")
            except (ValueError, TypeError):
                pass

    if pts >= 2:
        return _signal("動量", "bullish", 1.0, " | ".join(reasons))
    elif pts <= -1:
        return _signal("動量", "bearish", -1.0, " | ".join(reasons))
    return _signal("動量", "neutral", 0.0, " | ".join(reasons) or "數據不足")


def _volatility_signal(hist: dict | None) -> dict:
    if not hist or "error" in hist:
        return _signal("波動率", "neutral", 0.0, "數據不足")

    vol = hist.get("volatility_30d", "N/A")
    if vol == "N/A":
        return _signal("波動率", "neutral", 0.0, "數據不足")

    try:
        v = float(vol)
        if v < 20:
            return _signal("波動率", "bullish", 0.5, f"低波動{v:.0f}%(穩定)")
        elif v > 50:
            return _signal("波動率", "bearish", -0.5, f"高波動{v:.0f}%(風險高)")
        return _signal("波動率", "neutral", 0.0, f"中等波動{v:.0f}%")
    except (ValueError, TypeError):
        return _signal("波動率", "neutral", 0.0, "數據不足")


# ═══════════════════════════════════════════
# 情緒 / 催化劑信號
# ═══════════════════════════════════════════

def _sentiment_signal(tv: dict, analyst: dict | None) -> dict:
    pts = 0
    reasons = []

    rec = tv.get("recommendation", "N/A")
    if rec in ("STRONG_BUY", "BUY"):
        pts += 1
        reasons.append(f"技術面:{rec}")
    elif rec in ("STRONG_SELL", "SELL"):
        pts -= 1
        reasons.append(f"技術面:{rec}")

    buy = tv.get("buy_signals", 0)
    sell = tv.get("sell_signals", 0)
    try:
        b, s = int(buy), int(sell)
        if b > 0 and s > 0:
            ratio = b / (b + s)
            if ratio > 0.65:
                pts += 1
                reasons.append(f"買入信號佔{ratio:.0%}")
            elif ratio < 0.35:
                pts -= 1
                reasons.append(f"賣出信號佔{1 - ratio:.0%}")
    except (ValueError, TypeError):
        pass

    if analyst and "error" not in analyst:
        consensus = analyst.get("consensus", "N/A")
        if consensus in ("buy", "strongBuy"):
            pts += 1
            reasons.append(f"分析師共識:{consensus}")
        elif consensus in ("sell", "strongSell"):
            pts -= 1
            reasons.append(f"分析師共識:{consensus}")

    if pts >= 2:
        return _signal("市場情緒", "bullish", 1.0, " | ".join(reasons))
    elif pts <= -1:
        return _signal("市場情緒", "bearish", -1.0, " | ".join(reasons))
    return _signal("市場情緒", "neutral", 0.0, " | ".join(reasons) or "數據不足")


# ═══════════════════════════════════════════
# 新增：內部人 / EPS 紀錄 / 宏觀 / 相對強弱
# ═══════════════════════════════════════════

def _insider_signal(insider: dict | None) -> dict:
    if not insider or "error" in insider:
        return _signal("內部人動向", "neutral", 0.0, "數據不足")

    total_tx = insider.get("total_transactions", 0)
    if not total_tx:
        return _signal("內部人動向", "neutral", 0.0, "近 90 天無內部人交易")

    sentiment = insider.get("net_sentiment", "neutral")
    bv = insider.get("buy_value", 0) or 0
    sv = insider.get("sell_value", 0) or 0
    bc = insider.get("buy_count", 0) or 0
    sc = insider.get("sell_count", 0) or 0

    reason = f"買{bc}筆(${bv:,.0f})/賣{sc}筆(${sv:,.0f})"
    if sentiment == "bullish":
        return _signal("內部人動向", "bullish", 1.0, f"內部人淨買入 | {reason}")
    if sentiment == "bearish":
        return _signal("內部人動向", "bearish", -1.0, f"內部人淨賣出 | {reason}")
    return _signal("內部人動向", "neutral", 0.0, reason)


def _earnings_consistency_signal(earnings: dict | None) -> dict:
    if not earnings or "error" in earnings:
        return _signal("EPS 紀錄", "neutral", 0.0, "數據不足")

    total = earnings.get("total_quarters", 0) or 0
    if total == 0:
        return _signal("EPS 紀錄", "neutral", 0.0, "無 EPS 歷史")

    beat = earnings.get("beat_count", 0) or 0
    miss = earnings.get("miss_count", 0) or 0
    track = earnings.get("track_record", "mixed")
    reason = f"{beat}/{total} 季超預期"

    if track in ("excellent",):
        return _signal("EPS 紀錄", "bullish", 1.0, f"連續達標 | {reason}")
    if track == "good":
        return _signal("EPS 紀錄", "bullish", 0.5, reason)
    if track == "poor" or miss >= total * 0.75:
        return _signal("EPS 紀錄", "bearish", -1.0, f"常態低於預期 | {reason}")
    return _signal("EPS 紀錄", "neutral", 0.0, reason)


def _macro_signal(macro: dict | None) -> dict:
    if not macro or "error" in macro:
        return _signal("宏觀環境", "neutral", 0.0, "數據不足")

    regime = macro.get("risk_environment", "N/A")
    vix = macro.get("vix", "N/A")
    us10y = macro.get("us10y", "N/A")
    label = macro.get("risk_label", "")

    parts = []
    if vix != "N/A":
        parts.append(f"VIX {vix}")
    if us10y != "N/A":
        parts.append(f"10Y {us10y}%")
    if label:
        parts.append(label)
    reason = " | ".join(parts) if parts else "數據不足"

    if regime == "risk_on":
        return _signal("宏觀環境", "bullish", 1.0, reason)
    if regime == "risk_off":
        return _signal("宏觀環境", "bearish", -1.0, reason)
    return _signal("宏觀環境", "neutral", 0.0, reason)


def _relative_strength_signal(hist: dict | None) -> dict:
    if not hist or "error" in hist:
        return _signal("相對強弱", "neutral", 0.0, "數據不足")

    a30 = hist.get("alpha_vs_spy_30d", "N/A")
    a90 = hist.get("alpha_vs_spy_90d", "N/A")

    pts = 0
    reasons = []
    try:
        if a30 != "N/A":
            v = float(a30)
            reasons.append(f"30d Alpha {v:+.1f}%")
            if v > 5:
                pts += 1
            elif v < -5:
                pts -= 1
    except (ValueError, TypeError):
        pass

    try:
        if a90 != "N/A":
            v = float(a90)
            reasons.append(f"90d Alpha {v:+.1f}%")
            if v > 10:
                pts += 1
            elif v < -10:
                pts -= 1
    except (ValueError, TypeError):
        pass

    reason = " | ".join(reasons) or "數據不足"
    if pts >= 2:
        return _signal("相對強弱", "bullish", 1.0, f"持續跑贏大盤 | {reason}")
    if pts >= 1:
        return _signal("相對強弱", "bullish", 0.5, reason)
    if pts <= -2:
        return _signal("相對強弱", "bearish", -1.0, f"持續跑輸大盤 | {reason}")
    if pts <= -1:
        return _signal("相對強弱", "bearish", -0.5, reason)
    return _signal("相對強弱", "neutral", 0.0, reason)


# ═══════════════════════════════════════════
# 主函數：計算所有信號 + 共識投票
# ═══════════════════════════════════════════

WEIGHTS = {
    "獲利能力": 0.12,
    "成長動能": 0.08,
    "財務健康": 0.08,
    "估值": 0.12,
    "趨勢": 0.12,
    "動量": 0.10,
    "波動率": 0.03,
    "市場情緒": 0.10,
    "內部人動向": 0.08,
    "EPS 紀錄": 0.07,
    "宏觀環境": 0.05,
    "相對強弱": 0.05,
}


def compute_signals(
    finnhub_data: dict,
    fundamentals_data: dict,
    tradingview_data: dict,
    history_data: dict | None = None,
    peer_data: dict | None = None,
    analyst_data: dict | None = None,
    insider_data: dict | None = None,
    earnings_data: dict | None = None,
    macro_data: dict | None = None,
) -> dict:
    """
    計算量化信號共識（12 維度）。

    Returns:
        dict: {
            "consensus": "BULLISH" | "BEARISH" | "NEUTRAL",
            "confidence": 0-100,
            "weighted_score": -1.0 ~ 1.0,
            "signals": [每個子信號的詳細資訊],
            "bullish_count": int,
            "bearish_count": int,
        }
    """
    signals = [
        _profitability_signal(fundamentals_data),
        _growth_signal(fundamentals_data),
        _health_signal(fundamentals_data),
        _valuation_signal(fundamentals_data, peer_data),
        _trend_signal(tradingview_data, finnhub_data),
        _momentum_signal(tradingview_data, history_data),
        _volatility_signal(history_data),
        _sentiment_signal(tradingview_data, analyst_data),
        _insider_signal(insider_data),
        _earnings_consistency_signal(earnings_data),
        _macro_signal(macro_data),
        _relative_strength_signal(history_data),
    ]

    bullish = sum(1 for s in signals if s["signal"] == "bullish")
    bearish = sum(1 for s in signals if s["signal"] == "bearish")

    weighted_score = sum(
        s["score"] * WEIGHTS.get(s["name"], 0.1) for s in signals
    )

    if weighted_score > 0.2:
        consensus = "BULLISH"
    elif weighted_score < -0.2:
        consensus = "BEARISH"
    else:
        consensus = "NEUTRAL"

    total = len(signals)
    dominant = max(bullish, bearish)
    confidence = int((dominant / total) * 100) if total > 0 else 0

    return {
        "consensus": consensus,
        "confidence": confidence,
        "weighted_score": round(weighted_score, 3),
        "signals": signals,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": total - bullish - bearish,
    }


def _parse_number(value) -> float | None:
    if value is None or value == "N/A":
        return None
    s = str(value).replace(",", "")
    try:
        if s.endswith("T"):
            return float(s[:-1]) * 1e12
        if s.endswith("B"):
            return float(s[:-1]) * 1e9
        if s.endswith("M"):
            return float(s[:-1]) * 1e6
        if s.endswith("K"):
            return float(s[:-1]) * 1e3
        return float(s)
    except (ValueError, TypeError):
        return None
