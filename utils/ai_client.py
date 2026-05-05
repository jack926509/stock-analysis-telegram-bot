"""
共用 LLM client（OpenRouter，OpenAI-compatible 介面）。

設計重點：
- 用 openai.AsyncOpenAI 指向 OpenRouter base url，所有 AI 模組共用同一條 HTTP
  連線池，並讓長 system prompt 可走 OpenRouter 的 cache_control passthrough。
- system_message() 把 system prompt 包成 OpenRouter 的 cache_control 格式：
  routed 到 Anthropic 模型時 OpenRouter 會把 hint 轉給上游，給我們 prompt
  cache 折扣（典型 ~70% input token 節省）；routed 到非 Anthropic 模型時，
  cache_control 會被忽略，但 message 結構仍合法。
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


def _build_default_headers() -> dict:
    """OpenRouter 推薦（但非必填）的歸屬 headers。"""
    headers: dict = {}
    if Config.OPENROUTER_HTTP_REFERER:
        headers["HTTP-Referer"] = Config.OPENROUTER_HTTP_REFERER
    if Config.OPENROUTER_APP_TITLE:
        headers["X-Title"] = Config.OPENROUTER_APP_TITLE
    return headers


def get_ai_client() -> openai.AsyncOpenAI:
    """Return the shared AsyncOpenAI client pointed at OpenRouter."""
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(
            api_key=Config.OPENROUTER_API_KEY,
            base_url=Config.OPENROUTER_BASE_URL,
            timeout=_HTTPX_TIMEOUT,
            max_retries=2,
            default_headers=_build_default_headers() or None,
        )
    return _client


def system_message(prompt: str, *, cache: bool = True) -> dict:
    """
    產出 chat.completions 用的 system message。

    cache=True 時用 OpenRouter cache_control 寫法：routed 到 Anthropic 模型時
    會走 prompt caching；其他模型（OpenAI / Gemini 等）會把 content 視為純文字
    陣列，仍合法。
    """
    if cache:
        return {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    return {"role": "system", "content": prompt}


def extract_text(response) -> str:
    """從 chat.completions 回應抓 assistant 的純文字輸出。"""
    if not response.choices:
        return ""
    msg = response.choices[0].message
    return (msg.content or "").strip() if msg.content is not None else ""


def extract_usage(response) -> tuple[int, int]:
    """回傳 (prompt_tokens, completion_tokens)；缺值補 0。"""
    usage = getattr(response, "usage", None)
    if not usage:
        return 0, 0
    pt = getattr(usage, "prompt_tokens", 0) or 0
    ct = getattr(usage, "completion_tokens", 0) or 0
    return pt, ct
