"""
yfinance 基本面數據抓取模組
使用 yfinance 取得公司基本面資訊。
"""

import asyncio

import yfinance as yf


def _safe_get(info: dict, key: str, fallback="N/A"):
    """安全取值，None 或缺失一律回傳 fallback。"""
    val = info.get(key)
    return val if val is not None else fallback


def _format_market_cap(market_cap) -> str:
    """格式化市值為易讀字串。"""
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


async def fetch_yfinance_fundamentals(ticker: str) -> dict:
    """
    非同步抓取 yfinance 基本面數據。

    Args:
        ticker: 股票代碼（如 AAPL）

    Returns:
        dict: 包含基本面數據，缺失值標記為 "N/A"
    """
    try:
        stock = yf.Ticker(ticker.upper())
        info = await asyncio.to_thread(lambda: stock.info)

        if not info or info.get("regularMarketPrice") is None:
            # 嘗試檢查是否有任何有效數據
            if not info or len(info) <= 1:
                return {
                    "source": "yfinance",
                    "error": f"無法取得 {ticker.upper()} 的基本面數據（可能為無效代碼）",
                }

        raw_market_cap = _safe_get(info, "marketCap")

        return {
            "source": "yfinance",
            "ticker": ticker.upper(),
            "company_name": _safe_get(info, "longName"),
            "sector": _safe_get(info, "sector"),
            "industry": _safe_get(info, "industry"),
            "market_cap_raw": raw_market_cap,
            "market_cap": _format_market_cap(raw_market_cap),
            "pe_ratio": _safe_get(info, "trailingPE"),
            "forward_pe": _safe_get(info, "forwardPE"),
            "eps": _safe_get(info, "trailingEps"),
            "dividend_yield": (
                f"{info['dividendYield'] * 100:.2f}%"
                if info.get("dividendYield")
                else "N/A"
            ),
            "52w_high": _safe_get(info, "fiftyTwoWeekHigh"),
            "52w_low": _safe_get(info, "fiftyTwoWeekLow"),
            "50d_avg": _safe_get(info, "fiftyDayAverage"),
            "200d_avg": _safe_get(info, "twoHundredDayAverage"),
            "revenue": _safe_get(info, "totalRevenue"),
            "profit_margin": (
                f"{info['profitMargins'] * 100:.2f}%"
                if info.get("profitMargins")
                else "N/A"
            ),
            "business_summary": (
                info.get("longBusinessSummary", "N/A")[:300] + "..."
                if info.get("longBusinessSummary") and len(info.get("longBusinessSummary", "")) > 300
                else _safe_get(info, "longBusinessSummary")
            ),
        }

    except Exception as e:
        return {
            "source": "yfinance",
            "error": f"yfinance 數據錯誤: {str(e)}",
        }
