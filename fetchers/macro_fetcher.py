"""
宏觀環境指標模組
使用 yfinance 取得 VIX 恐慌指數與 10 年期美國公債殖利率。
靈感來自 ai-hedge-fund 的 macro context。
"""

import asyncio

import yfinance as yf

from utils.retry import retry_async_call


async def fetch_macro_data() -> dict:
    """
    抓取宏觀環境指標。

    Returns:
        dict: 包含 VIX、10Y 殖利率、風險環境評估
    """
    try:
        vix_task = retry_async_call(
            asyncio.to_thread,
            lambda: yf.Ticker("^VIX").history(period="5d", interval="1d"),
            source_name="yfinance_VIX",
        )
        tnx_task = retry_async_call(
            asyncio.to_thread,
            lambda: yf.Ticker("^TNX").history(period="5d", interval="1d"),
            source_name="yfinance_TNX",
        )

        vix_hist, tnx_hist = await asyncio.gather(vix_task, tnx_task, return_exceptions=True)

        result = {"source": "macro"}

        # VIX
        if isinstance(vix_hist, Exception) or vix_hist is None or vix_hist.empty:
            result["vix"] = "N/A"
            result["vix_level"] = "N/A"
        else:
            vix_val = round(float(vix_hist["Close"].iloc[-1]), 2)
            result["vix"] = vix_val
            if vix_val < 15:
                result["vix_level"] = "低波動（樂觀）"
            elif vix_val < 25:
                result["vix_level"] = "正常"
            elif vix_val < 35:
                result["vix_level"] = "偏高（謹慎）"
            else:
                result["vix_level"] = "恐慌（極端）"

        # 10Y Treasury Yield
        if isinstance(tnx_hist, Exception) or tnx_hist is None or tnx_hist.empty:
            result["us10y"] = "N/A"
            result["yield_level"] = "N/A"
        else:
            tnx_val = round(float(tnx_hist["Close"].iloc[-1]), 2)
            result["us10y"] = tnx_val
            if tnx_val < 3.5:
                result["yield_level"] = "低利率環境（利多成長股）"
            elif tnx_val < 4.5:
                result["yield_level"] = "中性利率環境"
            else:
                result["yield_level"] = "高利率環境（利空成長股）"

        # 風險環境綜合判斷
        vix = result.get("vix", "N/A")
        us10y = result.get("us10y", "N/A")
        if vix != "N/A" and us10y != "N/A":
            if vix < 20 and us10y < 4.0:
                result["risk_environment"] = "risk_on"
                result["risk_label"] = "風險偏好（有利股市）"
            elif vix > 30 or us10y > 5.0:
                result["risk_environment"] = "risk_off"
                result["risk_label"] = "風險趨避（不利股市）"
            else:
                result["risk_environment"] = "neutral"
                result["risk_label"] = "中性環境"
        else:
            result["risk_environment"] = "N/A"
            result["risk_label"] = "數據不足"

        return result

    except Exception as e:
        return {
            "source": "macro",
            "error": f"宏觀數據錯誤: {str(e)}",
        }
