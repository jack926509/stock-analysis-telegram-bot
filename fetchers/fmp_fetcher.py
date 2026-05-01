"""
Financial Modeling Prep (FMP) 數據抓取模組
- 走 FMP /stable/ 免費端點：profile / quote / batch-quote
- key-metrics / ratios / historical 屬 Premium，已分流到 Finnhub 與 Stooq
- 免費版每日 250 次請求
"""

import asyncio
import logging

import httpx

from config import Config
from fetchers.finnhub_fetcher import fetch_finnhub_metrics
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


def _fmt_pct_finnhub(value) -> str:
    """Finnhub 已是百分比數字（25.5 = 25.5%），直接加 %。"""
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.2f}%"
    except (ValueError, TypeError):
        return "N/A"


def _safe(val, fallback="N/A"):
    return val if val is not None else fallback


def _ratio_to_pct(val):
    """Finnhub D/E 是倍數（1.5），formatter 期望百分比（150）。"""
    if val is None:
        return None
    try:
        return round(float(val) * 100, 2)
    except (ValueError, TypeError):
        return None


# ── 共用 HTTP helper ──


async def _fmp_get(endpoint: str, **params) -> list | dict:
    """
    FMP /stable/{endpoint} GET。錯誤訊息含 endpoint 名與 status。
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


async def fetch_fmp_fundamentals(ticker: str) -> dict:
    """
    基本面：FMP profile（公司資訊）+ Finnhub metrics（財務指標）。
    輸出 shape 對應 formatter / signals / analyzer 期望的契約。
    """
    ticker = ticker.upper()

    if not Config.FMP_API_KEY:
        return {"source": "FMP", "error": "FMP_API_KEY 未設定"}

    try:
        profile_data, metrics = await asyncio.gather(
            retry_async_call(
                _fmp_get, "profile", symbol=ticker,
                max_retries=2, base_delay=2.0, source_name="FMP_profile",
            ),
            fetch_finnhub_metrics(ticker),
        )
    except Exception as e:
        logger.warning(f"[FMP] {ticker} profile 失敗: {e}")
        return {"source": "FMP", "error": f"FMP profile 錯誤: {e}"}

    profile = profile_data[0] if isinstance(profile_data, list) and profile_data else {}
    if not profile:
        return {"source": "FMP", "error": "FMP profile 回傳空"}

    raw_market_cap = profile.get("marketCap") or profile.get("mktCap")
    last_div = profile.get("lastDividend") or profile.get("lastDiv")
    price = profile.get("price")

    div_yield = "N/A"
    fh_div_yield = metrics.get("currentDividendYieldTTM")
    if fh_div_yield is not None:
        try:
            div_yield = f"{float(fh_div_yield):.2f}%"
        except (ValueError, TypeError):
            pass
    elif last_div not in (None, 0) and price:
        try:
            div_yield = f"{(float(last_div) / float(price)) * 100:.2f}%"
        except (ValueError, TypeError, ZeroDivisionError):
            pass

    summary = profile.get("description") or "N/A"
    if summary != "N/A" and len(summary) > 300:
        summary = summary[:300] + "..."

    range_str = profile.get("range") or ""
    range_parts = [p.strip() for p in range_str.split("-")] if range_str else []
    if len(range_parts) == 2:
        low_52w, high_52w = range_parts[0], range_parts[1]
    else:
        low_52w = _safe(metrics.get("52WeekLow"))
        high_52w = _safe(metrics.get("52WeekHigh"))

    avg_vol_10d = metrics.get("10DayAverageTradingVolume")
    if avg_vol_10d is not None:
        try:
            # Finnhub 以百萬股為單位
            avg_vol_10d = float(avg_vol_10d) * 1e6
        except (ValueError, TypeError):
            avg_vol_10d = None

    return {
        "source": "FMP",
        "ticker": ticker,
        "company_name": _safe(profile.get("companyName")),
        "sector": _safe(profile.get("sector")),
        "industry": _safe(profile.get("industry")),
        "market_cap_raw": _safe(raw_market_cap),
        "market_cap": _format_market_cap(raw_market_cap),
        "pe_ratio": _safe(metrics.get("peTTM") or metrics.get("peExclExtraTTM")),
        "forward_pe": "N/A",
        "eps": _safe(metrics.get("epsTTM") or profile.get("eps")),
        "peg_ratio": _safe(metrics.get("pegTTM")),
        "dividend_yield": div_yield,
        "profit_margin": _fmt_pct_finnhub(metrics.get("netProfitMarginTTM")),
        "revenue_growth": _fmt_pct_finnhub(metrics.get("revenueGrowthTTMYoy")),
        "earnings_growth": _fmt_pct_finnhub(metrics.get("epsGrowthTTMYoy")),
        "52w_high": high_52w,
        "52w_low": low_52w,
        "50d_avg": "N/A",
        "200d_avg": "N/A",
        "volume": _format_large_number(profile.get("volAvg") or profile.get("averageVolume")),
        "avg_volume": _format_large_number(profile.get("volAvg") or profile.get("averageVolume")),
        "avg_volume_10d": _format_large_number(avg_vol_10d),
        "beta": _safe(profile.get("beta") or metrics.get("beta")),
        "short_ratio": "N/A",
        "short_pct_float": "N/A",
        "held_pct_insiders": "N/A",
        "held_pct_institutions": "N/A",
        "revenue": "N/A",
        "roe": _fmt_pct_finnhub(metrics.get("roeTTM")),
        "roa": _fmt_pct_finnhub(metrics.get("roaTTM")),
        "operating_margin": _fmt_pct_finnhub(metrics.get("operatingMarginTTM")),
        "gross_margin": _fmt_pct_finnhub(metrics.get("grossMarginTTM")),
        "free_cash_flow": "N/A",
        "operating_cash_flow": "N/A",
        "debt_to_equity": _safe(_ratio_to_pct(
            metrics.get("totalDebt/totalEquityAnnual")
            or metrics.get("longTermDebt/equityAnnual"),
        )),
        "current_ratio": _safe(
            metrics.get("currentRatioAnnual") or metrics.get("currentRatioQuarterly"),
        ),
        "total_debt": "N/A",
        "total_cash": "N/A",
        "price_to_book": _safe(metrics.get("pbAnnual") or metrics.get("pbQuarterly")),
        "price_to_sales": _safe(metrics.get("psAnnual") or metrics.get("psTTM")),
        "enterprise_value": "N/A",
        "ev_to_ebitda": "N/A",
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
