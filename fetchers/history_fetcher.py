"""
歷史數據回測模組
從 Stooq 抓日 K 線，計算近 30/60/90 天報酬率、波動率、
支撐壓力位、相對 SPY 的 alpha。
"""

import numpy as np

from fetchers.stooq_fetcher import fetch_stooq_history


async def fetch_history_analysis(ticker: str) -> dict:
    """抓取歷史數據並計算回測指標。"""
    try:
        rows = await fetch_stooq_history(ticker.upper(), days=252)
        if not rows or len(rows) < 10:
            return {
                "source": "stooq_history",
                "error": f"無法取得 {ticker.upper()} 的歷史數據",
            }

        closes = np.array([float(r["close"]) for r in rows])
        highs = np.array([float(r["high"]) for r in rows])
        lows = np.array([float(r["low"]) for r in rows])
        volumes = np.array([float(r.get("volume") or 0) for r in rows])
        current_price = float(closes[-1])

        result = {
            "source": "stooq_history",
            "ticker": ticker.upper(),
            "data_points": len(closes),
        }

        # 區間報酬率
        for days, label in [(7, "7d"), (30, "30d"), (60, "60d"), (90, "90d")]:
            if len(closes) > days:
                past_price = float(closes[-(days + 1)])
                ret = ((current_price - past_price) / past_price) * 100
                result[f"return_{label}"] = round(ret, 2)
            else:
                result[f"return_{label}"] = "N/A"

        # 波動率（年化，30 日日報酬標準差）
        if len(closes) > 30:
            daily_returns = np.diff(closes[-31:]) / closes[-31:-1]
            volatility = float(np.std(daily_returns) * np.sqrt(252) * 100)
            result["volatility_30d"] = round(volatility, 2)
        else:
            result["volatility_30d"] = "N/A"

        # 支撐壓力
        result["support_resistance"] = _calc_support_resistance(
            current_price, closes, highs, lows,
        )

        # 量能趨勢（近 5 日 vs 近 20 日平均）
        if len(volumes) >= 20:
            vol_5d = float(np.mean(volumes[-5:]))
            vol_20d = float(np.mean(volumes[-20:]))
            result["volume_trend"] = round(vol_5d / vol_20d, 2) if vol_20d > 0 else "N/A"
        else:
            result["volume_trend"] = "N/A"

        # 相對 SPY 強弱
        if ticker.upper() != "SPY":
            try:
                result.update(await _fetch_relative_strength(closes))
            except Exception:
                result["relative_strength_vs_spy"] = "N/A"

        return result

    except Exception as e:
        return {"source": "stooq_history", "error": f"歷史數據錯誤: {e}"}


async def _fetch_relative_strength(stock_closes: np.ndarray) -> dict:
    """計算個股 vs SPY 的相對強弱（30/90 日 alpha）。"""
    spy_rows = await fetch_stooq_history("SPY", days=252)
    if not spy_rows:
        return {"relative_strength_vs_spy": "N/A"}

    spy_closes = np.array([float(r["close"]) for r in spy_rows])
    min_len = min(len(stock_closes), len(spy_closes))

    out = {}
    for days, label in [(30, "30d"), (90, "90d")]:
        if min_len > days:
            stock_ret = (
                float(stock_closes[-1]) - float(stock_closes[-(days + 1)])
            ) / float(stock_closes[-(days + 1)]) * 100
            spy_ret = (
                float(spy_closes[-1]) - float(spy_closes[-(days + 1)])
            ) / float(spy_closes[-(days + 1)]) * 100
            out[f"alpha_vs_spy_{label}"] = round(stock_ret - spy_ret, 2)
            out[f"spy_return_{label}"] = round(spy_ret, 2)
        else:
            out[f"alpha_vs_spy_{label}"] = "N/A"
            out[f"spy_return_{label}"] = "N/A"

    return out


def _calc_support_resistance(
    current_price: float,
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
) -> dict:
    """近 20/60 日高低 + SMA20/SMA50 動態支撐壓力。"""
    sr = {}

    if len(lows) >= 20:
        sr["support_20d"] = round(float(np.min(lows[-20:])), 2)
        sr["resistance_20d"] = round(float(np.max(highs[-20:])), 2)

    if len(lows) >= 60:
        sr["support_60d"] = round(float(np.min(lows[-60:])), 2)
        sr["resistance_60d"] = round(float(np.max(highs[-60:])), 2)

    if len(closes) >= 20:
        sma20 = round(float(np.mean(closes[-20:])), 2)
        sr["sma20"] = sma20
        sr["sma20_position"] = "支撐" if current_price > sma20 else "壓力"

    if len(closes) >= 50:
        sma50 = round(float(np.mean(closes[-50:])), 2)
        sr["sma50"] = sma50
        sr["sma50_position"] = "支撐" if current_price > sma50 else "壓力"

    return sr
