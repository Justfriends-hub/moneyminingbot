"""
telegram_bot/bot_handler.py
----------------------------
All Telegram bot command handlers.
Every command is restricted to authorised user IDs only.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import TelegramConfig, TradingConfig, RiskConfig, StrategyConfig
from database.db_manager import DatabaseManager
from analytics.performance import compute_performance, format_performance_message
from utils.helpers import (
    fmt_usd, fmt_pct, utcnow_str, is_valid_pair,
    is_valid_timeframe, format_trade_message, clamp
)

logger = logging.getLogger(__name__)


def restricted(func):
    """Decorator: reject commands from unauthorised users."""
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if TelegramConfig.ALLOWED_USER_IDS and user_id not in TelegramConfig.ALLOWED_USER_IDS:
            logger.warning(f"[Auth] Rejected command from user {user_id}")
            await update.message.reply_text("Unauthorised. Access denied.")
            return
        return await func(self, update, context)
    wrapper.__name__ = func.__name__
    return wrapper


class BotHandler:
    """Registers and handles all Telegram commands."""

    def __init__(self, executor, paper_engine, notifier):
        self.executor = executor
        self.paper = paper_engine
        self.notifier = notifier
        self.db = DatabaseManager.get_instance()
        self._app: Optional[Application] = None

    def build_app(self, token: str) -> Application:
        """Build and configure the Telegram Application."""
        self._app = Application.builder().token(token).build()
        self.notifier.set_bot(self._app.bot)

        commands = [
            ("start",          self.cmd_start),
            ("help",           self.cmd_help),
            ("status",         self.cmd_status),
            ("balance",        self.cmd_balance),
            ("positions",      self.cmd_positions),
            ("open_orders",    self.cmd_open_orders),
            ("trade_on",       self.cmd_trade_on),
            ("trade_off",      self.cmd_trade_off),
            ("paper_on",       self.cmd_paper_on),
            ("paper_off",      self.cmd_paper_off),
            ("setpair",        self.cmd_setpair),
            ("setrisk",        self.cmd_setrisk),
            ("settimeframe",   self.cmd_settimeframe),
            ("setstrategy",    self.cmd_setstrategy),
            ("setbalance",     self.cmd_setbalance),
            ("performance",    self.cmd_performance),
            ("lasttrades",     self.cmd_lasttrades),
            ("backtest",       self.cmd_backtest),
            ("closeall",       self.cmd_closeall),
            ("dailysummary",   self.cmd_dailysummary),
        ]

        for name, handler in commands:
            self._app.add_handler(CommandHandler(name, handler))

        self._app.add_handler(MessageHandler(filters.COMMAND, self.cmd_unknown))
        return self._app

    async def set_commands(self):
        """Register command list in Telegram menu."""
        if not self._app:
            return
        await self._app.bot.set_my_commands([
            BotCommand("start",        "Start the bot"),
            BotCommand("help",         "Show all commands"),
            BotCommand("status",       "Bot status overview"),
            BotCommand("balance",      "Show current balance"),
            BotCommand("positions",    "Show open positions"),
            BotCommand("open_orders",  "Show pending orders"),
            BotCommand("trade_on",     "Enable auto-trading"),
            BotCommand("trade_off",    "Disable auto-trading"),
            BotCommand("paper_on",     "Switch to paper mode"),
            BotCommand("paper_off",    "Switch to live mode"),
            BotCommand("setpair",      "Set trading pair"),
            BotCommand("setrisk",      "Set risk per trade pct"),
            BotCommand("settimeframe", "Set candle timeframe"),
            BotCommand("setstrategy",  "Set trading strategy"),
            BotCommand("setbalance",   "Set paper balance 5-1000"),
            BotCommand("performance",  "Full performance report"),
            BotCommand("lasttrades",   "Last 10 trades"),
            BotCommand("backtest",     "Run a backtest"),
            BotCommand("closeall",     "Emergency close all"),
            BotCommand("dailysummary", "Today P&L summary"),
        ])

    # ── /start ────────────────────────────────

    @restricted
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        mode = "Paper" if TradingConfig.is_paper() else "LIVE"
        balance = self.paper.get_balance() if TradingConfig.is_paper() else self.executor.get_current_balance()
        trading_state = "ON" if self.executor.is_trading_enabled() else "OFF"
        await update.message.reply_text(
            f"Crypto Trading Bot Active\n\n"
            f"Mode:         {mode}\n"
            f"Balance:      {fmt_usd(balance)}\n"
            f"Auto-trading: {trading_state}\n"
            f"Exchange:     Bybit\n\n"
            f"Use /help to see all commands.\n"
            f"Use /trade_on to start auto-trading.\n\n"
            f"Default is paper trading. No real money at risk."
        )

    # ── /help ────────────────────────────────

    @restricted
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Command Reference\n\n"
            "INFO\n"
            "/start         - Welcome screen\n"
            "/status        - Full bot status\n"
            "/balance       - Current balance\n"
            "/positions     - Open positions\n"
            "/open_orders   - Pending orders\n"
            "/lasttrades    - Last 10 closed trades\n"
            "/performance   - Full analytics report\n"
            "/dailysummary  - Today P&L\n\n"
            "CONTROL\n"
            "/trade_on      - Enable auto-trading\n"
            "/trade_off     - Disable auto-trading\n"
            "/paper_on      - Switch to paper mode\n"
            "/paper_off     - Switch to LIVE mode\n"
            "/closeall      - Emergency close all\n\n"
            "SETTINGS\n"
            "/setpair BTC/USDT  - Set pair or AUTO\n"
            "/setrisk 0.5       - Set risk pct 0.1 to 2.0\n"
            "/settimeframe 1h   - Set timeframe\n"
            "/setstrategy auto  - Set strategy\n"
            "/setbalance 500    - Set paper balance\n"
            "/backtest          - Run backtest\n\n"
            "Strategies: trend | mean_reversion | breakout | auto\n"
            "Timeframes: 15m | 1h | 4h | all"
        )

    # ── /status ───────────────────────────────

    @restricted
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        mode = "Paper" if TradingConfig.is_paper() else "LIVE"
        balance = self.paper.get_balance() if TradingConfig.is_paper() else self.executor.get_current_balance()
        open_count = self.db.count_open_trades()
        daily_pnl_pct = self.paper.get_daily_pnl_pct() if TradingConfig.is_paper() else 0.0
        cooldown_secs = self.executor.risk.get_cooldown_remaining_seconds()
        consecutive = self.executor.risk.get_consecutive_losses()
        strategy = self.executor._strategy_mode
        timeframe = self.executor._active_timeframe
        pairs = self.executor._active_pairs
        pairs_str = "AUTO" if pairs == ["AUTO"] else ", ".join(pairs)
        trading_state = "ON" if self.executor.is_trading_enabled() else "OFF"

        msg = (
            f"Bot Status\n\n"
            f"Mode:            {mode}\n"
            f"Auto-trading:    {trading_state}\n"
            f"Balance:         {fmt_usd(balance)}\n"
            f"Open positions:  {open_count}/{RiskConfig.MAX_CONCURRENT_TRADES}\n"
            f"Daily P&L:       {fmt_pct(daily_pnl_pct)}\n"
            f"Strategy:        {strategy.upper()}\n"
            f"Timeframe:       {timeframe.upper()}\n"
            f"Pairs:           {pairs_str}\n"
            f"Risk/trade:      {RiskConfig.RISK_PER_TRADE_PCT}%\n"
            f"Consec. losses:  {consecutive}\n"
        )

        if cooldown_secs > 0:
            msg += f"Cooldown:        {cooldown_secs}s remaining\n"

        await update.message.reply_text(msg)

    # ── /balance ─────────────────────────────

    @restricted
    async def cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if TradingConfig.is_paper():
            balance = self.paper.get_balance()
            daily_pnl_pct = self.paper.get_daily_pnl_pct()
            await update.message.reply_text(
                f"Paper Balance\n\n"
                f"Balance:    {fmt_usd(balance)}\n"
                f"Daily P&L:  {fmt_pct(daily_pnl_pct)}\n"
                f"Mode:       Paper (no real money)"
            )
        else:
            balance = self.executor.get_current_balance()
            await update.message.reply_text(
                f"Live Balance\n\n"
                f"USDT Free:  {fmt_usd(balance)}\n"
                f"Exchange:   Bybit"
            )

    # ── /positions ───────────────────────────

    @restricted
    async def cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        positions = self.paper.get_open_positions() if TradingConfig.is_paper() else []

        if not positions:
            await update.message.reply_text("No open positions currently.")
            return

        lines = [f"Open Positions ({len(positions)})\n"]
        for pos in positions:
            entry = pos.get("entry_price", 0)
            sl = pos.get("stop_loss", 0)
            tp = pos.get("take_profit", 0)
            val = pos.get("position_value", 0)
            side_str = "BUY" if pos.get("side") == "buy" else "SELL"
            lines.append(
                f"{pos.get('pair')} {side_str}\n"
                f"  Strategy: {pos.get('strategy')} | TF: {pos.get('timeframe')}\n"
                f"  Entry: {entry:.4f} | SL: {sl:.4f} | TP: {tp:.4f}\n"
                f"  Value: {fmt_usd(val)} | Score: {pos.get('signal_score', 0):.0f}/100\n"
            )

        await update.message.reply_text("\n".join(lines))

    # ── /open_orders ─────────────────────────

    @restricted
    async def cmd_open_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if TradingConfig.is_paper():
            await update.message.reply_text(
                "Paper mode: positions fill instantly.\n"
                "Use /positions to see open trades."
            )
            return

        orders = self.executor.exchange.fetch_open_orders()
        if not orders:
            await update.message.reply_text("No open orders on Bybit.")
        else:
            lines = [f"Open Orders ({len(orders)})\n"]
            for o in orders[:10]:
                lines.append(
                    f"- {o.get('symbol')} {o.get('side','').upper()} "
                    f"@ {fmt_usd(o.get('price', 0))} | {o.get('amount')} units"
                )
            await update.message.reply_text("\n".join(lines))

    # ── /trade_on ────────────────────────────

    @restricted
    async def cmd_trade_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.executor.enable_trading()
        mode = "Paper" if TradingConfig.is_paper() else "LIVE"
        await update.message.reply_text(
            f"Auto-trading ENABLED\n"
            f"Mode: {mode}\n"
            f"The bot will now scan for signals and place trades automatically."
        )

    # ── /trade_off ───────────────────────────

    @restricted
    async def cmd_trade_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.executor.disable_trading()
        await update.message.reply_text(
            "Auto-trading DISABLED\n"
            "No new trades will be opened. Existing positions remain open."
        )

    # ── /paper_on ────────────────────────────

    @restricted
    async def cmd_paper_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TradingConfig.MODE = "paper"
        self.executor.disable_trading()
        await update.message.reply_text(
            "Switched to Paper Mode\n"
            "All trades are now simulated. No real money at risk.\n"
            "Use /trade_on to resume auto-trading in paper mode."
        )

    # ── /paper_off ───────────────────────────

    @restricted
    async def cmd_paper_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Switching to LIVE Mode\n\n"
            "This will use REAL money on Bybit.\n\n"
            "Steps to go live:\n"
            "1. Set BYBIT_API_KEY and BYBIT_API_SECRET in your .env file\n"
            "2. Set BYBIT_TESTNET=false in .env\n"
            "3. Set TRADING_MODE=live in .env\n"
            "4. Restart the bot\n\n"
            "Trading involves substantial risk of loss. "
            "Only trade with money you can afford to lose."
        )

    # ── /setpair ─────────────────────────────

    @restricted
    async def cmd_setpair(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args

        if not args:
            await update.message.reply_text(
                "Usage: /setpair BTC/USDT\n"
                "Or: /setpair AUTO  (bot picks best opportunity)\n"
                "Or: /setpair BTC/USDT ETH/USDT SOL/USDT"
            )
            return

        if args[0].upper() == "AUTO":
            self.executor.set_pairs(["AUTO"])
            await update.message.reply_text("Pairs set to AUTO. Bot will scan all pairs.")
            return

        valid_pairs = []
        invalid = []
        for arg in args:
            pair = arg.upper().replace("-", "/")
            if is_valid_pair(pair):
                valid_pairs.append(pair)
            else:
                invalid.append(arg)

        if invalid:
            await update.message.reply_text(
                f"Invalid pairs: {', '.join(invalid)}\n"
                f"Format must be: BTC/USDT"
            )
            return

        self.executor.set_pairs(valid_pairs)
        await update.message.reply_text(f"Trading pairs set to: {', '.join(valid_pairs)}")

    # ── /setrisk ─────────────────────────────

    @restricted
    async def cmd_setrisk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args

        if not args:
            await update.message.reply_text(
                f"Usage: /setrisk 0.5\n"
                f"Range: 0.1 to 2.0 (percentage of balance per trade)\n"
                f"Current: {RiskConfig.RISK_PER_TRADE_PCT}%"
            )
            return

        try:
            new_risk = float(args[0])
        except ValueError:
            await update.message.reply_text("Invalid value. Example: /setrisk 0.5")
            return

        new_risk = clamp(new_risk, 0.1, 2.0)
        RiskConfig.RISK_PER_TRADE_PCT = new_risk
        self.db.set_setting("risk_per_trade_pct", new_risk)

        await update.message.reply_text(
            f"Risk per trade set to: {new_risk}%\n"
            f"At current balance {fmt_usd(self.paper.get_balance())}, "
            f"max risk per trade = {fmt_usd(self.paper.get_balance() * new_risk / 100)}"
        )

    # ── /settimeframe ────────────────────────

    @restricted
    async def cmd_settimeframe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args

        if not args:
            await update.message.reply_text(
                f"Usage: /settimeframe 1h\n"
                f"Options: 15m | 1h | 4h | all\n"
                f"Current: {self.executor._active_timeframe}"
            )
            return

        tf = args[0].lower()

        if tf == "all":
            self.executor.set_timeframe("ALL")
            await update.message.reply_text("Timeframe set to ALL. Bot will scan 15m, 1h, and 4h.")
            return

        if not is_valid_timeframe(tf):
            await update.message.reply_text(
                f"Invalid timeframe: {tf}\n"
                "Valid options: 15m, 1h, 4h, all"
            )
            return

        self.executor.set_timeframe(tf)
        self.db.set_setting("timeframe", tf)
        await update.message.reply_text(f"Timeframe set to: {tf}")

    # ── /setstrategy ─────────────────────────

    @restricted
    async def cmd_setstrategy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args

        if not args:
            await update.message.reply_text(
                f"Usage: /setstrategy auto\n"
                f"Options: trend | mean_reversion | breakout | auto\n"
                f"Current: {self.executor._strategy_mode}"
            )
            return

        strategy = args[0].lower()
        valid = ["trend", "mean_reversion", "breakout", "auto"]

        if strategy not in valid:
            await update.message.reply_text(
                f"Invalid strategy: {strategy}\n"
                f"Valid options: {' | '.join(valid)}"
            )
            return

        self.executor.set_strategy(strategy)
        self.db.set_setting("strategy", strategy)

        desc = {
            "trend":          "EMA crossover + MACD. Best in trending markets.",
            "mean_reversion": "RSI + Bollinger Bands. Best in ranging markets.",
            "breakout":       "ATR + Volume. Catches breakouts from key levels.",
            "auto":           "Scans all strategies. Picks highest-scoring signal.",
        }

        await update.message.reply_text(
            f"Strategy set to: {strategy}\n{desc.get(strategy, '')}"
        )

    # ── /setbalance ──────────────────────────

    @restricted
    async def cmd_setbalance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args

        if not args:
            await update.message.reply_text(
                f"Usage: /setbalance 500\n"
                f"Range: $5 to $1000\n"
                f"Current: {fmt_usd(self.paper.get_balance())}"
            )
            return

        if not TradingConfig.is_paper():
            await update.message.reply_text(
                "/setbalance only works in paper mode.\n"
                "Switch with /paper_on first."
            )
            return

        try:
            new_balance = float(args[0].replace("$", "").replace(",", ""))
        except ValueError:
            await update.message.reply_text("Invalid amount. Example: /setbalance 200")
            return

        new_balance = clamp(new_balance, 5.0, 1000.0)
        self.paper.set_balance(new_balance)
        await update.message.reply_text(f"Paper balance reset to: {fmt_usd(new_balance)}")

    # ── /performance ─────────────────────────

    @restricted
    async def cmd_performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        trades = self.db.get_all_closed_trades()
        metrics = compute_performance(trades)
        msg = format_performance_message(metrics)
        await update.message.reply_text(msg, parse_mode="HTML")

    # ── /lasttrades ──────────────────────────

    @restricted
    async def cmd_lasttrades(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        trades = self.db.get_last_n_trades(10)

        if not trades:
            await update.message.reply_text("No closed trades yet.")
            return

        lines = [f"Last {len(trades)} Trades\n"]
        for t in trades:
            pnl = t.get("pnl", 0)
            result = "WIN" if pnl > 0 else "LOSS"
            side_str = t.get("side", "").upper()
            lines.append(
                f"{result} | {t.get('pair')} {side_str} [{t.get('strategy')}]\n"
                f"  PnL: {fmt_usd(pnl)} ({t.get('pnl_pct', 0):+.2f}%) | "
                f"{t.get('close_reason','').upper()} | {str(t.get('closed_at',''))[:16]}\n"
            )

        await update.message.reply_text("\n".join(lines))

    # ── /backtest ────────────────────────────

    @restricted
    async def cmd_backtest(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Usage: /backtest [pair] [timeframe] [strategy]
        Examples:
          /backtest
          /backtest BTC/USDT 1h trend
          /backtest ETH/USDT 4h breakout
        """
        args = context.args
        pair = "BTC/USDT"
        timeframe = "1h"
        strategy = "trend"

        if len(args) >= 1:
            pair = args[0].upper().replace("-", "/")
        if len(args) >= 2:
            timeframe = args[1].lower()
        if len(args) >= 3:
            strategy = args[2].lower()

        valid_strategies = ["trend", "mean_reversion", "breakout"]
        if strategy not in valid_strategies:
            await update.message.reply_text(
                f"Invalid strategy: {strategy}\n"
                f"Valid: {' | '.join(valid_strategies)}"
            )
            return

        if not is_valid_timeframe(timeframe):
            await update.message.reply_text(f"Invalid timeframe: {timeframe}")
            return

        await update.message.reply_text(
            f"Running backtest...\n"
            f"Pair: {pair} | TF: {timeframe} | Strategy: {strategy}\n"
            f"This may take up to 30 seconds..."
        )

        try:
            from backtesting.backtester import Backtester

            df = self.executor.exchange.fetch_ohlcv(pair, timeframe, limit=500)

            if df is None or df.empty:
                await update.message.reply_text(
                    f"Could not fetch data for {pair} {timeframe}"
                )
                return

            bt = Backtester(starting_balance=self.paper.get_balance())
            result = bt.run(df, pair, timeframe, strategy)
            msg = bt.format_for_telegram(result)
            await update.message.reply_text(msg, parse_mode="HTML")

        except Exception as e:
            logger.error(f"[Backtest] Error: {e}", exc_info=True)
            await update.message.reply_text(f"Backtest error: {str(e)[:200]}")

    # ── /closeall ────────────────────────────

    @restricted
    async def cmd_closeall(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        open_count = self.db.count_open_trades()

        if open_count == 0:
            await update.message.reply_text("No open positions to close.")
            return

        await update.message.reply_text(f"Closing all {open_count} open position(s)...")
        self.executor.disable_trading()
        closed = self.executor.close_all_positions()
        total_pnl = sum(t.get("pnl", 0) for t in closed)

        await update.message.reply_text(
            f"All Positions Closed\n\n"
            f"Closed: {len(closed)} position(s)\n"
            f"Total P&L: {fmt_usd(total_pnl)}\n"
            f"Auto-trading: OFF\n\n"
            f"Use /trade_on to re-enable trading."
        )

    # ── /dailysummary ────────────────────────

    @restricted
    async def cmd_dailysummary(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        trades = self.db.get_today_trades()
        closed_today = [t for t in trades if t.get("status") == "closed"]
        open_today   = [t for t in trades if t.get("status") == "open"]
        total_pnl = sum(t.get("pnl", 0) for t in closed_today)
        wins = sum(1 for t in closed_today if t.get("pnl", 0) > 0)
        losses = len(closed_today) - wins
        balance = self.paper.get_balance() if TradingConfig.is_paper() else self.executor.get_current_balance()
        daily_pnl_pct = self.paper.get_daily_pnl_pct() if TradingConfig.is_paper() else 0.0

        await update.message.reply_text(
            f"Daily Summary - {today}\n\n"
            f"Closed today:  {len(closed_today)}\n"
            f"Still open:    {len(open_today)}\n"
            f"Wins / Losses: {wins} / {losses}\n"
            f"Net P&L:       {fmt_usd(total_pnl)} ({fmt_pct(daily_pnl_pct)})\n"
            f"Balance:       {fmt_usd(balance)}\n\n"
            f"Past performance does not guarantee future results."
        )

    # ── Unknown command ───────────────────────

    async def cmd_unknown(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if TelegramConfig.ALLOWED_USER_IDS and user_id not in TelegramConfig.ALLOWED_USER_IDS:
            return
        await update.message.reply_text("Unknown command. Use /help to see all available commands.")
