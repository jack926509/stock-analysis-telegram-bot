"""
Per-user API 請求限制模組
防止單一使用者濫用 Bot 資源。
"""

import time
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# 設定：每位使用者每分鐘最多 N 次請求
MAX_REQUESTS_PER_MINUTE = 5
WINDOW_SECONDS = 60


class RateLimiter:
    """滑動窗口 rate limiter。"""

    def __init__(self, max_requests: int = MAX_REQUESTS_PER_MINUTE,
                 window: int = WINDOW_SECONDS):
        self._max_requests = max_requests
        self._window = window
        self._requests: dict[int, list[float]] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        """檢查使用者是否可以發送請求。"""
        now = time.time()
        cutoff = now - self._window

        # 清理過期記錄
        self._requests[user_id] = [
            t for t in self._requests[user_id] if t > cutoff
        ]

        if len(self._requests[user_id]) >= self._max_requests:
            return False

        self._requests[user_id].append(now)
        return True

    def remaining(self, user_id: int) -> int:
        """取得使用者剩餘可用請求數。"""
        now = time.time()
        cutoff = now - self._window
        active = [t for t in self._requests[user_id] if t > cutoff]
        return max(0, self._max_requests - len(active))

    def retry_after(self, user_id: int) -> int:
        """取得使用者需要等待的秒數。"""
        if not self._requests[user_id]:
            return 0
        now = time.time()
        cutoff = now - self._window
        active = [t for t in self._requests[user_id] if t > cutoff]
        if len(active) < self._max_requests:
            return 0
        oldest = min(active)
        return max(0, int(oldest + self._window - now) + 1)


# 全域 rate limiter 實例
rate_limiter = RateLimiter()
