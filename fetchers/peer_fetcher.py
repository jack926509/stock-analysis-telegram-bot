"""
同業比較模組
使用 FMP profile + key-metrics-ttm 取得同產業公司關鍵指標進行橫向對比。
"""

import asyncio

from fetchers.fmp_fetcher import _fmp_get


# 各產業代表性公司對照（找不到 peer 時的 fallback）
SECTOR_LEADERS = {
    "Technology": ["AAPL", "MSFT", "GOOGL", "NVDA", "META"],
    "Communication Services": ["GOOGL", "META", "DIS", "NFLX", "T"],
    "Consumer Cyclical": ["AMZN", "TSLA", "HD", "NKE", "MCD"],
    "Consumer Defensive": ["PG", "KO", "PEP", "WMT", "COST"],
    "Healthcare": ["UNH", "JNJ", "LLY", "PFE", "ABBV"],
    "Financial Services": ["JPM", "V", "MA", "BAC", "GS"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG"],
    "Industrials": ["CAT", "UNP", "HON", "BA", "GE"],
    "Real Estate": ["PLD", "AMT", "CCI", "SPG", "O"],
    "Utilities": ["NEE", "DUK", "SO", "D", "AEP"],
    "Basic Materials": ["LIN", "APD", "SHW", "FCX", "NEM"],
}


async def fetch_peer_comparison(ticker: str, sector: str = "", industry: str = "") -> dict:
    """取得同業比較數據（最多 4 家代表股）。"""
    try:
        peers = _get_peers(ticker.upper(), sector)
        if not peers:
            return {"source": "peer_comparison", "error": "無法找到同業公司進行比較"}

        results = await asyncio.gather(
            *[_fetch_peer_metrics(p) for p in peers],
            return_exceptions=True,
        )

        peer_data = [
            r for r in results
            if isinstance(r, dict) and "error" not in r
        ]

        if not peer_data:
            return {"source": "peer_comparison", "error": "同業數據全部取得失敗"}

        avg_pe = _avg([d.get("pe") for d in peer_data])
        avg_fwd_pe = _avg([d.get("forward_pe") for d in peer_data])
        avg_margin = _avg([d.get("profit_margin") for d in peer_data])
        avg_growth = _avg([d.get("revenue_growth") for d in peer_data])

        return {
            "source": "peer_comparison",
            "ticker": ticker.upper(),
            "sector": sector,
            "peers": [d["ticker"] for d in peer_data],
            "peer_details": peer_data,
            "sector_avg_pe": round(avg_pe, 2) if avg_pe else "N/A",
            "sector_avg_forward_pe": round(avg_fwd_pe, 2) if avg_fwd_pe else "N/A",
            "sector_avg_profit_margin": round(avg_margin, 4) if avg_margin else "N/A",
            "sector_avg_revenue_growth": round(avg_growth, 4) if avg_growth else "N/A",
        }

    except Exception as e:
        return {"source": "peer_comparison", "error": f"同業比較錯誤: {e}"}


def _get_peers(ticker: str, sector: str) -> list[str]:
    """取得同業公司列表（排除自身，最多 4 家）。"""
    if sector and sector in SECTOR_LEADERS:
        peers = [p for p in SECTOR_LEADERS[sector] if p != ticker]
    else:
        peers = [p for p in ["AAPL", "MSFT", "GOOGL", "AMZN"] if p != ticker]
    return peers[:4]


async def _fetch_peer_metrics(ticker: str) -> dict:
    """從 FMP 抓單一公司的同業比較指標。"""
    try:
        profile_data, metrics_data = await asyncio.gather(
            _fmp_get("profile", symbol=ticker),
            _fmp_get("key-metrics-ttm", symbol=ticker),
        )
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}

    profile = profile_data[0] if isinstance(profile_data, list) and profile_data else {}
    metrics = metrics_data[0] if isinstance(metrics_data, list) and metrics_data else {}

    if not profile and not metrics:
        return {"ticker": ticker, "error": "無數據"}

    return {
        "ticker": ticker,
        "company_name": profile.get("companyName") or ticker,
        "pe": metrics.get("peRatioTTM"),
        # FMP 沒有 forward PE，用 TTM PE 充數讓平均仍可計算
        "forward_pe": metrics.get("peRatioTTM"),
        "profit_margin": metrics.get("netProfitMarginTTM"),
        "revenue_growth": metrics.get("revenueGrowthTTM"),
        "market_cap": profile.get("marketCap") or profile.get("mktCap"),
        "eps": profile.get("eps") or metrics.get("netIncomePerShareTTM"),
    }


def _avg(values: list) -> float | None:
    """計算非 None 數值的平均。"""
    valid = [v for v in values if v is not None and isinstance(v, (int, float))]
    if not valid:
        return None
    return sum(valid) / len(valid)
