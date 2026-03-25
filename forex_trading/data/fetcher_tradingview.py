"""
TradingView XAUUSD 技術指標抓取模組
使用 tradingview-ta 取得外匯市場技術分析指標。
"""

import asyncio
import logging

from tradingview_ta import TA_Handler, Interval

from forex_trading.config import ForexConfig

logger = logging.getLogger(__name__)

# 時間框架對照
INTERVAL_MAP = {
    "1h": Interval.INTERVAL_1_HOUR,
    "4h": Interval.INTERVAL_4_HOURS,
    "1d": Interval.INTERVAL_1_DAY,
}


async def fetch_xauusd_analysis(interval: str = "1h") -> dict | None:
    """
    抓取 XAUUSD TradingView 技術分析。

    Args:
        interval: "1h", "4h", "1d"

    Returns:
        dict: 包含建議、指標值
    """
    def _fetch():
        tv_interval = INTERVAL_MAP.get(interval, Interval.INTERVAL_1_HOUR)

        try:
            handler = TA_Handler(
                symbol=ForexConfig.TV_SYMBOL,
                screener=ForexConfig.TV_SCREENER,
                exchange=ForexConfig.TV_EXCHANGE,
                interval=tv_interval,
            )
            analysis = handler.get_analysis()

            indicators = analysis.indicators
            summary = analysis.summary

            def _safe_round(key, decimals=2):
                val = indicators.get(key)
                return round(val, decimals) if val is not None else None

            return {
                "interval": interval,
                "recommendation": summary.get("RECOMMENDATION", "N/A"),
                "buy_signals": summary.get("BUY", 0),
                "sell_signals": summary.get("SELL", 0),
                "neutral_signals": summary.get("NEUTRAL", 0),
                "rsi_14": _safe_round("RSI"),
                "macd": _safe_round("MACD.macd", 4),
                "macd_signal": _safe_round("MACD.signal", 4),
                "ema_9": _safe_round("EMA9"),
                "ema_20": _safe_round("EMA20"),
                "ema_21": _safe_round("EMA20"),  # TV 沒有 EMA21，用 EMA20 近似
                "ema_50": _safe_round("EMA50"),
                "sma_50": _safe_round("SMA50"),
                "sma_200": _safe_round("SMA200"),
                "adx": _safe_round("ADX"),
                "adi_plus": _safe_round("ADX+DI"),
                "adi_minus": _safe_round("ADX-DI"),
                "atr": _safe_round("ATR", 4),
                "stoch_k": _safe_round("Stoch.K"),
                "stoch_d": _safe_round("Stoch.D"),
                "bb_upper": _safe_round("BB.upper"),
                "bb_lower": _safe_round("BB.lower"),
                "close": _safe_round("close"),
                "open": _safe_round("open"),
                "high": _safe_round("high"),
                "low": _safe_round("low"),
                "volume": indicators.get("volume"),
                "moving_averages": {
                    "recommendation": (
                        analysis.moving_averages.get("RECOMMENDATION", "N/A")
                        if hasattr(analysis, "moving_averages") else "N/A"
                    ),
                },
                "oscillators": {
                    "recommendation": (
                        analysis.oscillators.get("RECOMMENDATION", "N/A")
                        if hasattr(analysis, "oscillators") else "N/A"
                    ),
                },
            }
        except Exception as e:
            logger.error(f"TradingView XAUUSD ({interval}) 分析失敗: {e}")
            return None

    return await asyncio.to_thread(_fetch)


async def fetch_multi_timeframe_analysis() -> dict:
    """
    抓取多時間框架分析（1H + 4H + 1D）。

    Returns:
        dict: {"1h": {...}, "4h": {...}, "1d": {...}}
    """
    results = await asyncio.gather(
        fetch_xauusd_analysis("1h"),
        fetch_xauusd_analysis("4h"),
        fetch_xauusd_analysis("1d"),
        return_exceptions=True,
    )

    output = {}
    for interval, result in zip(["1h", "4h", "1d"], results):
        if isinstance(result, Exception):
            logger.warning(f"TradingView {interval} 失敗: {result}")
            output[interval] = None
        else:
            output[interval] = result

    return output
