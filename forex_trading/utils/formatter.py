"""
Telegram 訊息格式化工具
格式化交易信號、持倉狀態、回測結果等訊息。
"""

from datetime import datetime, timezone


def format_signal(signal_data: dict) -> str:
    """格式化交易信號訊息。"""
    direction = signal_data.get("direction", "")
    emoji = "🟢" if direction == "BUY" else "🔴"

    lines = [
        f"{emoji} *XAUUSD {direction} 信號*",
        "",
        f"策略: {signal_data.get('strategy', '')}",
        f"進場: ${signal_data.get('entry_price', 0):.2f}",
        f"停損: ${signal_data.get('stop_loss', 0):.2f}",
        f"停利: ${signal_data.get('take_profit', 0):.2f}",
        f"手數: {signal_data.get('lot_size', 0):.4f}",
        f"風險: ${signal_data.get('risk_amount', 0):.2f}",
        "",
        f"原因: {signal_data.get('reason', '')}",
        "",
        "此為模擬交易信號，不構成投資建議。",
    ]
    return "\n".join(lines)


def format_position_closed(closed_data: dict) -> str:
    """格式化平倉通知。"""
    pnl = closed_data.get("pnl", 0)
    emoji = "🟢" if pnl > 0 else "🔴"
    reason = "停利" if closed_data.get("close_reason") == "tp" else "停損"

    lines = [
        f"{emoji} *XAUUSD 平倉通知 ({reason})*",
        "",
        f"方向: {closed_data.get('direction', '')}",
        f"進場: ${closed_data.get('entry_price', 0):.2f}",
        f"平倉: ${closed_data.get('close_price', 0):.2f}",
        f"損益: ${pnl:+.2f}",
    ]
    return "\n".join(lines)


def format_portfolio_status(status: dict) -> str:
    """格式化模擬倉狀態。"""
    return_pct = status.get("return_pct", 0)
    return_emoji = "🟢" if return_pct >= 0 else "🔴"

    lines = [
        "📊 *XAUUSD 模擬倉狀態*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"權益: ${status.get('equity', 0):,.2f}",
        f"報酬: {return_emoji} {return_pct:+.2f}%",
        f"已實現 P&L: ${status.get('realized_pnl', 0):+,.2f}",
        f"未實現 P&L: ${status.get('unrealized_pnl', 0):+,.2f}",
        f"持倉數: {status.get('open_positions', 0)}",
        f"總交易: {status.get('total_trades', 0)}",
        f"勝率: {status.get('win_rate', 0):.1f}%",
    ]

    # 列出未平倉部位
    positions = status.get("positions", [])
    if positions:
        lines.append("")
        lines.append("未平倉部位:")
        for pos in positions:
            direction = pos.get("direction", "")
            d_emoji = "🟢" if direction == "BUY" else "🔴"
            unrealized = pos.get("unrealized_pnl", 0)
            u_emoji = "+" if unrealized >= 0 else ""
            lines.append(
                f"  {d_emoji} {direction} @ ${pos.get('entry_price', 0):.2f} "
                f"| P&L: ${u_emoji}{unrealized:.2f}"
            )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.extend(["", f"更新時間: {now}"])

    return "\n".join(lines)


def format_backtest_result(result) -> str:
    """格式化回測結果。"""
    m = result.metrics

    lines = [
        f"📊 *回測結果: {result.strategy_name}*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"期間: {result.period_start[:10]} ~ {result.period_end[:10]}",
        f"時間框架: {result.timeframe}",
        "",
        f"總交易: {m.get('total_trades', 0)}",
        f"勝率: {m.get('win_rate', 0):.1f}%",
        f"總報酬: {m.get('total_return', 0):+.2f}%",
        f"年化報酬: {m.get('annual_return', 0):+.2f}%",
        f"最大回撤: {m.get('max_drawdown_pct', 0):.2f}%",
        f"夏普比率: {m.get('sharpe_ratio', 0):.2f}",
        f"獲利因子: {m.get('profit_factor', 0):.2f}",
        f"平均持倉: {m.get('avg_hold_time_hours', 0):.1f} 小時",
        f"Alpha vs 買持: {m.get('alpha_vs_buyhold', 0):+.2f}%",
        "",
        f"最大單筆盈利: ${m.get('largest_win', 0):,.2f}",
        f"最大單筆虧損: ${m.get('largest_loss', 0):,.2f}",
        f"期望值/筆: ${m.get('expectancy', 0):,.2f}",
    ]
    return "\n".join(lines)


def format_trade_history(trades: list[dict]) -> str:
    """格式化交易歷史。"""
    if not trades:
        return "暫無交易記錄。"

    lines = ["📋 *近期交易記錄*", "━━━━━━━━━━━━━━━━━━━━"]

    for i, trade in enumerate(trades[:10], 1):
        pnl = trade.get("realized_pnl", 0)
        emoji = "🟢" if pnl > 0 else "🔴"
        direction = trade.get("direction", "")
        reason = trade.get("close_reason", "")

        lines.append(
            f"{i}. {emoji} {direction} "
            f"${trade.get('entry_price', 0):.2f} -> ${trade.get('close_price', 0):.2f} "
            f"| P&L: ${pnl:+.2f} ({reason})"
        )

    return "\n".join(lines)


def format_weekly_summary(status: dict, performance: dict[str, dict]) -> str:
    """格式化每週績效摘要。"""
    lines = [
        "📊 *XAUUSD 每週績效摘要*",
        "══════════════════════",
        "",
        f"權益: ${status.get('equity', 0):,.2f}",
        f"週報酬: {status.get('return_pct', 0):+.2f}%",
        f"總交易: {status.get('total_trades', 0)}",
        f"勝率: {status.get('win_rate', 0):.1f}%",
        "",
        "各策略績效:",
    ]

    for name, perf in performance.items():
        if perf.get("trades", 0) > 0:
            lines.append(
                f"  {name}: 勝率 {perf.get('win_rate', 0):.0f}%, "
                f"PF {perf.get('profit_factor', 0):.2f}, "
                f"P&L ${perf.get('total_pnl', 0):+,.2f}"
            )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.extend(["", f"統計時間: {now}"])

    return "\n".join(lines)
