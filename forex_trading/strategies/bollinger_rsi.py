"""
策略 3: 布林通道均值回歸 + RSI
價格觸及布林帶邊緣且 RSI 超買/超賣時進場，適用盤整市場。
"""

import logging
from datetime import datetime

import pandas as pd

from forex_trading.indicators.technical import (
    bollinger_bands, rsi, adx, atr, detect_rsi_divergence,
)
from forex_trading.strategies.base import Strategy, Signal

logger = logging.getLogger(__name__)


class BollingerRSIStrategy(Strategy):
    """布林通道均值回歸 + RSI 策略。"""

    name = "bollinger_rsi"

    def analyze(
        self,
        gold_df: pd.DataFrame,
        dxy_df: pd.DataFrame | None,
        current_time: datetime,
    ) -> Signal | None:
        if gold_df is None or len(gold_df) < 50:
            return None

        close = gold_df["Close"]
        high = gold_df["High"]
        low = gold_df["Low"]

        # 布林通道
        bb_upper, bb_middle, bb_lower = bollinger_bands(close, 20, 2.0)

        # RSI
        rsi_values = rsi(close, 14)

        # ADX 過濾：只在盤整市場交易（ADX < 20）
        adx_values, _, _ = adx(high, low, close, 14)

        # ATR 用於停損
        atr_values = atr(high, low, close, 14)

        current_price = float(close.iloc[-1])
        current_upper = float(bb_upper.iloc[-1])
        current_middle = float(bb_middle.iloc[-1])
        current_lower = float(bb_lower.iloc[-1])
        current_rsi = float(rsi_values.iloc[-1])
        current_adx = float(adx_values.iloc[-1])
        current_atr = float(atr_values.iloc[-1])

        # 過濾：ADX > 25 表示趨勢太強，不適合均值回歸
        if current_adx > 25:
            return None

        direction = None
        confidence = 0.5

        # 做多：價格觸及下軌 + RSI 超賣
        if current_price <= current_lower and current_rsi < 30:
            direction = "BUY"
            stop_loss = current_price - current_atr * 1.5
            take_profit = current_middle

        # 做空：價格觸及上軌 + RSI 超買
        elif current_price >= current_upper and current_rsi > 70:
            direction = "SELL"
            stop_loss = current_price + current_atr * 1.5
            take_profit = current_middle

        else:
            return None

        # RSI 背離加強信號
        divergence = detect_rsi_divergence(close, rsi_values, lookback=10)
        if divergence == "bullish" and direction == "BUY":
            confidence += 0.15
        elif divergence == "bearish" and direction == "SELL":
            confidence += 0.15

        # RSI 越極端，信心越高
        if direction == "BUY" and current_rsi < 25:
            confidence += 0.1
        elif direction == "SELL" and current_rsi > 75:
            confidence += 0.1

        # ADX 越低（越盤整），信心越高
        if current_adx < 15:
            confidence += 0.1

        confidence = min(1.0, confidence)

        divergence_text = ""
        if divergence:
            divergence_text = f", RSI {'看漲' if divergence == 'bullish' else '看跌'}背離"

        reason = (
            f"均值回歸: 價格({current_price:.2f}) 觸及"
            f"{'下' if direction == 'BUY' else '上'}軌({current_lower:.2f if direction == 'BUY' else current_upper:.2f}), "
            f"RSI={current_rsi:.1f}, ADX={current_adx:.1f}(盤整)"
            f"{divergence_text}, 目標中軌 {current_middle:.2f}"
        )

        return Signal(
            direction=direction,
            entry_price=current_price,
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            confidence=round(confidence, 2),
            strategy_name=self.name,
            reason=reason,
            timestamp=current_time,
        )

    def suitability_score(
        self,
        gold_df: pd.DataFrame,
        dxy_df: pd.DataFrame | None,
        current_time: datetime,
    ) -> float:
        if gold_df is None or len(gold_df) < 50:
            return 1.0

        close = gold_df["Close"]
        high = gold_df["High"]
        low = gold_df["Low"]

        adx_values, _, _ = adx(high, low, close, 14)
        current_adx = float(adx_values.iloc[-1])

        rsi_values = rsi(close, 14)
        current_rsi = float(rsi_values.iloc[-1])

        bb_upper, bb_middle, bb_lower = bollinger_bands(close, 20, 2.0)
        current_price = float(close.iloc[-1])
        current_upper = float(bb_upper.iloc[-1])
        current_lower = float(bb_lower.iloc[-1])

        score = 1.0

        # ADX 越低分數越高
        if current_adx < 15:
            score = 8.0
        elif current_adx < 20:
            score = 6.5
        elif current_adx < 25:
            score = 4.0
        else:
            return 2.0  # 趨勢太強，不適合

        # 價格接近布林帶邊緣加分
        bb_width = current_upper - current_lower
        if bb_width > 0:
            dist_to_lower = (current_price - current_lower) / bb_width
            dist_to_upper = (current_upper - current_price) / bb_width
            if dist_to_lower < 0.1 or dist_to_upper < 0.1:
                score = min(10.0, score + 1.5)

        # RSI 極端值加分
        if current_rsi < 30 or current_rsi > 70:
            score = min(10.0, score + 1.0)

        return score
