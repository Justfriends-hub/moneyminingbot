"""
main.py
-------
Entry point for the crypto trading bot.
Wires all modules together, starts the scheduler, and launches the Telegram bot.

Run with:
    python main.py
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import (
    setup_logging, validate_config,
    TelegramConfig, TradingConfig, RiskConfig
)
from database.db_manager import DatabaseManager
from exchange.bybit_client import BybitClient
from exchange.paper_engine import PaperEngine
from risk.risk_manager import RiskManager
from execution.trade_executor import TradeExecutor
from telegram_bot.notifier import Notifier
from telegram_bot.bot_handler import BotHandler
from utils.helpers import utcnow_str, fmt_usd

setup_logging()
logger = logging.getLogger(__name__)


async def main():
    """Main async entry point."""

    logger.info("=" * 55)
    logger.info("  CRYPTO TRADING BOT - STARTING UP")
    logger.info(f"  Time: {utcnow_str()}")
    logger.info("=" * 55)

    # Config validation
    warnings = validate_config()
    for w in warnings:
        logger.warning(f"[Config] {w}")

    if not TelegramConfig.BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN is not set. Cannot start bot.")
        sys.exit(1)

    if TradingConfig.is_paper():
        logger.info(f"[Mode] PAPER TRADING | Balance: {fmt_usd(TradingConfig.PAPER_BALANCE)}")
    else:
        logger.warning("[Mode] LIVE TRADING - Real money at risk!")

    # Initialise core components
    logger.info("[Init] Initialising database...")
    db = DatabaseManager.get_instance()

    logger.info("[Init] Initialising exchange client (Bybit)...")
    exchange = BybitClient()

    try:
        exchange.load_markets()
    except Exception as e:
        logger.warning(f"[Init] Could not load markets: {e}")

    logger.info("[Init] Initialising paper engine...")
    paper = PaperEngine(starting_balance=TradingConfig.PAPER_BALANCE)

    logger.info("[Init] Initialising risk manager...")
    risk = RiskManager()

    logger.info("[Init] Initialising notifier...")
    notifier = Notifier()

    logger.info("[Init] Initialising trade executor...")
    executor = TradeExecutor(
        exchange_client=exchange,
        paper_engine=paper,
        risk_manager=risk,
        notifier=notifier,
    )

    _restore_settings(executor, db)

    # Build Telegram bot
    logger.info("[Init] Building Telegram bot...")
    handler = BotHandler(executor=executor, paper_engine=paper, notifier=notifier)
    app = handler.build_app(TelegramConfig.BOT_TOKEN)

    # Scheduler
    logger.info("[Init] Setting up scheduler...")
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        func=executor.run_cycle,
        trigger="interval",
        seconds=60,
        id="trading_cycle",
        max_instances=1,
        misfire_grace_time=30,
    )

    scheduler.add_job(
        func=_daily_reset,
        args=[paper, notifier, db, executor],
        trigger="cron",
        hour=0,
        minute=0,
        id="daily_reset",
    )

    scheduler.add_job(
        func=_send_daily_summary,
        args=[paper, notifier, db],
        trigger="cron",
        hour=23,
        minute=55,
        id="daily_summary",
    )

    logger.info("[Start] Starting scheduler...")
    scheduler.start()

    async with app:
        await app.initialize()

        try:
            await handler.set_commands()
            logger.info("[Start] Telegram command menu registered")
        except Exception as e:
            logger.warning(f"[Start] Could not register commands: {e}")

        balance = paper.get_balance() if TradingConfig.is_paper() else exchange.get_usdt_balance()
        notifier.set_bot(app.bot)

        try:
            await notifier._send_async(
                f"Bot Started\n\n"
                f"Mode:     {'Paper' if TradingConfig.is_paper() else 'LIVE'}\n"
                f"Balance:  {fmt_usd(balance)}\n"
                f"Time:     {utcnow_str()}\n\n"
                f"Auto-trading is OFF. Use /trade_on to start."
            )
        except Exception as e:
            logger.warning(f"[Start] Startup notification failed: {e}")

        logger.info("[Start] Bot is running. Press Ctrl+C to stop.")
        logger.info("[Start] Send /start in Telegram to begin.")

        await app.start()
        await app.updater.start_polling(
            allowed_updates=["message"],
            drop_pending_updates=True,
        )

        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            logger.info("[Stop] Shutdown signal received")
        finally:
            logger.info("[Stop] Shutting down...")
            scheduler.shutdown(wait=False)
            try:
                await notifier._send_async("Bot Stopped. All systems offline.")
            except Exception:
                pass
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            logger.info("[Stop] Bot stopped cleanly.")


def _daily_reset(paper: PaperEngine, notifier: Notifier, db: DatabaseManager, executor):
    """Called at midnight UTC. Resets daily tracking."""
    logger.info("[Scheduler] Daily reset triggered")
    paper.reset_day_start_balance()
    executor.risk._consecutive_losses = 0
    executor.risk._cooldown_until = None
    executor.risk._save_daily_state()
    logger.info(f"[Scheduler] New day started. Balance: {fmt_usd(paper.get_balance())}")


def _send_daily_summary(paper: PaperEngine, notifier: Notifier, db: DatabaseManager):
    """Called at 23:55 UTC. Sends daily summary."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trades = db.get_today_trades()
    closed = [t for t in trades if t.get("status") == "closed"]
    total_pnl = sum(t.get("pnl", 0) for t in closed)
    wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
    balance = paper.get_balance()

    notifier.notify_daily_summary({
        "date":           today,
        "total_trades":   len(closed),
        "winning_trades": wins,
        "net_pnl":        total_pnl,
        "ending_balance": balance,
    })

    db.upsert_daily_summary({
        "date":              today,
        "starting_balance":  paper.day_start_balance,
        "ending_balance":    balance,
        "total_trades":      len(closed),
        "winning_trades":    wins,
        "losing_trades":     len(closed) - wins,
        "gross_pnl":         sum(t.get("pnl", 0) for t in closed if t.get("pnl", 0) > 0),
        "fees_paid":         0.0,
        "net_pnl":           total_pnl,
        "max_drawdown":      0.0,
        "is_paper":          1 if TradingConfig.is_paper() else 0,
    })


def _restore_settings(executor, db: DatabaseManager):
    """Restore user settings from DB after restart."""
    strategy = db.get_setting("strategy")
    if strategy:
        try:
            executor.set_strategy(strategy)
            logger.info(f"[Restore] Strategy restored: {strategy}")
        except Exception:
            pass

    timeframe = db.get_setting("timeframe")
    if timeframe:
        executor.set_timeframe(timeframe)
        logger.info(f"[Restore] Timeframe restored: {timeframe}")

    risk_pct = db.get_setting("risk_per_trade_pct")
    if risk_pct:
        RiskConfig.RISK_PER_TRADE_PCT = float(risk_pct)
        logger.info(f"[Restore] Risk restored: {risk_pct}%")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot interrupted by user.")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
