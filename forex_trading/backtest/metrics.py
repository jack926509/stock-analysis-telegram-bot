"""
回測績效指標計算模組
從交易記錄和權益曲線計算各項績效指標。
"""

import math

import numpy as np


def calculate_metrics(
    trades: list,
    equity_curve: list[float],
    initial_capital: float,
    total_bars: int,
    bars_per_year: float = 252 * 6.5,  # 1H bars per year (approx)
) -> dict:
    """
    計算回測績效指標。

    Args:
        trades: TradeResult 列表
        equity_curve: 權益曲線
        initial_capital: 初始資金
        total_bars: 總 K 線數
        bars_per_year: 每年的 K 線數

    Returns:
        dict: 績效指標
    """
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
            "profit_factor": 0.0,
            "avg_hold_time_hours": 0.0,
            "alpha_vs_buyhold": 0.0,
            "expectancy": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
        }

    # 基本統計
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    total_trades = len(trades)
    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0

    # 總報酬率
    final_equity = equity_curve[-1] if equity_curve else initial_capital
    total_return = (final_equity - initial_capital) / initial_capital * 100

    # 年化報酬率
    if total_bars > 0 and bars_per_year > 0:
        years = total_bars / bars_per_year
        if years > 0 and final_equity > 0:
            annual_return = ((final_equity / initial_capital) ** (1 / years) - 1) * 100
        else:
            annual_return = 0.0
    else:
        annual_return = 0.0

    # 最大回撤
    max_drawdown, max_drawdown_pct = _calculate_max_drawdown(equity_curve)

    # 夏普比率
    sharpe_ratio = _calculate_sharpe(equity_curve, bars_per_year)

    # 獲利因子
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # 平均持倉時間（以 bar 數計算，假設 1H）
    avg_hold_bars = sum(t.hold_bars for t in trades) / total_trades if total_trades > 0 else 0
    avg_hold_time_hours = avg_hold_bars  # 1 bar = 1 hour for 1H data

    # 期望值
    expectancy = sum(t.pnl for t in trades) / total_trades if total_trades > 0 else 0

    # 最大單筆盈虧
    largest_win = max((t.pnl for t in trades), default=0)
    largest_loss = min((t.pnl for t in trades), default=0)

    return {
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "total_return": round(total_return, 2),
        "annual_return": round(annual_return, 2),
        "max_drawdown": round(max_drawdown, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "sharpe_ratio": round(sharpe_ratio, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999.99,
        "avg_hold_time_hours": round(avg_hold_time_hours, 1),
        "alpha_vs_buyhold": 0.0,  # 需要 buy-hold 數據另外計算
        "expectancy": round(expectancy, 2),
        "largest_win": round(largest_win, 2),
        "largest_loss": round(largest_loss, 2),
        "wins": len(wins),
        "losses": len(losses),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
    }


def calculate_alpha(
    equity_curve: list[float],
    buyhold_curve: list[float],
    initial_capital: float,
) -> float:
    """計算相對於買入持有的 Alpha。"""
    if not equity_curve or not buyhold_curve:
        return 0.0

    strategy_return = (equity_curve[-1] - initial_capital) / initial_capital * 100
    buyhold_return = (buyhold_curve[-1] - buyhold_curve[0]) / buyhold_curve[0] * 100

    return round(strategy_return - buyhold_return, 2)


def _calculate_max_drawdown(equity_curve: list[float]) -> tuple[float, float]:
    """計算最大回撤（金額和百分比）。"""
    if not equity_curve or len(equity_curve) < 2:
        return 0.0, 0.0

    arr = np.array(equity_curve)
    peak = np.maximum.accumulate(arr)
    drawdown = arr - peak
    max_dd = float(drawdown.min())

    peak_at_dd = peak[np.argmin(drawdown)]
    max_dd_pct = (max_dd / peak_at_dd * 100) if peak_at_dd > 0 else 0.0

    return abs(max_dd), abs(max_dd_pct)


def _calculate_sharpe(
    equity_curve: list[float],
    bars_per_year: float,
    risk_free_rate: float = 0.05,
) -> float:
    """計算年化夏普比率。"""
    if len(equity_curve) < 3:
        return 0.0

    arr = np.array(equity_curve)
    returns = np.diff(arr) / arr[:-1]

    if len(returns) == 0 or np.std(returns) == 0:
        return 0.0

    mean_return = float(np.mean(returns))
    std_return = float(np.std(returns))

    rf_per_bar = risk_free_rate / bars_per_year
    sharpe = (mean_return - rf_per_bar) / std_return * math.sqrt(bars_per_year)

    return sharpe
