"""
Shared Anthropic AsyncAnthropic client with prompt-caching helper.

All AI modules (analyzer / planner / writer) should import from here so:
- A single HTTP connection pool is reused across AI call sites.
- The long system prompt can be wrapped with cache_control=ephemeral,
  letting Anthropic Prompt Caching amortize the ~2KB system prompt cost
  across many analyses (typically ~70% input-token savings after warm-up).
"""

import anthropic
import httpx

from config import Config

_client: anthropic.AsyncAnthropic | None = None

# 連線/讀取上限避免在 Anthropic 端網路異常時無限掛起；
# 個別呼叫端仍可用 messages.create(timeout=...) 覆寫此預設。
_HTTPX_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)


def get_ai_client() -> anthropic.AsyncAnthropic:
    """Return the shared AsyncAnthropic client (lazy-init on first call)."""
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(
            api_key=Config.ANTHROPIC_API_KEY,
            timeout=_HTTPX_TIMEOUT,
            max_retries=2,
        )
    return _client


def cached_system(prompt: str) -> list[dict]:
    """
    Wrap a long system prompt with cache_control=ephemeral for Prompt Caching.
    Caller passes this list directly as `system=` to messages.create().
    """
    return [
        {
            "type": "text",
            "text": prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]
