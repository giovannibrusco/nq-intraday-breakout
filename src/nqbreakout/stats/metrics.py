"""stats.metrics: performance metrics from daily PnL or equity series.

All functions accept pandas Series. NaN-safe: return nan when inputs are
degenerate (empty, zero variance, zero drawdown).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sharpe(
    daily_pnl: pd.Series,
    ann_factor: float = 252.0,
    risk_free_daily: float = 0.0,
) -> float:
    """Annualized Sharpe ratio.

    Returns nan if the series is empty or has zero standard deviation.
    """
    vals = np.asarray(daily_pnl.dropna(), dtype=float)
    if len(vals) == 0:
        return float("nan")
    excess = vals - risk_free_daily
    std = float(np.std(excess, ddof=1))
    if std == 0.0 or np.isnan(std):
        return float("nan")
    mean = float(np.mean(excess))
    return float(mean / std * np.sqrt(ann_factor))


def sortino(
    daily_pnl: pd.Series,
    ann_factor: float = 252.0,
    target: float = 0.0,
) -> float:
    """Annualized Sortino ratio using downside deviation.

    Returns nan if no negative deviations exist or series is empty.
    """
    vals = np.asarray(daily_pnl.dropna(), dtype=float)
    if len(vals) == 0:
        return float("nan")
    downside = vals[vals < target] - target
    if len(downside) == 0:
        # All returns >= target: return nan (infinite Sortino is not informative)
        return float("nan")
    downside_std = float(np.sqrt(np.mean(downside ** 2)))
    if downside_std == 0.0 or np.isnan(downside_std):
        return float("nan")
    mean_excess = float(np.mean(vals - target))
    return float(mean_excess / downside_std * np.sqrt(ann_factor))


def max_drawdown(equity: pd.Series) -> float:
    """Maximum peak-to-trough drawdown in dollars.

    Returns 0.0 for empty or constant equity series.
    """
    vals = np.asarray(equity.dropna(), dtype=float)
    if len(vals) == 0:
        return 0.0
    running_max = np.maximum.accumulate(vals)
    drawdowns = running_max - vals
    return float(np.max(drawdowns))


def max_drawdown_pct(equity: pd.Series) -> float:
    """Maximum peak-to-trough drawdown as fraction of the peak (0 to 1 scale).

    Returns 0.0 if equity is empty or peak is zero.
    """
    vals = np.asarray(equity.dropna(), dtype=float)
    if len(vals) == 0:
        return 0.0
    running_max = np.maximum.accumulate(vals)
    # Avoid division by zero when running_max contains zeros.
    with np.errstate(divide="ignore", invalid="ignore"):
        pct_dd = np.where(running_max > 0, (running_max - vals) / running_max, 0.0)
    return float(np.max(pct_dd))


def calmar(
    daily_pnl: pd.Series,
    equity: pd.Series,
    ann_factor: float = 252.0,
) -> float:
    """Annualized return % divided by max drawdown %.

    Uses the compound annualized return formula:
        ann_return = (final / initial) ^ (ann_factor / n_days) - 1

    Both numerator and denominator are dimensionless fractions, so the
    result is a unitless ratio (e.g. 1.5 means annualized return is 1.5x
    the max drawdown). The old implementation divided dollar PnL by a small
    fraction, producing artefact values in the millions.

    Returns nan if equity is empty, initial equity <= 0, or max drawdown
    is zero (division undefined).
    """
    eq_vals = np.asarray(equity.dropna(), dtype=float)
    n_days = int(len(daily_pnl.dropna()))
    if len(eq_vals) < 2 or n_days == 0:
        return float("nan")
    initial = float(eq_vals[0])
    final = float(eq_vals[-1])
    if initial <= 0:
        return float("nan")
    ann_return = (final / initial) ** (ann_factor / n_days) - 1.0
    mdd_pct = max_drawdown_pct(equity)
    if mdd_pct == 0.0 or np.isnan(mdd_pct):
        return float("nan")
    return float(ann_return / mdd_pct)


def profit_factor(trade_pnl: pd.Series) -> float:
    """Ratio of total winning PnL to absolute total losing PnL.

    Returns nan if there are no losing trades.
    """
    vals = np.asarray(trade_pnl.dropna(), dtype=float)
    winners = vals[vals > 0]
    losers = vals[vals < 0]
    if len(losers) == 0:
        return float("nan")
    gross_profit = float(np.sum(winners)) if len(winners) > 0 else 0.0
    gross_loss = float(abs(np.sum(losers)))
    if gross_loss == 0.0:
        return float("nan")
    return float(gross_profit / gross_loss)


def win_rate(trade_pnl: pd.Series) -> float:
    """Fraction of trades with positive PnL.

    Returns nan for empty series.
    """
    vals = np.asarray(trade_pnl.dropna(), dtype=float)
    if len(vals) == 0:
        return float("nan")
    return float(np.mean(vals > 0))


def summary(
    daily_pnl: pd.Series,
    equity: pd.Series,
    trade_pnl: pd.Series | None = None,
) -> dict:
    """Compute all metrics and return as a flat dict.

    trade_pnl is optional; profit_factor and win_rate are set to nan when absent.
    """
    result: dict = {
        "sharpe": sharpe(daily_pnl),
        "sortino": sortino(daily_pnl),
        "max_dd": max_drawdown(equity),
        "max_dd_pct": max_drawdown_pct(equity),
        "calmar": calmar(daily_pnl, equity),
        "total_pnl": float(daily_pnl.sum()) if len(daily_pnl) > 0 else 0.0,
        "n_days": int(len(daily_pnl.dropna())),
    }
    if trade_pnl is not None:
        result["profit_factor"] = profit_factor(trade_pnl)
        result["win_rate"] = win_rate(trade_pnl)
        result["n_trades"] = int(len(trade_pnl.dropna()))
    else:
        result["profit_factor"] = float("nan")
        result["win_rate"] = float("nan")
        result["n_trades"] = 0
    return result
