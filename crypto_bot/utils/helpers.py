"""
utils/helpers.py
----------------
Shared utility functions used across modules.
"""

import time
import logging
import functools
from datetime import datetime, timezone
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Time helpers
# ─────────────────────────────────────────────

def utcnow() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def utcnow_str() -> str:
    """Return current UTC time as readable string."""
    return utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def ts_to_dt(timestamp_ms: int) -> datetime:
    """Convert millisecond UNIX timestamp to UTC datetime."""
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def dt_to_ts(dt: datetime) -> int:
    """Convert datetime to millisecond UNIX timestamp."""
    return int(dt.timestamp() * 1000)


# ─────────────────────────────────────────────
# Number formatting helpers
# ─────────────────────────────────────────────

def fmt_price(price: float, decimals: int = 4) -> str:
    """Format a price with given decimal places."""
    return f"{price:.{decimals}f}"


def fmt_pct(value: float, decimals: int = 2) -> str:
    """Format a float as a percentage string."""
    return f"{value:+.{decimals}f}%"


def fmt_usd(value: float) -> str:
    """Format a value as a USD amount."""
    return f"${value:,.2f}"


def pct_change(old: float, new: float) -> float:
    """Calculate percentage change from old to new."""
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100.0


def round_to_tick(value: float, tick_size: float) -> float:
    """Round a price/quantity to the nearest tick size."""
    if tick_size <= 0:
        return value
    return round(round(value / tick_size) * tick_size, 10)


# ─────────────────────────────────────────────
# Retry decorator
# ─────────────────────────────────────────────

def retry(max_attempts: int = 3, delay: float = 2.0, exceptions=(Exception,)):
    """
    Decorator that retries a function on failure.

    Args:
        max_attempts: Maximum number of attempts.
        delay: Seconds to wait between attempts.
        exceptions: Tuple of exception types to catch.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_error = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_error = e
                    if attempt < max_attempts:
                        logger.warning(
                            f"[retry] {func.__name__} failed (attempt {attempt}/{max_attempts}): "
                            f"{e}. Retrying in {delay}s..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"[retry] {func.__name__} failed after {max_attempts} attempts: {e}"
                        )
            raise last_error
        return wrapper
    return decorator


# ─────────────────────────────────────────────
# Validation helpers
# ─────────────────────────────────────────────

def is_valid_pair(pair: str) -> bool:
    """Check if a trading pair string looks valid (e.g. BTC/USDT)."""
    if not pair or "/" not in pair:
        return False
    parts = pair.split("/")
    return len(parts) == 2 and all(p.strip().isalpha() for p in parts)


def is_valid_timeframe(tf: str) -> bool:
    """Check if a timeframe string is one of the supported values."""
    valid = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
    return tf in valid


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp value between min and max."""
    return max(min_val, min(max_val, value))


# ─────────────────────────────────────────────
# Signal scoring helper
# ─────────────────────────────────────────────

def score_signal(
    strategy_score: float,
    volume_score: float,
    regime_score: float,
    rr_ratio: float,
) -> float:
    """
    Composite signal quality score (0–100).
    Used to rank multiple signals when bot is in AUTO mode.

    Weights:
        strategy_score  → 40% (indicator alignment)
        volume_score    → 25% (volume confirmation)
        regime_score    → 20% (market regime suitability)
        rr_ratio        → 15% (reward:risk quality)

    Args:
        strategy_score: 0–100, how well indicators align.
        volume_score:   0–100, volume strength vs average.
        regime_score:   0–100, how suitable the market regime is.
        rr_ratio:       Reward-to-risk ratio (e.g. 2.0).

    Returns:
        Float 0–100.
    """
    rr_score = clamp((rr_ratio / 3.0) * 100.0, 0.0, 100.0)

    composite = (
        strategy_score * 0.40
        + volume_score * 0.25
        + regime_score * 0.20
        + rr_score * 0.15
    )
    return round(clamp(composite, 0.0, 100.0), 2)


# ─────────────────────────────────────────────
# Telegram message formatting
# ─────────────────────────────────────────────

def trade_emoji(side: str) -> str:
    """Return emoji for trade side."""
    return "🟢" if side.lower() == "buy" else "🔴"


def pnl_emoji(pnl: float) -> str:
    """Return emoji based on P&L sign."""
    if pnl > 0:
        return "✅"
    elif pnl < 0:
        return "❌"
    return "➖"


def format_trade_message(trade: dict) -> str:
    """Format a trade dict into a readable Telegram message."""
    side_emoji = trade_emoji(trade.get("side", "buy"))
    pnl = trade.get("pnl", None)
    pnl_str = ""
    if pnl is not None:
        pnl_str = f"\nP&L: {pnl_emoji(pnl)} {fmt_usd(pnl)} ({fmt_pct(trade.get('pnl_pct', 0))})"

    return (
        f"{side_emoji} <b>{trade.get('side', '').upper()} {trade.get('pair', '')}</b>\n"
        f"Strategy: {trade.get('strategy', 'N/A')}\n"
        f"Timeframe: {trade.get('timeframe', 'N/A')}\n"
        f"Entry: {fmt_price(trade.get('entry_price', 0))}\n"
        f"Stop Loss: {fmt_price(trade.get('stop_loss', 0))}\n"
        f"Take Profit: {fmt_price(trade.get('take_profit', 0))}\n"
        f"Size: {fmt_usd(trade.get('position_value', 0))}\n"
        f"Signal Score: {trade.get('signal_score', 0):.1f}/100"
        f"{pnl_str}"
    )
