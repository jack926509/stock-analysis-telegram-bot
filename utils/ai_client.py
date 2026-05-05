"""
共用 LLM client（OpenAI 官方 API）。

設計重點：
- 用 openai.AsyncOpenAI 共用一條 HTTP 連線池，避免每個模組重建 client。
- OpenAI 對 ≥1024 tokens 的 system prompt 會自動 prompt caching（命中時
  input tokens 享 50% 折扣），呼叫端不需手動加 hint，只要 system prompt
  保持穩定即可命中。
- system_message() 把 system prompt 包成標準 OpenAI message 結構，方便 caller
  直接塞進 messages 陣列首位。
- extract_text() / extract_usage() 把 chat.completions 的回應抽成統一介面，
  各 caller 不必碰 SDK 的 attribute 細節。
"""

import httpx
import openai

from config import Config

_client: openai.AsyncOpenAI | None = None

# 連線/讀取上限避免上游網路異常時無限掛起；個別呼叫端仍可用
# chat.completions.create(timeout=...) 覆寫此預設。
_HTTPX_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)


def get_ai_client() -> openai.AsyncOpenAI:
    """Return the shared AsyncOpenAI client（lazy-init）。"""
    global _client
    if _client is None:
        kwargs: dict = {
            "api_key": Config.OPENAI_API_KEY,
            "timeout": _HTTPX_TIMEOUT,
            "max_retries": 2,
        }
        if Config.OPENAI_BASE_URL:
            kwargs["base_url"] = Config.OPENAI_BASE_URL
        if Config.OPENAI_ORG_ID:
            kwargs["organization"] = Config.OPENAI_ORG_ID
        if Config.OPENAI_PROJECT_ID:
            kwargs["project"] = Config.OPENAI_PROJECT_ID
        _client = openai.AsyncOpenAI(**kwargs)
    return _client


def system_message(prompt: str) -> dict:
    """產出 chat.completions 用的 system message。"""
    return {"role": "system", "content": prompt}


def extract_text(response) -> str:
    """從 chat.completions 回應抓 assistant 的純文字輸出。"""
    if not response.choices:
        return ""
    msg = response.choices[0].message
    return (msg.content or "").strip() if msg.content is not None else ""


def extract_usage(response) -> tuple[int, int, int]:
    """
    回傳 (prompt_tokens, completion_tokens, cached_prompt_tokens)；缺值補 0。

    cached_prompt_tokens 是 OpenAI 自動 prompt caching 命中的 input tokens 數，
    用來在帳單上對應 50% 折扣。
    """
    usage = getattr(response, "usage", None)
    if not usage:
        return 0, 0, 0
    pt = getattr(usage, "prompt_tokens", 0) or 0
    ct = getattr(usage, "completion_tokens", 0) or 0
    # OpenAI 在 usage.prompt_tokens_details.cached_tokens 給出快取命中數
    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    return pt, ct, cached
