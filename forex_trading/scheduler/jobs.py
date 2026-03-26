"""
排程任務管理
使用 asyncio 任務定期執行策略分析、持倉監控和市場分析。
"""

import asyncio
import logging
from datetime import datetime, timezone

from forex_trading.config import ForexConfig
from forex_trading.data.market_data import MarketDataManager
from forex_trading.portfolio.simulator import PortfolioSimulator
from forex_trading.strategies.trend_following import TrendFollowingStrategy
from forex_trading.strategies.session_breakout import SessionBreakoutStrategy
from forex_trading.strategies.bollinger_rsi import BollingerRSIStrategy
from forex_trading.strategies.dxy_correlation import DXYCorrelationStrategy
from forex_trading.ai.strategy_selector import select_strategies
from forex_trading.ai.market_analyst import generate_daily_analysis
from forex_trading.db.database import save_market_analysis, save_ai_selection
from forex_trading.indicators.technical import (
    ema, rsi, adx, atr, bollinger_bands, bollinger_band_width, rolling_correlation,
)
from forex_trading.utils.formatter import (
    format_signal,
    format_position_closed,
    format_weekly_summary,
)

logger = logging.getLogger(__name__)

ALL_STRATEGIES = {
    "trend_following": TrendFollowingStrategy(),
    "session_breakout": SessionBreakoutStrategy(),
    "bollinger_rsi": BollingerRSIStrategy(),
    "dxy_correlation": DXYCorrelationStrategy(),
}


def is_market_open() -> bool:
    """判斷外匯市場是否開盤（週日 22:00 UTC ~ 週五 22:00 UTC）。"""
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
    hour = now.hour

    if weekday == 4 and hour >= 22:
        return False
    if weekday == 5:
        return False
    if weekday == 6 and hour < 22:
        return False
    return True


async def run_signal_check(
    data_manager: MarketDataManager,
    simulator: PortfolioSimulator,
    push_callback=None,
) -> list[dict]:
    """
    執行一次信號檢查：
    1. 抓取數據
    2. 計算各策略適用度
    3. AI 選擇策略
    4. 執行選中策略的分析
    5. 處理產生的信號

    Returns:
        已開倉的信號列表
    """
    logger.info("開始信號檢查...")

    # 1. 抓取數據
    gold_1h = await data_manager.get_gold_ohlcv("1h", "60d")
    dxy_1h = await data_manager.get_dxy_ohlcv("1h", "60d")
    quote = await data_manager.get_realtime_quote()

    if gold_1h is None or gold_1h.empty:
        logger.warning("無法取得黃金數據，跳過信號檢查")
        return []

    now = datetime.now(timezone.utc)

    # 2. 計算各策略適用度
    strategy_scores = {}
    for name, strategy in ALL_STRATEGIES.items():
        try:
            score = strategy.suitability_score(gold_1h, dxy_1h, now)
            strategy_scores[name] = round(score, 1)
        except Exception as e:
            logger.warning(f"策略 {name} 評分失敗: {e}")
            strategy_scores[name] = 1.0

    # 3. 收集市場數據摘要（給 AI 用）
    market_data = _build_market_summary(gold_1h, dxy_1h, quote, now)

    # 4. AI 策略選擇
    strategy_performance = await simulator.get_all_strategy_performance()

    ai_result = await select_strategies(
        market_data=market_data,
        strategy_scores=strategy_scores,
        strategy_performance=strategy_performance,
    )

    selected_names = ai_result.get("selected_strategies", [])
    market_regime = ai_result.get("market_regime", "uncertain")

    # 儲存 AI 選擇記錄
    await save_ai_selection(
        selected_strategies=selected_names,
        confidence=ai_result.get("confidence", 0),
        reasoning=ai_result.get("reasoning", ""),
        market_regime=market_regime,
        market_data_summary=market_data,
    )

    # 5. 執行選中策略
    opened = []
    for name in selected_names:
        strategy = ALL_STRATEGIES.get(name)
        if not strategy:
            continue

        try:
            signal = strategy.analyze(gold_1h, dxy_1h, now)
        except Exception as e:
            logger.error(f"策略 {name} 分析錯誤: {e}")
            continue

        if signal is None:
            continue

        # 6. 處理信號
        result = await simulator.process_signal(
            signal=signal,
            ai_selected=True,
            market_regime=market_regime,
        )

        if result:
            opened.append(result)

            # 推送通知
            if push_callback:
                msg = format_signal(result)
                try:
                    await push_callback(msg)
                except Exception as e:
                    logger.error(f"信號推送失敗: {e}")

    logger.info(f"信號檢查完成: {len(opened)} 筆新開倉")
    return opened


async def run_position_check(
    data_manager: MarketDataManager,
    simulator: PortfolioSimulator,
    push_callback=None,
) -> list[dict]:
    """
    檢查持倉停損/停利。
    """
    quote = await data_manager.get_realtime_quote()
    if not quote:
        return []

    current_price = quote.get("price", 0)
    if current_price <= 0:
        return []

    closed = await simulator.check_positions(current_price)

    for closed_pos in closed:
        if push_callback:
            msg = format_position_closed(closed_pos)
            try:
                await push_callback(msg)
            except Exception as e:
                logger.error(f"平倉通知推送失敗: {e}")

    # 記錄權益快照
    await simulator.record_snapshot()

    return closed


async def run_daily_analysis(
    data_manager: MarketDataManager,
    simulator: PortfolioSimulator,
    push_callback=None,
) -> str:
    """執行每日 AI 市場分析。"""
    logger.info("開始每日市場分析...")

    quote = await data_manager.get_realtime_quote()
    tv_data = await data_manager.get_tv_multi_timeframe()
    news = await data_manager.get_news_sentiment()

    status = await simulator.get_status()

    analysis = await generate_daily_analysis(
        quote=quote,
        tv_data=tv_data,
        gold_indicators=None,
        dxy_data=None,
        news_data=news,
        portfolio_status=status,
    )

    await save_market_analysis(analysis)

    if push_callback:
        try:
            await push_callback(analysis)
        except Exception as e:
            logger.error(f"每日分析推送失敗: {e}")

    logger.info("每日市場分析完成")
    return analysis


def _build_market_summary(gold_df, dxy_df, quote, current_time) -> dict:
    """建構市場數據摘要（給 AI 策略選擇器）。"""
    summary = {
        "price": quote.get("price") if quote else None,
        "session": ForexConfig.get_session(current_time.hour),
        "utc_time": current_time.strftime("%H:%M UTC"),
    }

    if gold_df is not None and len(gold_df) >= 200:
        close = gold_df["Close"]
        high = gold_df["High"]
        low = gold_df["Low"]

        try:
            adx_vals, _, _ = adx(high, low, close, 14)
            summary["adx_1h"] = round(float(adx_vals.iloc[-1]), 1)

            rsi_vals = rsi(close, 14)
            summary["rsi_1h"] = round(float(rsi_vals.iloc[-1]), 1)

            atr_vals = atr(high, low, close, 14)
            summary["atr_1h"] = round(float(atr_vals.iloc[-1]), 2)

            ema50 = ema(close, 50)
            ema200 = ema(close, 200)
            summary["ema50_4h"] = round(float(ema50.iloc[-1]), 2)
            summary["ema200_4h"] = round(float(ema200.iloc[-1]), 2)

            bb_upper, bb_mid, bb_lower = bollinger_bands(close, 20, 2.0)
            bb_w = bollinger_band_width(bb_upper, bb_lower, bb_mid)
            summary["bb_width_1h"] = round(float(bb_w.iloc[-1]), 2)

        except Exception as e:
            logger.warning(f"指標計算失敗: {e}")

    if dxy_df is not None and gold_df is not None:
        try:
            gold_close = gold_df["Close"]
            dxy_close = dxy_df["Close"]
            min_len = min(len(gold_close), len(dxy_close))
            if min_len >= 20:
                corr = rolling_correlation(
                    gold_close.iloc[-min_len:],
                    dxy_close.iloc[-min_len:],
                    20,
                )
                if not corr.empty:
                    summary["correlation"] = round(float(corr.iloc[-1]), 2)
        except Exception:
            pass

    return summary


class ForexScheduler:
    """外匯交易排程器。"""

    def __init__(
        self,
        data_manager: MarketDataManager,
        simulator: PortfolioSimulator,
        push_callback=None,
    ):
        self.data_manager = data_manager
        self.simulator = simulator
        self.push_callback = push_callback
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        """啟動所有排程任務。"""
        self._running = True
        self._tasks = [
            asyncio.create_task(self._signal_check_loop()),
            asyncio.create_task(self._position_monitor_loop()),
            asyncio.create_task(self._daily_analysis_loop()),
            asyncio.create_task(self._weekly_summary_loop()),
        ]
        logger.info("排程器已啟動")

    async def stop(self) -> None:
        """停止所有排程任務。"""
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        logger.info("排程器已停止")

    async def _signal_check_loop(self) -> None:
        """每 5 分鐘檢查交易信號。"""
        while self._running:
            try:
                if is_market_open():
                    await run_signal_check(
                        self.data_manager,
                        self.simulator,
                        self.push_callback,
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"信號檢查錯誤: {e}", exc_info=True)

            await asyncio.sleep(ForexConfig.SIGNAL_CHECK_INTERVAL)

    async def _position_monitor_loop(self) -> None:
        """每 1 分鐘監控持倉。"""
        while self._running:
            try:
                if is_market_open():
                    await run_position_check(
                        self.data_manager,
                        self.simulator,
                        self.push_callback,
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"持倉監控錯誤: {e}", exc_info=True)

            await asyncio.sleep(ForexConfig.POSITION_CHECK_INTERVAL)

    async def _daily_analysis_loop(self) -> None:
        """每日 06:00 UTC 執行市場分析。"""
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                if now.hour == ForexConfig.DAILY_ANALYSIS_HOUR and now.minute < 5:
                    await run_daily_analysis(
                        self.data_manager,
                        self.simulator,
                        self.push_callback,
                    )
                    await asyncio.sleep(3600)  # 防止重複觸發
                    continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"每日分析錯誤: {e}", exc_info=True)

            await asyncio.sleep(60)

    async def _weekly_summary_loop(self) -> None:
        """每週一 06:00 UTC 生成週報。"""
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                if (now.weekday() == ForexConfig.WEEKLY_SUMMARY_DAY and
                        now.hour == ForexConfig.DAILY_ANALYSIS_HOUR and
                        now.minute < 5):

                    status = await self.simulator.get_status()
                    performance = await self.simulator.get_all_strategy_performance()
                    msg = format_weekly_summary(status, performance)

                    if self.push_callback:
                        await self.push_callback(msg)

                    await asyncio.sleep(3600)
                    continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"週報錯誤: {e}", exc_info=True)

            await asyncio.sleep(60)
