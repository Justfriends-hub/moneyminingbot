"""
exchange/bybit_client.py
------------------------
Bybit exchange client using the ccxt library.
Handles all market data and order operations.
Swappable — any other ccxt exchange can replace this by changing EXCHANGE_ID in config.
"""

import time
import logging
import ccxt
import pandas as pd
from typing import Optional, List, Dict, Any, Tuple

from config import ExchangeConfig, TradingConfig
from utils.helpers import retry, ts_to_dt

logger = logging.getLogger(__name__)


class BybitClient:
    """
    Wrapper around ccxt Bybit exchange.
    Provides clean methods for OHLCV, ticker, balance, and order operations.
    """

    def __init__(self):
        self._exchange = self._create_exchange()
        self._markets: Dict = {}
        logger.info(
            f"[Exchange] Bybit client created. "
            f"Testnet={ExchangeConfig.TESTNET} | "
            f"Paper={TradingConfig.is_paper()}"
        )

    def _create_exchange(self) -> ccxt.bybit:
        """Instantiate the ccxt Bybit exchange object."""
        exchange = ccxt.bybit({
            "apiKey":    ExchangeConfig.API_KEY,
            "secret":    ExchangeConfig.API_SECRET,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",   # Use 'future' for perpetuals
                "adjustForTimeDifference": True,
            },
        })

        if ExchangeConfig.TESTNET:
            exchange.set_sandbox_mode(True)
            logger.info("[Exchange] Testnet/sandbox mode ENABLED")

        return exchange

    # ─────────────────────────────────────────
    # Market data loading
    # ─────────────────────────────────────────

    @retry(max_attempts=3, delay=2.0)
    def load_markets(self) -> Dict:
        """Load and cache all markets. Should be called once at startup."""
        self._markets = self._exchange.load_markets()
        logger.info(f"[Exchange] Loaded {len(self._markets)} markets from Bybit")
        return self._markets

    def get_tradeable_pairs(
        self,
        quote_currency: str = "USDT",
        min_volume: float = None,
    ) -> List[str]:
        """
        Return a list of active USDT spot pairs above minimum volume threshold.

        Args:
            quote_currency: Quote asset to filter by (e.g. 'USDT').
            min_volume: Minimum 24h volume in USDT. Uses config default if None.

        Returns:
            Sorted list of pair symbols.
        """
        if not self._markets:
            self.load_markets()

        min_vol = min_volume or ExchangeConfig.MIN_VOLUME_USDT
        result = []

        for symbol, market in self._markets.items():
            if not market.get("active", False):
                continue
            if market.get("quote", "") != quote_currency:
                continue
            if market.get("type", "spot") != "spot":
                continue
            result.append(symbol)

        return sorted(result)

    # ─────────────────────────────────────────
    # OHLCV candle data
    # ─────────────────────────────────────────

    @retry(max_attempts=3, delay=2.0)
    def fetch_ohlcv(
        self,
        pair: str,
        timeframe: str = "1h",
        limit: int = None,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candle data for a trading pair.

        Args:
            pair:      Trading pair symbol (e.g. 'BTC/USDT').
            timeframe: Candle timeframe (e.g. '1h', '15m', '4h').
            limit:     Number of candles to fetch. Uses config default if None.

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
            Returns None on failure.
        """
        limit = limit or ExchangeConfig.OHLCV_LIMIT

        if not self._exchange.has.get("fetchOHLCV"):
            logger.error("[Exchange] Bybit does not support OHLCV fetching")
            return None

        raw = self._exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)

        if not raw:
            logger.warning(f"[Exchange] Empty OHLCV response for {pair} {timeframe}")
            return None

        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp")
        df = df.astype(float)

        # Remove the last candle if it's incomplete (current forming candle)
        df = df.iloc[:-1]

        logger.debug(f"[Exchange] Fetched {len(df)} candles for {pair} {timeframe}")
        return df

    # ─────────────────────────────────────────
    # Ticker / market state
    # ─────────────────────────────────────────

    @retry(max_attempts=3, delay=2.0)
    def fetch_ticker(self, pair: str) -> Optional[Dict]:
        """
        Fetch latest ticker data for a pair.
        Returns dict with: bid, ask, last, volume, percentage (24h change).
        """
        ticker = self._exchange.fetch_ticker(pair)
        return ticker

    def get_spread_pct(self, pair: str) -> float:
        """
        Calculate current bid-ask spread as a percentage.
        Returns 999.0 if ticker fetch fails (treated as untradeable).
        """
        try:
            ticker = self.fetch_ticker(pair)
            bid = ticker.get("bid", 0)
            ask = ticker.get("ask", 0)
            if bid <= 0 or ask <= 0:
                return 999.0
            return ((ask - bid) / bid) * 100.0
        except Exception as e:
            logger.warning(f"[Exchange] Could not get spread for {pair}: {e}")
            return 999.0

    def is_liquid_enough(self, pair: str) -> bool:
        """
        Check if a pair is liquid enough to trade.
        Rejects if spread exceeds configured maximum.
        """
        spread = self.get_spread_pct(pair)
        if spread > ExchangeConfig.MAX_SPREAD_PCT:
            logger.debug(
                f"[Exchange] {pair} rejected — spread {spread:.3f}% > "
                f"max {ExchangeConfig.MAX_SPREAD_PCT}%"
            )
            return False
        return True

    # ─────────────────────────────────────────
    # Account / balance
    # ─────────────────────────────────────────

    @retry(max_attempts=3, delay=2.0)
    def fetch_balance(self) -> Dict:
        """
        Fetch account balances.
        Returns dict of {currency: {free, used, total}}.
        Returns empty dict on failure.
        """
        if TradingConfig.is_paper():
            logger.debug("[Exchange] Paper mode — balance from paper engine")
            return {}

        balance = self._exchange.fetch_balance()
        return balance

    def get_usdt_balance(self) -> float:
        """Return free USDT balance. Returns 0.0 on any failure."""
        if TradingConfig.is_paper():
            return 0.0
        try:
            balance = self.fetch_balance()
            return float(balance.get("USDT", {}).get("free", 0.0))
        except Exception as e:
            logger.error(f"[Exchange] Failed to get USDT balance: {e}")
            return 0.0

    # ─────────────────────────────────────────
    # Order operations (live only)
    # ─────────────────────────────────────────

    @retry(max_attempts=2, delay=1.0)
    def place_market_order(
        self,
        pair: str,
        side: str,
        quantity: float,
    ) -> Optional[Dict]:
        """
        Place a market order on Bybit.
        Should ONLY be called when TradingConfig.MODE == 'live'.

        Args:
            pair:     Trading pair symbol.
            side:     'buy' or 'sell'.
            quantity: Amount of base asset to buy/sell.

        Returns:
            ccxt order dict on success, None on failure.
        """
        if TradingConfig.is_paper():
            logger.warning("[Exchange] place_market_order called in paper mode — ignoring")
            return None

        logger.info(f"[Exchange] Placing LIVE market order: {side} {quantity} {pair}")
        order = self._exchange.create_market_order(pair, side, quantity)
        logger.info(f"[Exchange] Order placed: {order.get('id')} | Status: {order.get('status')}")
        return order

    @retry(max_attempts=2, delay=1.0)
    def place_limit_order(
        self,
        pair: str,
        side: str,
        quantity: float,
        price: float,
    ) -> Optional[Dict]:
        """Place a limit order on Bybit (live only)."""
        if TradingConfig.is_paper():
            logger.warning("[Exchange] place_limit_order called in paper mode — ignoring")
            return None

        order = self._exchange.create_limit_order(pair, side, quantity, price)
        return order

    def cancel_order(self, order_id: str, pair: str) -> bool:
        """Cancel an open order by ID (live only)."""
        if TradingConfig.is_paper():
            return True
        try:
            self._exchange.cancel_order(order_id, pair)
            logger.info(f"[Exchange] Order {order_id} cancelled")
            return True
        except Exception as e:
            logger.error(f"[Exchange] Failed to cancel order {order_id}: {e}")
            return False

    def fetch_open_orders(self, pair: str = None) -> List[Dict]:
        """Return list of open orders for a pair (or all pairs)."""
        if TradingConfig.is_paper():
            return []
        try:
            orders = self._exchange.fetch_open_orders(pair)
            return orders
        except Exception as e:
            logger.error(f"[Exchange] Failed to fetch open orders: {e}")
            return []

    # ─────────────────────────────────────────
    # Market info helpers
    # ─────────────────────────────────────────

    def get_min_order_size(self, pair: str) -> float:
        """Return minimum order size for a pair (base asset)."""
        if not self._markets:
            self.load_markets()
        market = self._markets.get(pair, {})
        limits = market.get("limits", {}).get("amount", {})
        return float(limits.get("min", 0.0001))

    def get_price_precision(self, pair: str) -> int:
        """Return number of decimal places for price on this pair."""
        if not self._markets:
            self.load_markets()
        market = self._markets.get(pair, {})
        precision = market.get("precision", {}).get("price", 4)
        return int(precision) if isinstance(precision, (int, float)) else 4

    def get_quantity_precision(self, pair: str) -> int:
        """Return number of decimal places for quantity on this pair."""
        if not self._markets:
            self.load_markets()
        market = self._markets.get(pair, {})
        precision = market.get("precision", {}).get("amount", 4)
        return int(precision) if isinstance(precision, (int, float)) else 4

    def get_current_price(self, pair: str) -> float:
        """Return the latest traded price for a pair."""
        try:
            ticker = self.fetch_ticker(pair)
            return float(ticker.get("last", 0.0))
        except Exception:
            return 0.0
