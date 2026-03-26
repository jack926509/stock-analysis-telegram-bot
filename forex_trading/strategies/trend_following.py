"""
策略 1: 多時間框架趨勢追蹤
4H EMA50/EMA200 判斷趨勢，1H EMA9/EMA21 交叉進場，ADX 過濾，DXY 確認。
"""

import logging
from datetime import datetime

import pandas as pd

from forex_trading.indicators.technical import ema, adx, atr, rolling_correlation
from forex_trading.strategies.base import Strategy, Signal

logger = logging.getLogger(__name__)


class TrendFollowingStrategy(Strategy):
    """多時間框架趨勢追蹤策略。"""

    name = "trend_following"

    def analyze(
        self,
        gold_df: pd.DataFrame,
        dxy_df: pd.DataFrame | None,
        current_time: datetime,
    ) -> Signal | None:
        if gold_df is None or len(gold_df) < 200:
            return None

        close = gold_df["Close"]
        high = gold_df["High"]
        low = gold_df["Low"]

        # 4H 趨勢判斷（使用重新取樣或直接計算長期 EMA）
        ema50 = ema(close, 50)
        ema200 = ema(close, 200)

        # 1H 進場信號
        ema9 = ema(close, 9)
        ema21 = ema(close, 21)

        # ADX 趨勢強度過濾
        adx_values, di_plus, di_minus = adx(high, low, close, 14)

        # ATR 用於停損計算
        atr_values = atr(high, low, close, 14)

        # 取最新值
        current_price = float(close.iloc[-1])
        current_ema50 = float(ema50.iloc[-1])
        current_ema200 = float(ema200.iloc[-1])
        current_ema9 = float(ema9.iloc[-1])
        current_ema21 = float(ema21.iloc[-1])
        prev_ema9 = float(ema9.iloc[-2])
        prev_ema21 = float(ema21.iloc[-2])
        current_adx = float(adx_values.iloc[-1])
        current_atr = float(atr_values.iloc[-1])

        # 條件 1: ADX > 25 確認趨勢存在
        if current_adx < 25:
            return None

        # 條件 2: 判斷趨勢方向（EMA50 vs EMA200）
        is_uptrend = current_ema50 > current_ema200
        is_downtrend = current_ema50 < current_ema200

        if not is_uptrend and not is_downtrend:
            return None

        # 條件 3: EMA9/EMA21 交叉（與大趨勢一致）
        bullish_cross = prev_ema9 <= prev_ema21 and current_ema9 > current_ema21
        bearish_cross = prev_ema9 >= prev_ema21 and current_ema9 < current_ema21

        direction = None
        if is_uptrend and bullish_cross:
            direction = "BUY"
        elif is_downtrend and bearish_cross:
            direction = "SELL"
        else:
            return None

        # 條件 4: DXY 反向確認（可選）
        dxy_confirms = True
        if dxy_df is not None and len(dxy_df) >= 20:
            dxy_close = dxy_df["Close"]
            correlation = rolling_correlation(close.tail(len(dxy_close)), dxy_close, 20)
            if not correlation.empty and not pd.isna(correlation.iloc[-1]):
                corr_val = float(correlation.iloc[-1])
                if direction == "BUY" and corr_val > 0.3:
                    dxy_confirms = False
                elif direction == "SELL" and corr_val < -0.3:
                    dxy_confirms = False

        if not dxy_confirms:
            return None

        # 計算停損和停利
        if direction == "BUY":
            stop_loss = current_price - (current_atr * 2)
            take_profit = current_price + (current_atr * 4)
        else:
            stop_loss = current_price + (current_atr * 2)
            take_profit = current_price - (current_atr * 4)

        confidence = min(1.0, (current_adx - 25) / 25 * 0.5 + 0.5)

        reason = (
            f"{'上升' if is_uptrend else '下降'}趨勢確認: "
            f"EMA50({current_ema50:.2f}) {'>' if is_uptrend else '<'} EMA200({current_ema200:.2f}), "
            f"EMA9/21 {'金叉' if direction == 'BUY' else '死叉'}, "
            f"ADX={current_adx:.1f}, ATR={current_atr:.2f}"
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
        if gold_df is None or len(gold_df) < 200:
            return 1.0

        close = gold_df["Close"]
        high = gold_df["High"]
        low = gold_df["Low"]

        adx_values, _, _ = adx(high, low, close, 14)
        current_adx = float(adx_values.iloc[-1])

        ema50_val = float(ema(close, 50).iloc[-1])
        ema200_val = float(ema(close, 200).iloc[-1])
        ema_alignment = abs(ema50_val - ema200_val) / ema200_val * 100

        score = 1.0
        if current_adx > 40:
            score = 9.0
        elif current_adx > 30:
            score = 7.0
        elif current_adx > 25:
            score = 5.0
        elif current_adx > 20:
            score = 3.0

        if ema_alignment > 2:
            score = min(10.0, score + 1.0)

        return score
