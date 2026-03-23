"""
risk/risk_manager.py
---------------------
Risk management engine.
Handles position sizing, trade validation, daily loss tracking,
consecutive loss cooldown, and pre-trade safety checks.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict

from config import RiskConfig, TradingConfig
from database.db_manager import DatabaseManager
from utils.helpers import utcnow_str, fmt_usd, fmt_pct

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Central risk management controller.
    All trade requests must be validated here before execution.
    """

    def __init__(self):
        self.db = DatabaseManager.get_instance()

        # Consecutive loss tracking (in memory — resets on restart)
        self._consecutive_losses: int = 0
        self._cooldown_until: Optional[float] = None   # Unix timestamp

        # Load today's starting state
        self._load_daily_state()

        logger.info(
            f"[Risk] Manager initialised | "
            f"Risk/trade: {RiskConfig.RISK_PER_TRADE_PCT}% | "
            f"Max daily loss: {RiskConfig.MAX_DAILY_LOSS_PCT}% | "
            f"Max concurrent: {RiskConfig.MAX_CONCURRENT_TRADES}"
        )

    def _load_daily_state(self):
        """Restore daily state from DB (handles bot restarts)."""
        self._consecutive_losses = self.db.get_state("consecutive_losses") or 0
        cooldown = self.db.get_state("cooldown_until")
        self._cooldown_until = float(cooldown) if cooldown else None

    def _save_daily_state(self):
        """Persist daily state to DB."""
        self.db.set_state("consecutive_losses", self._consecutive_losses)
        self.db.set_state("cooldown_until", self._cooldown_until)

    # ─────────────────────────────────────────
    # Position sizing
    # ─────────────────────────────────────────

    def calculate_position_size(
        self,
        balance: float,
        entry_price: float,
        stop_loss: float,
    ) -> Tuple[float, float]:
        """
        Calculate the quantity and position value for a trade.

        Uses fixed fractional risk sizing:
            risk_amount = balance * risk_pct
            quantity = risk_amount / (entry_price - stop_loss)

        Args:
            balance:     Available account balance (USDT).
            entry_price: Expected entry price.
            stop_loss:   Stop loss price.

        Returns:
            (quantity, position_value_usdt)
            Returns (0, 0) if sizing is invalid.
        """
        if balance <= 0 or entry_price <= 0 or stop_loss <= 0:
            return 0.0, 0.0

        stop_distance = abs(entry_price - stop_loss)
        if stop_distance <= 0:
            logger.warning("[Risk] Stop distance is zero — cannot size position")
            return 0.0, 0.0

        # Dollar amount we are willing to risk on this trade
        risk_amount = balance * (RiskConfig.RISK_PER_TRADE_PCT / 100.0)

        # Number of units: how many units × stop distance = risk amount
        quantity = risk_amount / stop_distance

        # Position value in USDT
        position_value = quantity * entry_price

        # Safety: never risk more than 10% of balance in a single trade
        max_position_value = balance * 0.10
        if position_value > max_position_value:
            quantity = max_position_value / entry_price
            position_value = max_position_value
            logger.debug(
                f"[Risk] Position capped at 10% of balance: {fmt_usd(position_value)}"
            )

        # Safety: position value must be at least $1
        if position_value < 1.0:
            logger.debug(f"[Risk] Position value too small: {fmt_usd(position_value)}")
            return 0.0, 0.0

        return round(quantity, 8), round(position_value, 4)

    # ─────────────────────────────────────────
    # Pre-trade validation
    # ─────────────────────────────────────────

    def can_trade(self, balance: float, pair: str = "") -> Tuple[bool, str]:
        """
        Run all pre-trade safety checks.

        Returns:
            (allowed: bool, reason: str)
            If allowed=False, reason explains why trading is blocked.
        """
        # 1. Check cooldown
        if self._cooldown_until and time.time() < self._cooldown_until:
            remaining = int(self._cooldown_until - time.time())
            return False, f"🕐 Cooldown active — {remaining}s remaining after consecutive losses"

        # 2. Check max concurrent trades
        open_count = self.db.count_open_trades()
        if open_count >= RiskConfig.MAX_CONCURRENT_TRADES:
            return False, f"🚫 Max concurrent trades reached ({open_count}/{RiskConfig.MAX_CONCURRENT_TRADES})"

        # 3. Check daily loss limit
        today_pnl_pct = self._get_today_pnl_pct(balance)
        if today_pnl_pct <= -abs(RiskConfig.MAX_DAILY_LOSS_PCT):
            return False, (
                f"🛑 Daily loss limit hit: {today_pnl_pct:+.2f}% "
                f"(limit: -{RiskConfig.MAX_DAILY_LOSS_PCT}%) — trading paused for today"
            )

        # 4. Check minimum balance
        if balance < 5.0:
            return False, f"💸 Balance too low: {fmt_usd(balance)} (minimum $5)"

        return True, "OK"

    def _get_today_pnl_pct(self, current_balance: float) -> float:
        """Calculate today's P&L percentage vs day-start balance."""
        day_start = self.db.get_state("paper_day_start_balance")
        if not day_start or day_start <= 0:
            return 0.0
        return ((current_balance - day_start) / day_start) * 100.0

    # ─────────────────────────────────────────
    # Signal quality validation
    # ─────────────────────────────────────────

    def validate_signal_quality(
        self,
        signal_score: float,
        rr_ratio: float,
        spread_pct: float,
        min_score: float = 40.0,
    ) -> Tuple[bool, str]:
        """
        Validate the quality of a signal before acting on it.

        Args:
            signal_score: Composite quality score (0–100).
            rr_ratio:     Reward-to-risk ratio.
            spread_pct:   Current bid-ask spread %.
            min_score:    Minimum score to accept (default 40/100).

        Returns:
            (valid: bool, reason: str)
        """
        if signal_score < min_score:
            return False, f"Signal score too low: {signal_score:.1f} < {min_score}"

        if rr_ratio < RiskConfig.MIN_REWARD_RISK_RATIO:
            return False, (
                f"RR ratio too low: {rr_ratio:.2f} < "
                f"{RiskConfig.MIN_REWARD_RISK_RATIO}"
            )

        if spread_pct > 0 and spread_pct > 0.3:
            return False, f"Spread too wide: {spread_pct:.3f}%"

        return True, "OK"

    # ─────────────────────────────────────────
    # Loss tracking
    # ─────────────────────────────────────────

    def record_trade_result(self, is_win: bool):
        """
        Update consecutive loss counter and trigger cooldown if needed.
        Call this after every trade closes.

        Args:
            is_win: True if the trade was profitable.
        """
        if is_win:
            self._consecutive_losses = 0
            self._cooldown_until = None
            logger.debug("[Risk] Consecutive losses reset (win recorded)")
        else:
            self._consecutive_losses += 1
            logger.info(
                f"[Risk] Consecutive losses: {self._consecutive_losses}"
            )

            if self._consecutive_losses >= RiskConfig.CONSECUTIVE_LOSS_TRIGGER:
                cooldown_secs = RiskConfig.LOSS_COOLDOWN_MINUTES * 60
                self._cooldown_until = time.time() + cooldown_secs
                logger.warning(
                    f"[Risk] 🕐 Cooldown triggered: {RiskConfig.LOSS_COOLDOWN_MINUTES} min "
                    f"after {self._consecutive_losses} consecutive losses"
                )

        self._save_daily_state()

    def get_consecutive_losses(self) -> int:
        return self._consecutive_losses

    def is_in_cooldown(self) -> bool:
        return self._cooldown_until is not None and time.time() < self._cooldown_until

    def get_cooldown_remaining_seconds(self) -> int:
        if not self.is_in_cooldown():
            return 0
        return max(0, int(self._cooldown_until - time.time()))

    # ─────────────────────────────────────────
    # Status report
    # ─────────────────────────────────────────

    def get_status_report(self, current_balance: float) -> str:
        """Return a formatted risk status string for Telegram."""
        open_count = self.db.count_open_trades()
        today_pnl_pct = self._get_today_pnl_pct(current_balance)
        cooldown_remaining = self.get_cooldown_remaining_seconds()

        lines = [
            "⚖️ <b>Risk Status</b>",
            f"Risk/trade: {RiskConfig.RISK_PER_TRADE_PCT}%",
            f"Open trades: {open_count}/{RiskConfig.MAX_CONCURRENT_TRADES}",
            f"Today's P&L: {today_pnl_pct:+.2f}% (limit: -{RiskConfig.MAX_DAILY_LOSS_PCT}%)",
            f"Consecutive losses: {self._consecutive_losses}",
        ]

        if cooldown_remaining > 0:
            lines.append(f"🕐 Cooldown: {cooldown_remaining}s remaining")

        if today_pnl_pct <= -abs(RiskConfig.MAX_DAILY_LOSS_PCT):
            lines.append("🛑 DAILY LIMIT HIT — trading paused")

        return "\n".join(lines)
