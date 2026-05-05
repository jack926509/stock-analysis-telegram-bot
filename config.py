"""
設定管理模組
集中管理所有環境變數與應用設定。
支援 dev/staging/production 環境配置。
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()


class Config:
    """應用程式設定"""

    # ── 環境 ──
    ENV: str = os.getenv("APP_ENV", "production")  # dev / staging / production

    # ── Telegram ──
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    BOT_MODE: str = os.getenv("BOT_MODE", "polling")  # polling / webhook
    WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")  # Webhook 模式需要設定

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
    # 自訂 base URL（如 Azure OpenAI / 自架 proxy）；空字串走 SDK 預設
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "")
    OPENAI_ORG_ID: str = os.getenv("OPENAI_ORG_ID", "")
    OPENAI_PROJECT_ID: str = os.getenv("OPENAI_PROJECT_ID", "")
    # 主分析模型 — 需要較強推理
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
    # 結構化任務（newsletter planner / tenk 中的 JSON 萃取與分類）— 用 mini 省錢
    OPENAI_PLANNER_MODEL: str = os.getenv("OPENAI_PLANNER_MODEL", "gpt-4o-mini")

    # ── 健康檢查 ──
    HEALTH_PORT: int = int(os.getenv("HEALTH_PORT", "8080"))
    HEALTH_ENABLED: bool = os.getenv("HEALTH_ENABLED", "true").lower() == "true"

    # ── Rate Limiting ──
    RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "5"))

    # ── 資料庫 ──
    DB_PATH: str = os.getenv("DB_PATH", "bot_data.db")

    # ── 快取 ──
    CACHE_TTL: int = int(os.getenv("CACHE_TTL", "300"))  # 秒

    # ── 同業比較 ──
    PEER_COMPARISON_ENABLED: bool = os.getenv("PEER_COMPARISON_ENABLED", "true").lower() == "true"

    # ── 歷史回測 ──
    HISTORY_ENABLED: bool = os.getenv("HISTORY_ENABLED", "true").lower() == "true"

    # ── Newsletter 日報 ──
    NEWSLETTER_ENABLED: bool = os.getenv("NEWSLETTER_ENABLED", "true").lower() == "true"

    # ── 10-K / 10-Q 深度分析（tenk）──
    # 寫死預設值，Zeabur 不需要設定新環境變數
    TENK_ENABLED: bool = True
    TENK_CACHE_DIR: str = "data/tenk_cache"  # 財報 HTM、章節切割快取
    TENK_OUTPUT_DIR: str = "data/tenk_output"  # 報告 markdown + context log
    TENK_DAILY_LIMIT: int = 3  # 每用戶每日次數
    TENK_REPORT_TTL_DAYS: int = 180  # 半年內不重跑
    TENK_PIPELINE_TIMEOUT: int = 1800  # 單次最長 30 分鐘
    # SEC EDGAR 要求 User-Agent 含可聯絡資訊（避免被擋）
    TENK_SEC_USER_AGENT: str = "stock-analysis-telegram-bot xieh.gemini@gmail.com"
    # LlamaParse 是 doc_converter 的 fallback，沒有就走 markitdown（保留 env 讀取，未設則停用）
    LLAMA_CLOUD_API_KEY: str = os.getenv("LLAMA_CLOUD_API_KEY", "")

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

        if cls.BOT_MODE == "webhook" and not cls.WEBHOOK_URL:
            missing.append("WEBHOOK_URL (webhook 模式必需)")

        if missing:
            print(f"❌ 缺少必要環境變數: {', '.join(missing)}")
            print("請在 .env 檔案中設定以上變數（參考 .env.example）")
            sys.exit(1)

    @classmethod
    def is_dev(cls) -> bool:
        return cls.ENV == "dev"

    @classmethod
    def is_production(cls) -> bool:
        return cls.ENV == "production"
