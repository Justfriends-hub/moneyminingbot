"""
analytics/performance.py
-------------------------
Performance analytics engine.
Computes all key metrics from closed trade history.

⚠️  DISCLAIMER: Past performance does not guarantee future results.
    These metrics reflect historical simulation and carry no predictive value.
"""

import logging
import math
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)


def compute_performance(trades: List[Dict]) -> Dict[str, Any]:
    """
    Compute comprehensive performance metrics from a list of closed trades.

    Args:
        trades: List of closed trade dicts from the database.

    Returns:
        Dict of performance metrics.
    """
    if not trades:
        return _empty_metrics()

    closed = [t for t in trades if t.get("status") == "closed"]

    if not closed:
        return _empty_metrics()

    total = len(closed)
    wins  = [t for t in closed if t.get("pnl", 0) > 0]
    losses = [t for t in closed if t.get("pnl", 0) <= 0]

    n_wins   = len(wins)
    n_losses = len(losses)
    win_rate = (n_wins / total) * 100.0 if total > 0 else 0.0

    # ── P&L aggregates ────────────────────────
    total_pnl = sum(t.get("pnl", 0) for t in closed)
    gross_profit = sum(t.get("pnl", 0) for t in wins)
    gross_loss   = abs(sum(t.get("pnl", 0) for t in losses))

    avg_win  = gross_profit / n_wins   if n_wins   > 0 else 0.0
    avg_loss = gross_loss   / n_losses if n_losses > 0 else 0.0

    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    # ── Max drawdown ──────────────────────────
    max_drawdown, max_dd_pct = _compute_max_drawdown(closed)

    # ── Expectancy ────────────────────────────
    # Expected P&L per trade in dollars
    expectancy = (
        (win_rate / 100) * avg_win
        - ((100 - win_rate) / 100) * avg_loss
    )

    # ── Sharpe-like ratio ─────────────────────
    sharpe = _compute_sharpe(closed)

    # ── Best / worst trade ────────────────────
    best_trade  = max(closed, key=lambda t: t.get("pnl", 0), default=None)
    worst_trade = min(closed, key=lambda t: t.get("pnl", 0), default=None)

    # ── Breakdown by strategy ─────────────────
    strategy_breakdown = _strategy_breakdown(closed)

    # ── Close reason breakdown ────────────────
    tp_count     = sum(1 for t in closed if t.get("close_reason") == "tp")
    sl_count     = sum(1 for t in closed if t.get("close_reason") == "sl")
    manual_count = sum(1 for t in closed if t.get("close_reason") not in ("tp", "sl"))

    return {
        "total_trades":      total,
        "winning_trades":    n_wins,
        "losing_trades":     n_losses,
        "win_rate_pct":      round(win_rate, 2),
        "total_pnl":         round(total_pnl, 4),
        "gross_profit":      round(gross_profit, 4),
        "gross_loss":        round(gross_loss, 4),
        "avg_win":           round(avg_win, 4),
        "avg_loss":          round(avg_loss, 4),
        "profit_factor":     round(profit_factor, 3),
        "max_drawdown_usd":  round(max_drawdown, 4),
        "max_drawdown_pct":  round(max_dd_pct, 2),
        "expectancy_usd":    round(expectancy, 4),
        "sharpe_ratio":      round(sharpe, 3),
        "best_trade_pnl":    round(best_trade["pnl"], 4) if best_trade else 0.0,
        "worst_trade_pnl":   round(worst_trade["pnl"], 4) if worst_trade else 0.0,
        "tp_hits":           tp_count,
        "sl_hits":           sl_count,
        "manual_closes":     manual_count,
        "strategy_breakdown": strategy_breakdown,
    }


def _empty_metrics() -> Dict[str, Any]:
    """Return zeroed metrics when there are no trades."""
    return {
        "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
        "win_rate_pct": 0.0, "total_pnl": 0.0, "gross_profit": 0.0,
        "gross_loss": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        "profit_factor": 0.0, "max_drawdown_usd": 0.0, "max_drawdown_pct": 0.0,
        "expectancy_usd": 0.0, "sharpe_ratio": 0.0,
        "best_trade_pnl": 0.0, "worst_trade_pnl": 0.0,
        "tp_hits": 0, "sl_hits": 0, "manual_closes": 0,
        "strategy_breakdown": {},
    }


def _compute_max_drawdown(trades: List[Dict]) -> Tuple[float, float]:
    """
    Compute max drawdown from cumulative P&L curve.

    Returns:
        (max_drawdown_usd, max_drawdown_pct)
    """
    if not trades:
        return 0.0, 0.0

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    peak_usd = 0.0

    for trade in trades:
        cumulative += trade.get("pnl", 0)
        if cumulative > peak:
            peak = cumulative
            peak_usd = cumulative
        drawdown = peak - cumulative
        if drawdown > max_dd:
            max_dd = drawdown

    max_dd_pct = (max_dd / peak_usd * 100.0) if peak_usd > 0 else 0.0
    return max_dd, max_dd_pct


def _compute_sharpe(trades: List[Dict], risk_free_rate: float = 0.0) -> float:
    """
    Compute a Sharpe-like ratio from per-trade returns.
    Uses trade P&L percentages as the return series.
    Note: This is an approximation, not a time-series Sharpe ratio.
    """
    returns = [t.get("pnl_pct", 0) for t in trades]
    if len(returns) < 2:
        return 0.0

    n = len(returns)
    mean_return = sum(returns) / n
    variance = sum((r - mean_return) ** 2 for r in returns) / (n - 1)
    std_dev = math.sqrt(variance) if variance > 0 else 0.0

    if std_dev == 0:
        return 0.0

    sharpe = (mean_return - risk_free_rate) / std_dev
    return sharpe


def _strategy_breakdown(trades: List[Dict]) -> Dict[str, Dict]:
    """Break down performance metrics by strategy."""
    breakdown: Dict[str, List] = {}

    for trade in trades:
        strategy = trade.get("strategy", "unknown")
        if strategy not in breakdown:
            breakdown[strategy] = []
        breakdown[strategy].append(trade)

    result = {}
    for strategy, strat_trades in breakdown.items():
        n = len(strat_trades)
        wins = sum(1 for t in strat_trades if t.get("pnl", 0) > 0)
        pnl = sum(t.get("pnl", 0) for t in strat_trades)
        result[strategy] = {
            "trades": n,
            "wins": wins,
            "win_rate_pct": round((wins / n) * 100, 1) if n > 0 else 0.0,
            "total_pnl": round(pnl, 4),
        }

    return result


def format_performance_message(metrics: Dict[str, Any]) -> str:
    """Format performance metrics into a Telegram-ready message."""

    if metrics["total_trades"] == 0:
        return (
            "📊 <b>Performance Report</b>\n\n"
            "No closed trades yet.\n"
            "Start paper trading to see results here.\n\n"
            "⚠️ <i>Past performance does not guarantee future results.</i>"
        )

    pf_str = (
        f"{metrics['profit_factor']:.2f}"
        if metrics['profit_factor'] != float("inf")
        else "∞ (no losses)"
    )

    breakdown_lines = []
    for strat, data in metrics.get("strategy_breakdown", {}).items():
        breakdown_lines.append(
            f"  • {strat}: {data['trades']} trades | "
            f"{data['win_rate_pct']}% WR | "
            f"${data['total_pnl']:+.2f}"
        )
    breakdown_str = "\n".join(breakdown_lines) if breakdown_lines else "  N/A"

    return (
        "📊 <b>Performance Report</b>\n"
        "─────────────────────\n"
        f"Total trades:     {metrics['total_trades']}\n"
        f"Win rate:         {metrics['win_rate_pct']:.1f}%\n"
        f"Wins / Losses:    {metrics['winning_trades']} / {metrics['losing_trades']}\n"
        f"TP hits:          {metrics['tp_hits']}\n"
        f"SL hits:          {metrics['sl_hits']}\n"
        "\n"
        f"Total P&L:        ${metrics['total_pnl']:+.2f}\n"
        f"Avg win:          ${metrics['avg_win']:+.2f}\n"
        f"Avg loss:         ${metrics['avg_loss']:-.2f}\n"
        f"Best trade:       ${metrics['best_trade_pnl']:+.2f}\n"
        f"Worst trade:      ${metrics['worst_trade_pnl']:+.2f}\n"
        "\n"
        f"Profit factor:    {pf_str}\n"
        f"Expectancy:       ${metrics['expectancy_usd']:+.4f}/trade\n"
        f"Sharpe (approx):  {metrics['sharpe_ratio']:.3f}\n"
        f"Max drawdown:     ${metrics['max_drawdown_usd']:.2f} ({metrics['max_drawdown_pct']:.1f}%)\n"
        "\n"
        "<b>By Strategy:</b>\n"
        f"{breakdown_str}\n"
        "\n"
        "⚠️ <i>Past performance does not guarantee future results.\n"
        "This is paper/simulated trading data.</i>"
    )
