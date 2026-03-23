"""
strategies/mean_reversion.py
-----------------------------
Mean Reversion Strategy: RSI + Bollinger Bands.

Logic:
  LONG entry (oversold bounce):
    - Price touches or breaks below lower Bollinger Band
    - RSI < 30 (oversold)
    - Previous candle closed BELOW lower band, current candle is closing ABOVE it (bounce)
    - Candle body is green (close > open) for confirmation
    - Volume above average (real buyers stepping in)

  SHORT entry (overbought fade):
    - Price touches or breaks above upper Bollinger Band
    - RSI > 70 (overbought)
    - Previous candle closed ABOVE upper band, current closing BELOW it
    - Candle body is red (close < open)
    - Volume above average

  Stop Loss: lower band - (ATR * 0.5) for long / upper band + (ATR * 0.5) for short
  Take Profit: middle Bollinger Band (mean reversion target)
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

MIN_CANDLES_REQUIRED = 50


class MeanReversionStrategy(BaseStrategy):
    """
    Mean reversion strategy using RSI extremes and Bollinger Band touches.
    Best suited to ranging/sideways markets.
    """

    def __init__(self):
        super().__init__("mean_reversion")

    def generate_signal(
        self,
        df: pd.DataFrame,
        pair: str,
        timeframe: str,
    ) -> Optional[Signal]:
        """
        Analyse OHLCV data and return a signal if mean-reversion conditions are met.
        """
        if not self._has_enough_data(df, MIN_CANDLES_REQUIRED):
            return None

        try:
            # ── Regime check ─────────────────────────────
            regime, regime_score = detect_regime(df)
            if not is_regime_suitable_for_strategy(regime, "mean_reversion"):
                self.logger.debug(
                    f"{pair} {timeframe}: Regime '{regime}' not suitable for mean reversion"
                )
                return None

            # ── Calculate indicators ──────────────────────
            close  = df["close"]
            open_  = df["open"]
            high   = df["high"]
            low    = df["low"]
            volume = df["volume"]

            # RSI
            rsi = ta.rsi(close, length=StrategyConfig.MR_RSI_PERIOD)

            # Bollinger Bands
            bb = ta.bbands(
                close,
                length=StrategyConfig.MR_BB_PERIOD,
                std=StrategyConfig.MR_BB_STD,
            )

            # ATR for stop placement
            atr = ta.atr(high, low, close, length=14)

            # Volume SMA
            vol_sma = ta.sma(volume, length=20)

            # ── Null checks ───────────────────────────────
            if any(x is None or x.empty for x in [rsi, bb, atr, vol_sma]):
                return None

            # BB column names from pandas_ta: BBL_20_2.0, BBM_20_2.0, BBU_20_2.0
            bb_lower_col = [c for c in bb.columns if c.startswith("BBL")]
            bb_mid_col   = [c for c in bb.columns if c.startswith("BBM")]
            bb_upper_col = [c for c in bb.columns if c.startswith("BBU")]

            if not all([bb_lower_col, bb_mid_col, bb_upper_col]):
                return None

            # ── Extract latest values ─────────────────────
            rsi_now  = self._safe_last(rsi)
            rsi_prev = self._safe_prev(rsi)

            bb_lower_now  = self._safe_last(bb[bb_lower_col[0]])
            bb_lower_prev = self._safe_prev(bb[bb_lower_col[0]])
            bb_mid_now    = self._safe_last(bb[bb_mid_col[0]])
            bb_upper_now  = self._safe_last(bb[bb_upper_col[0]])
            bb_upper_prev = self._safe_prev(bb[bb_upper_col[0]])

            current_atr   = self._safe_last(atr)
            current_close = self._safe_last(close)
            current_open  = self._safe_last(open_)
            prev_close    = self._safe_prev(close)
            prev_open     = self._safe_prev(open_)
            current_vol   = self._safe_last(volume)
            avg_vol       = self._safe_last(vol_sma)

            if any(v <= 0 for v in [current_atr, current_close, bb_lower_now, bb_upper_now]):
                return None

            # ── Volume score ──────────────────────────────
            vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0.0
            vol_score = min(100.0, vol_ratio * 70.0)

            # ── LONG signal: oversold bounce ──────────────
            prev_candle_below_lower = prev_close < bb_lower_prev
            curr_candle_above_lower = current_close >= bb_lower_now
            rsi_oversold = rsi_now < StrategyConfig.MR_RSI_OVERSOLD
            rsi_recovering = rsi_now > rsi_prev
            green_candle = current_close > current_open
            volume_ok = vol_ratio >= 1.0

            if (prev_candle_below_lower and curr_candle_above_lower
                    and rsi_oversold and rsi_recovering and green_candle and volume_ok):

                # SL: just below lower band
                stop_loss   = bb_lower_now - (current_atr * 0.5)
                take_profit = bb_mid_now     # Target: mean (middle band)

                rr_distance = current_close - stop_loss
                tp_distance = take_profit - current_close

                if rr_distance <= 0 or tp_distance <= 0:
                    return None

                rr_ratio = tp_distance / rr_distance

                if rr_ratio < RiskConfig.MIN_REWARD_RISK_RATIO:
                    self.logger.debug(
                        f"{pair} {timeframe}: LONG MR rejected — RR {rr_ratio:.2f} < min"
                    )
                    return None

                conditions_met = sum([
                    prev_candle_below_lower,
                    curr_candle_above_lower,
                    rsi_oversold,
                    rsi_recovering,
                    green_candle,
                    volume_ok,
                ])
                strategy_score = (conditions_met / 6) * 100.0
                composite_score = score_signal(strategy_score, vol_score, regime_score, rr_ratio)

                self.logger.info(
                    f"✅ MR LONG signal: {pair} {timeframe} | "
                    f"Score: {composite_score:.1f} | RSI: {rsi_now:.1f} | RR: {rr_ratio:.2f}"
                )

                return Signal(
                    pair=pair,
                    timeframe=timeframe,
                    strategy="mean_reversion",
                    side="buy",
                    entry_price=current_close,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    signal_score=composite_score,
                    strategy_score=strategy_score,
                    volume_score=vol_score,
                    regime_score=regime_score,
                    rr_ratio=rr_ratio,
                    notes=f"BB bounce | RSI: {rsi_now:.1f} | Vol: {vol_ratio:.2f}x",
                )

            # ── SHORT signal: overbought fade ─────────────
            prev_candle_above_upper = prev_close > bb_upper_prev
            curr_candle_below_upper = current_close <= bb_upper_now
            rsi_overbought = rsi_now > StrategyConfig.MR_RSI_OVERBOUGHT
            rsi_falling = rsi_now < rsi_prev
            red_candle = current_close < current_open

            if (prev_candle_above_upper and curr_candle_below_upper
                    and rsi_overbought and rsi_falling and red_candle and volume_ok):

                stop_loss   = bb_upper_now + (current_atr * 0.5)
                take_profit = bb_mid_now    # Target: mean

                rr_distance = stop_loss - current_close
                tp_distance = current_close - take_profit

                if rr_distance <= 0 or tp_distance <= 0:
                    return None

                rr_ratio = tp_distance / rr_distance

                if rr_ratio < RiskConfig.MIN_REWARD_RISK_RATIO:
                    return None

                conditions_met = sum([
                    prev_candle_above_upper,
                    curr_candle_below_upper,
                    rsi_overbought,
                    rsi_falling,
                    red_candle,
                    volume_ok,
                ])
                strategy_score = (conditions_met / 6) * 100.0
                composite_score = score_signal(strategy_score, vol_score, regime_score, rr_ratio)

                self.logger.info(
                    f"✅ MR SHORT signal: {pair} {timeframe} | "
                    f"Score: {composite_score:.1f} | RSI: {rsi_now:.1f} | RR: {rr_ratio:.2f}"
                )

                return Signal(
                    pair=pair,
                    timeframe=timeframe,
                    strategy="mean_reversion",
                    side="sell",
                    entry_price=current_close,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    signal_score=composite_score,
                    strategy_score=strategy_score,
                    volume_score=vol_score,
                    regime_score=regime_score,
                    rr_ratio=rr_ratio,
                    notes=f"BB rejection | RSI: {rsi_now:.1f} | Vol: {vol_ratio:.2f}x",
                )

            return None

        except Exception as e:
            self.logger.error(f"[MRStrategy] Error for {pair} {timeframe}: {e}")
            return None
