"""
即時模擬倉管理
管理虛擬資金、持倉追蹤和 P&L 計算。
"""

import logging
from datetime import datetime, timezone

from forex_trading.config import ForexConfig
from forex_trading.db.database import (
    save_signal,
    update_signal_status,
    open_position,
    close_position,
    update_position_price,
    get_open_positions,
    get_closed_positions,
    get_strategy_performance,
    save_equity_snapshot,
    get_equity_history,
)
from forex_trading.strategies.base import Signal

logger = logging.getLogger(__name__)


class PortfolioSimulator:
    """即時模擬倉管理器。"""

    def __init__(self):
        self.initial_capital = ForexConfig.INITIAL_CAPITAL
        self.risk_per_trade = ForexConfig.RISK_PER_TRADE
        self.max_positions = ForexConfig.MAX_OPEN_POSITIONS

    async def process_signal(self, signal: Signal, ai_selected: bool = False, market_regime: str = "") -> dict | None:
        """
        處理交易信號：儲存信號並開倉。

        Returns:
            持倉資訊 dict 或 None（若無法開倉）
        """
        # 檢查是否還能開新倉
        open_pos = await get_open_positions()
        if len(open_pos) >= self.max_positions:
            logger.info(f"已達最大持倉數 {self.max_positions}，跳過信號")
            return None

        # 儲存信號
        signal_id = await save_signal(
            strategy_name=signal.strategy_name,
            direction=signal.direction,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            confidence=signal.confidence,
            ai_selected=ai_selected,
            reason=signal.reason,
            market_regime=market_regime,
        )

        # 計算倉位大小
        equity = await self.get_current_equity()
        risk_amount = equity * self.risk_per_trade
        sl_distance = abs(signal.entry_price - signal.stop_loss)

        if sl_distance <= 0:
            await update_signal_status(signal_id, "cancelled")
            return None

        lot_size = round(risk_amount / sl_distance, 4)

        # 套用點差
        spread = ForexConfig.SPREAD
        if signal.direction == "BUY":
            entry_price = signal.entry_price + spread / 2
        else:
            entry_price = signal.entry_price - spread / 2

        # 開倉
        position_id = await open_position(
            signal_id=signal_id,
            direction=signal.direction,
            entry_price=entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            lot_size=lot_size,
            risk_amount=risk_amount,
        )

        await update_signal_status(signal_id, "executed")

        result = {
            "position_id": position_id,
            "signal_id": signal_id,
            "direction": signal.direction,
            "entry_price": entry_price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "lot_size": lot_size,
            "risk_amount": risk_amount,
            "strategy": signal.strategy_name,
            "reason": signal.reason,
        }

        logger.info(
            f"開倉: {signal.direction} @ {entry_price:.2f}, "
            f"SL={signal.stop_loss:.2f}, TP={signal.take_profit:.2f}, "
            f"手數={lot_size}, 策略={signal.strategy_name}"
        )

        return result

    async def check_positions(self, current_price: float) -> list[dict]:
        """
        檢查所有持倉的停損/停利。

        Returns:
            已平倉的持倉列表
        """
        open_pos = await get_open_positions()
        closed = []

        for pos in open_pos:
            direction = pos["direction"]
            sl = pos["stop_loss"]
            tp = pos["take_profit"]
            entry = pos["entry_price"]
            lot_size = pos["lot_size"]

            hit_sl = False
            hit_tp = False

            if direction == "BUY":
                hit_sl = current_price <= sl
                hit_tp = current_price >= tp
            else:
                hit_sl = current_price >= sl
                hit_tp = current_price <= tp

            if hit_sl or hit_tp:
                close_price = sl if hit_sl else tp
                close_reason = "sl" if hit_sl else "tp"

                if direction == "BUY":
                    pnl = (close_price - entry) * lot_size
                else:
                    pnl = (entry - close_price) * lot_size

                await close_position(
                    position_id=pos["id"],
                    close_price=close_price,
                    realized_pnl=round(pnl, 2),
                    close_reason=close_reason,
                )

                closed.append({
                    "position_id": pos["id"],
                    "direction": direction,
                    "entry_price": entry,
                    "close_price": close_price,
                    "pnl": round(pnl, 2),
                    "close_reason": close_reason,
                    "strategy": pos.get("strategy_name", ""),
                })

                logger.info(
                    f"平倉: {direction} @ {close_price:.2f} "
                    f"({'停損' if hit_sl else '停利'}), "
                    f"P&L={pnl:.2f}"
                )
            else:
                # 更新當前價格和未實現損益
                if direction == "BUY":
                    unrealized = (current_price - entry) * lot_size
                else:
                    unrealized = (entry - current_price) * lot_size

                await update_position_price(pos["id"], current_price, round(unrealized, 2))

        return closed

    async def get_current_equity(self) -> float:
        """計算當前權益。"""
        open_pos = await get_open_positions()
        unrealized = sum(pos.get("unrealized_pnl", 0) for pos in open_pos)
        closed_pos = await get_closed_positions(limit=1000)
        realized = sum(pos.get("realized_pnl", 0) for pos in closed_pos)
        return self.initial_capital + realized + unrealized

    async def get_status(self) -> dict:
        """取得模擬倉狀態。"""
        open_pos = await get_open_positions()
        closed_pos = await get_closed_positions(limit=50)
        equity = await self.get_current_equity()

        total_realized = sum(pos.get("realized_pnl", 0) for pos in closed_pos)
        total_unrealized = sum(pos.get("unrealized_pnl", 0) for pos in open_pos)

        wins = sum(1 for pos in closed_pos if (pos.get("realized_pnl") or 0) > 0)
        total_closed = len(closed_pos)
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0

        return {
            "equity": round(equity, 2),
            "cash": round(self.initial_capital + total_realized, 2),
            "unrealized_pnl": round(total_unrealized, 2),
            "realized_pnl": round(total_realized, 2),
            "return_pct": round((equity - self.initial_capital) / self.initial_capital * 100, 2),
            "open_positions": len(open_pos),
            "total_trades": total_closed,
            "win_rate": round(win_rate, 1),
            "positions": open_pos,
        }

    async def record_snapshot(self) -> None:
        """記錄權益快照。"""
        open_pos = await get_open_positions()
        equity = await self.get_current_equity()
        closed_pos = await get_closed_positions(limit=1000)
        total_realized = sum(pos.get("realized_pnl", 0) for pos in closed_pos)
        cash = self.initial_capital + total_realized
        unrealized = sum(pos.get("unrealized_pnl", 0) for pos in open_pos)

        await save_equity_snapshot(
            equity=round(equity, 2),
            cash=round(cash, 2),
            unrealized_pnl=round(unrealized, 2),
            open_positions=len(open_pos),
        )

    async def get_all_strategy_performance(self) -> dict[str, dict]:
        """取得所有策略的績效。"""
        strategies = ["trend_following", "session_breakout", "bollinger_rsi", "dxy_correlation"]
        result = {}
        for name in strategies:
            result[name] = await get_strategy_performance(name)
        return result
