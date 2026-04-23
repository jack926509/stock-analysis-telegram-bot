"""
yfinance 基本面數據抓取模組（防空值強化版）
- stock.info 返回空值時觸發重試
- fast_info 備援機制
- 增加重試次數應對 Yahoo 限流
"""

import asyncio
import logging

import yfinance as yf

from utils.retry import retry_async_call

logger = logging.getLogger(__name__)


def _safe_get(info: dict, key: str, fallback="N/A"):
    val = info.get(key)
    return val if val is not None else fallback


def _format_market_cap(market_cap) -> str:
    if market_cap == "N/A" or market_cap is None:
        return "N/A"
    try:
        cap = float(market_cap)
        if cap >= 1e12:
            return f"${cap / 1e12:.2f}T"
        elif cap >= 1e9:
            return f"${cap / 1e9:.2f}B"
        elif cap >= 1e6:
            return f"${cap / 1e6:.2f}M"
        else:
            return f"${cap:,.0f}"
    except (ValueError, TypeError):
        return "N/A"


def _format_large_number(value) -> str:
    if value == "N/A" or value is None:
        return "N/A"
    try:
        num = float(value)
        if num >= 1e9:
            return f"{num / 1e9:.2f}B"
        elif num >= 1e6:
            return f"{num / 1e6:.2f}M"
        elif num >= 1e3:
            return f"{num / 1e3:.1f}K"
        else:
            return f"{num:,.0f}"
    except (ValueError, TypeError):
        return "N/A"


def _format_percentage(value) -> str:
    if value == "N/A" or value is None:
        return "N/A"
    try:
        return f"{float(value) * 100:.2f}%"
    except (ValueError, TypeError):
        return "N/A"


def _format_earnings_date(info: dict) -> str:
    try:
        from datetime import datetime, timezone
        for key in ("earningsTimestamp", "earningsDate", "nextEarningsDate"):
            val = info.get(key)
            if val is None:
                continue
            if isinstance(val, (list, tuple)) and len(val) > 0:
                val = val[0]
            if isinstance(val, (int, float)):
                dt = datetime.fromtimestamp(int(val), tz=timezone.utc)
                return dt.strftime("%Y-%m-%d")
            if isinstance(val, str) and val:
                return val
        return "N/A"
    except (ValueError, TypeError, OSError):
        return "N/A"


# 用於驗證 info 資料品質的核心欄位
_CRITICAL_FIELDS = [
    "marketCap", "trailingPE", "forwardPE", "profitMargins",
    "returnOnEquity", "freeCashflow", "revenueGrowth",
]


def _validate_info(info: dict) -> bool:
    """檢查 info 是否有足夠有效資料。至少 3 個核心欄位有值才算有效。"""
    if not info or not isinstance(info, dict):
        return False
    valid_count = sum(1 for k in _CRITICAL_FIELDS if info.get(k) is not None)
    return valid_count >= 3


def _fetch_info_sync(ticker: str) -> dict:
    """同步抓取 stock.info 並驗證資料品質。品質不足則拋出異常觸發重試。"""
    stock = yf.Ticker(ticker)
    info = stock.info

    if not _validate_info(info):
        available = {k: info.get(k) for k in _CRITICAL_FIELDS if info.get(k) is not None}
        logger.warning(
            f"[yfinance] {ticker} info 資料不足 — "
            f"僅有 {len(available)}/{len(_CRITICAL_FIELDS)} 核心欄位: {list(available.keys())}"
        )
        raise ValueError(
            f"yfinance 返回資料不完整（{len(available)}/{len(_CRITICAL_FIELDS)} 核心欄位），觸發重試"
        )

    return info


def _fetch_fast_info_sync(ticker: str) -> dict:
    """使用 fast_info 取得基本價格/市值資料作為備援。"""
    stock = yf.Ticker(ticker)
    fi = stock.fast_info
    result = {}
    for attr in ["market_cap", "last_price", "fifty_day_average",
                 "two_hundred_day_average", "year_high", "year_low",
                 "shares", "currency"]:
        try:
            result[attr] = getattr(fi, attr, None)
        except Exception:
            result[attr] = None
    return result


async def fetch_yfinance_fundamentals(ticker: str) -> dict:
    """
    非同步抓取 yfinance 基本面數據。
    自帶資料品質驗證：核心欄位不足時自動重試最多 3 次。
    全失敗則退回 fast_info 基本資料。
    """
    ticker = ticker.upper()

    try:
        info = await retry_async_call(
            asyncio.to_thread, _fetch_info_sync, ticker,
            max_retries=3,
            base_delay=3.0,
            source_name="yfinance",
        )
    except Exception as e:
        logger.warning(f"[yfinance] {ticker} stock.info 全部失敗: {e}，嘗試 fast_info 備援")
        info = None

    # stock.info 失敗 → 嘗試 fast_info 備援
    if info is None:
        try:
            fi = await asyncio.to_thread(_fetch_fast_info_sync, ticker)
            if fi and fi.get("market_cap"):
                logger.info(f"[yfinance] {ticker} 使用 fast_info 備援")
                return {
                    "source": "yfinance",
                    "ticker": ticker,
                    "company_name": ticker,
                    "sector": "N/A",
                    "industry": "N/A",
                    "market_cap_raw": fi.get("market_cap"),
                    "market_cap": _format_market_cap(fi.get("market_cap")),
                    "pe_ratio": "N/A",
                    "forward_pe": "N/A",
                    "eps": "N/A",
                    "peg_ratio": "N/A",
                    "dividend_yield": "N/A",
                    "profit_margin": "N/A",
                    "revenue_growth": "N/A",
                    "earnings_growth": "N/A",
                    "52w_high": fi.get("year_high", "N/A"),
                    "52w_low": fi.get("year_low", "N/A"),
                    "50d_avg": fi.get("fifty_day_average", "N/A"),
                    "200d_avg": fi.get("two_hundred_day_average", "N/A"),
                    "volume": "N/A",
                    "avg_volume": "N/A",
                    "avg_volume_10d": "N/A",
                    "beta": "N/A",
                    "short_ratio": "N/A",
                    "short_pct_float": "N/A",
                    "held_pct_insiders": "N/A",
                    "held_pct_institutions": "N/A",
                    "revenue": "N/A",
                    "roe": "N/A",
                    "roa": "N/A",
                    "operating_margin": "N/A",
                    "gross_margin": "N/A",
                    "free_cash_flow": "N/A",
                    "operating_cash_flow": "N/A",
                    "debt_to_equity": "N/A",
                    "current_ratio": "N/A",
                    "total_debt": "N/A",
                    "total_cash": "N/A",
                    "price_to_book": "N/A",
                    "price_to_sales": "N/A",
                    "enterprise_value": "N/A",
                    "ev_to_ebitda": "N/A",
                    "earnings_date": "N/A",
                    "business_summary": "N/A",
                    "_data_quality": "fast_info_fallback",
                }
        except Exception as fallback_err:
            logger.error(f"[yfinance] {ticker} fast_info 備援也失敗: {fallback_err}")

        return {
            "source": "yfinance",
            "error": f"yfinance 無法取得 {ticker} 基本面數據（Yahoo 限流或 API 異常）",
        }

    # stock.info 成功 → 正常組裝資料
    raw_market_cap = _safe_get(info, "marketCap")

    return {
        "source": "yfinance",
        "ticker": ticker,
        "company_name": _safe_get(info, "longName"),
        "sector": _safe_get(info, "sector"),
        "industry": _safe_get(info, "industry"),
        "market_cap_raw": raw_market_cap,
        "market_cap": _format_market_cap(raw_market_cap),
        "pe_ratio": _safe_get(info, "trailingPE"),
        "forward_pe": _safe_get(info, "forwardPE"),
        "eps": _safe_get(info, "trailingEps"),
        "peg_ratio": _safe_get(info, "pegRatio"),
        "dividend_yield": (
            f"{info['dividendYield'] * 100:.2f}%"
            if info.get("dividendYield")
            else "N/A"
        ),
        "profit_margin": _format_percentage(info.get("profitMargins")),
        "revenue_growth": _format_percentage(info.get("revenueGrowth")),
        "earnings_growth": _format_percentage(info.get("earningsGrowth")),
        "52w_high": _safe_get(info, "fiftyTwoWeekHigh"),
        "52w_low": _safe_get(info, "fiftyTwoWeekLow"),
        "50d_avg": _safe_get(info, "fiftyDayAverage"),
        "200d_avg": _safe_get(info, "twoHundredDayAverage"),
        "volume": _format_large_number(info.get("volume")),
        "avg_volume": _format_large_number(info.get("averageVolume")),
        "avg_volume_10d": _format_large_number(info.get("averageDailyVolume10Day")),
        "beta": _safe_get(info, "beta"),
        "short_ratio": _safe_get(info, "shortRatio"),
        "short_pct_float": _format_percentage(info.get("shortPercentOfFloat")),
        "held_pct_insiders": _format_percentage(info.get("heldPercentInsiders")),
        "held_pct_institutions": _format_percentage(info.get("heldPercentInstitutions")),
        "revenue": _format_large_number(info.get("totalRevenue")),
        "roe": _format_percentage(info.get("returnOnEquity")),
        "roa": _format_percentage(info.get("returnOnAssets")),
        "operating_margin": _format_percentage(info.get("operatingMargins")),
        "gross_margin": _format_percentage(info.get("grossMargins")),
        "free_cash_flow": _format_large_number(info.get("freeCashflow")),
        "operating_cash_flow": _format_large_number(info.get("operatingCashflow")),
        "debt_to_equity": _safe_get(info, "debtToEquity"),
        "current_ratio": _safe_get(info, "currentRatio"),
        "total_debt": _format_large_number(info.get("totalDebt")),
        "total_cash": _format_large_number(info.get("totalCash")),
        "price_to_book": _safe_get(info, "priceToBook"),
        "price_to_sales": _safe_get(info, "priceToSalesTrailing12Months"),
        "enterprise_value": _format_large_number(info.get("enterpriseValue")),
        "ev_to_ebitda": _safe_get(info, "enterpriseToEbitda"),
        "earnings_date": _format_earnings_date(info),
        "business_summary": (
            info.get("longBusinessSummary", "N/A")[:300] + "..."
            if info.get("longBusinessSummary") and len(info.get("longBusinessSummary", "")) > 300
            else _safe_get(info, "longBusinessSummary")
        ),
    }
