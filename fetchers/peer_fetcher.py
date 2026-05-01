"""
同業比較模組
FMP profile（產業／市值）+ Finnhub metrics（PE/利潤率）做橫向對比。
"""

import asyncio

from fetchers.finnhub_fetcher import fetch_finnhub_metrics
from fetchers.fmp_fetcher import _fmp_get


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
    """同業比較（最多 4 家代表股）。"""
    try:
        peers = _get_peers(ticker.upper(), sector)
        if not peers:
            return {"source": "peer_comparison", "error": "無法找到同業公司進行比較"}

        results = await asyncio.gather(
            *[_fetch_peer_metrics(p) for p in peers],
            return_exceptions=True,
        )

        peer_data = [r for r in results if isinstance(r, dict) and "error" not in r]

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
            "sector_avg_profit_margin": round(avg_margin, 2) if avg_margin else "N/A",
            "sector_avg_revenue_growth": round(avg_growth, 2) if avg_growth else "N/A",
        }

    except Exception as e:
        return {"source": "peer_comparison", "error": f"同業比較錯誤: {e}"}


def _get_peers(ticker: str, sector: str) -> list[str]:
    if sector and sector in SECTOR_LEADERS:
        peers = [p for p in SECTOR_LEADERS[sector] if p != ticker]
    else:
        peers = [p for p in ["AAPL", "MSFT", "GOOGL", "AMZN"] if p != ticker]
    return peers[:4]


async def _fetch_peer_metrics(ticker: str) -> dict:
    """單一公司：FMP profile + Finnhub metrics。"""
    try:
        profile_data, metrics = await asyncio.gather(
            _fmp_get("profile", symbol=ticker),
            fetch_finnhub_metrics(ticker),
        )
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}

    profile = profile_data[0] if isinstance(profile_data, list) and profile_data else {}

    if not profile and not metrics:
        return {"ticker": ticker, "error": "無數據"}

    pe = metrics.get("peTTM") or metrics.get("peExclExtraTTM")
    return {
        "ticker": ticker,
        "company_name": profile.get("companyName") or ticker,
        "pe": pe,
        # Finnhub 沒有 forward PE，用 TTM PE 充數讓平均仍可計算
        "forward_pe": pe,
        "profit_margin": metrics.get("netProfitMarginTTM"),
        "revenue_growth": metrics.get("revenueGrowthTTMYoy"),
        "market_cap": profile.get("marketCap") or profile.get("mktCap"),
        "eps": metrics.get("epsTTM") or profile.get("eps"),
    }


def _avg(values: list) -> float | None:
    valid = [v for v in values if v is not None and isinstance(v, (int, float))]
    if not valid:
        return None
    return sum(valid) / len(valid)
