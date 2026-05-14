"""
設定管理模組
集中管理所有環境變數與應用設定，支援 dev/staging/production 環境。

設計原則：
- 所有環境變數在這裡集中讀取一次，避免散落各處
- 啟動時呼叫 Config.validate() 失敗即退出（fail-fast）
- 純 class attributes，匯入即可用；不引入 Pydantic 額外依賴
"""

from __future__ import annotations

import logging
import os
import sys
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Config:
    """應用程式設定"""

    # ── 環境 ──
    ENV: str = os.getenv("APP_ENV", "production")  # dev / staging / production

    # ── Slack ──
    # Bot Token (xoxb-…)：呼叫 Web API 用，scopes 詳見 README
    SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
    # App-Level Token (xapp-…)：Socket Mode 用，scope: connections:write
    SLACK_APP_TOKEN: str = os.getenv("SLACK_APP_TOKEN", "")
    # Signing Secret：HTTP 模式驗 webhook 用；Socket Mode 可空但 production 建議設
    SLACK_SIGNING_SECRET: str = os.getenv("SLACK_SIGNING_SECRET", "")
    # 預設播報頻道：留空時 bot 只在被 mention / DM / slash command 的當前頻道回覆
    SLACK_DEFAULT_CHANNEL: str = os.getenv("SLACK_DEFAULT_CHANNEL", "")

    # ── Finnhub ──
    FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "")

    # ── FMP (Financial Modeling Prep) ──
    FMP_API_KEY: str = os.getenv("FMP_API_KEY", "")

    # ── Tavily ──
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

    # ── LLM (OpenAI 官方 API) ──
    # OpenAI 對長 system prompt（≥1024 tokens）會自動 prompt caching，無需額外 hint，
    # 命中時 input tokens 享 50% 折扣。
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "")
    OPENAI_ORG_ID: str = os.getenv("OPENAI_ORG_ID", "")
    OPENAI_PROJECT_ID: str = os.getenv("OPENAI_PROJECT_ID", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
    OPENAI_PLANNER_MODEL: str = os.getenv("OPENAI_PLANNER_MODEL", "gpt-4o-mini")

    # ── 健康檢查 ──
    HEALTH_PORT: int = _int("HEALTH_PORT", 8080)
    HEALTH_ENABLED: bool = _bool("HEALTH_ENABLED", True)

    # ── Rate Limiting ──
    RATE_LIMIT_PER_MINUTE: int = _int("RATE_LIMIT_PER_MINUTE", 5)

    # ── 並發控制 ──
    ANALYSIS_CONCURRENCY: int = _int("ANALYSIS_CONCURRENCY", 3)

    # ── 資料庫（Postgres）──
    # 標準 DSN：postgresql://user:pass@host:5432/dbname
    # Zeabur Postgres add-on 會自動注入 DATABASE_URL；若沒注入會回退到 POSTGRES_CONNECTION_STRING
    DATABASE_URL: str = (
        os.getenv("DATABASE_URL")
        or os.getenv("POSTGRES_CONNECTION_STRING", "")
    )
    # 連線池大小：production 預設提高到 15，因為 newsletter + watchlist + tenk
    # 可能在背景並發查詢。可透過 DB_POOL_MAX 環境變數覆寫。
    DB_POOL_MIN: int = _int("DB_POOL_MIN", 1)
    DB_POOL_MAX: int = _int("DB_POOL_MAX", 15)
    # 連線取得逾時（秒）— 避免在 pool 滿時無限等待
    DB_POOL_TIMEOUT: int = _int("DB_POOL_TIMEOUT", 30)

    # ── 快取 ──
    CACHE_TTL: int = _int("CACHE_TTL", 300)

    # ── 同業比較 / 歷史 / 日報 ──
    PEER_COMPARISON_ENABLED: bool = _bool("PEER_COMPARISON_ENABLED", True)
    HISTORY_ENABLED: bool = _bool("HISTORY_ENABLED", True)
    NEWSLETTER_ENABLED: bool = _bool("NEWSLETTER_ENABLED", True)

    # ── 10-K / 10-Q 深度分析（tenk）──
    TENK_ENABLED: bool = _bool("TENK_ENABLED", True)
    TENK_CACHE_DIR: str = os.getenv("TENK_CACHE_DIR", "data/tenk_cache")
    TENK_OUTPUT_DIR: str = os.getenv("TENK_OUTPUT_DIR", "data/tenk_output")
    TENK_DAILY_LIMIT: int = _int("TENK_DAILY_LIMIT", 3)
    TENK_REPORT_TTL_DAYS: int = _int("TENK_REPORT_TTL_DAYS", 180)
    TENK_PIPELINE_TIMEOUT: int = _int("TENK_PIPELINE_TIMEOUT", 1800)
    TENK_SEC_USER_AGENT: str = os.getenv(
        "TENK_SEC_USER_AGENT", "stock-analysis-slack-bot xieh.gemini@gmail.com"
    )
    LLAMA_CLOUD_API_KEY: str = os.getenv("LLAMA_CLOUD_API_KEY", "")

    @classmethod
    def validate(cls) -> None:
        """驗證所有必要的環境變數是否已設定。失敗即 exit(1)。

        日誌走 stderr / logger，避免在 production 用 print() 漏到 stdout
        被 log aggregator 視為 INFO 級別。
        """
        missing: list[str] = []
        if not cls.SLACK_BOT_TOKEN:
            missing.append("SLACK_BOT_TOKEN (xoxb-…)")
        if not cls.SLACK_APP_TOKEN:
            missing.append("SLACK_APP_TOKEN (xapp-…)")
        if not cls.FINNHUB_API_KEY:
            missing.append("FINNHUB_API_KEY")
        if not cls.TAVILY_API_KEY:
            missing.append("TAVILY_API_KEY")
        if not cls.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY")
        if not cls.DATABASE_URL:
            missing.append("DATABASE_URL (postgresql://user:pass@host:5432/dbname)")

        # Token 前綴/長度檢查
        if cls.SLACK_BOT_TOKEN and not cls.SLACK_BOT_TOKEN.startswith("xoxb-"):
            missing.append("SLACK_BOT_TOKEN 應以 xoxb- 開頭")
        if cls.SLACK_APP_TOKEN and not cls.SLACK_APP_TOKEN.startswith("xapp-"):
            missing.append("SLACK_APP_TOKEN 應以 xapp- 開頭（Socket Mode App-Level Token）")

        if missing:
            err = "❌ 缺少必要環境變數: " + ", ".join(missing)
            hint = "請在 .env 檔案中設定以上變數（參考 .env.example）"
            # 直接寫 stderr，避免依賴 logger 在 validate 前的初始化順序
            print(err, file=sys.stderr)
            print(hint, file=sys.stderr)
            sys.exit(1)

        # Production 警告：Signing Secret 雖然 Socket Mode 不必要，但若未來切 HTTP
        # webhook 才不會留下安全洞
        if cls.is_production() and not cls.SLACK_SIGNING_SECRET:
            logger.warning(
                "⚠️ production 環境未設定 SLACK_SIGNING_SECRET。"
                "Socket Mode 可省略，但切換到 HTTP webhook 模式前務必補上。"
            )

    @classmethod
    def is_dev(cls) -> bool:
        return cls.ENV == "dev"

    @classmethod
    def is_production(cls) -> bool:
        return cls.ENV == "production"
