"""
API 指數退避重試模組
為不穩定的外部 API 呼叫提供自動重試機制。

設計：
- 4xx 用戶端錯誤（401/403/404/422）不重試，浪費 quota 無意義
- 429 / 5xx / 連線錯誤才重試
- 退避加 jitter（避免 thundering herd）
"""

import asyncio
import logging
import random

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 2
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 30.0

# 不可重試的 HTTP 狀態（4xx 用戶端錯誤，retry 無用）
_NON_RETRYABLE_STATUS = {400, 401, 403, 404, 405, 422}

# 不可重試錯誤訊息關鍵字（針對 SDK 未暴露 status_code 的情況）
_NON_RETRYABLE_KEYWORDS = (
    "unauthorized",
    "invalid api key",
    "missing api key",
    "forbidden",
    "not found",
    "invalid token",
    "authentication",
)


def _is_non_retryable(exc: Exception) -> bool:
    """判斷例外是否屬於不可重試類別。"""
    # 1. httpx.HTTPStatusError → 看 response.status_code
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status_code, int) and status_code in _NON_RETRYABLE_STATUS:
        return True

    # 2. 部分 SDK 把錯誤訊息丟在 args[0]，比對關鍵字
    msg = str(exc).lower()
    return any(kw in msg for kw in _NON_RETRYABLE_KEYWORDS)


async def retry_async_call(coro_func, *args,
                           max_retries: int = DEFAULT_MAX_RETRIES,
                           base_delay: float = DEFAULT_BASE_DELAY,
                           max_delay: float = DEFAULT_MAX_DELAY,
                           source_name: str = "",
                           **kwargs):
    """
    對非同步呼叫進行指數退避重試（含 jitter）。

    用法:
        result = await retry_async_call(
            asyncio.to_thread, client.quote, ticker,
            source_name="Finnhub"
        )

    Args:
        coro_func: 要呼叫的非同步函數
        *args: 傳給 coro_func 的位置參數
        max_retries: 最大重試次數
        base_delay: 基礎等待秒數（每次重試 *2 + jitter）
        max_delay: 單次退避上限秒數
        source_name: 數據源名稱（用於日誌）
    """
    last_exception: Exception | None = None
    name = source_name or str(coro_func)

    for attempt in range(max_retries + 1):
        try:
            return await coro_func(*args, **kwargs)
        except Exception as e:
            last_exception = e

            # 認證/客戶端錯誤：直接失敗，避免燒 quota
            if _is_non_retryable(e):
                logger.error(
                    f"[{name}] 不可重試錯誤（auth/4xx），中止: {e}"
                )
                raise

            if attempt < max_retries:
                # exponential backoff + 1s 上限 jitter
                delay = min(max_delay, base_delay * (2 ** attempt)) + random.uniform(0, 1)
                logger.warning(
                    f"[{name}] 第 {attempt + 1} 次失敗: {e}，"
                    f"{delay:.1f}s 後重試..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"[{name}] 已重試 {max_retries} 次仍失敗: {e}"
                )

    assert last_exception is not None
    raise last_exception
