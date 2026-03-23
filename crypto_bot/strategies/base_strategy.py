"""
strategies/base_strategy.py
---------------------------
Abstract base class for all trading strategies.
Every strategy must inherit from BaseStrategy and implement generate_signal().
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import pandas as pd

logger = logging.getLogger(__name__)


class Signal:
    """
    Represents a trading signal produced by a strategy.
    """

    def __init__(
        self,
        pair: str,
        timeframe: str,
        strategy: str,
        side: str,                  # 'buy' or 'sell'
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        signal_score: float,        # 0–100 quality score
        strategy_score: float,      # indicator alignment score
        volume_score: float,        # volume confirmation score
        regime_score: float,        # market regime suitability
        rr_ratio: float,            # reward:risk ratio
        notes: str = "",
    ):
        self.pair = pair
        self.timeframe = timeframe
        self.strategy = strategy
        self.side = side
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.signal_score = signal_score
        self.strategy_score = strategy_score
        self.volume_score = volume_score
        self.regime_score = regime_score
        self.rr_ratio = rr_ratio
        self.notes = notes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pair":           self.pair,
            "timeframe":      self.timeframe,
            "strategy":       self.strategy,
            "side":           self.side,
            "entry_price":    self.entry_price,
            "stop_loss":      self.stop_loss,
            "take_profit":    self.take_profit,
            "signal_score":   self.signal_score,
            "strategy_score": self.strategy_score,
            "volume_score":   self.volume_score,
            "regime_score":   self.regime_score,
            "rr_ratio":       self.rr_ratio,
            "notes":          self.notes,
        }

    def __repr__(self):
        return (
            f"Signal({self.side.upper()} {self.pair} | "
            f"Strategy: {self.strategy} | TF: {self.timeframe} | "
            f"Score: {self.signal_score:.1f} | RR: {self.rr_ratio:.2f})"
        )


class BaseStrategy(ABC):
    """
    Abstract base for all strategies.
    Subclasses must implement:
        - generate_signal(df, pair, timeframe) → Optional[Signal]
    """

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"strategy.{name}")

    @abstractmethod
    def generate_signal(
        self,
        df: pd.DataFrame,
        pair: str,
        timeframe: str,
    ) -> Optional[Signal]:
        """
        Analyse OHLCV data and return a Signal if conditions are met.

        Args:
            df:        DataFrame with columns: open, high, low, close, volume.
                       Index is datetime (UTC).
            pair:      Trading pair (e.g. 'BTC/USDT').
            timeframe: Candle timeframe (e.g. '1h').

        Returns:
            Signal object if a valid setup is found, else None.
        """
        pass

    def _has_enough_data(self, df: pd.DataFrame, min_rows: int) -> bool:
        """Check if DataFrame has enough rows to calculate indicators."""
        if df is None or len(df) < min_rows:
            self.logger.debug(
                f"Not enough data: {len(df) if df is not None else 0} rows, need {min_rows}"
            )
            return False
        return True

    def _safe_last(self, series: pd.Series) -> float:
        """Safely get the last value of a Series."""
        try:
            val = series.iloc[-1]
            return float(val) if pd.notna(val) else 0.0
        except Exception:
            return 0.0

    def _safe_prev(self, series: pd.Series, offset: int = 1) -> float:
        """Safely get a previous value of a Series."""
        try:
            val = series.iloc[-(1 + offset)]
            return float(val) if pd.notna(val) else 0.0
        except Exception:
            return 0.0
