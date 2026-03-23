"""
telegram_bot/notifier.py
-------------------------
Telegram push notification sender.
Sends async notifications for trade events, errors, and daily summaries.
"""

import logging
import asyncio
from typing import Optional, List, Dict, Any

from config import TelegramConfig
from utils.helpers import fmt_usd, fmt_pct, pnl_emoji, trade_emoji, format_trade_message

logger = logging.getLogger(__name__)


class Notifier:
    """
    Sends push notifications to authorised Telegram users.
    Uses the Bot object from python-telegram-bot.
    Must be initialised with the running Bot instance.
    """

    def __init__(self, bot=None):
        self._bot = bot
        self._allowed_ids: List[int] = TelegramConfig.ALLOWED_USER_IDS

    def set_bot(self, bot):
        """Set the Telegram Bot instance (called after bot initialises)."""
        self._bot = bot

    def _send(self, text: str):
        """
        Fire-and-forget notification to all authorised users.
        Runs in background without blocking the trading loop.
        """
        if not self._bot:
            logger.debug(f"[Notifier] Bot not set — skipping notification: {text[:60]}")
            return

        for user_id in self._allowed_ids:
            try:
                # Schedule async send without blocking
                asyncio.create_task(
                    self._bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode="HTML",
                    )
                )
            except Exception as e:
                logger.warning(f"[Notifier] Failed to send to {user_id}: {e}")

    async def _send_async(self, text: str):
        """Async version for use within async handlers."""
        if not self._bot:
            return
        for user_id in self._allowed_ids:
            try:
                await self._bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(f"[Notifier] Failed to send to {user_id}: {e}")

    # ─────────────────────────────────────────
    # Event notifications
    # ─────────────────────────────────────────

    def notify_bot_started(self, mode: str, balance: float):
        self._send(
            f"🤖 <b>Bot Started</b>\n"
            f"Mode: {'📝 Paper' if mode == 'paper' else '💰 LIVE'}\n"
            f"Balance: {fmt_usd(balance)}\n"
            f"Auto-trading: OFF (use /trade_on to enable)"
        )

    def notify_bot_stopped(self):
        self._send("🛑 <b>Bot Stopped</b>\nAll systems offline.")

    def notify_trade_opened(self, trade: Dict[str, Any]):
        side_emoji = trade_emoji(trade.get("side", "buy"))
        self._send(
            f"{side_emoji} <b>Trade Opened</b>\n"
            f"{format_trade_message(trade)}"
        )

    def notify_trade_closed(self, trade: Dict[str, Any]):
        pnl = trade.get("pnl", 0)
        emoji = pnl_emoji(pnl)
        self._send(
            f"{emoji} <b>Trade Closed</b>\n"
            f"{format_trade_message(trade)}"
        )

    def notify_take_profit(self, trade: Dict[str, Any]):
        pnl = trade.get("pnl", 0)
        pnl_pct = trade.get("pnl_pct", 0)
        self._send(
            f"🎯 <b>Take Profit Hit!</b>\n"
            f"Pair: {trade.get('pair')}\n"
            f"P&L: +{fmt_usd(pnl)} ({fmt_pct(pnl_pct)})\n"
            f"Exit: {trade.get('exit_price', 0):.4f}"
        )

    def notify_stop_loss(self, trade: Dict[str, Any]):
        pnl = trade.get("pnl", 0)
        pnl_pct = trade.get("pnl_pct", 0)
        self._send(
            f"🛑 <b>Stop Loss Hit</b>\n"
            f"Pair: {trade.get('pair')}\n"
            f"P&L: {fmt_usd(pnl)} ({fmt_pct(pnl_pct)})\n"
            f"Exit: {trade.get('exit_price', 0):.4f}"
        )

    def notify_daily_summary(self, summary: Dict[str, Any]):
        pnl = summary.get("net_pnl", 0)
        trades = summary.get("total_trades", 0)
        wins = summary.get("winning_trades", 0)
        balance = summary.get("ending_balance", 0)
        emoji = "📈" if pnl >= 0 else "📉"
        self._send(
            f"{emoji} <b>Daily Summary</b>\n"
            f"Date:     {summary.get('date', 'Today')}\n"
            f"Trades:   {trades} ({wins} wins)\n"
            f"Net P&L:  {fmt_usd(pnl)}\n"
            f"Balance:  {fmt_usd(balance)}"
        )

    def notify_daily_limit_hit(self, pnl_pct: float, balance: float):
        self._send(
            f"🚨 <b>Daily Loss Limit Hit!</b>\n"
            f"Today's P&L: {fmt_pct(pnl_pct)}\n"
            f"Current balance: {fmt_usd(balance)}\n"
            f"Trading is PAUSED for the rest of the day.\n"
            f"Use /status to check state."
        )

    def notify_error(self, error_msg: str):
        self._send(f"⚠️ <b>Bot Error</b>\n<code>{error_msg[:200]}</code>")

    def notify_cooldown_triggered(self, losses: int, minutes: int):
        self._send(
            f"🕐 <b>Cooldown Triggered</b>\n"
            f"After {losses} consecutive losses.\n"
            f"Trading paused for {minutes} minutes."
        )
