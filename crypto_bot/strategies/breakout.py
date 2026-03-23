"""
strategies/breakout.py
-----------------------
Breakout Strategy: ATR + Volume + Support/Resistance levels.

Logic:
  LONG entry (bullish breakout):
    - Identify swing high over lookback period
    - Price closes ABOVE the swing high by at least 0.5 ATR (clean break, not noise)
    - Volume is >= 1.5x average (confirms conviction)
    - RSI is not in overbought territory (< 75) — avoid chasing exhausted moves
    - ATR is in a healthy range (not too flat, not blow-off)

  SHORT entry (bearish breakdown):
    - Identify swing low over lookback period
    - Price closes BELOW the swing low by at least 0.5 ATR
    - Volume >= 1.5x average
    - RSI not oversold (> 25)

  Stop Loss: on the other side of the broken level (inside the range)
  Take Profit: entry + (break distance * 2)  → measured move target
"""

import logging
import pandas as pd
import pandas_ta as ta
from typing import Optional

from strategies.base_strategy import BaseStrategy, Signal
from strategies.regime_filter import detect_regime, is_regime_suitable_for_strategy
from config import StrategyConfig, RiskConfig
from utils.helpers import score_signal

logger = logging.getLogger(__name__)

MIN_CANDLES_REQUIRED = 60


class BreakoutStrategy(BaseStrategy):
    """
    Breakout strategy using ATR-confirmed breaks of swing highs/lows
    with volume surge confirmation.
    """

    def __init__(self):
        super().__init__("breakout")

    def generate_signal(
        self,
        df: pd.DataFrame,
        pair: str,
        timeframe: str,
    ) -> Optional[Signal]:
        """
        Analyse OHLCV data and return a signal if breakout conditions are met.
        """
        if not self._has_enough_data(df, MIN_CANDLES_REQUIRED):
            return None

        try:
            # ── Regime check ─────────────────────────────
            regime, regime_score = detect_regime(df)
            if not is_regime_suitable_for_strategy(regime, "breakout"):
                self.logger.debug(
                    f"{pair} {timeframe}: Regime '{regime}' not suitable for breakout"
                )
                return None

            # ── Calculate indicators ──────────────────────
            close  = df["close"]
            high   = df["high"]
            low    = df["low"]
            volume = df["volume"]

            # ATR for breakout confirmation and SL sizing
            atr = ta.atr(high, low, close, length=StrategyConfig.BO_ATR_PERIOD)

            # RSI to avoid entering exhausted moves
            rsi = ta.rsi(close, length=14)

            # Volume SMA
            vol_sma = ta.sma(volume, length=20)

            if any(x is None or x.empty for x in [atr, rsi, vol_sma]):
                return None

            # ── Extract values ────────────────────────────
            current_atr   = self._safe_last(atr)
            current_close = self._safe_last(close)
            current_rsi   = self._safe_last(rsi)
            current_vol   = self._safe_last(volume)
            avg_vol       = self._safe_last(vol_sma)

            if any(v <= 0 for v in [current_atr, current_close, avg_vol]):
                return None

            # ── Swing high / swing low identification ─────
            # Use the lookback period, excluding the LAST candle (current)
            lookback = StrategyConfig.BO_LOOKBACK
            lookback_df = df.iloc[-(lookback + 1):-1]  # Exclude current candle

            if len(lookback_df) < lookback:
                return None

            swing_high = float(lookback_df["high"].max())
            swing_low  = float(lookback_df["low"].min())

            # Minimum break distance to confirm a real breakout (not just noise)
            min_break = current_atr * StrategyConfig.BO_ATR_BREAKOUT_MULT

            # ── Volume score ──────────────────────────────
            vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0.0
            vol_score = min(100.0, (vol_ratio / StrategyConfig.BO_VOLUME_SURGE) * 85.0)

            # ── LONG signal: bullish breakout ─────────────
            price_breaks_high = current_close > (swing_high + min_break)
            volume_surge = vol_ratio >= StrategyConfig.BO_VOLUME_SURGE
            rsi_not_overbought = current_rsi < 75.0

            if price_breaks_high and volume_surge and rsi_not_overbought:
                # SL: back inside the range (just below the swing high)
                stop_loss = swing_high - (current_atr * 0.3)

                # TP: measured move (break distance projected upward × 2)
                break_distance = current_close - swing_high
                take_profit = current_close + (break_distance * 2.0)

                rr_distance = current_close - stop_loss
                tp_distance = take_profit - current_close

                if rr_distance <= 0 or tp_distance <= 0:
                    return None

                rr_ratio = tp_distance / rr_distance

                if rr_ratio < RiskConfig.MIN_REWARD_RISK_RATIO:
                    self.logger.debug(
                        f"{pair} {timeframe}: LONG breakout rejected — RR {rr_ratio:.2f} < min"
                    )
                    return None

                conditions_met = sum([
                    price_breaks_high,
                    volume_surge,
                    rsi_not_overbought,
                    vol_ratio >= 2.0,        # Extra strong volume = bonus
                    current_rsi < 65.0,      # More room to run
                ])
                strategy_score = (conditions_met / 5) * 100.0
                composite_score = score_signal(strategy_score, vol_score, regime_score, rr_ratio)

                self.logger.info(
                    f"✅ BREAKOUT LONG signal: {pair} {timeframe} | "
                    f"Score: {composite_score:.1f} | Break: {current_close:.4f} > {swing_high:.4f} | "
                    f"Vol: {vol_ratio:.2f}x | RR: {rr_ratio:.2f}"
                )

                return Signal(
                    pair=pair,
                    timeframe=timeframe,
                    strategy="breakout",
                    side="buy",
                    entry_price=current_close,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    signal_score=composite_score,
                    strategy_score=strategy_score,
                    volume_score=vol_score,
                    regime_score=regime_score,
                    rr_ratio=rr_ratio,
                    notes=(
                        f"BO above {swing_high:.4f} | "
                        f"Break: {break_distance:.4f} | Vol: {vol_ratio:.2f}x"
                    ),
                )

            # ── SHORT signal: bearish breakdown ───────────
            price_breaks_low = current_close < (swing_low - min_break)
            rsi_not_oversold = current_rsi > 25.0

            if price_breaks_low and volume_surge and rsi_not_oversold:
                stop_loss = swing_low + (current_atr * 0.3)

                break_distance = swing_low - current_close
                take_profit = current_close - (break_distance * 2.0)

                rr_distance = stop_loss - current_close
                tp_distance = current_close - take_profit

                if rr_distance <= 0 or tp_distance <= 0:
                    return None

                rr_ratio = tp_distance / rr_distance

                if rr_ratio < RiskConfig.MIN_REWARD_RISK_RATIO:
                    return None

                conditions_met = sum([
                    price_breaks_low,
                    volume_surge,
                    rsi_not_oversold,
                    vol_ratio >= 2.0,
                    current_rsi > 35.0,
                ])
                strategy_score = (conditions_met / 5) * 100.0
                composite_score = score_signal(strategy_score, vol_score, regime_score, rr_ratio)

                self.logger.info(
                    f"✅ BREAKOUT SHORT signal: {pair} {timeframe} | "
                    f"Score: {composite_score:.1f} | Break: {current_close:.4f} < {swing_low:.4f} | "
                    f"Vol: {vol_ratio:.2f}x | RR: {rr_ratio:.2f}"
                )

                return Signal(
                    pair=pair,
                    timeframe=timeframe,
                    strategy="breakout",
                    side="sell",
                    entry_price=current_close,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    signal_score=composite_score,
                    strategy_score=strategy_score,
                    volume_score=vol_score,
                    regime_score=regime_score,
                    rr_ratio=rr_ratio,
                    notes=(
                        f"BO below {swing_low:.4f} | "
                        f"Break: {break_distance:.4f} | Vol: {vol_ratio:.2f}x"
                    ),
                )

            return None

        except Exception as e:
            self.logger.error(f"[BreakoutStrategy] Error for {pair} {timeframe}: {e}")
            return None
