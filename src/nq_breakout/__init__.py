"""Opening-range breakout backtest on Nasdaq-100 futures.

Layout:
- :mod:`nq_breakout.data`        loading and 1-min -> 5-min resampling
- :mod:`nq_breakout.backtest`    the event-driven engine, entries, exits, sizing, costs
- :mod:`nq_breakout.metrics`     Sharpe, CAGR, drawdown, volatility, t-stat
- :mod:`nq_breakout.diagnostics` feed sanity checks (timezone, rolls, gaps)
- :mod:`nq_breakout.plots`       matplotlib figures (needs the ``notebook`` extra)

The engine carries a :class:`~nq_breakout.backtest.SimFlags` set. The
defaults are the corrected conventions; ``LEGACY_FLAGS`` reproduces the
conventions of the original notebook. ``docs/bias-audit.md`` explains why
both exist and what separates them.
"""

from .backtest import (LEGACY_CONFIG_KWARGS, LEGACY_FLAGS, BacktestResult,
                       Config, SimFlags, position_size, replace, run_backtest)
from .data import load_nq_1min, load_qqq, resample_5min
from .diagnostics import run_data_diagnostics
from .metrics import (TRADING_DAYS_PER_YEAR, align_benchmark, compute_metrics,
                      format_metrics, legacy_equity_curve, metrics_table,
                      pad_to_calendar_days, trade_pnl_tstat)

__version__ = '0.2.0'

__all__ = [
    'Config', 'SimFlags', 'LEGACY_FLAGS', 'LEGACY_CONFIG_KWARGS',
    'BacktestResult', 'run_backtest', 'position_size', 'replace',
    'load_nq_1min', 'load_qqq', 'resample_5min', 'run_data_diagnostics',
    'compute_metrics', 'trade_pnl_tstat', 'align_benchmark', 'metrics_table',
    'format_metrics', 'pad_to_calendar_days', 'legacy_equity_curve',
    'TRADING_DAYS_PER_YEAR',
    '__version__',
]
