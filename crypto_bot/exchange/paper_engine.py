"""
exchange/paper_engine.py
------------------------
Paper trading simulation engine.
Mirrors live trading logic exactly — same position sizing, same SL/TP checks —
but uses virtual balance and simulated fills at market price.
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any

from config import TradingConfig, RiskConfig
from database.db_manager import DatabaseManager
from utils.helpers import utcnow_str, fmt_usd

logger = logging.getLogger(__name__)


class PaperEngine:
    """
    Simulated trading engine.
    Manages a virtual cash balance and open position tracking.
    All fills are assumed to be at the provided price (market fill simulation).
    Fees and slippage are applied per the RiskConfig settings.
    """

    def __init__(self, starting_balance: float = None):
        self.db = DatabaseManager.get_instance()

        # Load balance from DB if available (persists across restarts)
        saved_balance = self.db.get_state("paper_balance")
        if saved_balance is not None:
            self.balance = float(saved_balance)
            logger.info(f"[Paper] Restored balance from DB: {fmt_usd(self.balance)}")
        else:
            # Clamp starting balance to allowed range
            raw = starting_balance or TradingConfig.PAPER_BALANCE
            self.balance = max(5.0, min(1000.0, raw))
            self.db.set_state("paper_balance", self.balance)
            logger.info(f"[Paper] New paper session started. Balance: {fmt_usd(self.balance)}")

        # Track starting balance for today's drawdown calculation
        self.day_start_balance: float = self.db.get_state("paper_day_start_balance") or self.balance

        # In-memory open positions: {trade_id: position_dict}
        self._open_positions: Dict[str, Dict] = {}

        # Restore open positions from DB
        self._restore_open_positions()

    # ─────────────────────────────────────────
    # Balance management
    # ─────────────────────────────────────────

    def get_balance(self) -> float:
        """Return current virtual USDT balance."""
        return round(self.balance, 4)

    def set_balance(self, new_balance: float):
        """Manually reset paper balance (used by /setbalance command)."""
        new_balance = max(5.0, min(1000.0, new_balance))
        self.balance = new_balance
        self.day_start_balance = new_balance
        self.db.set_state("paper_balance", self.balance)
        self.db.set_state("paper_day_start_balance", self.day_start_balance)
        logger.info(f"[Paper] Balance manually reset to {fmt_usd(self.balance)}")

    def _save_balance(self):
        """Persist current balance to DB."""
        self.db.set_state("paper_balance", self.balance)

    # ─────────────────────────────────────────
    # Position management
    # ─────────────────────────────────────────

    def _restore_open_positions(self):
        """Re-load open trades from DB on startup."""
        open_trades = self.db.get_open_trades()
        for trade in open_trades:
            if trade.get("is_paper"):
                self._open_positions[trade["trade_id"]] = trade
        if self._open_positions:
            logger.info(f"[Paper] Restored {len(self._open_positions)} open position(s) from DB")

    def get_open_positions(self) -> List[Dict]:
        """Return list of all open paper positions."""
        return list(self._open_positions.values())

    def count_open_positions(self) -> int:
        """Return number of currently open positions."""
        return len(self._open_positions)

    # ─────────────────────────────────────────
    # Open a trade
    # ─────────────────────────────────────────

    def open_position(
        self,
        pair: str,
        side: str,
        entry_price: float,
        quantity: float,
        stop_loss: float,
        take_profit: float,
        strategy: str,
        timeframe: str,
        signal_score: float = 0.0,
    ) -> Optional[Dict]:
        """
        Open a new paper trade.

        Args:
            pair:         Trading pair (e.g. 'BTC/USDT').
            side:         'buy' or 'sell'.
            entry_price:  Simulated fill price.
            quantity:     Base asset quantity.
            stop_loss:    Stop loss price.
            take_profit:  Take profit price.
            strategy:     Strategy name.
            timeframe:    Candle timeframe used.
            signal_score: Signal quality score (0–100).

        Returns:
            Trade dict on success, None if insufficient balance.
        """
        # Apply fee + slippage to entry price
        total_cost_pct = (RiskConfig.FEE_PCT + RiskConfig.SLIPPAGE_PCT) / 100.0
        if side == "buy":
            effective_entry = entry_price * (1 + total_cost_pct)
        else:
            effective_entry = entry_price * (1 - total_cost_pct)

        position_value = effective_entry * quantity

        # Check if we have enough balance
        if position_value > self.balance:
            logger.warning(
                f"[Paper] Insufficient balance {fmt_usd(self.balance)} "
                f"for trade of {fmt_usd(position_value)}"
            )
            return None

        # Generate unique trade ID
        trade_id = f"PAPER-{pair.replace('/', '')}-{uuid.uuid4().hex[:8].upper()}"

        trade = {
            "trade_id":       trade_id,
            "pair":           pair,
            "side":           side,
            "strategy":       strategy,
            "timeframe":      timeframe,
            "status":         "open",
            "entry_price":    effective_entry,
            "stop_loss":      stop_loss,
            "take_profit":    take_profit,
            "quantity":       quantity,
            "position_value": position_value,
            "signal_score":   signal_score,
            "is_paper":       True,
            "opened_at":      utcnow_str(),
            "notes":          f"Paper trade | Score: {signal_score:.1f}",
        }

        # Deduct cost from balance (reserved while trade is open)
        self.balance -= position_value
        self._save_balance()

        # Save to memory + DB
        self._open_positions[trade_id] = trade
        self.db.save_trade(trade)

        logger.info(
            f"[Paper] Opened {side.upper()} {pair} | "
            f"Entry: {effective_entry:.4f} | Qty: {quantity:.6f} | "
            f"Value: {fmt_usd(position_value)} | SL: {stop_loss:.4f} | TP: {take_profit:.4f}"
        )

        return trade

    # ─────────────────────────────────────────
    # Close a trade
    # ─────────────────────────────────────────

    def close_position(
        self,
        trade_id: str,
        exit_price: float,
        close_reason: str = "manual",
    ) -> Optional[Dict]:
        """
        Close an open paper position.

        Args:
            trade_id:     Trade ID to close.
            exit_price:   Current market price for simulated fill.
            close_reason: 'tp', 'sl', 'manual', 'closeall', 'daily_limit'.

        Returns:
            Updated trade dict with P&L, or None if not found.
        """
        trade = self._open_positions.get(trade_id)
        if not trade:
            logger.warning(f"[Paper] Tried to close unknown trade: {trade_id}")
            return None

        # Apply fee + slippage to exit price
        total_cost_pct = (RiskConfig.FEE_PCT + RiskConfig.SLIPPAGE_PCT) / 100.0
        if trade["side"] == "buy":
            effective_exit = exit_price * (1 - total_cost_pct)
        else:
            effective_exit = exit_price * (1 + total_cost_pct)

        quantity = trade["quantity"]
        entry_price = trade["entry_price"]
        position_value = trade["position_value"]

        # Calculate gross P&L
        if trade["side"] == "buy":
            gross_pnl = (effective_exit - entry_price) * quantity
        else:
            gross_pnl = (entry_price - effective_exit) * quantity

        pnl_pct = (gross_pnl / position_value) * 100.0

        # Return proceeds to balance
        proceeds = position_value + gross_pnl
        self.balance += proceeds
        self._save_balance()

        # Update trade record
        trade["status"] = "closed"
        trade["exit_price"] = effective_exit
        trade["pnl"] = round(gross_pnl, 4)
        trade["pnl_pct"] = round(pnl_pct, 4)
        trade["close_reason"] = close_reason
        trade["closed_at"] = utcnow_str()

        # Remove from open positions
        del self._open_positions[trade_id]

        # Update DB
        self.db.close_trade(
            trade_id=trade_id,
            exit_price=effective_exit,
            pnl=trade["pnl"],
            pnl_pct=trade["pnl_pct"],
            close_reason=close_reason,
        )

        logger.info(
            f"[Paper] Closed {trade['side'].upper()} {trade['pair']} | "
            f"Exit: {effective_exit:.4f} | PnL: {fmt_usd(gross_pnl)} ({pnl_pct:+.2f}%) | "
            f"Reason: {close_reason} | Balance: {fmt_usd(self.balance)}"
        )

        return trade

    # ─────────────────────────────────────────
    # SL/TP check (called on every price update)
    # ─────────────────────────────────────────

    def check_sl_tp(self, current_prices: Dict[str, float]) -> List[Dict]:
        """
        Check all open positions for SL/TP hits.

        Args:
            current_prices: {pair: current_price} for all open pairs.

        Returns:
            List of closed trade dicts (those that hit SL or TP).
        """
        closed_trades = []

        for trade_id, trade in list(self._open_positions.items()):
            pair = trade["pair"]
            price = current_prices.get(pair)

            if price is None or price <= 0:
                continue

            side = trade["side"]
            sl = trade["stop_loss"]
            tp = trade["take_profit"]

            hit_sl = (side == "buy" and price <= sl) or (side == "sell" and price >= sl)
            hit_tp = (side == "buy" and price >= tp) or (side == "sell" and price <= tp)

            if hit_tp:
                closed = self.close_position(trade_id, tp, close_reason="tp")
                if closed:
                    closed_trades.append(closed)
            elif hit_sl:
                closed = self.close_position(trade_id, sl, close_reason="sl")
                if closed:
                    closed_trades.append(closed)

        return closed_trades

    def close_all_positions(self, current_prices: Dict[str, float]) -> List[Dict]:
        """Emergency close all open positions at current market prices."""
        closed = []
        for trade_id in list(self._open_positions.keys()):
            pair = self._open_positions[trade_id]["pair"]
            price = current_prices.get(pair, self._open_positions[trade_id]["entry_price"])
            result = self.close_position(trade_id, price, close_reason="closeall")
            if result:
                closed.append(result)
        return closed

    # ─────────────────────────────────────────
    # Daily loss check
    # ─────────────────────────────────────────

    def get_daily_pnl_pct(self) -> float:
        """Return today's P&L as % of day-start balance."""
        if self.day_start_balance <= 0:
            return 0.0
        daily_pnl = self.balance - self.day_start_balance
        return (daily_pnl / self.day_start_balance) * 100.0

    def is_daily_loss_limit_hit(self) -> bool:
        """True if today's losses exceed the max daily loss % in config."""
        pnl_pct = self.get_daily_pnl_pct()
        return pnl_pct <= -abs(RiskConfig.MAX_DAILY_LOSS_PCT)

    def reset_day_start_balance(self):
        """Called at the start of each new trading day."""
        self.day_start_balance = self.balance
        self.db.set_state("paper_day_start_balance", self.day_start_balance)
