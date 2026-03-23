"""
database/db_manager.py
----------------------
SQLite database manager.
Handles all persistent storage: trades, signals, settings, bot state.
Designed so tables can later be migrated to PostgreSQL with minimal changes.
"""

import sqlite3
import logging
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from config import DatabaseConfig

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Schema definitions
# ─────────────────────────────────────────────

SCHEMA = """
-- All open and closed trades
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        TEXT UNIQUE NOT NULL,
    pair            TEXT NOT NULL,
    side            TEXT NOT NULL,          -- 'buy' or 'sell'
    strategy        TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',  -- 'open', 'closed', 'cancelled'
    entry_price     REAL NOT NULL,
    exit_price      REAL,
    stop_loss       REAL NOT NULL,
    take_profit     REAL NOT NULL,
    quantity        REAL NOT NULL,
    position_value  REAL NOT NULL,
    pnl             REAL DEFAULT 0.0,
    pnl_pct         REAL DEFAULT 0.0,
    close_reason    TEXT,                   -- 'tp', 'sl', 'manual', 'closeall'
    signal_score    REAL DEFAULT 0.0,
    is_paper        INTEGER NOT NULL DEFAULT 1,
    opened_at       TEXT NOT NULL,
    closed_at       TEXT,
    notes           TEXT
);

-- All signals generated (even ones not traded)
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pair            TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    side            TEXT NOT NULL,
    signal_score    REAL NOT NULL,
    entry_price     REAL NOT NULL,
    stop_loss       REAL NOT NULL,
    take_profit     REAL NOT NULL,
    acted_on        INTEGER DEFAULT 0,      -- 1 if a trade was opened
    generated_at    TEXT NOT NULL
);

-- Daily performance summary
CREATE TABLE IF NOT EXISTS daily_summary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT UNIQUE NOT NULL,
    starting_balance REAL NOT NULL,
    ending_balance  REAL NOT NULL,
    total_trades    INTEGER DEFAULT 0,
    winning_trades  INTEGER DEFAULT 0,
    losing_trades   INTEGER DEFAULT 0,
    gross_pnl       REAL DEFAULT 0.0,
    fees_paid       REAL DEFAULT 0.0,
    net_pnl         REAL DEFAULT 0.0,
    max_drawdown    REAL DEFAULT 0.0,
    is_paper        INTEGER NOT NULL DEFAULT 1
);

-- Bot state (persists across restarts)
CREATE TABLE IF NOT EXISTS bot_state (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- User-adjustable settings
CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair);
CREATE INDEX IF NOT EXISTS idx_trades_opened_at ON trades(opened_at);
CREATE INDEX IF NOT EXISTS idx_signals_generated_at ON signals(generated_at);
"""


# ─────────────────────────────────────────────
# Database Manager
# ─────────────────────────────────────────────

class DatabaseManager:
    """
    Thread-safe SQLite database manager.
    Use as a singleton — call DatabaseManager.get_instance().
    """

    _instance: Optional["DatabaseManager"] = None

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DatabaseConfig.PATH
        self._init_db()
        logger.info(f"[DB] Database initialised at: {self.db_path}")

    @classmethod
    def get_instance(cls) -> "DatabaseManager":
        """Return singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @contextmanager
    def _conn(self):
        """Context manager providing a DB connection with auto-commit/rollback."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row  # Rows behave like dicts
        conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent access
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"[DB] Transaction rolled back: {e}")
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Create all tables if they don't exist."""
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # ─────────────────────────────────────────
    # TRADES
    # ─────────────────────────────────────────

    def save_trade(self, trade: Dict[str, Any]) -> bool:
        """Insert a new trade record. Returns True on success."""
        sql = """
            INSERT OR IGNORE INTO trades
            (trade_id, pair, side, strategy, timeframe, status,
             entry_price, stop_loss, take_profit, quantity,
             position_value, signal_score, is_paper, opened_at, notes)
            VALUES
            (:trade_id, :pair, :side, :strategy, :timeframe, :status,
             :entry_price, :stop_loss, :take_profit, :quantity,
             :position_value, :signal_score, :is_paper, :opened_at, :notes)
        """
        try:
            with self._conn() as conn:
                conn.execute(sql, {
                    "trade_id":       trade["trade_id"],
                    "pair":           trade["pair"],
                    "side":           trade["side"],
                    "strategy":       trade["strategy"],
                    "timeframe":      trade["timeframe"],
                    "status":         trade.get("status", "open"),
                    "entry_price":    trade["entry_price"],
                    "stop_loss":      trade["stop_loss"],
                    "take_profit":    trade["take_profit"],
                    "quantity":       trade["quantity"],
                    "position_value": trade["position_value"],
                    "signal_score":   trade.get("signal_score", 0.0),
                    "is_paper":       1 if trade.get("is_paper", True) else 0,
                    "opened_at":      trade.get("opened_at", self._now()),
                    "notes":          trade.get("notes", ""),
                })
            logger.info(f"[DB] Trade saved: {trade['trade_id']}")
            return True
        except Exception as e:
            logger.error(f"[DB] Failed to save trade: {e}")
            return False

    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
        close_reason: str,
    ) -> bool:
        """Mark a trade as closed with exit details."""
        sql = """
            UPDATE trades
            SET status = 'closed',
                exit_price = :exit_price,
                pnl = :pnl,
                pnl_pct = :pnl_pct,
                close_reason = :close_reason,
                closed_at = :closed_at
            WHERE trade_id = :trade_id AND status = 'open'
        """
        try:
            with self._conn() as conn:
                conn.execute(sql, {
                    "trade_id":     trade_id,
                    "exit_price":   exit_price,
                    "pnl":          pnl,
                    "pnl_pct":      pnl_pct,
                    "close_reason": close_reason,
                    "closed_at":    self._now(),
                })
            return True
        except Exception as e:
            logger.error(f"[DB] Failed to close trade {trade_id}: {e}")
            return False

    def get_open_trades(self) -> List[Dict]:
        """Return all currently open trades."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'open' ORDER BY opened_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_trade_by_id(self, trade_id: str) -> Optional[Dict]:
        """Return a single trade by its trade_id."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE trade_id = ?", (trade_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_last_n_trades(self, n: int = 10) -> List[Dict]:
        """Return the N most recently closed trades."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'closed' ORDER BY closed_at DESC LIMIT ?",
                (n,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_today_trades(self) -> List[Dict]:
        """Return all trades opened today (UTC)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE opened_at LIKE ? ORDER BY opened_at DESC",
                (f"{today}%",)
            ).fetchall()
        return [dict(r) for r in rows]

    def count_open_trades(self) -> int:
        """Count currently open trades."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM trades WHERE status = 'open'"
            ).fetchone()
        return row["cnt"] if row else 0

    def trade_exists(self, trade_id: str) -> bool:
        """Check if a trade_id already exists (dedup guard)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM trades WHERE trade_id = ?", (trade_id,)
            ).fetchone()
        return row is not None

    def get_all_closed_trades(self) -> List[Dict]:
        """Return all closed trades (for analytics/backtest)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'closed' ORDER BY closed_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ─────────────────────────────────────────
    # SIGNALS
    # ─────────────────────────────────────────

    def save_signal(self, signal: Dict[str, Any]) -> bool:
        """Log a signal to the database."""
        sql = """
            INSERT INTO signals
            (pair, timeframe, strategy, side, signal_score,
             entry_price, stop_loss, take_profit, acted_on, generated_at)
            VALUES
            (:pair, :timeframe, :strategy, :side, :signal_score,
             :entry_price, :stop_loss, :take_profit, :acted_on, :generated_at)
        """
        try:
            with self._conn() as conn:
                conn.execute(sql, {
                    "pair":          signal["pair"],
                    "timeframe":     signal["timeframe"],
                    "strategy":      signal["strategy"],
                    "side":          signal["side"],
                    "signal_score":  signal.get("signal_score", 0.0),
                    "entry_price":   signal["entry_price"],
                    "stop_loss":     signal["stop_loss"],
                    "take_profit":   signal["take_profit"],
                    "acted_on":      1 if signal.get("acted_on", False) else 0,
                    "generated_at":  signal.get("generated_at", self._now()),
                })
            return True
        except Exception as e:
            logger.error(f"[DB] Failed to save signal: {e}")
            return False

    # ─────────────────────────────────────────
    # BOT STATE (persists across restarts)
    # ─────────────────────────────────────────

    def set_state(self, key: str, value: Any):
        """Save a bot state value. Value is JSON-serialised."""
        sql = """
            INSERT OR REPLACE INTO bot_state (key, value, updated_at)
            VALUES (?, ?, ?)
        """
        with self._conn() as conn:
            conn.execute(sql, (key, json.dumps(value), self._now()))

    def get_state(self, key: str, default: Any = None) -> Any:
        """Retrieve a bot state value."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM bot_state WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    # ─────────────────────────────────────────
    # SETTINGS
    # ─────────────────────────────────────────

    def set_setting(self, key: str, value: Any):
        """Save a user setting."""
        sql = """
            INSERT OR REPLACE INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
        """
        with self._conn() as conn:
            conn.execute(sql, (key, json.dumps(value), self._now()))

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Retrieve a user setting."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def get_all_settings(self) -> Dict[str, Any]:
        """Return all settings as a dict."""
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        result = {}
        for row in rows:
            try:
                result[row["key"]] = json.loads(row["value"])
            except Exception:
                result[row["key"]] = row["value"]
        return result

    # ─────────────────────────────────────────
    # DAILY SUMMARY
    # ─────────────────────────────────────────

    def upsert_daily_summary(self, summary: Dict[str, Any]) -> bool:
        """Insert or update today's daily summary."""
        sql = """
            INSERT OR REPLACE INTO daily_summary
            (date, starting_balance, ending_balance, total_trades,
             winning_trades, losing_trades, gross_pnl, fees_paid,
             net_pnl, max_drawdown, is_paper)
            VALUES
            (:date, :starting_balance, :ending_balance, :total_trades,
             :winning_trades, :losing_trades, :gross_pnl, :fees_paid,
             :net_pnl, :max_drawdown, :is_paper)
        """
        try:
            with self._conn() as conn:
                conn.execute(sql, summary)
            return True
        except Exception as e:
            logger.error(f"[DB] Failed to upsert daily summary: {e}")
            return False

    def get_daily_summaries(self, days: int = 30) -> List[Dict]:
        """Return the last N days of daily summaries."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_summary ORDER BY date DESC LIMIT ?", (days,)
            ).fetchall()
        return [dict(r) for r in rows]
