"""
TradingView 技術指標抓取模組
使用 tradingview-ta 取得技術分析指標。
"""

import asyncio

from tradingview_ta import TA_Handler, Interval


# 常見美股交易所對照
EXCHANGES = ["NASDAQ", "NYSE", "AMEX"]


async def fetch_tradingview_analysis(ticker: str) -> dict:
    """
    非同步抓取 TradingView 技術指標。

    會嘗試多個交易所，直到找到有效數據。

    Args:
        ticker: 股票代碼（如 AAPL）

    Returns:
        dict: 包含技術指標數據
    """

    def _fetch():
        for exchange in EXCHANGES:
            try:
                handler = TA_Handler(
                    symbol=ticker.upper(),
                    screener="america",
                    exchange=exchange,
                    interval=Interval.INTERVAL_1_DAY,
                )
                analysis = handler.get_analysis()

                indicators = analysis.indicators
                summary = analysis.summary

                return {
                    "source": "TradingView",
                    "ticker": ticker.upper(),
                    "exchange": exchange,
                    "recommendation": summary.get("RECOMMENDATION", "N/A"),
                    "buy_signals": summary.get("BUY", "N/A"),
                    "sell_signals": summary.get("SELL", "N/A"),
                    "neutral_signals": summary.get("NEUTRAL", "N/A"),
                    "rsi_14": (
                        round(indicators.get("RSI", 0), 2)
                        if indicators.get("RSI") is not None
                        else "N/A"
                    ),
                    "macd": (
                        round(indicators.get("MACD.macd", 0), 4)
                        if indicators.get("MACD.macd") is not None
                        else "N/A"
                    ),
                    "macd_signal": (
                        round(indicators.get("MACD.signal", 0), 4)
                        if indicators.get("MACD.signal") is not None
                        else "N/A"
                    ),
                    "ema_20": (
                        round(indicators.get("EMA20", 0), 2)
                        if indicators.get("EMA20") is not None
                        else "N/A"
                    ),
                    "sma_50": (
                        round(indicators.get("SMA50", 0), 2)
                        if indicators.get("SMA50") is not None
                        else "N/A"
                    ),
                    "sma_200": (
                        round(indicators.get("SMA200", 0), 2)
                        if indicators.get("SMA200") is not None
                        else "N/A"
                    ),
                    "adx": (
                        round(indicators.get("ADX", 0), 2)
                        if indicators.get("ADX") is not None
                        else "N/A"
                    ),
                    "stoch_k": (
                        round(indicators.get("Stoch.K", 0), 2)
                        if indicators.get("Stoch.K") is not None
                        else "N/A"
                    ),
                    "stoch_d": (
                        round(indicators.get("Stoch.D", 0), 2)
                        if indicators.get("Stoch.D") is not None
                        else "N/A"
                    ),
                    "bb_upper": (
                        round(indicators.get("BB.upper", 0), 2)
                        if indicators.get("BB.upper") is not None
                        else "N/A"
                    ),
                    "bb_lower": (
                        round(indicators.get("BB.lower", 0), 2)
                        if indicators.get("BB.lower") is not None
                        else "N/A"
                    ),
                    "atr": (
                        round(indicators.get("ATR", 0), 4)
                        if indicators.get("ATR") is not None
                        else "N/A"
                    ),
                    "moving_averages": {
                        "recommendation": (
                            analysis.moving_averages.get("RECOMMENDATION", "N/A")
                            if hasattr(analysis, "moving_averages")
                            else "N/A"
                        ),
                        "buy": (
                            analysis.moving_averages.get("BUY", "N/A")
                            if hasattr(analysis, "moving_averages")
                            else "N/A"
                        ),
                        "sell": (
                            analysis.moving_averages.get("SELL", "N/A")
                            if hasattr(analysis, "moving_averages")
                            else "N/A"
                        ),
                    },
                    "oscillators": {
                        "recommendation": (
                            analysis.oscillators.get("RECOMMENDATION", "N/A")
                            if hasattr(analysis, "oscillators")
                            else "N/A"
                        ),
                        "buy": (
                            analysis.oscillators.get("BUY", "N/A")
                            if hasattr(analysis, "oscillators")
                            else "N/A"
                        ),
                        "sell": (
                            analysis.oscillators.get("SELL", "N/A")
                            if hasattr(analysis, "oscillators")
                            else "N/A"
                        ),
                    },
                }
            except Exception:
                continue

        # 所有交易所都失敗
        return None

    try:
        result = await asyncio.to_thread(_fetch)
        if result:
            return result
        return {
            "source": "TradingView",
            "error": f"無法取得 {ticker.upper()} 的技術指標（嘗試交易所: {', '.join(EXCHANGES)}）",
        }

    except Exception as e:
        return {
            "source": "TradingView",
            "error": f"TradingView 技術分析錯誤: {str(e)}",
        }
