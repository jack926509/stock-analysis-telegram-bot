"""
策略 4: DXY 相關性策略
利用黃金與美元指數的反向相關性交易。
"""

import logging
from datetime import datetime

import numpy as np
import pandas as pd

from forex_trading.indicators.technical import (
    rolling_correlation, support_resistance, atr, ema,
)
from forex_trading.strategies.base import Strategy, Signal

logger = logging.getLogger(__name__)


class DXYCorrelationStrategy(Strategy):
    """DXY 相關性策略。"""

    name = "dxy_correlation"

    def analyze(
        self,
        gold_df: pd.DataFrame,
        dxy_df: pd.DataFrame | None,
        current_time: datetime,
    ) -> Signal | None:
        if gold_df is None or dxy_df is None:
            return None

        if len(gold_df) < 30 or len(dxy_df) < 30:
            return None

        gold_close = gold_df["Close"]
        dxy_close = dxy_df["Close"]

        # 對齊時間序列
        min_len = min(len(gold_close), len(dxy_close))
        gold_aligned = gold_close.iloc[-min_len:]
        dxy_aligned = dxy_close.iloc[-min_len:]

        # 計算滾動相關係數
        correlation = rolling_correlation(gold_aligned, dxy_aligned, 20)

        if correlation.empty or pd.isna(correlation.iloc[-1]):
            return None

        current_corr = float(correlation.iloc[-1])

        # 只有強反向相關時才交易（< -0.7）
        if current_corr > -0.7:
            return None

        # DXY 關鍵位突破
        dxy_high = dxy_df["High"]
        dxy_low = dxy_df["Low"]
        dxy_resistance, dxy_support = support_resistance(dxy_high, dxy_low, 20)

        current_dxy = float(dxy_close.iloc[-1])
        prev_dxy = float(dxy_close.iloc[-2])

        current_gold_price = float(gold_close.iloc[-1])
        gold_atr = atr(gold_df["High"], gold_df["Low"], gold_close, 14)
        current_atr = float(gold_atr.iloc[-1])

        direction = None

        # DXY 突破阻力 → 美元走強 → 黃金做空
        if current_dxy > dxy_resistance and prev_dxy <= dxy_resistance:
            direction = "SELL"
        # DXY 跌破支撐 → 美元走弱 → 黃金做多
        elif current_dxy < dxy_support and prev_dxy >= dxy_support:
            direction = "BUY"
        else:
            return None

        # 結合黃金自身技術位精煉進場
        gold_resistance, gold_support = support_resistance(
            gold_df["High"], gold_df["Low"], 20,
        )

        if direction == "BUY":
            # 做多時，如果價格已在壓力位附近，降低信心
            if current_gold_price > gold_resistance * 0.99:
                return None
            stop_loss = current_gold_price - current_atr * 2
            take_profit = current_gold_price + current_atr * 4
        else:
            # 做空時，如果價格已在支撐位附近，降低信心
            if current_gold_price < gold_support * 1.01:
                return None
            stop_loss = current_gold_price + current_atr * 2
            take_profit = current_gold_price - current_atr * 4

        # 信心基於相關性強度
        confidence = min(1.0, abs(current_corr) * 0.8 + 0.15)

        reason = (
            f"DXY 相關性策略: 相關係數={current_corr:.2f}, "
            f"DXY({current_dxy:.2f}) {'突破阻力' if direction == 'SELL' else '跌破支撐'}"
            f"({dxy_resistance:.2f}/{dxy_support:.2f}), "
            f"黃金 S/R: {gold_support:.2f}/{gold_resistance:.2f}"
        )

        return Signal(
            direction=direction,
            entry_price=current_gold_price,
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
        if gold_df is None or dxy_df is None:
            return 1.0

        if len(gold_df) < 30 or len(dxy_df) < 30:
            return 1.0

        gold_close = gold_df["Close"]
        dxy_close = dxy_df["Close"]

        min_len = min(len(gold_close), len(dxy_close))
        correlation = rolling_correlation(
            gold_close.iloc[-min_len:],
            dxy_close.iloc[-min_len:],
            20,
        )

        if correlation.empty or pd.isna(correlation.iloc[-1]):
            return 1.0

        current_corr = float(correlation.iloc[-1])

        # 相關性越強（越負），分數越高
        if current_corr < -0.85:
            return 9.0
        elif current_corr < -0.7:
            return 7.0
        elif current_corr < -0.5:
            return 4.0
        elif current_corr < -0.3:
            return 2.5
        else:
            return 1.0
