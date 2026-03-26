"""
策略基礎類別
定義 Signal 資料結構和 Strategy 抽象基底類別。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass
class Signal:
    """交易信號。"""
    direction: str          # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float       # 0.0 - 1.0
    strategy_name: str
    reason: str
    timestamp: datetime


class Strategy(ABC):
    """交易策略抽象基底類別。"""

    name: str = "base"

    @abstractmethod
    def analyze(
        self,
        gold_df: pd.DataFrame,
        dxy_df: pd.DataFrame | None,
        current_time: datetime,
    ) -> Signal | None:
        """
        分析市場數據，產生交易信號。

        Args:
            gold_df: XAUUSD OHLCV 數據（已包含所需時間框架）
            dxy_df: DXY OHLCV 數據（可為 None）
            current_time: 當前時間（UTC）

        Returns:
            Signal 或 None（無信號）
        """

    @abstractmethod
    def suitability_score(
        self,
        gold_df: pd.DataFrame,
        dxy_df: pd.DataFrame | None,
        current_time: datetime,
    ) -> float:
        """
        評估當前市場條件下本策略的適用度。

        Returns:
            1.0 - 10.0 的分數
        """
