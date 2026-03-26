"""
回測虛擬資金管理
管理回測過程中的資金、持倉和權益曲線。
"""

from dataclasses import dataclass, field

from forex_trading.config import ForexConfig


@dataclass
class BacktestPosition:
    """回測中的持倉。"""
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    risk_amount: float
    entry_bar: int
    strategy_name: str


@dataclass
class TradeResult:
    """交易結果。"""
    direction: str
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    risk_amount: float
    pnl: float
    pnl_pct: float
    entry_bar: int
    exit_bar: int
    hold_bars: int
    exit_reason: str  # "tp", "sl", "end"
    strategy_name: str


class BacktestPortfolio:
    """回測虛擬投資組合。"""

    def __init__(
        self,
        initial_capital: float | None = None,
        risk_per_trade: float | None = None,
        max_positions: int | None = None,
    ):
        self.initial_capital = initial_capital or ForexConfig.INITIAL_CAPITAL
        self.cash = self.initial_capital
        self.risk_per_trade = risk_per_trade or ForexConfig.RISK_PER_TRADE
        self.max_positions = max_positions or ForexConfig.MAX_OPEN_POSITIONS

        self.positions: list[BacktestPosition] = []
        self.trades: list[TradeResult] = []
        self.equity_curve: list[float] = [self.initial_capital]

    @property
    def equity(self) -> float:
        """當前權益 = 現金 + 未實現損益。"""
        unrealized = sum(self._unrealized_pnl(pos, pos.entry_price) for pos in self.positions)
        return self.cash + unrealized

    def can_open_position(self) -> bool:
        """是否還能開新倉。"""
        return len(self.positions) < self.max_positions

    def calculate_lot_size(self, entry_price: float, stop_loss: float) -> float:
        """
        根據風險計算倉位大小。
        風險金額 = 當前權益 * 風險百分比
        手數 = 風險金額 / 停損距離
        """
        risk_amount = self.equity * self.risk_per_trade
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            return 0.0
        lot_size = risk_amount / sl_distance
        return round(lot_size, 4)

    def open_position(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        bar_index: int,
        strategy_name: str,
    ) -> bool:
        """開設新持倉。"""
        if not self.can_open_position():
            return False

        lot_size = self.calculate_lot_size(entry_price, stop_loss)
        if lot_size <= 0:
            return False

        risk_amount = self.equity * self.risk_per_trade

        self.positions.append(BacktestPosition(
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=lot_size,
            risk_amount=risk_amount,
            entry_bar=bar_index,
            strategy_name=strategy_name,
        ))
        return True

    def check_exits(
        self,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        bar_index: int,
    ) -> list[TradeResult]:
        """
        檢查持倉是否觸及停損/停利。
        回傳已平倉的交易結果。
        """
        closed = []
        remaining = []

        for pos in self.positions:
            exit_price = None
            exit_reason = None

            if pos.direction == "BUY":
                if bar_low <= pos.stop_loss:
                    exit_price = pos.stop_loss
                    exit_reason = "sl"
                elif bar_high >= pos.take_profit:
                    exit_price = pos.take_profit
                    exit_reason = "tp"
            else:  # SELL
                if bar_high >= pos.stop_loss:
                    exit_price = pos.stop_loss
                    exit_reason = "sl"
                elif bar_low <= pos.take_profit:
                    exit_price = pos.take_profit
                    exit_reason = "tp"

            if exit_price is not None:
                pnl = self._calculate_pnl(pos, exit_price)
                self.cash += pnl

                result = TradeResult(
                    direction=pos.direction,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    stop_loss=pos.stop_loss,
                    take_profit=pos.take_profit,
                    lot_size=pos.lot_size,
                    risk_amount=pos.risk_amount,
                    pnl=round(pnl, 2),
                    pnl_pct=round(pnl / self.initial_capital * 100, 4),
                    entry_bar=pos.entry_bar,
                    exit_bar=bar_index,
                    hold_bars=bar_index - pos.entry_bar,
                    exit_reason=exit_reason,
                    strategy_name=pos.strategy_name,
                )
                self.trades.append(result)
                closed.append(result)
            else:
                remaining.append(pos)

        self.positions = remaining
        return closed

    def close_all(self, close_price: float, bar_index: int) -> list[TradeResult]:
        """平倉所有持倉（回測結束時）。"""
        closed = []
        for pos in self.positions:
            pnl = self._calculate_pnl(pos, close_price)
            self.cash += pnl

            result = TradeResult(
                direction=pos.direction,
                entry_price=pos.entry_price,
                exit_price=close_price,
                stop_loss=pos.stop_loss,
                take_profit=pos.take_profit,
                lot_size=pos.lot_size,
                risk_amount=pos.risk_amount,
                pnl=round(pnl, 2),
                pnl_pct=round(pnl / self.initial_capital * 100, 4),
                entry_bar=pos.entry_bar,
                exit_bar=bar_index,
                hold_bars=bar_index - pos.entry_bar,
                exit_reason="end",
                strategy_name=pos.strategy_name,
            )
            self.trades.append(result)
            closed.append(result)

        self.positions = []
        return closed

    def record_equity(self, current_price: float) -> None:
        """記錄當前權益（含未實現損益）。"""
        unrealized = sum(self._unrealized_pnl(pos, current_price) for pos in self.positions)
        self.equity_curve.append(self.cash + unrealized)

    def _calculate_pnl(self, pos: BacktestPosition, exit_price: float) -> float:
        """計算已實現損益。"""
        if pos.direction == "BUY":
            return (exit_price - pos.entry_price) * pos.lot_size
        else:
            return (pos.entry_price - exit_price) * pos.lot_size

    def _unrealized_pnl(self, pos: BacktestPosition, current_price: float) -> float:
        """計算未實現損益。"""
        if pos.direction == "BUY":
            return (current_price - pos.entry_price) * pos.lot_size
        else:
            return (pos.entry_price - current_price) * pos.lot_size
