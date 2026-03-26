"""
回測引擎
逐 K 線模擬交易，支援多策略和真實條件（點差、滑點）。
"""

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from forex_trading.config import ForexConfig
from forex_trading.backtest.portfolio import BacktestPortfolio, TradeResult
from forex_trading.backtest.metrics import calculate_metrics, calculate_alpha
from forex_trading.strategies.base import Strategy

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """回測結果。"""
    strategy_name: str
    timeframe: str
    period_start: str
    period_end: str
    metrics: dict
    trades: list[TradeResult]
    equity_curve: list[float]


class BacktestEngine:
    """回測引擎。"""

    def __init__(
        self,
        strategy: Strategy,
        gold_df: pd.DataFrame,
        dxy_df: pd.DataFrame | None = None,
        initial_capital: float | None = None,
        spread: float | None = None,
        slippage_max: float | None = None,
        risk_per_trade: float | None = None,
        max_positions: int | None = None,
    ):
        self.strategy = strategy
        self.gold_df = gold_df
        self.dxy_df = dxy_df
        self.spread = spread if spread is not None else ForexConfig.SPREAD
        self.slippage_max = slippage_max if slippage_max is not None else ForexConfig.SLIPPAGE_MAX

        self.portfolio = BacktestPortfolio(
            initial_capital=initial_capital,
            risk_per_trade=risk_per_trade,
            max_positions=max_positions,
        )

        # 回測需要足夠的回溯數據（至少 200 根 K 線用於計算指標）
        self.warmup_bars = 200

    def run(self) -> BacktestResult:
        """
        執行回測。
        逐 K 線走過歷史數據，在每根 K 線上：
        1. 檢查持倉是否觸及停損/停利
        2. 執行策略分析
        3. 若有信號且可開倉，套用點差/滑點後開倉
        4. 記錄權益
        """
        total_bars = len(self.gold_df)
        if total_bars < self.warmup_bars + 10:
            logger.warning(f"數據不足，需要至少 {self.warmup_bars + 10} 根 K 線")
            return self._empty_result()

        logger.info(
            f"開始回測 {self.strategy.name}: "
            f"{total_bars} 根 K 線, 暖機 {self.warmup_bars}"
        )

        for i in range(self.warmup_bars, total_bars):
            bar = self.gold_df.iloc[i]
            bar_high = float(bar["High"])
            bar_low = float(bar["Low"])
            bar_close = float(bar["Close"])

            # 1. 檢查持倉停損/停利
            self.portfolio.check_exits(bar_high, bar_low, bar_close, i)

            # 2. 準備截至當前 bar 的數據（防止前視偏差）
            gold_slice = self.gold_df.iloc[:i + 1]

            dxy_slice = None
            if self.dxy_df is not None:
                # 根據時間對齊
                dxy_mask = self.dxy_df.index <= self.gold_df.index[i]
                dxy_slice = self.dxy_df[dxy_mask]

            # 3. 執行策略分析
            current_time = self.gold_df.index[i]
            if hasattr(current_time, "to_pydatetime"):
                current_time = current_time.to_pydatetime()
            if current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=timezone.utc)

            try:
                signal = self.strategy.analyze(gold_slice, dxy_slice, current_time)
            except Exception as e:
                logger.debug(f"Bar {i} 策略分析錯誤: {e}")
                signal = None

            # 4. 開倉
            if signal and self.portfolio.can_open_position():
                entry_price = self._apply_spread_and_slippage(
                    signal.entry_price, signal.direction,
                )

                # 調整停損/停利以反映實際進場價
                price_diff = entry_price - signal.entry_price
                adjusted_sl = signal.stop_loss + price_diff
                adjusted_tp = signal.take_profit + price_diff

                self.portfolio.open_position(
                    direction=signal.direction,
                    entry_price=entry_price,
                    stop_loss=adjusted_sl,
                    take_profit=adjusted_tp,
                    bar_index=i,
                    strategy_name=signal.strategy_name,
                )

            # 5. 記錄權益
            self.portfolio.record_equity(bar_close)

        # 回測結束：平倉所有持倉
        if self.portfolio.positions:
            last_close = float(self.gold_df["Close"].iloc[-1])
            self.portfolio.close_all(last_close, total_bars - 1)

        # 計算績效指標
        metrics = calculate_metrics(
            trades=self.portfolio.trades,
            equity_curve=self.portfolio.equity_curve,
            initial_capital=self.portfolio.initial_capital,
            total_bars=total_bars - self.warmup_bars,
        )

        # 計算 Alpha vs Buy-and-Hold
        buyhold_start = float(self.gold_df["Close"].iloc[self.warmup_bars])
        buyhold_end = float(self.gold_df["Close"].iloc[-1])
        if buyhold_start > 0:
            buyhold_return = (buyhold_end - buyhold_start) / buyhold_start * 100
            metrics["alpha_vs_buyhold"] = round(metrics["total_return"] - buyhold_return, 2)

        # 組裝結果
        period_start = str(self.gold_df.index[self.warmup_bars])
        period_end = str(self.gold_df.index[-1])

        logger.info(
            f"回測完成 {self.strategy.name}: "
            f"{metrics['total_trades']} 筆交易, "
            f"勝率 {metrics['win_rate']}%, "
            f"總報酬 {metrics['total_return']}%"
        )

        return BacktestResult(
            strategy_name=self.strategy.name,
            timeframe="1h",
            period_start=period_start,
            period_end=period_end,
            metrics=metrics,
            trades=self.portfolio.trades,
            equity_curve=self.portfolio.equity_curve,
        )

    def _apply_spread_and_slippage(self, price: float, direction: str) -> float:
        """套用點差和滑點。"""
        slippage = random.uniform(0, self.slippage_max)

        if direction == "BUY":
            return price + self.spread / 2 + slippage
        else:
            return price - self.spread / 2 - slippage

    def _empty_result(self) -> BacktestResult:
        """數據不足時回傳空結果。"""
        return BacktestResult(
            strategy_name=self.strategy.name,
            timeframe="1h",
            period_start="",
            period_end="",
            metrics=calculate_metrics([], [], self.portfolio.initial_capital, 0),
            trades=[],
            equity_curve=[self.portfolio.initial_capital],
        )
