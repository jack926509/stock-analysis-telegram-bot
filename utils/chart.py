"""
K 線圖生成模組
從 FMP 抓 OHLCV，組成 DataFrame 後用 mplfinance 繪圖輸出 PNG。
"""

import asyncio
import io
import logging

import mplfinance as mpf
import pandas as pd

from fetchers.fmp_fetcher import fetch_fmp_history

logger = logging.getLogger(__name__)


async def generate_chart(ticker: str, days: int = 60) -> io.BytesIO | None:
    """生成 K 線圖。失敗回 None。"""
    try:
        rows = await fetch_fmp_history(ticker.upper(), days=days + 30)
        if not rows or len(rows) < 10:
            return None
        return await asyncio.to_thread(_render_chart, ticker, rows, days)
    except Exception as e:
        logger.warning(f"[{ticker}] K 線圖生成失敗: {e}")
        return None


def _render_chart(ticker: str, rows: list[dict], days: int) -> io.BytesIO | None:
    df = pd.DataFrame(
        [
            {
                "Date": r["date"],
                "Open": float(r["open"]),
                "High": float(r["high"]),
                "Low": float(r["low"]),
                "Close": float(r["close"]),
                "Volume": float(r.get("volume") or 0),
            }
            for r in rows
        ]
    )
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    df = df.tail(days)

    mc = mpf.make_marketcolors(
        up="#26a69a", down="#ef5350",
        edge="inherit",
        wick={"up": "#26a69a", "down": "#ef5350"},
        volume={"up": "#26a69a80", "down": "#ef535080"},
    )
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mc,
        gridstyle="--",
        gridcolor="#333333",
        facecolor="#1a1a2e",
        figcolor="#1a1a2e",
        rc={
            "font.size": 9,
            "axes.labelcolor": "#cccccc",
            "xtick.color": "#999999",
            "ytick.color": "#999999",
        },
    )

    buf = io.BytesIO()
    fig, axes = mpf.plot(
        df,
        type="candle",
        style=style,
        volume=True,
        mav=(5, 20, 60),
        title=f"\n{ticker.upper()} — {days}D",
        figsize=(10, 6),
        returnfig=True,
        tight_layout=True,
    )

    axes[0].legend(
        ["MA5", "MA20", "MA60"],
        loc="upper left",
        fontsize=8,
        facecolor="#1a1a2e",
        edgecolor="#444444",
        labelcolor="#cccccc",
    )

    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="#1a1a2e")
    buf.seek(0)
    return buf
