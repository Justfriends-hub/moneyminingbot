"""
execution/trade_executor.py
----------------------------
Trade execution engine.
Routes orders to either the paper engine or live exchange,
runs signal scanning across all pairs/timeframes,
and checks open positions for SL/TP hits on each cycle.
"""

import logging
import time
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from config import TradingConfig, StrategyConfig, RiskConfig
from exchange.bybit_client import BybitClient
from exchange.paper_engine import PaperEngine
from strategies.base_strategy import Signal
from strategies.trend_following import TrendFollowingStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.breakout import BreakoutStrategy
from risk.risk_manager import RiskManager
from database.db_manager import DatabaseManager
from utils.helpers import utcnow_str, score_signal

logger = logging.getLogger(__name__)


class TradeExecutor:
    """
    Orchestrates the full trading loop:
      1. Scan all configured pairs and timeframes for signals
      2. Score and rank signals
      3. Validate via risk manager
      4. Execute via paper or live engine
      5. Monitor open positions for SL/TP
      6. Report results
    """

    def __init__(
        self,
        exchange_client: BybitClient,
        paper_engine: PaperEngine,
        risk_manager: RiskManager,
        notifier=None,   # Telegram notifier — injected to avoid circular import
    ):
        self.exchange = exchange_client
        self.paper = paper_engine
        self.risk = risk_manager
        self.db = DatabaseManager.get_instance()
        self.notifier = notifier

        # Strategy registry
        self._strategies = {
            "trend":          TrendFollowingStrategy(),
            "mean_reversion": MeanReversionStrategy(),
            "breakout":       BreakoutStrategy(),
        }

        # Runtime state
        self._trade_enabled: bool = False
        self._strategy_mode: str = StrategyConfig.DEFAULT_STRATEGY
        self._active_pairs: List[str] = StrategyConfig.DEFAULT_PAIRS
        self._active_timeframe: str = StrategyConfig.DEFAULT_TIMEFRAME

        # Track last scan time to avoid redundant scans
        self._last_scan_ts: float = 0.0
        self._scan_interval: float = 60.0   # seconds between scans

        logger.info("[Executor] TradeExecutor initialised")

    # ─────────────────────────────────────────
    # Settings control (called from Telegram)
    # ─────────────────────────────────────────

    def enable_trading(self):
        self._trade_enabled = True
        logger.info("[Executor] Auto-trading ENABLED")

    def disable_trading(self):
        self._trade_enabled = False
        logger.info("[Executor] Auto-trading DISABLED")

    def is_trading_enabled(self) -> bool:
        return self._trade_enabled

    def set_strategy(self, strategy: str):
        strategy = strategy.lower().strip()
        valid = list(self._strategies.keys()) + ["auto"]
        if strategy not in valid:
            raise ValueError(f"Unknown strategy '{strategy}'. Valid: {valid}")
        self._strategy_mode = strategy
        logger.info(f"[Executor] Strategy set to: {strategy}")

    def set_pairs(self, pairs: List[str]):
        self._active_pairs = pairs
        logger.info(f"[Executor] Active pairs: {pairs}")

    def set_timeframe(self, timeframe: str):
        self._active_timeframe = timeframe
        logger.info(f"[Executor] Timeframe set to: {timeframe}")

    def get_current_balance(self) -> float:
        if TradingConfig.is_paper():
            return self.paper.get_balance()
        return self.exchange.get_usdt_balance()

    # ─────────────────────────────────────────
    # Main trading loop (called by scheduler)
    # ─────────────────────────────────────────

    def run_cycle(self):
        """
        One full trading cycle:
          1. Check SL/TP on open positions
          2. Scan for new signals (if trading enabled)
          3. Execute best signal found
        """
        try:
            # Step 1: Check existing positions for SL/TP hits
            self._check_open_positions()

            # Step 2: Only scan for new trades if enabled
            if not self._trade_enabled:
                return

            # Step 3: Throttle scan frequency
            now = time.time()
            if now - self._last_scan_ts < self._scan_interval:
                return
            self._last_scan_ts = now

            # Step 4: Pre-trade risk check
            balance = self.get_current_balance()
            can_trade, reason = self.risk.can_trade(balance)
            if not can_trade:
                logger.info(f"[Executor] Trading blocked: {reason}")
                return

            # Step 5: Scan and get best signal
            best_signal = self._scan_for_signals()

            if best_signal is None:
                logger.debug("[Executor] No qualifying signals found this cycle")
                return

            # Step 6: Execute the trade
            self._execute_signal(best_signal)

        except Exception as e:
            logger.error(f"[Executor] Error in run_cycle: {e}", exc_info=True)

    # ─────────────────────────────────────────
    # Signal scanning
    # ─────────────────────────────────────────

    def _scan_for_signals(self) -> Optional[Signal]:
        """
        Scan all configured pairs and timeframes.
        Returns the highest-scoring valid signal, or None.
        """
        # Determine which pairs to scan
        pairs_to_scan = (
            StrategyConfig.SCAN_PAIRS
            if self._active_pairs == ["AUTO"]
            else self._active_pairs
        )

        # Determine which timeframes to scan
        timeframes = (
            StrategyConfig.ALL_TIMEFRAMES
            if self._active_timeframe == "ALL"
            else [self._active_timeframe]
        )

        # Determine which strategies to run
        if self._strategy_mode == "auto":
            strategies_to_run = list(self._strategies.values())
        else:
            strategy = self._strategies.get(self._strategy_mode)
            strategies_to_run = [strategy] if strategy else list(self._strategies.values())

        all_signals: List[Signal] = []

        for pair in pairs_to_scan:
            for timeframe in timeframes:
                # Fetch OHLCV data once per pair/timeframe combo
                df = None
                try:
                    df = self.exchange.fetch_ohlcv(pair, timeframe)
                except Exception as e:
                    logger.warning(f"[Executor] Could not fetch {pair} {timeframe}: {e}")
                    continue

                if df is None or df.empty:
                    continue

                # Run each strategy on this data
                for strategy in strategies_to_run:
                    try:
                        signal = strategy.generate_signal(df, pair, timeframe)
                        if signal is not None:
                            # Log signal to DB (acted_on=False until we actually trade)
                            self.db.save_signal({
                                **signal.to_dict(),
                                "acted_on": False,
                                "generated_at": utcnow_str(),
                            })
                            all_signals.append(signal)
                            logger.info(f"[Executor] Signal: {signal}")
                    except Exception as e:
                        logger.warning(
                            f"[Executor] Strategy error on {pair} {timeframe}: {e}"
                        )

        if not all_signals:
            return None

        # Return the highest-scoring signal
        all_signals.sort(key=lambda s: s.signal_score, reverse=True)
        best = all_signals[0]
        logger.info(
            f"[Executor] Best signal selected: {best.pair} {best.strategy} "
            f"| Score: {best.signal_score:.1f}"
        )
        return best

    # ─────────────────────────────────────────
    # Trade execution
    # ─────────────────────────────────────────

    def _execute_signal(self, signal: Signal):
        """Execute a validated signal via paper or live engine."""
        balance = self.get_current_balance()

        # Validate signal quality
        spread_pct = self.exchange.get_spread_pct(signal.pair)
        valid, reason = self.risk.validate_signal_quality(
            signal.signal_score, signal.rr_ratio, spread_pct
        )
        if not valid:
            logger.info(f"[Executor] Signal rejected by risk: {reason}")
            return

        # Calculate position size
        quantity, position_value = self.risk.calculate_position_size(
            balance=balance,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
        )

        if quantity <= 0 or position_value <= 0:
            logger.warning("[Executor] Position size calculation returned zero")
            return

        logger.info(
            f"[Executor] Executing trade: {signal.side.upper()} {signal.pair} | "
            f"Qty: {quantity:.6f} | Value: ${position_value:.2f}"
        )

        if TradingConfig.is_paper():
            trade = self.paper.open_position(
                pair=signal.pair,
                side=signal.side,
                entry_price=signal.entry_price,
                quantity=quantity,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                strategy=signal.strategy,
                timeframe=signal.timeframe,
                signal_score=signal.signal_score,
            )

            if trade and self.notifier:
                self.notifier.notify_trade_opened(trade)

        else:
            # Live execution
            order = self.exchange.place_market_order(
                pair=signal.pair,
                side=signal.side,
                quantity=quantity,
            )
            if order:
                logger.info(f"[Executor] Live order placed: {order.get('id')}")
            else:
                logger.error("[Executor] Live order failed")

    # ─────────────────────────────────────────
    # Position monitoring
    # ─────────────────────────────────────────

    def _check_open_positions(self):
        """Check all open positions for SL/TP hits."""
        if TradingConfig.is_paper():
            open_positions = self.paper.get_open_positions()
            if not open_positions:
                return

            # Get current prices for all open pairs
            current_prices: Dict[str, float] = {}
            for pos in open_positions:
                pair = pos["pair"]
                if pair not in current_prices:
                    try:
                        price = self.exchange.get_current_price(pair)
                        if price > 0:
                            current_prices[pair] = price
                    except Exception:
                        pass

            # Check SL/TP
            closed_trades = self.paper.check_sl_tp(current_prices)

            for trade in closed_trades:
                is_win = trade.get("pnl", 0) > 0
                self.risk.record_trade_result(is_win)

                if self.notifier:
                    reason = trade.get("close_reason", "")
                    if reason == "tp":
                        self.notifier.notify_take_profit(trade)
                    elif reason == "sl":
                        self.notifier.notify_stop_loss(trade)
                    else:
                        self.notifier.notify_trade_closed(trade)

    def close_all_positions(self) -> List[Dict]:
        """Emergency close all positions."""
        if TradingConfig.is_paper():
            open_positions = self.paper.get_open_positions()
            current_prices = {}
            for pos in open_positions:
                pair = pos["pair"]
                if pair not in current_prices:
                    try:
                        price = self.exchange.get_current_price(pair)
                        current_prices[pair] = price if price > 0 else pos["entry_price"]
                    except Exception:
                        current_prices[pair] = pos["entry_price"]

            closed = self.paper.close_all_positions(current_prices)
            for trade in closed:
                self.risk.record_trade_result(trade.get("pnl", 0) > 0)
            return closed
        return []

    # ─────────────────────────────────────────
    # Manual single-pair scan (for /status)
    # ─────────────────────────────────────────

    def scan_pair_now(self, pair: str, timeframe: str = "1h") -> Optional[Signal]:
        """Immediately scan a single pair on one timeframe. Used by Telegram commands."""
        try:
            df = self.exchange.fetch_ohlcv(pair, timeframe)
            if df is None or df.empty:
                return None
            best = None
            for strategy in self._strategies.values():
                signal = strategy.generate_signal(df, pair, timeframe)
                if signal and (best is None or signal.signal_score > best.signal_score):
                    best = signal
            return best
        except Exception as e:
            logger.error(f"[Executor] scan_pair_now error: {e}")
            return None
