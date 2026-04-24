"""
Financial Modeling Prep (FMP) 基本面數據抓取模組
- 主要數據源，取代 yfinance 作為基本面主力
- 使用 /api/v3/profile + /api/v3/key-metrics-ttm + /api/v3/ratios-ttm
- 免費版每日 250 次請求
"""

import asyncio
import logging

import httpx
import yfinance as yf

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


def _yf_quote_sync(ticker: str) -> dict | None:
    """Synchronous yfinance fast_info quote fetch (runs in thread pool)."""
    try:
        stock = yf.Ticker(ticker)
        fi = stock.fast_info
        price = getattr(fi, "last_price", None)
        if not price:
            return None
        prev_close = getattr(fi, "previous_close", None)
        change = (price - prev_close) if prev_close else None
        change_pct = ((change / prev_close) * 100) if (change is not None and prev_close) else None
        return {
            "price": price,
            "change": change,
            "change_pct": change_pct,
            "name": ticker,
            "day_high": getattr(fi, "day_high", None),
            "day_low": getattr(fi, "day_low", None),
            "year_high": getattr(fi, "fifty_two_week_high", None),
            "year_low": getattr(fi, "fifty_two_week_low", None),
            "price_avg_50": None,
            "price_avg_200": None,
            "volume": getattr(fi, "last_volume", None),
            "avg_volume": getattr(fi, "three_month_average_volume", None),
            "market_cap": getattr(fi, "market_cap", None),
            "pe": None,
            "earnings_announcement": None,
            "_source": "yfinance",
        }
    except Exception as e:
        logger.debug(f"[yfinance] Quote fallback for {ticker} failed: {e}")
        return None


async def _fetch_yfinance_quote(ticker: str) -> tuple[str, dict | None]:
    result = await asyncio.to_thread(_yf_quote_sync, ticker.upper())
    return ticker.upper(), result


async def _fetch_finnhub_quote_normalized(ticker: str) -> tuple[str, dict | None]:
    """Fetch Finnhub quote and normalize to batch-price format."""
    try:
        import finnhub
        from config import Config as _Config

        if not _Config.FINNHUB_API_KEY:
            return ticker.upper(), None

        def _get():
            client = finnhub.Client(api_key=_Config.FINNHUB_API_KEY)
            return client.quote(ticker.upper())

        q = await asyncio.to_thread(_get)
        price = q.get("c")
        if not price:
            return ticker.upper(), None

        prev = q.get("pc") or 0
        change = price - prev
        change_pct = (change / prev * 100) if prev else None
        return ticker.upper(), {
            "price": price,
            "change": change,
            "change_pct": change_pct,
            "name": ticker.upper(),
            "day_high": q.get("h"),
            "day_low": q.get("l"),
            "year_high": None,
            "year_low": None,
            "price_avg_50": None,
            "price_avg_200": None,
            "volume": None,
            "avg_volume": None,
            "market_cap": None,
            "pe": None,
            "earnings_announcement": None,
            "_source": "finnhub",
        }
    except Exception as e:
        logger.debug(f"[Finnhub] Quote fallback for {ticker} failed: {e}")
        return ticker.upper(), None


async def fetch_fmp_batch_prices(tickers: list[str]) -> dict[str, dict]:
    """
    Batch fetch live prices for multiple tickers (watchlist use).
    Primary: FMP /api/v3/quote. Fallback: yfinance → Finnhub for missing tickers.
    Returns {ticker: {price, change, change_pct, ...}}
    """
    if not tickers:
        return {}

    upper_tickers = [t.upper() for t in tickers]
    result: dict[str, dict] = {}

    # ── Primary: FMP batch ──
    if Config.FMP_API_KEY:
        try:
            csv_tickers = ",".join(upper_tickers)
            url = f"{_BASE_URL}/quote/{csv_tickers}"
            params = {"apikey": Config.FMP_API_KEY}

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            if isinstance(data, list):
                for item in data:
                    sym = item.get("symbol")
                    if sym and item.get("price") is not None:
                        result[sym] = {
                            "price": item.get("price"),
                            "change": item.get("change"),
                            "change_pct": item.get("changesPercentage"),
                            "name": item.get("name", ""),
                            "day_high": item.get("dayHigh"),
                            "day_low": item.get("dayLow"),
                            "year_high": item.get("yearHigh"),
                            "year_low": item.get("yearLow"),
                            "price_avg_50": item.get("priceAvg50"),
                            "price_avg_200": item.get("priceAvg200"),
                            "volume": item.get("volume"),
                            "avg_volume": item.get("avgVolume"),
                            "market_cap": item.get("marketCap"),
                            "pe": item.get("pe"),
                            "earnings_announcement": item.get("earningsAnnouncement"),
                            "_source": "fmp",
                        }
        except Exception as e:
            logger.warning(f"[FMP] Batch price fetch failed: {e}")

    # ── Fallback: yfinance for tickers missing from FMP ──
    missing = [t for t in upper_tickers if t not in result]
    if missing:
        logger.info(f"[FMP] {len(missing)} tickers missing from FMP, trying yfinance: {missing}")
        yf_tasks = [_fetch_yfinance_quote(t) for t in missing]
        yf_results = await asyncio.gather(*yf_tasks, return_exceptions=True)

        still_missing = []
        for item in yf_results:
            if isinstance(item, tuple):
                sym, data = item
                if data is not None:
                    result[sym] = data
                    logger.info(f"[yfinance] Fallback quote for {sym}: ${data.get('price'):.2f}")
                else:
                    still_missing.append(sym)
            else:
                still_missing.append(item)

        # ── Secondary fallback: Finnhub for tickers yfinance also missed ──
        if still_missing:
            logger.info(f"[yfinance] {len(still_missing)} still missing, trying Finnhub: {still_missing}")
            fh_tasks = [_fetch_finnhub_quote_normalized(t) for t in still_missing]
            fh_results = await asyncio.gather(*fh_tasks, return_exceptions=True)
            for item in fh_results:
                if isinstance(item, tuple):
                    sym, data = item
                    if data is not None:
                        result[sym] = data
                        logger.info(f"[Finnhub] Fallback quote for {sym}: ${data.get('price'):.2f}")

    return result
