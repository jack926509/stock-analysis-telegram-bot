"""
分層快取模組（LRU 上限）
- raw_cache: 原始 API 數據快取（TTL 較長，減少 API 呼叫）
- report_cache: 組裝後報告快取（TTL 較短，確保即時性）
使用 OrderedDict 實現 LRU 淘汰。
"""

import time
import logging
from collections import OrderedDict

logger = logging.getLogger(__name__)

DEFAULT_RAW_TTL = 1800      # 30 分鐘
DEFAULT_REPORT_TTL = 300    # 5 分鐘
DEFAULT_MAX_ENTRIES = 100


class LRUCache:
    """帶 TTL 的 LRU 快取。"""

    def __init__(self, ttl: int, max_entries: int = DEFAULT_MAX_ENTRIES):
        self._ttl = ttl
        self._max = max_entries
        self._store: OrderedDict[str, tuple[object, float]] = OrderedDict()

    def get(self, key: str):
        """取得快取值。命中時移到最新位置。過期回傳 None。"""
        if key not in self._store:
            return None
        value, ts = self._store[key]
        if time.time() - ts > self._ttl:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value):
        """設定快取值。超過上限時淘汰最舊項目。"""
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (value, time.time())
        while len(self._store) > self._max:
            evicted_key, _ = self._store.popitem(last=False)
            logger.debug(f"LRU 淘汰: {evicted_key}")

    def invalidate(self, key: str):
        """手動移除快取項目。"""
        self._store.pop(key, None)

    def clear(self):
        """清空快取。"""
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)

    def get_age(self, key: str) -> int | None:
        """取得快取項目的存活秒數。"""
        if key not in self._store:
            return None
        _, ts = self._store[key]
        age = int(time.time() - ts)
        if age > self._ttl:
            del self._store[key]
            return None
        return age


raw_cache = LRUCache(ttl=DEFAULT_RAW_TTL, max_entries=DEFAULT_MAX_ENTRIES)
report_cache = LRUCache(ttl=DEFAULT_REPORT_TTL, max_entries=50)
