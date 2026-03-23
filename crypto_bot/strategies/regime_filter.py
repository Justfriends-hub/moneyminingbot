"""
strategies/regime_filter.py
----------------------------
Market regime filter.
Detects whether current market conditions are suitable for trading.
Uses ADX for trend strength and ATR/price ratio for volatility.

Regime types:
  - TRENDING    → Good for trend-following
  - RANGING     → Good for mean reversion
  - VOLATILE    → Too choppy, skip all strategies
  - LOW_VOL     → Too flat, skip all strategies
"""

import logging
import pandas as pd
import pandas_ta as ta
from typing import Tuple

from config import StrategyConfig

logger = logging.getLogger(__name__)

# Regime labels
REGIME_TRENDING  = "trending"
REGIME_RANGING   = "ranging"
REGIME_VOLATILE  = "volatile"
REGIME_LOW_VOL   = "low_volatility"
REGIME_UNKNOWN   = "unknown"


def detect_regime(df: pd.DataFrame) -> Tuple[str, float]:
    """
    Detect the current market regime from OHLCV data.

    Args:
        df: OHLCV DataFrame (needs at least 30 candles).

    Returns:
        Tuple of (regime_label, regime_score_0_to_100)
        Higher score = more suitable for trading.
    """
    if df is None or len(df) < 30:
        return REGIME_UNKNOWN, 0.0

    try:
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # ── ATR-based volatility ratio ──────────────
        atr = ta.atr(high, low, close, length=StrategyConfig.REGIME_ADX_PERIOD)
        if atr is None or atr.empty:
            return REGIME_UNKNOWN, 0.0

        current_close = float(close.iloc[-1])
        current_atr = float(atr.iloc[-1])

        if current_close <= 0:
            return REGIME_UNKNOWN, 0.0

        atr_ratio = current_atr / current_close

        # ── ADX for trend strength ───────────────────
        adx_result = ta.adx(
            high, low, close,
            length=StrategyConfig.REGIME_ADX_PERIOD
        )

        if adx_result is None or adx_result.empty:
            return REGIME_UNKNOWN, 0.0

        # pandas_ta returns columns: ADX_14, DMP_14, DMN_14
        adx_col = [c for c in adx_result.columns if c.startswith("ADX")]
        if not adx_col:
            return REGIME_UNKNOWN, 0.0

        current_adx = float(adx_result[adx_col[0]].iloc[-1])

        # ── Regime classification ───────────────────
        min_atr = StrategyConfig.REGIME_ATR_RATIO_MIN
        max_atr = StrategyConfig.REGIME_ATR_RATIO_MAX
        adx_trend_min = StrategyConfig.REGIME_ADX_TRENDING_MIN

        # Too volatile — skip trading
        if atr_ratio > max_atr:
            score = max(0.0, 100.0 - (atr_ratio / max_atr) * 100.0)
            logger.debug(
                f"[Regime] VOLATILE | ATR ratio: {atr_ratio:.4f} | ADX: {current_adx:.1f} | Score: {score:.0f}"
            )
            return REGIME_VOLATILE, round(score, 1)

        # Too flat / no volatility — skip trading
        if atr_ratio < min_atr:
            score = (atr_ratio / min_atr) * 50.0
            logger.debug(
                f"[Regime] LOW_VOL | ATR ratio: {atr_ratio:.4f} | ADX: {current_adx:.1f} | Score: {score:.0f}"
            )
            return REGIME_LOW_VOL, round(score, 1)

        # Strong trend
        if current_adx >= adx_trend_min:
            # Score: higher ADX + moderate volatility = better for trend
            adx_score = min(100.0, (current_adx / 50.0) * 100.0)
            vol_score = 100.0 - abs(atr_ratio - 0.02) / 0.02 * 50.0
            score = (adx_score * 0.6 + vol_score * 0.4)
            score = max(0.0, min(100.0, score))
            logger.debug(
                f"[Regime] TRENDING | ATR ratio: {atr_ratio:.4f} | ADX: {current_adx:.1f} | Score: {score:.0f}"
            )
            return REGIME_TRENDING, round(score, 1)

        # Ranging market (low ADX, normal volatility)
        range_score = (1.0 - current_adx / adx_trend_min) * 100.0
        range_score = max(0.0, min(100.0, range_score))
        logger.debug(
            f"[Regime] RANGING | ATR ratio: {atr_ratio:.4f} | ADX: {current_adx:.1f} | Score: {range_score:.0f}"
        )
        return REGIME_RANGING, round(range_score, 1)

    except Exception as e:
        logger.error(f"[Regime] Detection failed: {e}")
        return REGIME_UNKNOWN, 0.0


def is_regime_suitable_for_strategy(regime: str, strategy: str) -> bool:
    """
    Check if the detected regime suits a given strategy.

    Args:
        regime:   Regime label from detect_regime().
        strategy: Strategy name ('trend', 'mean_reversion', 'breakout').

    Returns:
        True if the strategy should run in this regime.
    """
    suitability = {
        "trend": [REGIME_TRENDING],
        "mean_reversion": [REGIME_RANGING],
        "breakout": [REGIME_TRENDING, REGIME_RANGING],  # Breakouts can form in both
    }
    allowed = suitability.get(strategy, [])
    return regime in allowed
