"""
XAUUSD 交易系統設定管理模組
獨立的環境變數與常數管理。
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()


class ForexConfig:
    """外匯交易系統設定"""

    # ── 環境 ──
    ENV: str = os.getenv("APP_ENV", "production")

    # ── Telegram ──
    TELEGRAM_BOT_TOKEN: str = os.getenv("FOREX_TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("FOREX_TELEGRAM_CHAT_ID", "")

    # ── Anthropic ──
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("FOREX_ANTHROPIC_MODEL", "claude-sonnet-4-6")

    # ── Tavily ──
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

    # ── 資料庫 ──
    DB_PATH: str = os.getenv("FOREX_DB_PATH", "forex_trading.db")

    # ── 模擬倉 ──
    INITIAL_CAPITAL: float = float(os.getenv("FOREX_INITIAL_CAPITAL", "100000"))
    RISK_PER_TRADE: float = float(os.getenv("FOREX_RISK_PER_TRADE", "0.01"))
    MAX_RISK_PER_TRADE: float = float(os.getenv("FOREX_MAX_RISK_PER_TRADE", "0.02"))
    MAX_OPEN_POSITIONS: int = int(os.getenv("FOREX_MAX_OPEN_POSITIONS", "3"))

    # ── 交易條件 ──
    SPREAD: float = float(os.getenv("FOREX_SPREAD", "0.30"))
    SLIPPAGE_MAX: float = float(os.getenv("FOREX_SLIPPAGE_MAX", "0.15"))
    MIN_RR_RATIO: float = float(os.getenv("FOREX_MIN_RR_RATIO", "2.0"))

    # ── 排程 ──
    SIGNAL_CHECK_INTERVAL: int = int(os.getenv("FOREX_SIGNAL_CHECK_INTERVAL", "300"))
    POSITION_CHECK_INTERVAL: int = int(os.getenv("FOREX_POSITION_CHECK_INTERVAL", "60"))
    DAILY_ANALYSIS_HOUR: int = int(os.getenv("FOREX_DAILY_ANALYSIS_HOUR", "6"))
    WEEKLY_SUMMARY_DAY: int = int(os.getenv("FOREX_WEEKLY_SUMMARY_DAY", "0"))

    # ── yfinance 符號 ──
    GOLD_SYMBOL: str = os.getenv("FOREX_GOLD_SYMBOL", "XAUUSD=X")
    GOLD_FUTURES: str = os.getenv("FOREX_GOLD_FUTURES", "GC=F")
    DXY_SYMBOL: str = os.getenv("FOREX_DXY_SYMBOL", "DX-Y.NYB")

    # ── TradingView ──
    TV_SCREENER: str = os.getenv("FOREX_TV_SCREENER", "forex")
    TV_EXCHANGE: str = os.getenv("FOREX_TV_EXCHANGE", "FX_IDC")
    TV_SYMBOL: str = os.getenv("FOREX_TV_SYMBOL", "XAUUSD")

    # ── 模式 ──
    MODE: str = os.getenv("FOREX_MODE", "simulation")

    # ── 健康檢查 ──
    HEALTH_PORT: int = int(os.getenv("FOREX_HEALTH_PORT", "8081"))

    # ── 時段定義（UTC）──
    ASIAN_SESSION_START: int = 0   # 00:00 UTC
    ASIAN_SESSION_END: int = 8     # 08:00 UTC
    LONDON_SESSION_START: int = 8  # 08:00 UTC
    LONDON_SESSION_END: int = 16   # 16:00 UTC
    NY_SESSION_START: int = 13     # 13:00 UTC
    NY_SESSION_END: int = 22       # 22:00 UTC

    # ── 指標預設值 ──
    DEFAULT_RSI_PERIOD: int = 14
    DEFAULT_MACD_FAST: int = 12
    DEFAULT_MACD_SLOW: int = 26
    DEFAULT_MACD_SIGNAL: int = 9
    DEFAULT_BB_PERIOD: int = 20
    DEFAULT_BB_STD: float = 2.0
    DEFAULT_ADX_PERIOD: int = 14
    DEFAULT_ATR_PERIOD: int = 14

    @classmethod
    def validate(cls) -> None:
        """驗證必要環境變數。"""
        missing = []
        if not cls.TELEGRAM_BOT_TOKEN:
            missing.append("FOREX_TELEGRAM_BOT_TOKEN")
        if not cls.TELEGRAM_CHAT_ID:
            missing.append("FOREX_TELEGRAM_CHAT_ID")
        if not cls.ANTHROPIC_API_KEY:
            missing.append("ANTHROPIC_API_KEY")

        if missing:
            print(f"缺少必要環境變數: {', '.join(missing)}")
            sys.exit(1)

    @classmethod
    def is_dev(cls) -> bool:
        return cls.ENV == "dev"

    @classmethod
    def is_production(cls) -> bool:
        return cls.ENV == "production"

    @classmethod
    def get_session(cls, hour: int) -> str:
        """根據 UTC 小時判斷當前交易時段。"""
        if cls.ASIAN_SESSION_START <= hour < cls.ASIAN_SESSION_END:
            return "asian"
        elif cls.LONDON_SESSION_START <= hour < cls.NY_SESSION_START:
            return "london"
        elif cls.NY_SESSION_START <= hour < cls.NY_SESSION_END:
            return "new_york"
        else:
            return "off_hours"
