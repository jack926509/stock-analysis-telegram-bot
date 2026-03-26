"""
XAUUSD Telegram Bot 介面
處理使用者指令和推送通知。
"""

import asyncio
import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

from forex_trading.config import ForexConfig
from forex_trading.portfolio.simulator import PortfolioSimulator
from forex_trading.data.market_data import MarketDataManager
from forex_trading.backtest.engine import BacktestEngine
from forex_trading.ai.market_analyst import generate_daily_analysis
from forex_trading.db.database import get_closed_positions, get_backtest_results
from forex_trading.utils.formatter import (
    format_portfolio_status,
    format_backtest_result,
    format_trade_history,
)
from forex_trading.strategies.trend_following import TrendFollowingStrategy
from forex_trading.strategies.session_breakout import SessionBreakoutStrategy
from forex_trading.strategies.bollinger_rsi import BollingerRSIStrategy
from forex_trading.strategies.dxy_correlation import DXYCorrelationStrategy

logger = logging.getLogger(__name__)

# 策略對照表
STRATEGIES = {
    "trend_following": TrendFollowingStrategy(),
    "session_breakout": SessionBreakoutStrategy(),
    "bollinger_rsi": BollingerRSIStrategy(),
    "dxy_correlation": DXYCorrelationStrategy(),
}


async def fx_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /fx_start 指令"""
    welcome = (
        "黃金(XAUUSD) AI 交易系統\n"
        "\n"
        "本系統使用 AI 動態選擇最優策略進行黃金模擬交易。\n"
        "\n"
        "指令:\n"
        "  /fx_status     - 模擬倉狀態\n"
        "  /fx_signal     - 立即執行策略分析\n"
        "  /fx_backtest   - 執行回測\n"
        "  /fx_analysis   - AI 市場分析\n"
        "  /fx_history    - 交易記錄\n"
        "  /fx_performance - 各策略績效\n"
        "\n"
        "策略:\n"
        "  1. 多時間框架趨勢追蹤\n"
        "  2. 倫敦/紐約時段突破\n"
        "  3. 布林通道均值回歸\n"
        "  4. DXY 相關性策略\n"
        "\n"
        "數據源: yfinance | TradingView | Tavily | Claude AI\n"
        "模式: 模擬交易（不連接真實交易所）"
    )
    await update.message.reply_text(welcome)


async def fx_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /fx_status 指令"""
    try:
        simulator = PortfolioSimulator()
        status = await simulator.get_status()
        msg = format_portfolio_status(status)
        await _safe_send(update, msg)
    except Exception as e:
        logger.error(f"取得狀態失敗: {e}")
        await update.message.reply_text(f"取得狀態失敗: {str(e)[:200]}")


async def fx_signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /fx_signal 指令：立即執行策略分析"""
    loading = await update.message.reply_text("正在分析市場數據...")

    try:
        from forex_trading.scheduler.jobs import run_signal_check
        data_manager = MarketDataManager()
        simulator = PortfolioSimulator()
        signals = await run_signal_check(data_manager, simulator)

        if signals:
            msg_parts = ["策略分析完成，產生以下信號:\n"]
            for sig_data in signals:
                direction = sig_data.get("direction", "")
                emoji = "🟢" if direction == "BUY" else "🔴"
                msg_parts.append(
                    f"{emoji} {direction} @ ${sig_data.get('entry_price', 0):.2f} "
                    f"(策略: {sig_data.get('strategy', '')})"
                )
            msg = "\n".join(msg_parts)
        else:
            msg = "分析完成，目前無交易信號。"

        try:
            await loading.edit_text(msg)
        except Exception:
            await update.message.reply_text(msg)

    except Exception as e:
        logger.error(f"信號分析失敗: {e}", exc_info=True)
        try:
            await loading.edit_text(f"分析失敗: {str(e)[:200]}")
        except Exception:
            await update.message.reply_text(f"分析失敗: {str(e)[:200]}")


async def fx_backtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /fx_backtest [strategy] [period] 指令"""
    # 解析參數
    strategy_name = "trend_following"
    period = "60d"

    if context.args:
        if len(context.args) >= 1:
            arg = context.args[0].lower()
            if arg in STRATEGIES:
                strategy_name = arg
        if len(context.args) >= 2:
            period = context.args[1]

    loading = await update.message.reply_text(
        f"正在回測 {strategy_name} ({period})..."
    )

    try:
        data_manager = MarketDataManager()
        gold_df = await data_manager.get_gold_ohlcv(interval="1h", period=period)
        dxy_df = await data_manager.get_dxy_ohlcv(interval="1h", period=period)

        if gold_df is None or gold_df.empty:
            await loading.edit_text("無法取得黃金歷史數據，請稍後重試。")
            return

        strategy = STRATEGIES[strategy_name]
        engine = BacktestEngine(
            strategy=strategy,
            gold_df=gold_df,
            dxy_df=dxy_df,
        )
        result = engine.run()

        msg = format_backtest_result(result)

        try:
            await loading.edit_text(msg, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await loading.edit_text(msg.replace("*", ""))

    except Exception as e:
        logger.error(f"回測失敗: {e}", exc_info=True)
        try:
            await loading.edit_text(f"回測失敗: {str(e)[:200]}")
        except Exception:
            await update.message.reply_text(f"回測失敗: {str(e)[:200]}")


async def fx_analysis_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /fx_analysis 指令"""
    loading = await update.message.reply_text("正在生成 AI 市場分析...")

    try:
        data_manager = MarketDataManager()

        quote, tv_data, news = await asyncio.gather(
            data_manager.get_realtime_quote(),
            data_manager.get_tv_multi_timeframe(),
            data_manager.get_news_sentiment(),
            return_exceptions=True,
        )

        quote = quote if not isinstance(quote, Exception) else None
        tv_data = tv_data if not isinstance(tv_data, Exception) else None
        news = news if not isinstance(news, Exception) else None

        analysis = await generate_daily_analysis(
            quote=quote,
            tv_data=tv_data,
            gold_indicators=None,
            dxy_data=None,
            news_data=news,
        )

        try:
            await loading.delete()
        except Exception:
            pass

        await _safe_send(update, analysis)

    except Exception as e:
        logger.error(f"市場分析失敗: {e}", exc_info=True)
        try:
            await loading.edit_text(f"分析生成失敗: {str(e)[:200]}")
        except Exception:
            pass


async def fx_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /fx_history 指令"""
    try:
        trades = await get_closed_positions(limit=10)
        msg = format_trade_history(trades)
        await _safe_send(update, msg)
    except Exception as e:
        await update.message.reply_text(f"取得記錄失敗: {str(e)[:200]}")


async def fx_performance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /fx_performance 指令"""
    try:
        simulator = PortfolioSimulator()
        performance = await simulator.get_all_strategy_performance()

        lines = ["各策略績效統計:", "━━━━━━━━━━━━━━━━━━━━"]

        for name, perf in performance.items():
            trades = perf.get("trades", 0)
            if trades > 0:
                lines.append(
                    f"\n{name}:\n"
                    f"  交易數: {trades}\n"
                    f"  勝率: {perf.get('win_rate', 0):.1f}%\n"
                    f"  獲利因子: {perf.get('profit_factor', 0):.2f}\n"
                    f"  總 P&L: ${perf.get('total_pnl', 0):+,.2f}"
                )
            else:
                lines.append(f"\n{name}: 尚無交易記錄")

        await update.message.reply_text("\n".join(lines))

    except Exception as e:
        await update.message.reply_text(f"取得績效失敗: {str(e)[:200]}")


# ══════════════════════════════════════════
# 工具函數
# ══════════════════════════════════════════

async def _safe_send(update: Update, text: str) -> None:
    """安全發送訊息（處理過長和 Markdown 問題）。"""
    chunks = _split_message(text, 4096)
    for chunk in chunks:
        try:
            await update.message.reply_text(
                chunk,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
        except Exception:
            clean = chunk.replace("*", "").replace("_", "").replace("`", "")
            try:
                await update.message.reply_text(clean, disable_web_page_preview=True)
            except Exception as e:
                logger.error(f"訊息發送失敗: {e}")


def _split_message(text: str, max_length: int = 4096) -> list[str]:
    """分割過長訊息。"""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        split_pos = text.rfind("\n\n", 0, max_length)
        if split_pos == -1:
            split_pos = text.rfind("\n", 0, max_length)
        if split_pos == -1:
            split_pos = max_length

        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")

    return chunks


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """全局錯誤處理器。"""
    logger.error(f"未處理的異常: {context.error}", exc_info=context.error)
    if update and isinstance(update, Update) and update.message:
        try:
            await update.message.reply_text("系統發生錯誤，請稍後重試。")
        except Exception:
            pass


async def push_message(app: Application, text: str) -> None:
    """主動推送訊息到設定的聊天室。"""
    chat_id = ForexConfig.TELEGRAM_CHAT_ID
    if not chat_id:
        return

    chunks = _split_message(text, 4096)
    for chunk in chunks:
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
        except Exception:
            clean = chunk.replace("*", "").replace("_", "").replace("`", "")
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=clean,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.error(f"推送訊息失敗: {e}")


def create_forex_bot_application() -> Application:
    """建立 XAUUSD Telegram Bot Application。"""
    app = Application.builder().token(ForexConfig.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("fx_start", fx_start_command))
    app.add_handler(CommandHandler("fx_status", fx_status_command))
    app.add_handler(CommandHandler("fx_signal", fx_signal_command))
    app.add_handler(CommandHandler("fx_backtest", fx_backtest_command))
    app.add_handler(CommandHandler("fx_analysis", fx_analysis_command))
    app.add_handler(CommandHandler("fx_history", fx_history_command))
    app.add_handler(CommandHandler("fx_performance", fx_performance_command))

    app.add_error_handler(error_handler)

    return app
