"""
XAUUSD AI 自動化交易系統
獨立入口點

啟動方式：python -m forex_trading.main
"""

import asyncio
import logging
import signal
import sys

from forex_trading.config import ForexConfig
from forex_trading.utils.logger import setup_logging
from forex_trading.bot.telegram_bot import create_forex_bot_application, push_message
from forex_trading.data.market_data import MarketDataManager
from forex_trading.portfolio.simulator import PortfolioSimulator
from forex_trading.scheduler.jobs import ForexScheduler


def main():
    """主程式進入點。"""
    setup_logging()
    logger = logging.getLogger(__name__)

    # 驗證環境變數
    logger.info("驗證環境變數...")
    ForexConfig.validate()
    logger.info("環境變數驗證通過")

    logger.info("正在啟動 XAUUSD AI 交易系統...")
    logger.info(f"AI 模型: {ForexConfig.ANTHROPIC_MODEL}")
    logger.info(f"環境: {ForexConfig.ENV}")
    logger.info(f"模式: {ForexConfig.MODE}")
    logger.info(f"初始資金: ${ForexConfig.INITIAL_CAPITAL:,.0f}")
    logger.info(f"每筆風險: {ForexConfig.RISK_PER_TRADE * 100}%")

    # 建立 Bot Application
    app = create_forex_bot_application()

    # 設定排程器
    data_manager = MarketDataManager()
    simulator = PortfolioSimulator()

    async def _push(text: str):
        await push_message(app, text)

    scheduler = ForexScheduler(
        data_manager=data_manager,
        simulator=simulator,
        push_callback=_push,
    )

    # 設定 post_init hook 來啟動排程器
    async def _post_init(application):
        await scheduler.start()
        logger.info("排程器已啟動")
        logger.info(
            f"信號檢查間隔: {ForexConfig.SIGNAL_CHECK_INTERVAL}s, "
            f"持倉監控間隔: {ForexConfig.POSITION_CHECK_INTERVAL}s"
        )

    async def _post_shutdown(application):
        await scheduler.stop()
        logger.info("系統已完全關閉")

    app.post_init = _post_init
    app.post_shutdown = _post_shutdown

    logger.info("Bot 已啟動！等待指令中...")
    logger.info(
        "可用指令: /fx_start, /fx_status, /fx_signal, "
        "/fx_backtest, /fx_analysis, /fx_history, /fx_performance"
    )

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
