"""
技術指標計算引擎
純 pandas/numpy 實作，所有函數接收 Series/DataFrame，回傳計算結果。
用於回測引擎的歷史指標計算。
"""

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """簡單移動平均線。"""
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """指數移動平均線。"""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Wilder's RSI（相對強弱指標）。
    使用 Wilder 平滑法（等同 EMA alpha=1/period）。
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD 指標。

    Returns:
        (macd_line, signal_line, histogram)
    """
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    布林通道。

    Returns:
        (upper_band, middle_band, lower_band)
    """
    middle = sma(series, period)
    rolling_std = series.rolling(window=period, min_periods=period).std()
    upper = middle + (rolling_std * std_dev)
    lower = middle - (rolling_std * std_dev)
    return upper, middle, lower


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    平均真實範圍（Average True Range）。
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    ADX（平均趨向指標）。

    Returns:
        (adx_values, di_plus, di_minus)
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    plus_dm = (high - prev_high).where((high - prev_high) > (prev_low - low), 0.0)
    plus_dm = plus_dm.where(plus_dm > 0, 0.0)

    minus_dm = (prev_low - low).where((prev_low - low) > (high - prev_high), 0.0)
    minus_dm = minus_dm.where(minus_dm > 0, 0.0)

    atr_values = atr(high, low, close, period)

    smoothed_plus_dm = plus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    smoothed_minus_dm = minus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    di_plus = 100.0 * smoothed_plus_dm / atr_values.replace(0, np.nan)
    di_minus = 100.0 * smoothed_minus_dm / atr_values.replace(0, np.nan)

    dx = 100.0 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx_values = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    return adx_values, di_plus, di_minus


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_period: int = 3,
    smooth: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """
    Stochastic Oscillator（隨機指標）。

    Returns:
        (percent_k, percent_d)
    """
    lowest_low = low.rolling(window=k_period, min_periods=k_period).min()
    highest_high = high.rolling(window=k_period, min_periods=k_period).max()

    raw_k = 100.0 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)

    percent_k = raw_k.rolling(window=smooth, min_periods=1).mean()
    percent_d = percent_k.rolling(window=d_period, min_periods=1).mean()

    return percent_k, percent_d


def rolling_correlation(
    series_a: pd.Series,
    series_b: pd.Series,
    period: int = 20,
) -> pd.Series:
    """
    滾動 Pearson 相關係數。
    用於計算 XAUUSD 與 DXY 的相關性。
    """
    return series_a.rolling(window=period, min_periods=period).corr(series_b)


def support_resistance(
    high: pd.Series,
    low: pd.Series,
    period: int = 20,
) -> tuple[float, float]:
    """
    計算簡單的支撐壓力位（近 period 期最高/最低價）。

    Returns:
        (resistance, support)
    """
    resistance = high.tail(period).max()
    support = low.tail(period).min()
    return float(resistance), float(support)


def detect_rsi_divergence(
    close: pd.Series,
    rsi_values: pd.Series,
    lookback: int = 10,
) -> str | None:
    """
    檢測 RSI 背離。

    Returns:
        "bullish" - 看漲背離（價格新低但 RSI 未新低）
        "bearish" - 看跌背離（價格新高但 RSI 未新高）
        None - 無背離
    """
    if len(close) < lookback * 2 or len(rsi_values) < lookback * 2:
        return None

    recent_close = close.iloc[-lookback:]
    prev_close = close.iloc[-lookback * 2:-lookback]
    recent_rsi = rsi_values.iloc[-lookback:]
    prev_rsi = rsi_values.iloc[-lookback * 2:-lookback]

    recent_close_min = recent_close.min()
    prev_close_min = prev_close.min()
    recent_rsi_min = recent_rsi.min()
    prev_rsi_min = prev_rsi.min()

    if recent_close_min < prev_close_min and recent_rsi_min > prev_rsi_min:
        return "bullish"

    recent_close_max = recent_close.max()
    prev_close_max = prev_close.max()
    recent_rsi_max = recent_rsi.max()
    prev_rsi_max = prev_rsi.max()

    if recent_close_max > prev_close_max and recent_rsi_max < prev_rsi_max:
        return "bearish"

    return None


def bollinger_band_width(upper: pd.Series, lower: pd.Series, middle: pd.Series) -> pd.Series:
    """布林帶寬度（百分比）。"""
    return ((upper - lower) / middle.replace(0, np.nan)) * 100.0
