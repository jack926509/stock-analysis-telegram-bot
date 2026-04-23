"""
Financial Modeling Prep (FMP) 基本面數據抓取模組
- 主要數據源，取代 yfinance 作為基本面主力
- 使用 /api/v3/profile + /api/v3/key-metrics-ttm + /api/v3/ratios-ttm
- 免費版每日 250 次請求
"""

import asyncio
import logging

import httpx

from config import Config
from utils.retry import retry_async_call

logger = logging.getLogger(__name__)

_BASE_URL = "https://financialmodelingprep.com/api/v3"


def _format_market_cap(market_cap) -> str:
    if market_cap is None or market_cap == "N/A":
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
    if value is None or value == "N/A":
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
    if value is None or value == "N/A":
        return "N/A"
    try:
        return f"{float(value) * 100:.2f}%"
    except (ValueError, TypeError):
        return "N/A"


def _safe(val, fallback="N/A"):
    return val if val is not None else fallback


async def _fetch_fmp_json(endpoint: str, ticker: str) -> list | dict:
    """Fetch a single FMP endpoint. Raises on failure."""
    url = f"{_BASE_URL}/{endpoint}/{ticker}"
    params = {"apikey": Config.FMP_API_KEY}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    if isinstance(data, dict) and data.get("Error Message"):
        raise ValueError(data["Error Message"])

    return data


async def _fetch_all_fmp_data(ticker: str) -> tuple[dict, dict, dict]:
    """Parallel fetch profile + key-metrics-ttm + ratios-ttm."""
    profile_data, metrics_data, ratios_data = await asyncio.gather(
        _fetch_fmp_json("profile", ticker),
        _fetch_fmp_json("key-metrics-ttm", ticker),
        _fetch_fmp_json("ratios-ttm", ticker),
    )

    profile = profile_data[0] if isinstance(profile_data, list) and profile_data else {}
    metrics = metrics_data[0] if isinstance(metrics_data, list) and metrics_data else {}
    ratios = ratios_data[0] if isinstance(ratios_data, list) and ratios_data else {}

    if not profile:
        raise ValueError("FMP profile returned empty data")

    return profile, metrics, ratios


async def fetch_fmp_fundamentals(ticker: str) -> dict:
    """
    Fetch fundamentals from FMP API.
    Output format matches yfinance_fetcher for seamless integration.
    """
    ticker = ticker.upper()

    if not Config.FMP_API_KEY:
        return {"source": "FMP", "error": "FMP_API_KEY 未設定"}

    try:
        profile, metrics, ratios = await retry_async_call(
            _fetch_all_fmp_data, ticker,
            max_retries=2,
            base_delay=2.0,
            source_name="FMP",
        )
    except Exception as e:
        logger.warning(f"[FMP] {ticker} 抓取失敗: {e}")
        return {"source": "FMP", "error": f"FMP API 錯誤: {str(e)}"}

    raw_market_cap = _safe(profile.get("mktCap"))
    dividend_yield_raw = _safe(profile.get("lastDiv"))
    price = profile.get("price")

    if dividend_yield_raw not in (None, "N/A", 0) and price:
        try:
            div_yield = f"{(float(dividend_yield_raw) / float(price)) * 100:.2f}%"
        except (ValueError, TypeError, ZeroDivisionError):
            div_yield = "N/A"
    else:
        div_yield = "N/A"

    summary = profile.get("description", "N/A")
    if summary and summary != "N/A" and len(summary) > 300:
        summary = summary[:300] + "..."

    return {
        "source": "FMP",
        "ticker": ticker,
        "company_name": _safe(profile.get("companyName")),
        "sector": _safe(profile.get("sector")),
        "industry": _safe(profile.get("industry")),
        "market_cap_raw": raw_market_cap,
        "market_cap": _format_market_cap(raw_market_cap),
        "pe_ratio": _safe(metrics.get("peRatioTTM")),
        "forward_pe": "N/A",
        "eps": _safe(profile.get("eps") if profile.get("eps") else metrics.get("netIncomePerShareTTM")),
        "peg_ratio": _safe(ratios.get("pegRatioTTM")),
        "dividend_yield": div_yield,
        "profit_margin": _format_percentage(ratios.get("netProfitMarginTTM")),
        "revenue_growth": _format_percentage(metrics.get("revenueGrowthTTM") if metrics.get("revenueGrowthTTM") else ratios.get("revenueGrowthTTM")),
        "earnings_growth": _format_percentage(metrics.get("netIncomeGrowthTTM") if metrics.get("netIncomeGrowthTTM") else ratios.get("netIncomeGrowthTTM")),
        "52w_high": _safe(profile.get("range", "").split("-")[-1].strip() if profile.get("range") else None),
        "52w_low": _safe(profile.get("range", "").split("-")[0].strip() if profile.get("range") else None),
        "50d_avg": "N/A",
        "200d_avg": "N/A",
        "volume": _format_large_number(profile.get("volAvg")),
        "avg_volume": _format_large_number(profile.get("volAvg")),
        "avg_volume_10d": "N/A",
        "beta": _safe(profile.get("beta")),
        "short_ratio": "N/A",
        "short_pct_float": "N/A",
        "held_pct_insiders": "N/A",
        "held_pct_institutions": "N/A",
        "revenue": _format_large_number(metrics.get("revenueTTM") if metrics.get("revenueTTM") else metrics.get("revenuePerShareTTM")),
        "roe": _format_percentage(ratios.get("returnOnEquityTTM")),
        "roa": _format_percentage(ratios.get("returnOnAssetsTTM")),
        "operating_margin": _format_percentage(ratios.get("operatingProfitMarginTTM")),
        "gross_margin": _format_percentage(ratios.get("grossProfitMarginTTM")),
        "free_cash_flow": _format_large_number(metrics.get("freeCashFlowTTM")),
        "operating_cash_flow": _format_large_number(metrics.get("operatingCashFlowTTM")),
        "debt_to_equity": _safe(ratios.get("debtEquityRatioTTM")),
        "current_ratio": _safe(ratios.get("currentRatioTTM")),
        "total_debt": _format_large_number(metrics.get("totalDebtTTM")),
        "total_cash": _format_large_number(metrics.get("cashAndCashEquivalentsTTM") if metrics.get("cashAndCashEquivalentsTTM") else metrics.get("cashPerShareTTM")),
        "price_to_book": _safe(ratios.get("priceToBookRatioTTM")),
        "price_to_sales": _safe(ratios.get("priceToSalesRatioTTM")),
        "enterprise_value": _format_large_number(metrics.get("enterpriseValueTTM")),
        "ev_to_ebitda": _safe(metrics.get("evToEbitdaTTM") if metrics.get("evToEbitdaTTM") else ratios.get("enterpriseValueOverEBITDATTM")),
        "earnings_date": "N/A",
        "business_summary": summary,
    }


async def fetch_fmp_batch_prices(tickers: list[str]) -> dict[str, dict]:
    """
    Batch fetch live prices for multiple tickers (watchlist use).
    Uses /api/v3/quote/{csv_tickers} endpoint.
    Returns {ticker: {price, change, changesPercentage, ...}}
    """
    if not Config.FMP_API_KEY or not tickers:
        return {}

    try:
        csv_tickers = ",".join(t.upper() for t in tickers)
        url = f"{_BASE_URL}/quote/{csv_tickers}"
        params = {"apikey": Config.FMP_API_KEY}

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        if not isinstance(data, list):
            return {}

        return {
            item["symbol"]: {
                "price": item.get("price"),
                "change": item.get("change"),
                "change_pct": item.get("changesPercentage"),
                "name": item.get("name", ""),
                "day_high": item.get("dayHigh"),
                "day_low": item.get("dayLow"),
                "volume": item.get("volume"),
                "market_cap": item.get("marketCap"),
            }
            for item in data
            if item.get("symbol")
        }
    except Exception as e:
        logger.warning(f"[FMP] Batch price fetch failed: {e}")
        return {}
