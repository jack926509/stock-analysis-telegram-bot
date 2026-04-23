"""
K 線圖生成模組
使用 mplfinance 繪製 K 線圖 + 均線，輸出為 PNG 圖片供 Telegram 發送。
"""

import asyncio
import io
import logging

import yfinance as yf
import mplfinance as mpf

logger = logging.getLogger(__name__)


async def generate_chart(ticker: str, days: int = 60) -> io.BytesIO | None:
    """
    生成 K 線圖。

    Args:
        ticker: 股票代碼
        days: 顯示天數

    Returns:
        BytesIO 圖片 buffer，失敗回傳 None
    """
    try:
        return await asyncio.to_thread(_render_chart, ticker, days)
    except Exception as e:
        logger.warning(f"[{ticker}] K 線圖生成失敗: {e}")
        return None


def _render_chart(ticker: str, days: int) -> io.BytesIO | None:
    stock = yf.Ticker(ticker.upper())
    hist = stock.history(period=f"{days + 30}d", interval="1d")

    if hist is None or hist.empty or len(hist) < 10:
        return None

    # 取最後 N 天
    hist = hist.tail(days)

    # 樣式
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
        hist,
        type="candle",
        style=style,
        volume=True,
        mav=(5, 20, 60),
        title=f"\n{ticker.upper()} — {days}D",
        figsize=(10, 6),
        returnfig=True,
        tight_layout=True,
    )

    # 加圖例
    ax_main = axes[0]
    ax_main.legend(
        [f"MA5", f"MA20", f"MA60"],
        loc="upper left",
        fontsize=8,
        facecolor="#1a1a2e",
        edgecolor="#444444",
        labelcolor="#cccccc",
    )

    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="#1a1a2e")
    buf.seek(0)
    return buf
