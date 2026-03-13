"""
零幻覺美股 Telegram 分析機器人
程式進入點

啟動方式：python main.py
"""

import asyncio
import logging
import signal
import sys

from config import Config
from bot.telegram_bot import create_bot_application


def _setup_logging():
    """設定結構化日誌。"""
    log_format = (
        '{"time":"%(asctime)s","level":"%(levelname)s",'
        '"module":"%(name)s","message":"%(message)s"}'
        if Config.is_production()
        else "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    logging.basicConfig(
        format=log_format,
        level=logging.INFO,
        stream=sys.stdout,
    )
    # 降低第三方庫日誌等級
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def _run_bot():
    """非同步啟動 Bot（支援健康檢查 + Graceful Shutdown）。"""
    logger = logging.getLogger(__name__)

    # 啟動健康檢查端點
    health_runner = None
    if Config.HEALTH_ENABLED:
        try:
            from utils.health import start_health_server
            health_runner = await start_health_server(Config.HEALTH_PORT)
        except Exception as e:
            logger.warning(f"⚠️ 健康檢查啟動失敗: {e}")

    # 建立 Bot Application
    app = create_bot_application()

    logger.info("✅ Bot 已啟動！等待指令中...")
    logger.info("📌 可用指令: /start, /report [TICKER], /watchlist, /watch, /unwatch")

    # 啟動 Bot
    if Config.BOT_MODE == "webhook":
        logger.info(f"🌐 使用 Webhook 模式: {Config.WEBHOOK_URL}")
        await app.initialize()
        await app.start()
        await app.updater.start_webhook(
            listen="0.0.0.0",
            port=int(Config.HEALTH_PORT) + 1,
            url_path="webhook",
            webhook_url=f"{Config.WEBHOOK_URL}/webhook",
            drop_pending_updates=True,
        )

        # 等待關閉信號
        stop_event = asyncio.Event()

        def _signal_handler():
            logger.info("📴 收到關閉信號，正在優雅關閉...")
            stop_event.set()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        await stop_event.wait()

        # 優雅關閉
        logger.info("⏳ 等待進行中的分析完成...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
    else:
        logger.info("📡 使用 Polling 模式")
        app.run_polling(drop_pending_updates=True)

    # 關閉健康檢查
    if health_runner:
        await health_runner.cleanup()
        logger.info("🏥 健康檢查端點已關閉")

    logger.info("👋 Bot 已完全關閉")


def main():
    """主程式進入點"""
    _setup_logging()
    logger = logging.getLogger(__name__)

    # 驗證環境變數
    logger.info("🔍 驗證環境變數...")
    Config.validate()
    logger.info("✅ 環境變數驗證通過")

    # 啟動 Bot
    logger.info("🚀 正在啟動零幻覺美股分析 Bot...")
    logger.info(f"📦 AI 模型: {Config.OPENAI_MODEL}")
    logger.info(f"🌍 環境: {Config.ENV}")
    logger.info(f"📡 模式: {Config.BOT_MODE}")

    if Config.BOT_MODE == "webhook":
        asyncio.run(_run_bot())
    else:
        # Polling 模式：直接用 run_polling（它自己管理 event loop）
        # 但先啟動健康檢查
        if Config.HEALTH_ENABLED:
            _run_polling_with_health()
        else:
            app = create_bot_application()
            logger.info("✅ Bot 已啟動！等待指令中...")
            logger.info("📌 可用指令: /start, /report, /watchlist, /watch, /unwatch")
            app.run_polling(drop_pending_updates=True)


def _run_polling_with_health():
    """Polling 模式下同時啟動健康檢查端點。"""
    logger = logging.getLogger(__name__)
    app = create_bot_application()

    async def _post_init(application):
        """Bot 啟動後的 hook，用來啟動健康檢查。"""
        try:
            from utils.health import start_health_server
            application.bot_data["health_runner"] = await start_health_server(
                Config.HEALTH_PORT
            )
        except Exception as e:
            logger.warning(f"⚠️ 健康檢查啟動失敗: {e}")

    async def _post_shutdown(application):
        """Bot 關閉後的 hook，用來關閉健康檢查。"""
        runner = application.bot_data.get("health_runner")
        if runner:
            await runner.cleanup()
            logger.info("🏥 健康檢查端點已關閉")
        logger.info("👋 Bot 已完全關閉")

    app.post_init = _post_init
    app.post_shutdown = _post_shutdown

    logger.info("✅ Bot 已啟動！等待指令中...")
    logger.info("📌 可用指令: /start, /report, /watchlist, /watch, /unwatch")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
