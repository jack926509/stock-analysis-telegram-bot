"""
歷史數據回測模組
使用 yfinance 取得歷史 K 線，計算近 30/60/90 天報酬率、波動率。
同時計算支撐壓力位（基於近期高低點與均線）。
"""

import asyncio
from datetime import datetime, timedelta

import yfinance as yf
import numpy as np


async def fetch_history_analysis(ticker: str) -> dict:
    """
    抓取歷史數據並計算回測指標。

    Returns:
        dict: 包含歷史報酬率、波動率、支撐壓力位
    """
    try:
        stock = yf.Ticker(ticker.upper())

        # 取 200 天歷史數據（足夠計算 90 天報酬 + 均線）
        hist = await asyncio.to_thread(
            lambda: stock.history(period="1y", interval="1d")
        )

        if hist is None or hist.empty or len(hist) < 10:
            return {
                "source": "yfinance_history",
                "error": f"無法取得 {ticker.upper()} 的歷史數據",
            }

        closes = hist["Close"].values
        highs = hist["High"].values
        lows = hist["Low"].values
        volumes = hist["Volume"].values
        current_price = float(closes[-1])

        result = {
            "source": "yfinance_history",
            "ticker": ticker.upper(),
            "data_points": len(closes),
        }

        # ── 區間報酬率 ──
        for days, label in [(7, "7d"), (30, "30d"), (60, "60d"), (90, "90d")]:
            if len(closes) > days:
                past_price = float(closes[-(days + 1)])
                ret = ((current_price - past_price) / past_price) * 100
                result[f"return_{label}"] = round(ret, 2)
            else:
                result[f"return_{label}"] = "N/A"

        # ── 波動率（年化，基於 30 日日報酬標準差）──
        if len(closes) > 30:
            daily_returns = np.diff(closes[-31:]) / closes[-31:-1]
            volatility = float(np.std(daily_returns) * np.sqrt(252) * 100)
            result["volatility_30d"] = round(volatility, 2)
        else:
            result["volatility_30d"] = "N/A"

        # ── 支撐壓力位計算 ──
        result["support_resistance"] = _calc_support_resistance(
            current_price, closes, highs, lows
        )

        # ── 量能趨勢（近 5 日 vs 近 20 日平均）──
        if len(volumes) >= 20:
            vol_5d = float(np.mean(volumes[-5:]))
            vol_20d = float(np.mean(volumes[-20:]))
            if vol_20d > 0:
                result["volume_trend"] = round(vol_5d / vol_20d, 2)
            else:
                result["volume_trend"] = "N/A"
        else:
            result["volume_trend"] = "N/A"

        # ── 相對強弱 vs SPY（Alpha 判斷核心）──
        if ticker.upper() != "SPY":
            try:
                spy_rel = await _fetch_relative_strength(ticker, closes)
                result.update(spy_rel)
            except Exception:
                result["relative_strength_vs_spy"] = "N/A"

        return result

    except Exception as e:
        return {
            "source": "yfinance_history",
            "error": f"歷史數據錯誤: {str(e)}",
        }


async def _fetch_relative_strength(ticker: str, stock_closes: np.ndarray) -> dict:
    """
    計算個股 vs SPY 的相對強弱。
    華爾街分析師核心指標：跑贏大盤 = 有 Alpha。
    """
    try:
        spy = yf.Ticker("SPY")
        spy_hist = await asyncio.to_thread(
            lambda: spy.history(period="1y", interval="1d")
        )

        if spy_hist is None or spy_hist.empty:
            return {"relative_strength_vs_spy": "N/A"}

        spy_closes = spy_hist["Close"].values

        result = {}
        # 確保兩者長度對齊（取較短的）
        min_len = min(len(stock_closes), len(spy_closes))

        for days, label in [(30, "30d"), (90, "90d")]:
            if min_len > days:
                stock_ret = (float(stock_closes[-1]) - float(stock_closes[-(days + 1)])) / float(stock_closes[-(days + 1)]) * 100
                spy_ret = (float(spy_closes[-1]) - float(spy_closes[-(days + 1)])) / float(spy_closes[-(days + 1)]) * 100
                alpha = round(stock_ret - spy_ret, 2)
                result[f"alpha_vs_spy_{label}"] = alpha
                result[f"spy_return_{label}"] = round(spy_ret, 2)
            else:
                result[f"alpha_vs_spy_{label}"] = "N/A"
                result[f"spy_return_{label}"] = "N/A"

        return result

    except Exception:
        return {"relative_strength_vs_spy": "N/A"}


def _calc_support_resistance(
    current_price: float,
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
) -> dict:
    """
    計算支撐壓力位。
    方法：
    1. 近 20 日最低點 → 短期支撐
    2. 近 60 日最低點 → 中期支撐
    3. 近 20 日最高點 → 短期壓力
    4. 近 60 日最高點 → 中期壓力
    5. SMA20, SMA50 作為動態支撐壓力參考
    """
    sr = {}

    # 短期（20 日）
    if len(lows) >= 20:
        sr["support_20d"] = round(float(np.min(lows[-20:])), 2)
        sr["resistance_20d"] = round(float(np.max(highs[-20:])), 2)

    # 中期（60 日）
    if len(lows) >= 60:
        sr["support_60d"] = round(float(np.min(lows[-60:])), 2)
        sr["resistance_60d"] = round(float(np.max(highs[-60:])), 2)

    # 動態均線支撐壓力
    if len(closes) >= 20:
        sma20 = round(float(np.mean(closes[-20:])), 2)
        sr["sma20"] = sma20
        sr["sma20_position"] = "支撐" if current_price > sma20 else "壓力"

    if len(closes) >= 50:
        sma50 = round(float(np.mean(closes[-50:])), 2)
        sr["sma50"] = sma50
        sr["sma50_position"] = "支撐" if current_price > sma50 else "壓力"

    return sr
