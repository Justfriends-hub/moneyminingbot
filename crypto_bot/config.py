"""
config.py
---------
Central configuration module.
Loads all settings from environment variables (.env file).
All other modules import from here — no raw os.getenv() calls scattered around.
"""

import os
import logging
from dotenv import load_dotenv

# Load .env file into environment
load_dotenv()


# ─────────────────────────────────────────────
# Helper: safe type-cast env vars
# ─────────────────────────────────────────────

def _get_str(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()

def _get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default

def _get_int(key: str, default: int = 0) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default

def _get_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, str(default)).strip().lower()
    return val in ("true", "1", "yes")

def _get_list(key: str, default: str = "") -> list:
    raw = os.getenv(key, default).strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


# ─────────────────────────────────────────────
# Bybit Exchange Config
# ─────────────────────────────────────────────

class ExchangeConfig:
    API_KEY: str = _get_str("BYBIT_API_KEY")
    API_SECRET: str = _get_str("BYBIT_API_SECRET")
    TESTNET: bool = _get_bool("BYBIT_TESTNET", default=True)
    EXCHANGE_ID: str = "bybit"

    # Rate limiting: max requests per second
    RATE_LIMIT_CALLS: int = 5
    RATE_LIMIT_PERIOD: float = 1.0

    # Retry settings for failed API calls
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 2.0  # seconds between retries

    # OHLCV fetch: number of candles to pull per request
    OHLCV_LIMIT: int = 200

    # Minimum 24h volume in USDT for a pair to be considered tradeable
    MIN_VOLUME_USDT: float = 1_000_000.0

    # Maximum allowed spread as % before rejecting a trade
    MAX_SPREAD_PCT: float = 0.5


# ─────────────────────────────────────────────
# Telegram Config
# ─────────────────────────────────────────────

class TelegramConfig:
    BOT_TOKEN: str = _get_str("TELEGRAM_BOT_TOKEN")
    ALLOWED_USER_IDS: list = [
        int(uid) for uid in _get_list("TELEGRAM_ALLOWED_USER_IDS")
        if uid.isdigit()
    ]


# ─────────────────────────────────────────────
# Trading Mode Config
# ─────────────────────────────────────────────

class TradingConfig:
    # "paper" or "live" — defaults to paper for safety
    MODE: str = _get_str("TRADING_MODE", "paper").lower()

    # Paper balance — clamped to $5–$1000 range
    _raw_balance = _get_float("PAPER_BALANCE", 500.0)
    PAPER_BALANCE: float = max(5.0, min(1000.0, _raw_balance))

    # Whether auto-trading is enabled (can be toggled via Telegram)
    AUTO_TRADE_ENABLED: bool = False  # Always starts OFF — must be enabled manually

    @classmethod
    def is_paper(cls) -> bool:
        return cls.MODE == "paper"

    @classmethod
    def is_live(cls) -> bool:
        return cls.MODE == "live"


# ─────────────────────────────────────────────
# Risk Management Config
# ─────────────────────────────────────────────

class RiskConfig:
    # Risk per trade as % of account — clamped 0.1% to 2%
    _raw_risk = _get_float("RISK_PER_TRADE", 0.25)
    RISK_PER_TRADE_PCT: float = max(0.1, min(2.0, _raw_risk))

    # Maximum daily loss as % of starting day balance
    MAX_DAILY_LOSS_PCT: float = _get_float("MAX_DAILY_LOSS_PCT", 2.0)

    # Maximum number of concurrent open positions
    MAX_CONCURRENT_TRADES: int = _get_int("MAX_CONCURRENT_TRADES", 3)

    # Cooldown after consecutive losses (minutes)
    LOSS_COOLDOWN_MINUTES: int = _get_int("LOSS_COOLDOWN_MINUTES", 30)

    # Number of consecutive losses before cooldown triggers
    CONSECUTIVE_LOSS_TRIGGER: int = 2

    # Minimum reward:risk ratio — reject trades below this
    MIN_REWARD_RISK_RATIO: float = 1.5

    # ATR multiplier for stop loss distance
    ATR_SL_MULTIPLIER: float = 1.5

    # ATR multiplier for take profit distance
    ATR_TP_MULTIPLIER: float = 2.5

    # Assumed fee per side (percentage)
    FEE_PCT: float = 0.1

    # Assumed slippage per side (percentage)
    SLIPPAGE_PCT: float = 0.05


# ─────────────────────────────────────────────
# Strategy Config
# ─────────────────────────────────────────────

class StrategyConfig:
    # "trend", "mean_reversion", "breakout", or "AUTO"
    DEFAULT_STRATEGY: str = _get_str("DEFAULT_STRATEGY", "AUTO").lower()

    # "AUTO" means the bot scans all pairs and picks the best setup
    # Or specify pairs like "BTC/USDT,ETH/USDT,SOL/USDT"
    DEFAULT_PAIRS_RAW: str = _get_str("DEFAULT_PAIRS", "AUTO")
    DEFAULT_PAIRS: list = (
        ["AUTO"] if DEFAULT_PAIRS_RAW.upper() == "AUTO"
        else _get_list("DEFAULT_PAIRS", "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT")
    )

    # All pairs to scan when in AUTO mode
    SCAN_PAIRS: list = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
        "XRP/USDT", "ADA/USDT", "DOGE/USDT", "AVAX/USDT",
        "MATIC/USDT", "LINK/USDT", "DOT/USDT", "UNI/USDT",
    ]

    # All timeframes to scan when AUTO is selected
    ALL_TIMEFRAMES: list = ["15m", "1h", "4h"]
    DEFAULT_TIMEFRAME: str = _get_str("DEFAULT_TIMEFRAME", "1h")

    # ── Trend Following (EMA + MACD) ──────────────
    TREND_EMA_FAST: int = 9
    TREND_EMA_MID: int = 21
    TREND_EMA_SLOW: int = 50
    TREND_MACD_FAST: int = 12
    TREND_MACD_SLOW: int = 26
    TREND_MACD_SIGNAL: int = 9
    TREND_VOLUME_MULTIPLIER: float = 1.2   # Volume must be > 1.2x average

    # ── Mean Reversion (RSI + Bollinger Bands) ────
    MR_RSI_PERIOD: int = 14
    MR_RSI_OVERSOLD: float = 30.0
    MR_RSI_OVERBOUGHT: float = 70.0
    MR_BB_PERIOD: int = 20
    MR_BB_STD: float = 2.0

    # ── Breakout (ATR + Volume) ───────────────────
    BO_ATR_PERIOD: int = 14
    BO_LOOKBACK: int = 20            # Candles to look back for S/R levels
    BO_VOLUME_SURGE: float = 1.5     # Volume must be > 1.5x average to confirm
    BO_ATR_BREAKOUT_MULT: float = 0.5  # Price must break level by 0.5 ATR

    # ── Regime Filter ─────────────────────────────
    REGIME_ADX_PERIOD: int = 14
    REGIME_ADX_TRENDING_MIN: float = 25.0   # ADX > 25 = trending market
    REGIME_ATR_RATIO_MIN: float = 0.003     # Minimum volatility (ATR/price)
    REGIME_ATR_RATIO_MAX: float = 0.08      # Maximum volatility (too wild)


# ─────────────────────────────────────────────
# Database Config
# ─────────────────────────────────────────────

class DatabaseConfig:
    PATH: str = _get_str("DATABASE_PATH", "crypto_bot.db")


# ─────────────────────────────────────────────
# Logging Config
# ─────────────────────────────────────────────

class LogConfig:
    LEVEL: str = _get_str("LOG_LEVEL", "INFO").upper()
    FILE: str = _get_str("LOG_FILE", "bot.log")
    FORMAT: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"


# ─────────────────────────────────────────────
# Validation on import
# ─────────────────────────────────────────────

def validate_config() -> list:
    """
    Check for critical missing config values.
    Returns a list of warning strings. Empty list = all good.
    """
    warnings = []

    if not TelegramConfig.BOT_TOKEN:
        warnings.append("TELEGRAM_BOT_TOKEN is not set in .env")

    if not TelegramConfig.ALLOWED_USER_IDS:
        warnings.append("TELEGRAM_ALLOWED_USER_IDS is not set — bot is OPEN to anyone!")

    if TradingConfig.is_live():
        if not ExchangeConfig.API_KEY:
            warnings.append("BYBIT_API_KEY is not set — live trading will fail")
        if not ExchangeConfig.API_SECRET:
            warnings.append("BYBIT_API_SECRET is not set — live trading will fail")

    if TradingConfig.PAPER_BALANCE < 5.0:
        warnings.append("PAPER_BALANCE is below minimum $5 — resetting to $5")
        TradingConfig.PAPER_BALANCE = 5.0

    if TradingConfig.PAPER_BALANCE > 1000.0:
        warnings.append("PAPER_BALANCE above $1000 cap — clamped to $1000")
        TradingConfig.PAPER_BALANCE = 1000.0

    return warnings


# ─────────────────────────────────────────────
# Setup logging (called once from main.py)
# ─────────────────────────────────────────────

def setup_logging():
    """Configure root logger. Call this once at startup."""
    import colorlog

    level = getattr(logging, LogConfig.LEVEL, logging.INFO)

    # Console handler with color
    console = colorlog.StreamHandler()
    console.setLevel(level)
    console.setFormatter(colorlog.ColoredFormatter(
        "%(log_color)s" + LogConfig.FORMAT,
        datefmt=LogConfig.DATE_FORMAT,
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        }
    ))

    # File handler (plain text)
    file_handler = logging.FileHandler(LogConfig.FILE, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(
        LogConfig.FORMAT, datefmt=LogConfig.DATE_FORMAT
    ))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)
