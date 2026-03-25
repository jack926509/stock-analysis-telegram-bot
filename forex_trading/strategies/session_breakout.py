"""
策略 2: 倫敦/紐約時段突破
標記亞洲時段區間，在倫敦/紐約開盤時段突破進場。
"""

import logging
from datetime import datetime, timezone

import pandas as pd

from forex_trading.config import ForexConfig
from forex_trading.indicators.technical import atr
from forex_trading.strategies.base import Strategy, Signal

logger = logging.getLogger(__name__)


class SessionBreakoutStrategy(Strategy):
    """倫敦/紐約時段突破策略。"""

    name = "session_breakout"

    def _get_asian_range(self, df: pd.DataFrame, current_time: datetime) -> tuple[float, float] | None:
        """計算亞洲時段區間（00:00-08:00 UTC）。"""
        if df.index.tz is None:
            df = df.copy()
            df.index = df.index.tz_localize("UTC")

        today = current_time.date()
        asian_mask = (
            (df.index.date == today) &
            (df.index.hour >= ForexConfig.ASIAN_SESSION_START) &
            (df.index.hour < ForexConfig.ASIAN_SESSION_END)
        )
        asian_data = df[asian_mask]

        if asian_data.empty or len(asian_data) < 3:
            return None

        asian_high = float(asian_data["High"].max())
        asian_low = float(asian_data["Low"].min())

        return asian_high, asian_low

    def analyze(
        self,
        gold_df: pd.DataFrame,
        dxy_df: pd.DataFrame | None,
        current_time: datetime,
    ) -> Signal | None:
        if gold_df is None or len(gold_df) < 50:
            return None

        current_hour = current_time.hour

        # 只在倫敦開盤（08:00-09:00）或紐約開盤（13:00-14:30）時段觸發
        is_london_open = ForexConfig.LONDON_SESSION_START <= current_hour < ForexConfig.LONDON_SESSION_START + 1
        is_ny_open = ForexConfig.NY_SESSION_START <= current_hour < ForexConfig.NY_SESSION_START + 1

        if not is_london_open and not is_ny_open:
            return None

        # 取得亞洲時段區間
        asian_range = self._get_asian_range(gold_df, current_time)
        if asian_range is None:
            return None

        asian_high, asian_low = asian_range
        range_size = asian_high - asian_low

        if range_size < 1.0:
            return None

        current_price = float(gold_df["Close"].iloc[-1])
        atr_values = atr(gold_df["High"], gold_df["Low"], gold_df["Close"], 14)
        current_atr = float(atr_values.iloc[-1])

        # 量能確認（如有 Volume 數據）
        volume_ok = True
        if "Volume" in gold_df.columns:
            if gold_df.index.tz is None:
                temp_df = gold_df.copy()
                temp_df.index = temp_df.index.tz_localize("UTC")
            else:
                temp_df = gold_df

            today = current_time.date()
            asian_mask = (
                (temp_df.index.date == today) &
                (temp_df.index.hour >= ForexConfig.ASIAN_SESSION_START) &
                (temp_df.index.hour < ForexConfig.ASIAN_SESSION_END)
            )
            asian_vol = temp_df.loc[asian_mask, "Volume"]
            if not asian_vol.empty:
                avg_asian_vol = float(asian_vol.mean())
                current_vol = float(gold_df["Volume"].iloc[-1])
                if avg_asian_vol > 0:
                    volume_ok = current_vol > avg_asian_vol * 1.2

        direction = None

        # 向上突破
        if current_price > asian_high:
            direction = "BUY"
            stop_loss = asian_low
            take_profit = current_price + range_size * 1.5
        # 向下突破
        elif current_price < asian_low:
            direction = "SELL"
            stop_loss = asian_high
            take_profit = current_price - range_size * 1.5
        else:
            return None

        if not volume_ok:
            return None

        session = "倫敦" if is_london_open else "紐約"
        confidence = 0.65 if volume_ok else 0.5

        # 區間越窄（壓縮越強），突破信心越高
        if range_size < current_atr * 0.8:
            confidence = min(1.0, confidence + 0.15)

        reason = (
            f"{session}時段突破: 亞洲區間 {asian_low:.2f}-{asian_high:.2f} "
            f"(幅度 {range_size:.2f}), 當前價 {current_price:.2f}"
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
        current_hour = current_time.hour

        # 在時段窗口內分數最高
        is_london = ForexConfig.LONDON_SESSION_START <= current_hour < ForexConfig.LONDON_SESSION_START + 2
        is_ny = ForexConfig.NY_SESSION_START <= current_hour < ForexConfig.NY_SESSION_START + 2

        if not is_london and not is_ny:
            return 1.0

        score = 6.0

        if gold_df is not None and len(gold_df) >= 50:
            asian_range = self._get_asian_range(gold_df, current_time)
            if asian_range:
                asian_high, asian_low = asian_range
                range_size = asian_high - asian_low
                atr_values = atr(gold_df["High"], gold_df["Low"], gold_df["Close"], 14)
                current_atr = float(atr_values.iloc[-1])

                # 區間越窄分數越高（壓縮 = 即將突破）
                if current_atr > 0:
                    ratio = range_size / current_atr
                    if ratio < 0.6:
                        score = 9.0
                    elif ratio < 0.8:
                        score = 7.5
                    elif ratio > 1.5:
                        score = 4.0

        return score
