"""Performance metrics computed on an equity series.

All functions here are pure: they take an equity ``pd.Series`` (or a trade
frame) and return numbers. They make no assumption about how the equity
series was built — which matters, because *how* it is built is itself one
of the largest drivers of the reported Sharpe (see ``docs/bias-audit.md``).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252

__all__ = [
    'TRADING_DAYS_PER_YEAR', 'compute_metrics', 'trade_pnl_tstat',
    'align_benchmark', 'metrics_table', 'format_metrics',
    'pad_to_calendar_days',
]


def pad_to_calendar_days(equity: pd.Series) -> pd.Series:
    """Reindex an equity series onto every calendar day and forward-fill.

    Padding inserts ~113 flat days per year (weekends and holidays) while
    ``compute_metrics`` still annualises with 252, which deflates both
    volatility and Sharpe by a factor of ``sqrt(252/365) ~ 0.831``. Kept
    only because reproducing the original results requires it.
    """
    full = pd.date_range(equity.index.min(), equity.index.max(), freq='D')
    return equity.reindex(full, method='ffill').ffill()


def legacy_equity_curve(trades: pd.DataFrame, initial_capital: float) -> pd.Series:
    """Rebuild the equity curve exactly as the original notebook did.

    The original stepped equity only on trade *exit* dates, spread that
    over every calendar day between the first entry and the last exit, and
    seeded the leading stretch with ``initial_capital``. It therefore
    carries two distinct biases at once: open-position drawdown is
    invisible (equity moves only when a trade is realised) and the series
    is calendar-padded (see :func:`pad_to_calendar_days`).

    Reproduced here so the published results remain regenerable; use
    ``run_backtest`` with ``daily_mark_to_market=True`` for anything else.
    """
    if trades.empty:
        return pd.Series(dtype=float, name='equity')
    entry = pd.to_datetime(trades['entry_time']).min().normalize()
    exit_ = pd.to_datetime(trades['exit_time']).max().normalize()
    all_dates = pd.date_range(entry, exit_, freq='D')
    by_exit_day = (pd.DataFrame({'date': pd.to_datetime(trades['exit_time']).dt.normalize(),
                                 'account_value': trades['account_value']})
                   .groupby('date')['account_value'].last())
    equity = by_exit_day.reindex(all_dates).ffill().fillna(initial_capital)
    equity.name = 'equity'
    equity.index.name = 'date'
    return equity


def compute_metrics(equity: pd.Series, freq: int = TRADING_DAYS_PER_YEAR) -> dict:
    """Sharpe, volatility, max drawdown and CAGR for an equity series.

    ``freq`` is the annualisation factor. It must match the sampling of
    ``equity``: a series indexed on *trading days only* annualises with
    252, but a series padded out to every calendar day has ~365
    observations per year and annualising it with 252 understates both
    volatility and Sharpe by a factor of ``sqrt(252/365) ~ 0.83``.

    No risk-free rate is subtracted: 'Sharpe' here is the excess-over-zero
    ratio, which is what the published numbers report.
    """
    returns = equity.pct_change().dropna()
    if returns.empty:
        return {k: np.nan for k in ('Sharpe', 'Volatility', 'Max Drawdown %',
                                    'Max Drawdown $', 'CAGR')}
    std_ret = returns.std(ddof=1)
    # The original guard was `std_ret > 0`, which floating-point noise
    # defeats: a perfectly smooth curve yields std ~1e-16 rather than
    # exactly 0 and Sharpe explodes to ~1e14 instead of returning NaN.
    # A real daily return series has std ~1e-2, so this floor cannot
    # affect any result computed from actual trades.
    sharpe = returns.mean() / std_ret * math.sqrt(freq) if std_ret > 1e-12 else np.nan
    running_max = equity.cummax()
    drawdown_abs = equity - running_max
    total_years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / total_years) - 1 if total_years > 0 else np.nan
    return {
        'Sharpe': sharpe,
        'Volatility': std_ret * math.sqrt(freq),
        'Max Drawdown %': (drawdown_abs / running_max).min(),
        'Max Drawdown $': drawdown_abs.min(),
        'CAGR': cagr,
    }


def trade_pnl_tstat(trades: pd.DataFrame) -> dict:
    """t-stat of mean per-contract trade PnL against zero, net of costs.

    This treats trades as independent draws. Overlapping multi-day longs
    and the two-trades-per-day cap both violate that assumption, so the
    t-stat is an optimistic bound rather than a clean significance test.
    """
    pnl = trades['pnl_per_contract'].dropna()
    n = len(pnl)
    if n < 2:
        return {'mean': np.nan, 'std': np.nan, 'n': n, 't_stat': np.nan}
    mean, std = pnl.mean(), pnl.std(ddof=1)
    return {'mean': mean, 'std': std, 'n': n,
            't_stat': mean / (std / math.sqrt(n)) if std > 0 else np.nan}


def align_benchmark(qqq: pd.DataFrame, equity_index: pd.DatetimeIndex,
                    initial_capital: float) -> pd.Series:
    """Buy & hold benchmark equity marked on the strategy's own dates.

    Reindexing onto ``equity_index`` is what keeps the comparison fair: the
    benchmark is sampled on exactly the dates the strategy is sampled on,
    so both annualise with the same factor.
    """
    close = qqq.set_index('date')['close'].reindex(equity_index, method='ffill').bfill()
    if close.isna().any():
        raise ValueError('Benchmark data does not cover the strategy period.')
    return initial_capital / close.iloc[0] * close


def metrics_table(named_equities: dict[str, pd.Series]) -> pd.DataFrame:
    return pd.DataFrame({name: compute_metrics(eq) for name, eq in named_equities.items()}).T


def format_metrics(df: pd.DataFrame) -> pd.DataFrame:
    formatted = df.copy()
    fmt = {
        'Sharpe': lambda x: f'{x:.2f}',
        'Volatility': lambda x: f'{x:.2%}',
        'Max Drawdown %': lambda x: f'{abs(x):.2%}',
        'Max Drawdown $': lambda x: f'{x:,.0f}',
        'CAGR': lambda x: f'{x:.2%}',
    }
    for col, f in fmt.items():
        if col in formatted.columns:
            formatted[col] = formatted[col].map(lambda x: f(x) if pd.notnull(x) else '—')
    return formatted
