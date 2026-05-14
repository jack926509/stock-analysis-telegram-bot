"""
Slack Web API 呼叫的可靠性包裝層

提供：
- post_message：包了 retry + Retry-After header + 連續呼叫間最低 spacing
- post_message_thread：同上，自動帶 thread_ts

設計：
- Slack Tier 1 預設 ~1 req/sec／channel；遇 429 必須讀 Retry-After
- 非 429 的 SlackApiError（如 channel_not_found）不重試
- 連續送多則訊息（_send_report）時，加 ~100ms 間距避免 burst
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BASE_DELAY = 1.0
_MAX_DELAY = 30.0
_INTER_MSG_SPACING = 0.1  # 連送訊息間最低間距，緩解 channel rate-limit


async def _post_with_retry(
    client: AsyncWebClient,
    /,
    **kwargs: Any,
) -> Any:
    """chat_postMessage 通用 retry 包裝。失敗時拋出原始 SlackApiError。"""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return await client.chat_postMessage(**kwargs)
        except SlackApiError as e:
            last_exc = e
            resp = e.response or {}
            status = getattr(resp, "status_code", None)
            error_code = resp.get("error") if hasattr(resp, "get") else None

            # 429 / 5xx 才重試；其他（channel_not_found、invalid_blocks）直接拋
            retryable = (status == 429) or (
                isinstance(status, int) and 500 <= status < 600
            ) or (error_code in {"ratelimited", "service_unavailable"})

            if not retryable or attempt >= _MAX_RETRIES:
                logger.error(
                    f"[Slack] chat_postMessage 失敗（不重試）: "
                    f"status={status} error={error_code} msg={e}"
                )
                raise

            # 優先用 Retry-After
            retry_after = None
            headers = getattr(resp, "headers", None) or {}
            if headers:
                try:
                    retry_after = float(headers.get("Retry-After") or 0) or None
                except (TypeError, ValueError):
                    retry_after = None

            delay = retry_after if retry_after else min(
                _MAX_DELAY, _BASE_DELAY * (2 ** attempt)
            )
            delay += random.uniform(0, 0.5)
            logger.warning(
                f"[Slack] 第 {attempt + 1} 次失敗（status={status} error={error_code}），"
                f"{delay:.1f}s 後重試..."
            )
            await asyncio.sleep(delay)

    assert last_exc is not None
    raise last_exc


async def post_message(
    client: AsyncWebClient,
    channel: str,
    *,
    text: str,
    blocks: list[dict] | None = None,
    thread_ts: str | None = None,
    unfurl_links: bool = False,
    unfurl_media: bool = False,
    **kwargs: Any,
) -> Any:
    """送一則訊息（含 retry）。"""
    payload: dict[str, Any] = {
        "channel": channel,
        "text": text,
        "unfurl_links": unfurl_links,
        "unfurl_media": unfurl_media,
    }
    if blocks is not None:
        payload["blocks"] = blocks
    if thread_ts:
        payload["thread_ts"] = thread_ts
    payload.update(kwargs)
    return await _post_with_retry(client, **payload)


async def post_messages_sequence(
    client: AsyncWebClient,
    channel: str,
    items: list[dict[str, Any]],
    *,
    thread_ts: str | None = None,
) -> None:
    """
    連送多則訊息，每則間隔 ~100ms 避免 channel-level burst limit。
    每個 item 需含 text / blocks。
    """
    for i, item in enumerate(items):
        if i > 0:
            await asyncio.sleep(_INTER_MSG_SPACING)
        await post_message(
            client,
            channel,
            text=item.get("text", ""),
            blocks=item.get("blocks"),
            thread_ts=thread_ts,
        )
