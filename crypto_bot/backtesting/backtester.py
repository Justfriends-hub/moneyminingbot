"""
backtesting/backtester.py
--------------------------
Historical backtesting engine.
Tests strategies on downloaded OHLCV data with realistic fee and slippage assumptions.

⚠️  DISCLAIMER:
    Backtesting is NOT predictive. Results on historical data do not
    guarantee any future performance. Markets change. Strategies degrade.
    Use backtests only to understand strategy mechanics — never to set
    profit expectations.
"""

import logging
from typing import List, Optional, Dict, Any
import pandas as pd

from config import RiskConfig, StrategyConfig
from strategies.trend_following import TrendFollowingStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.breakout import BreakoutStrategy
from analytics.performance import compute_performance, format_performance_message

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "\n⚠️  IMPORTANT: Past performance does NOT guarantee future results.\n"
    "   Backtests are run on historical data and are subject to look-ahead bias,\n"
    "   overfitting, and survivorship bias. Real trading results will differ.\n"
)


class BacktestResult:
    """Container for backtest results."""

    def __init__(
        self,
        pair: str,
        timeframe: str,
        strategy: str,
        trades: List[Dict],
        metrics: Dict[str, Any],
        start_balance: float,
        final_balance: float,
    ):
        self.pair = pair
        self.timeframe = timeframe
        self.strategy = strategy
        self.trades = trades
        self.metrics = metrics
        self.start_balance = start_balance
        self.final_balance = final_balance
        self.total_return_pct = (
            ((final_balance - start_balance) / start_balance) * 100.0
            if start_balance > 0 else 0.0
        )

    def summary(self) -> str:
        """Return a human-readable summary string."""
        m = self.metrics
        return (
            f"\n{'='*50}\n"
            f"BACKTEST: {self.pair} | {self.timeframe} | {self.strategy.upper()}\n"
            f"{'='*50}\n"
            f"Start balance:   ${self.start_balance:.2f}\n"
            f"Final balance:   ${self.final_balance:.2f}\n"
            f"Total return:    {self.total_return_pct:+.2f}%\n"
            f"Total trades:    {m['total_trades']}\n"
            f"Win rate:        {m['win_rate_pct']:.1f}%\n"
            f"Profit factor:   {m['profit_factor']:.2f}\n"
            f"Max drawdown:    ${m['max_drawdown_usd']:.2f} ({m['max_drawdown_pct']:.1f}%)\n"
            f"Sharpe (approx): {m['sharpe_ratio']:.3f}\n"
            f"Avg win:         ${m['avg_win']:.4f}\n"
            f"Avg loss:        ${m['avg_loss']:.4f}\n"
            f"Expectancy:      ${m['expectancy_usd']:.4f}/trade\n"
            f"{DISCLAIMER}"
        )


class Backtester:
    """
    Backtest a strategy on historical OHLCV data.
    Simulates walk-forward style by using a rolling window approach.
    """

    def __init__(self, starting_balance: float = 500.0):
        self.starting_balance = max(5.0, min(1000.0, starting_balance))
        self._strategies = {
            "trend":          TrendFollowingStrategy(),
            "mean_reversion": MeanReversionStrategy(),
            "breakout":       BreakoutStrategy(),
        }

    def run(
        self,
        df: pd.DataFrame,
        pair: str,
        timeframe: str,
        strategy_name: str = "trend",
        warmup_candles: int = 60,
    ) -> BacktestResult:
        """
        Run a backtest on a full historical DataFrame.

        Args:
            df:             Full OHLCV DataFrame (index=datetime, cols: open,high,low,close,volume).
            pair:           Trading pair symbol.
            timeframe:      Candle timeframe.
            strategy_name:  Which strategy to test.
            warmup_candles: Number of initial candles to skip (indicator warmup).

        Returns:
            BacktestResult with all trade history and metrics.
        """
        strategy = self._strategies.get(strategy_name)
        if strategy is None:
            raise ValueError(f"Unknown strategy: {strategy_name}")

        if df is None or len(df) < warmup_candles + 20:
            raise ValueError(f"Not enough data for backtest. Need > {warmup_candles + 20} candles")

        balance = self.starting_balance
        simulated_trades: List[Dict] = []
        open_trade: Optional[Dict] = None

        logger.info(
            f"[Backtest] Starting: {pair} {timeframe} {strategy_name} | "
            f"Candles: {len(df)} | Balance: ${balance:.2f}"
        )

        # Walk forward: for each candle from warmup onward
        for i in range(warmup_candles, len(df)):
            # Slice data up to and including candle i (no look-ahead)
            window = df.iloc[:i + 1].copy()
            current_close = float(window["close"].iloc[-1])

            # ── Check open trade ──────────────────────────
            if open_trade is not None:
                sl = open_trade["stop_loss"]
                tp = open_trade["take_profit"]
                side = open_trade["side"]
                entry = open_trade["entry_price"]
                qty = open_trade["quantity"]
                pos_val = open_trade["position_value"]

                hit_sl = (side == "buy"  and current_close <= sl) or \
                         (side == "sell" and current_close >= sl)
                hit_tp = (side == "buy"  and current_close >= tp) or \
                         (side == "sell" and current_close <= tp)

                if hit_tp or hit_sl:
                    exit_price = tp if hit_tp else sl
                    reason = "tp" if hit_tp else "sl"

                    # Apply fees and slippage
                    fee_slip = (RiskConfig.FEE_PCT + RiskConfig.SLIPPAGE_PCT) / 100.0
                    if side == "buy":
                        gross_pnl = (exit_price * (1 - fee_slip) - entry) * qty
                    else:
                        gross_pnl = (entry - exit_price * (1 + fee_slip)) * qty

                    pnl_pct = (gross_pnl / pos_val) * 100.0
                    balance += pos_val + gross_pnl

                    open_trade["status"]      = "closed"
                    open_trade["exit_price"]  = exit_price
                    open_trade["pnl"]         = round(gross_pnl, 6)
                    open_trade["pnl_pct"]     = round(pnl_pct, 4)
                    open_trade["close_reason"] = reason

                    simulated_trades.append(dict(open_trade))
                    open_trade = None

            # ── Look for new signal (only if flat) ────────
            if open_trade is None:
                signal = strategy.generate_signal(window, pair, timeframe)

                if signal is not None:
                    # Size the position
                    stop_distance = abs(signal.entry_price - signal.stop_loss)
                    if stop_distance > 0:
                        risk_amount = balance * (RiskConfig.RISK_PER_TRADE_PCT / 100.0)
                        qty = risk_amount / stop_distance
                        pos_val = qty * signal.entry_price

                        # Apply entry fee/slippage
                        fee_slip = (RiskConfig.FEE_PCT + RiskConfig.SLIPPAGE_PCT) / 100.0
                        if signal.side == "buy":
                            effective_entry = signal.entry_price * (1 + fee_slip)
                        else:
                            effective_entry = signal.entry_price * (1 - fee_slip)

                        # Check we have enough balance
                        if pos_val <= balance and pos_val >= 1.0:
                            balance -= pos_val  # Reserve cost

                            open_trade = {
                                "trade_id":      f"BT-{i}",
                                "pair":          pair,
                                "side":          signal.side,
                                "strategy":      strategy_name,
                                "timeframe":     timeframe,
                                "status":        "open",
                                "entry_price":   effective_entry,
                                "stop_loss":     signal.stop_loss,
                                "take_profit":   signal.take_profit,
                                "quantity":      qty,
                                "position_value": pos_val,
                                "signal_score":  signal.signal_score,
                                "is_paper":      1,
                                "opened_at":     str(window.index[-1]),
                            }

        # ── Force-close any remaining trade at last price ──
        if open_trade is not None:
            last_price = float(df["close"].iloc[-1])
            fee_slip = (RiskConfig.FEE_PCT + RiskConfig.SLIPPAGE_PCT) / 100.0
            side = open_trade["side"]
            entry = open_trade["entry_price"]
            qty = open_trade["quantity"]
            pos_val = open_trade["position_value"]

            if side == "buy":
                gross_pnl = (last_price * (1 - fee_slip) - entry) * qty
            else:
                gross_pnl = (entry - last_price * (1 + fee_slip)) * qty

            pnl_pct = (gross_pnl / pos_val) * 100.0
            balance += pos_val + gross_pnl

            open_trade["status"] = "closed"
            open_trade["exit_price"] = last_price
            open_trade["pnl"] = round(gross_pnl, 6)
            open_trade["pnl_pct"] = round(pnl_pct, 4)
            open_trade["close_reason"] = "end_of_data"
            simulated_trades.append(dict(open_trade))

        metrics = compute_performance(simulated_trades)
        result = BacktestResult(
            pair=pair,
            timeframe=timeframe,
            strategy=strategy_name,
            trades=simulated_trades,
            metrics=metrics,
            start_balance=self.starting_balance,
            final_balance=balance,
        )

        logger.info(
            f"[Backtest] Done: {len(simulated_trades)} trades | "
            f"Win rate: {metrics['win_rate_pct']:.1f}% | "
            f"Return: {result.total_return_pct:+.2f}%"
        )
        print(result.summary())
        return result

    def format_for_telegram(self, result: BacktestResult) -> str:
        """Format backtest result for Telegram message."""
        m = result.metrics
        pf = f"{m['profit_factor']:.2f}" if m['profit_factor'] != float("inf") else "∞"
        return (
            f"🔬 <b>Backtest Results</b>\n"
            f"─────────────────────\n"
            f"Pair:          {result.pair}\n"
            f"Timeframe:     {result.timeframe}\n"
            f"Strategy:      {result.strategy}\n"
            f"Candles used:  {len(result.trades)} trades\n"
            f"\n"
            f"Start balance: ${result.start_balance:.2f}\n"
            f"Final balance: ${result.final_balance:.2f}\n"
            f"Total return:  {result.total_return_pct:+.2f}%\n"
            f"\n"
            f"Win rate:      {m['win_rate_pct']:.1f}%\n"
            f"Profit factor: {pf}\n"
            f"Max drawdown:  ${m['max_drawdown_usd']:.2f} ({m['max_drawdown_pct']:.1f}%)\n"
            f"Sharpe:        {m['sharpe_ratio']:.3f}\n"
            f"Expectancy:    ${m['expectancy_usd']:.4f}/trade\n"
            f"\n"
            f"TP hits:       {m['tp_hits']}\n"
            f"SL hits:       {m['sl_hits']}\n"
            f"\n"
            f"⚠️ <i>Past performance ≠ future results.\n"
            f"Backtests are simulations only.</i>"
        )
