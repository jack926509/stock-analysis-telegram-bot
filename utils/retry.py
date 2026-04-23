"""
API 指數退避重試模組
為不穩定的外部 API 呼叫提供自動重試機制。
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 1
DEFAULT_BASE_DELAY = 2.0


async def retry_async_call(coro_func, *args,
                           max_retries: int = DEFAULT_MAX_RETRIES,
                           base_delay: float = DEFAULT_BASE_DELAY,
                           source_name: str = "",
                           **kwargs):
    """
    對非同步呼叫進行指數退避重試。

    用法:
        result = await retry_async_call(
            asyncio.to_thread, client.quote, ticker,
            source_name="Finnhub"
        )

    Args:
        coro_func: 要呼叫的非同步函數
        *args: 傳給 coro_func 的位置參數
        max_retries: 最大重試次數
        base_delay: 基礎等待秒數（每次重試 *2）
        source_name: 數據源名稱（用於日誌）
    """
    last_exception = None
    name = source_name or str(coro_func)

    for attempt in range(max_retries + 1):
        try:
            return await coro_func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"[{name}] 第 {attempt + 1} 次失敗: {e}，"
                    f"{delay:.0f}s 後重試..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"[{name}] 已重試 {max_retries} 次仍失敗: {e}"
                )

    raise last_exception
