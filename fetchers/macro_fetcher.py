"""
宏觀環境指標模組
透過 FMP `/quote/^VIX` 與 `/quote/^TNX` 取得 VIX 與 10 年期美債殖利率。
"""

import asyncio

from fetchers.fmp_fetcher import fetch_fmp_quote


async def fetch_macro_data() -> dict:
    """抓取宏觀環境指標（VIX、10Y 殖利率、風險環境分類）。"""
    try:
        vix_q, tnx_q = await asyncio.gather(
            fetch_fmp_quote("^VIX"),
            fetch_fmp_quote("^TNX"),
        )
    except Exception as e:
        return {"source": "macro", "error": f"宏觀數據錯誤: {e}"}

    result = {"source": "macro"}

    vix_val = _extract_price(vix_q)
    if vix_val is None:
        result["vix"] = "N/A"
        result["vix_level"] = "N/A"
    else:
        result["vix"] = round(vix_val, 2)
        if vix_val < 15:
            result["vix_level"] = "低波動（樂觀）"
        elif vix_val < 25:
            result["vix_level"] = "正常"
        elif vix_val < 35:
            result["vix_level"] = "偏高（謹慎）"
        else:
            result["vix_level"] = "恐慌（極端）"

    tnx_val = _extract_price(tnx_q)
    if tnx_val is None:
        result["us10y"] = "N/A"
        result["yield_level"] = "N/A"
    else:
        result["us10y"] = round(tnx_val, 2)
        if tnx_val < 3.5:
            result["yield_level"] = "低利率環境（利多成長股）"
        elif tnx_val < 4.5:
            result["yield_level"] = "中性利率環境"
        else:
            result["yield_level"] = "高利率環境（利空成長股）"

    vix = result.get("vix")
    us10y = result.get("us10y")
    if isinstance(vix, (int, float)) and isinstance(us10y, (int, float)):
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


def _extract_price(quote: dict | None) -> float | None:
    if not quote:
        return None
    price = quote.get("price")
    try:
        return float(price) if price is not None else None
    except (ValueError, TypeError):
        return None
