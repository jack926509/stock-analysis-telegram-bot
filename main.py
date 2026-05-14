"""
美股 Slack 分析機器人 — 程式進入點

啟動方式：python main.py

設計：
- Slack Bolt AsyncApp + AsyncSocketModeHandler（不需公開 URL）
- 同時啟動 aiohttp 健康檢查（/health）給 Zeabur 監控
- SIGINT / SIGTERM 收到後優雅關閉：取消 socket connection、關健康檢查
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from config import Config

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    """設定結構化日誌。生產環境輸出 JSON 行（方便 log aggregator）。"""
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
    logging.getLogger("slack_bolt").setLevel(logging.INFO)
    logging.getLogger("slack_sdk").setLevel(logging.WARNING)


async def _run() -> None:
    """非同步啟動 Bot（Postgres pool + Socket Mode + 健康檢查 + Graceful Shutdown）。"""
    # 1. Postgres pool + schema migration（最先做，後面所有 handler 都依賴它）
    #    給 90s buffer 以容忍 Zeabur Postgres 冷啟動；超過就放棄並退出讓 Zeabur 重啟
    from utils.database import init_db
    try:
        await asyncio.wait_for(init_db(), timeout=90)
    except asyncio.TimeoutError:
        logger.error("❌ init_db 逾時（90s），Postgres 不可達；退出讓 Zeabur 重啟容器")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ init_db 失敗: {e}", exc_info=True)
        sys.exit(1)

    # 2. 健康檢查
    health_runner = None
    if Config.HEALTH_ENABLED:
        try:
            from utils.health import start_health_server
            health_runner = await start_health_server(Config.HEALTH_PORT)
        except Exception as e:
            logger.warning(f"⚠️ 健康檢查啟動失敗: {e}")

    # 3. 非阻塞清理舊 tenk 檔案
    if Config.TENK_ENABLED:
        from utils.cleanup import cleanup_tenk_files
        asyncio.create_task(cleanup_tenk_files())

    # 4. 啟動時觸發日報生成（若啟用，非阻塞）
    if Config.NEWSLETTER_ENABLED:
        asyncio.create_task(_run_newsletter_on_startup())

    # 5. 建立 Slack App + Socket Mode handler
    from bot.slack_bot import create_slack_app
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    slack_app = create_slack_app()
    handler = AsyncSocketModeHandler(slack_app, Config.SLACK_APP_TOKEN)

    logger.info("✅ Slack Bot 已啟動（Socket Mode）")
    logger.info(
        "📌 已註冊 slash commands: "
        "/start /help /report /tenk /chart /compare /news "
        "/watchlist /scan /watch /unwatch /stats /cancel"
    )

    # 5. SIGINT / SIGTERM 優雅關閉
    stop_event = asyncio.Event()

    def _on_signal() -> None:
        logger.info("📴 收到關閉信號，正在優雅關閉...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            # Windows 上某些 signal 不支援；忽略
            pass

    # 6. 啟動 Socket Mode (背景 task) + 等待停止信號
    handler_task = asyncio.create_task(handler.start_async())
    try:
        await stop_event.wait()
    finally:
        logger.info("⏳ 關閉 Slack Socket Mode 連線...")
        await handler.close_async()
        handler_task.cancel()
        try:
            await handler_task
        except (asyncio.CancelledError, Exception):
            pass
        if health_runner:
            await health_runner.cleanup()
            logger.info("🏥 健康檢查端點已關閉")
        # 最後關 DB pool，確保所有進行中的查詢已經結束
        from utils.database import close_db
        await close_db()
        logger.info("👋 Bot 已完全關閉")


async def _run_newsletter_on_startup() -> None:
    """啟動時非同步執行日報生成。

    給 5 秒延遲讓 Slack handler 先 ready；用 timeout 避免長尾 AI 呼叫
    把背景 task 拖到容器被殺。
    """
    newsletter_logger = logging.getLogger("newsletter")
    # 延遲 5 秒，讓主流程（Socket Mode 連線、健康檢查）先 ready
    await asyncio.sleep(5)
    try:
        from app.pipeline import run_newsletter_pipeline
        newsletter_logger.info("📰 啟動時觸發日報生成...")
        # 10 分鐘上限：超過就放棄等下個 schedule
        newsletter = await asyncio.wait_for(
            run_newsletter_pipeline(), timeout=600
        )
        if newsletter:
            newsletter_logger.info(f"✅ 日報生成成功（{len(newsletter)} 字）")
        else:
            newsletter_logger.warning("⚠️ 日報生成返回空結果")
    except asyncio.TimeoutError:
        newsletter_logger.warning("⚠️ 日報生成逾時（>10min），放棄本次啟動觸發")
    except Exception as e:
        newsletter_logger.error(f"❌ 日報生成失敗: {e}", exc_info=True)


def main() -> None:
    _setup_logging()
    logger.info("🔍 驗證環境變數...")
    Config.validate()
    logger.info("✅ 環境變數驗證通過")

    logger.info("🚀 正在啟動美股分析 Slack Bot...")
    logger.info(f"📦 LLM (OpenAI): {Config.OPENAI_MODEL}")
    logger.info(f"🌍 環境: {Config.ENV}")
    logger.info("📡 模式: Socket Mode")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("👋 收到 KeyboardInterrupt，退出")


if __name__ == "__main__":
    main()
