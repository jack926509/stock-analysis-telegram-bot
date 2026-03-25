"""
統一數據存取層
協調所有數據源，提供快取和統一介面。
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import pandas as pd

from forex_trading.config import ForexConfig
from forex_trading.data.fetcher_yfinance import (
    fetch_gold_ohlcv,
    fetch_dxy_ohlcv,
    fetch_realtime_gold_quote,
)
from forex_trading.data.fetcher_tradingview import (
    fetch_xauusd_analysis,
    fetch_multi_timeframe_analysis,
)
from forex_trading.data.fetcher_tavily import fetch_gold_news

logger = logging.getLogger(__name__)

# 快取 TTL（秒）
OHLCV_CACHE_TTL = 300       # 5 分鐘
QUOTE_CACHE_TTL = 60         # 1 分鐘
TV_CACHE_TTL = 300           # 5 分鐘
NEWS_CACHE_TTL = 1800        # 30 分鐘


@dataclass
class CacheEntry:
    data: object
    timestamp: float = field(default_factory=time.time)

    def is_expired(self, ttl: int) -> bool:
        return (time.time() - self.timestamp) > ttl


class MarketDataManager:
    """統一市場數據管理器。"""

    def __init__(self):
        self._cache: dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()

    def _get_cached(self, key: str, ttl: int):
        entry = self._cache.get(key)
        if entry and not entry.is_expired(ttl):
            return entry.data
        return None

    def _set_cache(self, key: str, data):
        self._cache[key] = CacheEntry(data=data)

    async def get_gold_ohlcv(
        self,
        interval: str = "1h",
        period: str = "60d",
        use_cache: bool = True,
    ) -> pd.DataFrame | None:
        """取得黃金 OHLCV 數據。"""
        cache_key = f"gold_ohlcv_{interval}_{period}"

        if use_cache:
            cached = self._get_cached(cache_key, OHLCV_CACHE_TTL)
            if cached is not None:
                return cached

        data = await fetch_gold_ohlcv(interval=interval, period=period)
        if data is not None:
            self._set_cache(cache_key, data)
        return data

    async def get_dxy_ohlcv(
        self,
        interval: str = "1h",
        period: str = "60d",
        use_cache: bool = True,
    ) -> pd.DataFrame | None:
        """取得 DXY OHLCV 數據。"""
        cache_key = f"dxy_ohlcv_{interval}_{period}"

        if use_cache:
            cached = self._get_cached(cache_key, OHLCV_CACHE_TTL)
            if cached is not None:
                return cached

        data = await fetch_dxy_ohlcv(interval=interval, period=period)
        if data is not None:
            self._set_cache(cache_key, data)
        return data

    async def get_realtime_quote(self, use_cache: bool = True) -> dict | None:
        """取得即時報價。"""
        cache_key = "gold_quote"

        if use_cache:
            cached = self._get_cached(cache_key, QUOTE_CACHE_TTL)
            if cached is not None:
                return cached

        data = await fetch_realtime_gold_quote()
        if data is not None:
            self._set_cache(cache_key, data)
        return data

    async def get_tv_analysis(
        self,
        interval: str = "1h",
        use_cache: bool = True,
    ) -> dict | None:
        """取得 TradingView 技術分析。"""
        cache_key = f"tv_{interval}"

        if use_cache:
            cached = self._get_cached(cache_key, TV_CACHE_TTL)
            if cached is not None:
                return cached

        data = await fetch_xauusd_analysis(interval=interval)
        if data is not None:
            self._set_cache(cache_key, data)
        return data

    async def get_tv_multi_timeframe(self, use_cache: bool = True) -> dict:
        """取得多時間框架 TradingView 分析。"""
        cache_key = "tv_multi"

        if use_cache:
            cached = self._get_cached(cache_key, TV_CACHE_TTL)
            if cached is not None:
                return cached

        data = await fetch_multi_timeframe_analysis()
        self._set_cache(cache_key, data)
        return data

    async def get_news_sentiment(self, use_cache: bool = True) -> dict:
        """取得黃金新聞情緒。"""
        cache_key = "gold_news"

        if use_cache:
            cached = self._get_cached(cache_key, NEWS_CACHE_TTL)
            if cached is not None:
                return cached

        data = await fetch_gold_news()
        self._set_cache(cache_key, data)
        return data

    async def get_all_data(self) -> dict:
        """
        並行抓取所有數據源。
        用於策略分析和 AI 市場分析。
        """
        results = await asyncio.gather(
            self.get_gold_ohlcv("1h", "60d"),
            self.get_gold_ohlcv("4h", "60d"),
            self.get_gold_ohlcv("1d", "1y"),
            self.get_dxy_ohlcv("1h", "60d"),
            self.get_realtime_quote(),
            self.get_tv_multi_timeframe(),
            self.get_news_sentiment(),
            return_exceptions=True,
        )

        def _safe(result):
            return result if not isinstance(result, Exception) else None

        return {
            "gold_1h": _safe(results[0]),
            "gold_4h": _safe(results[1]),
            "gold_1d": _safe(results[2]),
            "dxy_1h": _safe(results[3]),
            "quote": _safe(results[4]),
            "tv": _safe(results[5]),
            "news": _safe(results[6]),
        }

    def clear_cache(self) -> None:
        """清除所有快取。"""
        self._cache.clear()
