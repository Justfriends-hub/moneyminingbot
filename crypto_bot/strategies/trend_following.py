"""
strategies/trend_following.py
------------------------------
Trend Following Strategy: EMA crossover + MACD confirmation + volume filter.

Logic:
  LONG entry:
    - EMA9 > EMA21 > EMA50 (bullish alignment)
    - Previous candle: EMA9 crossed above EMA21 (fresh cross)
    - MACD line > Signal line (momentum confirms)
    - MACD histogram turning positive
    - Volume > 1.2x average volume (not a fake breakout)

  SHORT entry (if applicable — currently disabled for spot, only for futures):
    - EMA9 < EMA21 < EMA50 (bearish alignment)
    - EMA9 crossed below EMA21
    - MACD line < Signal line
    - Volume > 1.2x average

  Stop Loss: entry price - (ATR * 1.5)
  Take Profit: entry price + (ATR * 2.5)  → 1.67:1 minimum RR
"""

import logging
import pandas as pd
import pandas_ta as ta
from typing import Optional

from strategies.base_strategy import BaseStrategy, Signal
from strategies.regime_filter import detect_regime, is_regime_suitable_for_strategy, REGIME_TRENDING
from config import StrategyConfig, RiskConfig
from utils.helpers import score_signal

logger = logging.getLogger(__name__)

MIN_CANDLES_REQUIRED = 60  # Need enough history for EMA50


class TrendFollowingStrategy(BaseStrategy):
    """
    Trend following strategy using EMA crossovers and MACD confirmation.
    Best suited to trending markets (detected by regime filter).
    """

    def __init__(self):
        super().__init__("trend")

    def generate_signal(
        self,
        df: pd.DataFrame,
        pair: str,
        timeframe: str,
    ) -> Optional[Signal]:
        """
        Analyse OHLCV data and return a long/short signal if trend conditions are met.
        """
        if not self._has_enough_data(df, MIN_CANDLES_REQUIRED):
            return None

        try:
            # ── Regime check ─────────────────────────────
            regime, regime_score = detect_regime(df)
            if not is_regime_suitable_for_strategy(regime, "trend"):
                self.logger.debug(
                    f"{pair} {timeframe}: Regime '{regime}' not suitable for trend strategy"
                )
                return None

            # ── Calculate indicators ──────────────────────
            close = df["close"]
            high = df["high"]
            low = df["low"]
            volume = df["volume"]

            # EMAs
            ema_fast = ta.ema(close, length=StrategyConfig.TREND_EMA_FAST)
            ema_mid  = ta.ema(close, length=StrategyConfig.TREND_EMA_MID)
            ema_slow = ta.ema(close, length=StrategyConfig.TREND_EMA_SLOW)

            # MACD
            macd_result = ta.macd(
                close,
                fast=StrategyConfig.TREND_MACD_FAST,
                slow=StrategyConfig.TREND_MACD_SLOW,
                signal=StrategyConfig.TREND_MACD_SIGNAL,
            )

            # ATR for stop loss / take profit
            atr = ta.atr(high, low, close, length=14)

            # Volume average (20-period SMA of volume)
            vol_sma = ta.sma(volume, length=20)

            # ── Null checks ───────────────────────────────
            if any(x is None or x.empty for x in [ema_fast, ema_mid, ema_slow, atr, vol_sma]):
                return None
            if macd_result is None or macd_result.empty:
                return None

            # ── Extract latest values ─────────────────────
            ema9_now  = self._safe_last(ema_fast)
            ema21_now = self._safe_last(ema_mid)
            ema50_now = self._safe_last(ema_slow)

            ema9_prev  = self._safe_prev(ema_fast)
            ema21_prev = self._safe_prev(ema_mid)

            # MACD columns: MACD_12_26_9, MACDh_12_26_9, MACDs_12_26_9
            macd_cols = {c.split("_")[0]: c for c in macd_result.columns}
            macd_line   = self._safe_last(macd_result[macd_cols.get("MACD", macd_result.columns[0])])
            macd_signal = self._safe_last(macd_result[macd_cols.get("MACDs", macd_result.columns[2])])
            macd_hist   = self._safe_last(macd_result[macd_cols.get("MACDh", macd_result.columns[1])])
            macd_hist_prev = self._safe_prev(macd_result[macd_cols.get("MACDh", macd_result.columns[1])])

            current_atr  = self._safe_last(atr)
            current_vol  = self._safe_last(volume)
            avg_vol      = self._safe_last(vol_sma)
            current_price = self._safe_last(close)

            if any(v <= 0 for v in [ema9_now, ema21_now, ema50_now, current_atr, current_price]):
                return None

            # ── Volume score ──────────────────────────────
            vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0.0
            vol_score = min(100.0, (vol_ratio / StrategyConfig.TREND_VOLUME_MULTIPLIER) * 80.0)

            # ── LONG signal conditions ────────────────────
            bullish_ema_stack = ema9_now > ema21_now > ema50_now
            ema_cross_up = (ema9_prev <= ema21_prev) and (ema9_now > ema21_now)
            macd_bullish = macd_line > macd_signal
            macd_hist_rising = macd_hist > macd_hist_prev
            volume_ok = vol_ratio >= StrategyConfig.TREND_VOLUME_MULTIPLIER

            if bullish_ema_stack and ema_cross_up and macd_bullish and macd_hist_rising and volume_ok:
                # Calculate SL and TP
                stop_loss   = current_price - (current_atr * RiskConfig.ATR_SL_MULTIPLIER)
                take_profit = current_price + (current_atr * RiskConfig.ATR_TP_MULTIPLIER)

                rr_distance = current_price - stop_loss
                tp_distance = take_profit - current_price
                rr_ratio = tp_distance / rr_distance if rr_distance > 0 else 0.0

                if rr_ratio < RiskConfig.MIN_REWARD_RISK_RATIO:
                    self.logger.debug(
                        f"{pair} {timeframe}: LONG rejected — RR {rr_ratio:.2f} < min {RiskConfig.MIN_REWARD_RISK_RATIO}"
                    )
                    return None

                # Strategy score based on how many conditions aligned
                conditions_met = sum([
                    bullish_ema_stack,
                    ema_cross_up,
                    macd_bullish,
                    macd_hist_rising,
                    volume_ok,
                ])
                strategy_score = (conditions_met / 5) * 100.0

                composite_score = score_signal(strategy_score, vol_score, regime_score, rr_ratio)

                self.logger.info(
                    f"✅ TREND LONG signal: {pair} {timeframe} | "
                    f"Score: {composite_score:.1f} | RR: {rr_ratio:.2f} | "
                    f"Entry: {current_price:.4f} | SL: {stop_loss:.4f} | TP: {take_profit:.4f}"
                )

                return Signal(
                    pair=pair,
                    timeframe=timeframe,
                    strategy="trend",
                    side="buy",
                    entry_price=current_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    signal_score=composite_score,
                    strategy_score=strategy_score,
                    volume_score=vol_score,
                    regime_score=regime_score,
                    rr_ratio=rr_ratio,
                    notes=f"EMA cross | MACD confirm | Vol ratio: {vol_ratio:.2f}x",
                )

            # ── SHORT signal conditions (futures / margin only) ───
            # Disabled for basic spot trading. Uncomment if using futures.
            # bearish_ema_stack = ema9_now < ema21_now < ema50_now
            # ema_cross_down = (ema9_prev >= ema21_prev) and (ema9_now < ema21_now)
            # macd_bearish = macd_line < macd_signal
            # macd_hist_falling = macd_hist < macd_hist_prev
            # if bearish_ema_stack and ema_cross_down and macd_bearish and macd_hist_falling and volume_ok:
            #     ...

            return None

        except Exception as e:
            self.logger.error(f"[TrendStrategy] Error generating signal for {pair} {timeframe}: {e}")
            return None
