"""
AI 策略選擇器
使用 Claude 根據市場條件動態選擇最優策略組合。
"""

import json
import logging

import anthropic

from forex_trading.config import ForexConfig

logger = logging.getLogger(__name__)

_anthropic_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=ForexConfig.ANTHROPIC_API_KEY)
    return _anthropic_client


SYSTEM_PROMPT = """You are a quantitative trading strategy selector for XAUUSD (Gold/USD) forex trading.
Your role is to analyze current market conditions and select the optimal trading strategy.

You have 4 strategies available:
1. trend_following: Multi-timeframe EMA crossover with ADX filter and DXY confirmation. Best in strong trending markets (ADX > 25).
2. session_breakout: Asian range breakout at London/NY open. Best during session transitions with tight Asian ranges.
3. bollinger_rsi: Mean reversion using Bollinger Bands + RSI. Best in ranging/choppy markets (ADX < 20).
4. dxy_correlation: Gold-DXY inverse correlation trades. Best when correlation is strong (< -0.7).

Rules:
- Select 1-2 strategies that best fit current conditions
- NEVER select contradictory strategies (e.g., trend_following + bollinger_rsi simultaneously)
- Be conservative: if conditions are unclear, select fewer strategies with lower confidence
- Consider recent performance: deprioritize strategies with recent losing streaks
- Respond ONLY in valid JSON format with keys: selected_strategies, confidence, reasoning, position_size_modifier, market_regime"""


async def select_strategies(
    market_data: dict,
    strategy_scores: dict[str, float],
    strategy_performance: dict[str, dict],
) -> dict:
    """
    使用 AI 選擇最適合的策略組合。

    Args:
        market_data: 當前市場指標
        strategy_scores: 各策略自評分數 {"trend_following": 7.5, ...}
        strategy_performance: 各策略近期績效 {"trend_following": {"win_rate": 65, "profit_factor": 1.8}, ...}

    Returns:
        dict: {
            "selected_strategies": ["trend_following"],
            "confidence": 0.85,
            "reasoning": "...",
            "position_size_modifier": 1.0,
            "market_regime": "trending"
        }
    """
    try:
        client = _get_client()

        user_prompt = f"""Current XAUUSD Market Conditions:

[Market Data]
- Price: {market_data.get('price', 'N/A')}
- 4H ADX: {market_data.get('adx_4h', 'N/A')} | 1H ADX: {market_data.get('adx_1h', 'N/A')}
- 4H EMA50/EMA200: {market_data.get('ema50_4h', 'N/A')}/{market_data.get('ema200_4h', 'N/A')}
- 1H RSI: {market_data.get('rsi_1h', 'N/A')} | 4H RSI: {market_data.get('rsi_4h', 'N/A')}
- 1H Bollinger Width: {market_data.get('bb_width_1h', 'N/A')}
- ATR(14) 1H: {market_data.get('atr_1h', 'N/A')}
- DXY-Gold Correlation (20-period): {market_data.get('correlation', 'N/A')}
- Current Session: {market_data.get('session', 'N/A')} (UTC time: {market_data.get('utc_time', 'N/A')})
- Asian Range: {market_data.get('asian_high', 'N/A')} - {market_data.get('asian_low', 'N/A')}

[Strategy Suitability Scores (self-reported)]
- trend_following: {strategy_scores.get('trend_following', 'N/A')}/10
- session_breakout: {strategy_scores.get('session_breakout', 'N/A')}/10
- bollinger_rsi: {strategy_scores.get('bollinger_rsi', 'N/A')}/10
- dxy_correlation: {strategy_scores.get('dxy_correlation', 'N/A')}/10

[Recent Performance (last 20 trades)]
- trend_following: {strategy_performance.get('trend_following', {}).get('win_rate', 'N/A')}% win rate, {strategy_performance.get('trend_following', {}).get('profit_factor', 'N/A')} profit factor
- session_breakout: {strategy_performance.get('session_breakout', {}).get('win_rate', 'N/A')}% win rate, {strategy_performance.get('session_breakout', {}).get('profit_factor', 'N/A')} profit factor
- bollinger_rsi: {strategy_performance.get('bollinger_rsi', {}).get('win_rate', 'N/A')}% win rate, {strategy_performance.get('bollinger_rsi', {}).get('profit_factor', 'N/A')} profit factor
- dxy_correlation: {strategy_performance.get('dxy_correlation', {}).get('win_rate', 'N/A')}% win rate, {strategy_performance.get('dxy_correlation', {}).get('profit_factor', 'N/A')} profit factor

Select the best strategy/strategies. Respond in JSON only."""

        response = await client.messages.create(
            model=ForexConfig.ANTHROPIC_MODEL,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.2,
            timeout=30,
        )

        response_text = response.content[0].text.strip()

        # 解析 JSON（可能包在 markdown code block 裡）
        if "```" in response_text:
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
            response_text = response_text.strip()

        result = json.loads(response_text)

        # 驗證必要欄位
        if "selected_strategies" not in result:
            raise ValueError("Missing selected_strategies")

        valid_strategies = {"trend_following", "session_breakout", "bollinger_rsi", "dxy_correlation"}
        result["selected_strategies"] = [
            s for s in result["selected_strategies"] if s in valid_strategies
        ]

        if not result["selected_strategies"]:
            raise ValueError("No valid strategies selected")

        result.setdefault("confidence", 0.5)
        result.setdefault("reasoning", "")
        result.setdefault("position_size_modifier", 1.0)
        result.setdefault("market_regime", "uncertain")

        logger.info(
            f"AI 策略選擇: {result['selected_strategies']}, "
            f"信心 {result['confidence']}, 市場狀態 {result['market_regime']}"
        )

        return result

    except Exception as e:
        logger.error(f"AI 策略選擇失敗: {e}")
        # 回退到最高自評分數的策略
        if strategy_scores:
            best = max(strategy_scores, key=strategy_scores.get)
            return {
                "selected_strategies": [best],
                "confidence": 0.4,
                "reasoning": f"AI 選擇失敗，回退到最高分策略 {best}",
                "position_size_modifier": 0.7,
                "market_regime": "uncertain",
            }
        return {
            "selected_strategies": ["trend_following"],
            "confidence": 0.3,
            "reasoning": "AI 選擇失敗，使用預設策略",
            "position_size_modifier": 0.5,
            "market_regime": "uncertain",
        }
