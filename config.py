"""
設定管理模組
集中管理所有環境變數與應用設定。
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()


class Config:
    """應用程式設定"""

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

    # Finnhub
    FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "")

    # Tavily
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")

    @classmethod
    def validate(cls) -> None:
        """驗證所有必要的環境變數是否已設定。"""
        missing = []
        if not cls.TELEGRAM_BOT_TOKEN:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not cls.FINNHUB_API_KEY:
            missing.append("FINNHUB_API_KEY")
        if not cls.TAVILY_API_KEY:
            missing.append("TAVILY_API_KEY")
        if not cls.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY")

        if missing:
            print(f"❌ 缺少必要環境變數: {', '.join(missing)}")
            print("請在 .env 檔案中設定以上變數（參考 .env.example）")
            sys.exit(1)
