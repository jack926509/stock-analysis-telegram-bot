"""
Financial Modeling Prep (FMP) 數據抓取模組
- 走 FMP /stable/ API（免費版可用；舊 /api/v3/ 已棄用，常 403）
- 提供 fundamentals / quote / history / batch_prices
- 免費版每日 250 次請求
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from config import Config
from utils.retry import retry_async_call

logger = logging.getLogger(__name__)

_BASE_URL = "https://financialmodelingprep.com/stable"


# ── 數值格式化 ──


def _format_market_cap(market_cap) -> str:
    if market_cap is None or market_cap == "N/A":
        return "N/A"
    try:
        cap = float(market_cap)
        if cap >= 1e12:
            return f"${cap / 1e12:.2f}T"
        if cap >= 1e9:
            return f"${cap / 1e9:.2f}B"
        if cap >= 1e6:
            return f"${cap / 1e6:.2f}M"
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
        if num >= 1e6:
            return f"{num / 1e6:.2f}M"
        if num >= 1e3:
            return f"{num / 1e3:.1f}K"
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


# ── 共用 HTTP helper ──


async def _fmp_get(endpoint: str, **params) -> list | dict:
    """
    FMP /stable/{endpoint} GET。raises HTTPStatusError / ValueError on failure。
    錯誤訊息含 endpoint 名與 status，方便排查 403/404/429。
    """
    url = f"{_BASE_URL}/{endpoint}"
    qs = {"apikey": Config.FMP_API_KEY}
    qs.update({k: v for k, v in params.items() if v is not None})

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=qs)

    if resp.status_code != 200:
        raise httpx.HTTPStatusError(
            f"FMP {endpoint} {resp.status_code}: {resp.text[:200]}",
            request=resp.request,
            response=resp,
        )

    data = resp.json()
    if isinstance(data, dict) and data.get("Error Message"):
        raise ValueError(f"FMP {endpoint} error: {data['Error Message']}")
    return data


# ── 基本面 ──


async def _fetch_all_fmp_data(ticker: str) -> tuple[dict, dict, dict]:
    """並行抓 profile + key-metrics-ttm + ratios-ttm。"""
    profile_data, metrics_data, ratios_data = await asyncio.gather(
        _fmp_get("profile", symbol=ticker),
        _fmp_get("key-metrics-ttm", symbol=ticker),
        _fmp_get("ratios-ttm", symbol=ticker),
    )

    profile = profile_data[0] if isinstance(profile_data, list) and profile_data else {}
    metrics = metrics_data[0] if isinstance(metrics_data, list) and metrics_data else {}
    ratios = ratios_data[0] if isinstance(ratios_data, list) and ratios_data else {}

    if not profile:
        raise ValueError("FMP profile returned empty data")

    return profile, metrics, ratios


async def fetch_fmp_fundamentals(ticker: str) -> dict:
    """
    FMP 基本面。輸出 shape 對應 formatter / signals / analyzer 期望的契約。
    欄位名同時相容 stable（marketCap / lastDividend）與 legacy（mktCap / lastDiv）。
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
        return {"source": "FMP", "error": f"FMP API 錯誤: {e}"}

    raw_market_cap = _safe(profile.get("marketCap") or profile.get("mktCap"))
    last_div = profile.get("lastDividend") or profile.get("lastDiv")
    price = profile.get("price")

    if last_div not in (None, 0) and price:
        try:
            div_yield = f"{(float(last_div) / float(price)) * 100:.2f}%"
        except (ValueError, TypeError, ZeroDivisionError):
            div_yield = "N/A"
    else:
        div_yield = "N/A"

    summary = profile.get("description") or "N/A"
    if summary != "N/A" and len(summary) > 300:
        summary = summary[:300] + "..."

    range_str = profile.get("range") or ""
    range_parts = [p.strip() for p in range_str.split("-")] if range_str else []
    low_52w = range_parts[0] if len(range_parts) == 2 else "N/A"
    high_52w = range_parts[1] if len(range_parts) == 2 else "N/A"

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
        "eps": _safe(profile.get("eps") or metrics.get("netIncomePerShareTTM")),
        "peg_ratio": _safe(ratios.get("pegRatioTTM")),
        "dividend_yield": div_yield,
        "profit_margin": _format_percentage(ratios.get("netProfitMarginTTM")),
        "revenue_growth": _format_percentage(metrics.get("revenueGrowthTTM") or ratios.get("revenueGrowthTTM")),
        "earnings_growth": _format_percentage(metrics.get("netIncomeGrowthTTM") or ratios.get("netIncomeGrowthTTM")),
        "52w_high": high_52w,
        "52w_low": low_52w,
        "50d_avg": "N/A",
        "200d_avg": "N/A",
        "volume": _format_large_number(profile.get("volAvg") or profile.get("averageVolume")),
        "avg_volume": _format_large_number(profile.get("volAvg") or profile.get("averageVolume")),
        "avg_volume_10d": "N/A",
        "beta": _safe(profile.get("beta")),
        "short_ratio": "N/A",
        "short_pct_float": "N/A",
        "held_pct_insiders": "N/A",
        "held_pct_institutions": "N/A",
        "revenue": _format_large_number(metrics.get("revenueTTM") or metrics.get("revenuePerShareTTM")),
        "roe": _format_percentage(ratios.get("returnOnEquityTTM")),
        "roa": _format_percentage(ratios.get("returnOnAssetsTTM")),
        "operating_margin": _format_percentage(ratios.get("operatingProfitMarginTTM")),
        "gross_margin": _format_percentage(ratios.get("grossProfitMarginTTM")),
        "free_cash_flow": _format_large_number(metrics.get("freeCashFlowTTM")),
        "operating_cash_flow": _format_large_number(metrics.get("operatingCashFlowTTM")),
        "debt_to_equity": _safe(ratios.get("debtEquityRatioTTM")),
        "current_ratio": _safe(ratios.get("currentRatioTTM")),
        "total_debt": _format_large_number(metrics.get("totalDebtTTM")),
        "total_cash": _format_large_number(metrics.get("cashAndCashEquivalentsTTM") or metrics.get("cashPerShareTTM")),
        "price_to_book": _safe(ratios.get("priceToBookRatioTTM")),
        "price_to_sales": _safe(ratios.get("priceToSalesRatioTTM")),
        "enterprise_value": _format_large_number(metrics.get("enterpriseValueTTM")),
        "ev_to_ebitda": _safe(metrics.get("evToEbitdaTTM") or ratios.get("enterpriseValueOverEBITDATTM")),
        "earnings_date": "N/A",
        "business_summary": summary,
    }


# ── Quote（單檔即時報價，含指數如 ^VIX / ^TNX）──


async def fetch_fmp_quote(symbol: str) -> dict | None:
    """單檔報價，None on failure。"""
    if not Config.FMP_API_KEY:
        return None
    try:
        data = await _fmp_get("quote", symbol=symbol)
        if isinstance(data, list) and data:
            return data[0]
    except Exception as e:
        logger.debug(f"[FMP] quote {symbol} 失敗: {e}")
    return None


# ── Historical OHLCV ──


async def fetch_fmp_history(ticker: str, days: int = 252) -> list[dict] | None:
    """
    抓日 K 歷史。返回舊→新時序的 dict list：
    {date, open, high, low, close, volume}
    """
    if not Config.FMP_API_KEY:
        return None

    # /stable/historical-price-eod/full 需要 from / to 日期
    # 多取 50% 緩衝抵銷週末與假期
    today = datetime.now(timezone.utc).date()
    from_date = today - timedelta(days=int(days * 1.5))
    try:
        data = await _fmp_get(
            "historical-price-eod/full",
            symbol=ticker.upper(),
            **{
                "from": from_date.strftime("%Y-%m-%d"),
                "to": today.strftime("%Y-%m-%d"),
            },
        )
    except Exception as e:
        logger.warning(f"[FMP] historical {ticker} 失敗: {e}")
        return None

    # stable 直接回 list；legacy 回 {symbol, historical: []}
    rows = data if isinstance(data, list) else (data.get("historical") if isinstance(data, dict) else None)
    if not isinstance(rows, list) or not rows:
        return None

    # 反轉成舊→新；裁掉超出 days 的部分
    rows = list(reversed(rows))[-days:]
    return rows


# ── Finnhub 報價 fallback（給 batch_prices 用）──


async def _fetch_finnhub_quote_normalized(ticker: str) -> tuple[str, dict | None]:
    try:
        import finnhub

        if not Config.FINNHUB_API_KEY:
            return ticker.upper(), None

        def _get():
            client = finnhub.Client(api_key=Config.FINNHUB_API_KEY)
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
    Batch 即時報價：FMP /stable/batch-quote → Finnhub fallback。
    返回 {ticker: {price, change, change_pct, ...}}
    """
    if not tickers:
        return {}

    upper_tickers = [t.upper() for t in tickers]
    result: dict[str, dict] = {}

    if Config.FMP_API_KEY:
        try:
            data = await _fmp_get("batch-quote", symbols=",".join(upper_tickers))
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
            logger.warning(f"[FMP] batch price fetch failed: {e}")

    missing = [t for t in upper_tickers if t not in result]
    if missing:
        logger.info(f"[FMP] {len(missing)} tickers missing, trying Finnhub: {missing}")
        fh_results = await asyncio.gather(
            *[_fetch_finnhub_quote_normalized(t) for t in missing],
            return_exceptions=True,
        )
        for item in fh_results:
            if isinstance(item, tuple):
                sym, data = item
                if data is not None:
                    result[sym] = data
                    logger.info(f"[Finnhub] fallback quote for {sym}: ${data.get('price'):.2f}")

    return result
