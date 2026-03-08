"""
零幻覺美股 Telegram 分析機器人
程式進入點

啟動方式：python main.py
"""

import logging

from config import Config
from bot.telegram_bot import create_bot_application


def main():
    """主程式進入點"""
    # 設定 logging
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    logger = logging.getLogger(__name__)

    # 驗證環境變數
    logger.info("🔍 驗證環境變數...")
    Config.validate()
    logger.info("✅ 環境變數驗證通過")

    # 建立並啟動 Bot
    logger.info("🚀 正在啟動零幻覺美股分析 Bot...")
    app = create_bot_application()

    logger.info("✅ Bot 已啟動！等待指令中...")
    logger.info("📌 可用指令: /start, /report [TICKER]")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
