"""
yfinance XAUUSD/DXY 數據抓取模組
抓取黃金現貨、黃金期貨、美元指數的 OHLCV 數據。
"""

import asyncio
import logging

import pandas as pd
import yfinance as yf

from forex_trading.config import ForexConfig

logger = logging.getLogger(__name__)


async def fetch_gold_ohlcv(
    interval: str = "1h",
    period: str = "60d",
) -> pd.DataFrame | None:
    """
    抓取 XAUUSD OHLCV 數據。

    Args:
        interval: 時間框架 ("1h", "4h", "1d")
        period: 回溯期間 ("7d", "30d", "60d", "1y", "2y")

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume
    """
    def _fetch():
        try:
            # 1h 數據最多 730 天，但 yfinance 限制 60 天
            # 4h 不被 yfinance 直接支援，需從 1h 重新取樣
            actual_interval = "1h" if interval == "4h" else interval

            df = yf.download(
                ForexConfig.GOLD_SYMBOL,
                period=period,
                interval=actual_interval,
                progress=False,
            )

            if df.empty:
                # 嘗試黃金期貨作為備用
                logger.warning(f"XAUUSD=X 數據為空，嘗試 GC=F")
                df = yf.download(
                    ForexConfig.GOLD_FUTURES,
                    period=period,
                    interval=actual_interval,
                    progress=False,
                )

            if df.empty:
                logger.error("無法取得黃金 OHLCV 數據")
                return None

            # 處理 MultiIndex columns (yfinance 可能回傳)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # 確保欄位名稱標準化
            df.columns = [c.strip() for c in df.columns]

            # 4H 重新取樣
            if interval == "4h":
                df = df.resample("4h").agg({
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                    "Volume": "sum",
                }).dropna()

            return df

        except Exception as e:
            logger.error(f"抓取黃金 OHLCV 失敗: {e}")
            return None

    return await asyncio.to_thread(_fetch)


async def fetch_dxy_ohlcv(
    interval: str = "1h",
    period: str = "60d",
) -> pd.DataFrame | None:
    """
    抓取美元指數 (DXY) OHLCV 數據。
    """
    def _fetch():
        try:
            actual_interval = "1h" if interval == "4h" else interval

            df = yf.download(
                ForexConfig.DXY_SYMBOL,
                period=period,
                interval=actual_interval,
                progress=False,
            )

            if df.empty:
                logger.error("無法取得 DXY OHLCV 數據")
                return None

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df.columns = [c.strip() for c in df.columns]

            if interval == "4h":
                df = df.resample("4h").agg({
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                    "Volume": "sum",
                }).dropna()

            return df

        except Exception as e:
            logger.error(f"抓取 DXY OHLCV 失敗: {e}")
            return None

    return await asyncio.to_thread(_fetch)


async def fetch_realtime_gold_quote() -> dict | None:
    """抓取黃金即時報價。"""
    def _fetch():
        try:
            ticker = yf.Ticker(ForexConfig.GOLD_SYMBOL)
            info = ticker.info

            if not info:
                return None

            price = info.get("regularMarketPrice") or info.get("previousClose")
            if price is None:
                return None

            return {
                "price": float(price),
                "previous_close": float(info.get("previousClose", 0)),
                "open": float(info.get("regularMarketOpen", 0)),
                "high": float(info.get("dayHigh", 0)),
                "low": float(info.get("dayLow", 0)),
                "volume": int(info.get("volume", 0)),
                "change": float(info.get("regularMarketChange", 0)),
                "change_percent": float(info.get("regularMarketChangePercent", 0)),
                "bid": float(info.get("bid", 0)),
                "ask": float(info.get("ask", 0)),
            }
        except Exception as e:
            logger.error(f"抓取即時報價失敗: {e}")
            return None

    return await asyncio.to_thread(_fetch)
