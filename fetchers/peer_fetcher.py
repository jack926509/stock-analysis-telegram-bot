"""
同業比較模組
使用 yfinance 取得同產業公司數據，進行橫向對比。
"""

import asyncio

import yfinance as yf


# 各產業代表性公司對照（用於找不到 peer 時的 fallback）
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
    """
    取得同業比較數據。

    Args:
        ticker: 目標股票代碼
        sector: 產業大類
        industry: 細分產業

    Returns:
        dict: 包含同業比較指標
    """
    try:
        # 選擇同業公司
        peers = _get_peers(ticker.upper(), sector)
        if not peers:
            return {
                "source": "peer_comparison",
                "error": "無法找到同業公司進行比較",
            }

        # 並行抓取同業數據
        tasks = [_fetch_peer_metrics(p) for p in peers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        peer_data = []
        for p, r in zip(peers, results):
            if isinstance(r, dict) and "error" not in r:
                peer_data.append(r)

        if not peer_data:
            return {
                "source": "peer_comparison",
                "error": "同業數據全部取得失敗",
            }

        # 計算同業平均值
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
        return {
            "source": "peer_comparison",
            "error": f"同業比較錯誤: {str(e)}",
        }


def _get_peers(ticker: str, sector: str) -> list[str]:
    """取得同業公司列表（排除自身，最多 4 家）。"""
    if sector and sector in SECTOR_LEADERS:
        peers = [p for p in SECTOR_LEADERS[sector] if p != ticker]
    else:
        # 預設用大盤指標股
        peers = [p for p in ["AAPL", "MSFT", "GOOGL", "AMZN"] if p != ticker]
    return peers[:4]


async def _fetch_peer_metrics(ticker: str) -> dict:
    """抓取單一公司的關鍵比較指標。"""
    try:
        stock = yf.Ticker(ticker)
        info = await asyncio.to_thread(lambda: stock.info)

        if not info:
            return {"ticker": ticker, "error": "無數據"}

        pe = info.get("trailingPE")
        fwd_pe = info.get("forwardPE")
        margin = info.get("profitMargins")
        growth = info.get("revenueGrowth")
        mcap = info.get("marketCap")
        eps = info.get("trailingEps")

        return {
            "ticker": ticker,
            "company_name": info.get("shortName", ticker),
            "pe": pe if pe else None,
            "forward_pe": fwd_pe if fwd_pe else None,
            "profit_margin": margin if margin else None,
            "revenue_growth": growth if growth else None,
            "market_cap": mcap if mcap else None,
            "eps": eps if eps else None,
        }

    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def _avg(values: list) -> float | None:
    """計算非 None 值的平均。"""
    valid = [v for v in values if v is not None and isinstance(v, (int, float))]
    if not valid:
        return None
    return sum(valid) / len(valid)
