"""Tests for the performance metrics.

Each metric is checked against a closed-form value on a series whose
answer is known analytically, not against a previously recorded output.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from nq_breakout import (TRADING_DAYS_PER_YEAR, align_benchmark, compute_metrics,
                         format_metrics, legacy_equity_curve, metrics_table,
                         pad_to_calendar_days, trade_pnl_tstat)


def bdays(n: int, start: str = '2020-01-01') -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


# --- Sharpe ---------------------------------------------------------------

def test_sharpe_of_constant_growth_is_infinite_volatility_zero():
    """A perfectly smooth curve has zero return variance -> Sharpe is NaN."""
    equity = pd.Series(1e6 * 1.001 ** np.arange(100), index=bdays(100))
    m = compute_metrics(equity)
    assert m['Volatility'] == pytest.approx(0.0, abs=1e-12)
    assert math.isnan(m['Sharpe'])


def test_sharpe_matches_manual_formula():
    rng = np.random.default_rng(0)
    rets = rng.normal(0.0005, 0.01, 500)
    equity = pd.Series(1e6 * np.cumprod(1 + rets), index=bdays(500))
    r = equity.pct_change().dropna()
    expected = r.mean() / r.std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR)
    assert compute_metrics(equity)['Sharpe'] == pytest.approx(expected)


def test_sharpe_sign_follows_drift():
    rng = np.random.default_rng(1)
    up = pd.Series(1e6 * np.cumprod(1 + rng.normal(0.001, 0.005, 400)), index=bdays(400))
    down = pd.Series(1e6 * np.cumprod(1 + rng.normal(-0.001, 0.005, 400)), index=bdays(400))
    assert compute_metrics(up)['Sharpe'] > 0
    assert compute_metrics(down)['Sharpe'] < 0


def test_annualisation_factor_scales_as_sqrt():
    rng = np.random.default_rng(2)
    equity = pd.Series(1e6 * np.cumprod(1 + rng.normal(0.0004, 0.01, 300)), index=bdays(300))
    a = compute_metrics(equity, freq=252)['Sharpe']
    b = compute_metrics(equity, freq=63)['Sharpe']
    assert a / b == pytest.approx(math.sqrt(252 / 63))


# --- volatility -----------------------------------------------------------

def test_volatility_matches_manual_formula():
    rng = np.random.default_rng(4)
    equity = pd.Series(1e6 * np.cumprod(1 + rng.normal(0, 0.02, 400)), index=bdays(400))
    expected = equity.pct_change().dropna().std(ddof=1) * math.sqrt(252)
    assert compute_metrics(equity)['Volatility'] == pytest.approx(expected)


# --- drawdown -------------------------------------------------------------

def test_max_drawdown_on_hand_built_path():
    # 100 -> 120 -> 90 -> 150. Worst peak-to-trough is 120 -> 90 = -25%.
    equity = pd.Series([100.0, 120.0, 90.0, 150.0], index=bdays(4))
    m = compute_metrics(equity)
    assert m['Max Drawdown %'] == pytest.approx(-0.25)
    assert m['Max Drawdown $'] == pytest.approx(-30.0)


def test_drawdown_is_zero_for_monotonic_series():
    equity = pd.Series([100.0, 101.0, 102.0, 103.0], index=bdays(4))
    m = compute_metrics(equity)
    assert m['Max Drawdown %'] == pytest.approx(0.0)
    assert m['Max Drawdown $'] == pytest.approx(0.0)


# --- CAGR -----------------------------------------------------------------

def test_cagr_doubling_over_one_year():
    idx = pd.DatetimeIndex(['2020-01-01', '2021-01-01'])  # 366 days
    equity = pd.Series([1e6, 2e6], index=idx)
    # (2)^(365.25/366) - 1
    expected = 2 ** (1 / (366 / 365.25)) - 1
    assert compute_metrics(equity)['CAGR'] == pytest.approx(expected)


def test_cagr_flat_is_zero():
    idx = pd.DatetimeIndex(['2020-01-01', '2024-01-01'])
    assert compute_metrics(pd.Series([1e6, 1e6], index=idx))['CAGR'] == pytest.approx(0.0)


# --- edge cases -----------------------------------------------------------

def test_empty_and_single_point_series_return_nan():
    for equity in (pd.Series(dtype=float), pd.Series([1e6], index=bdays(1))):
        m = compute_metrics(equity)
        assert all(math.isnan(v) for v in m.values())


# --- t-stat ---------------------------------------------------------------

def test_tstat_matches_manual_formula():
    rng = np.random.default_rng(5)
    trades = pd.DataFrame({'pnl_per_contract': rng.normal(50, 500, 800)})
    s = trade_pnl_tstat(trades)
    expected = s['mean'] / (s['std'] / math.sqrt(s['n']))
    assert s['t_stat'] == pytest.approx(expected)
    assert s['n'] == 800


def test_tstat_of_zero_mean_sample_is_small():
    rng = np.random.default_rng(6)
    trades = pd.DataFrame({'pnl_per_contract': rng.normal(0, 100, 5000)})
    assert abs(trade_pnl_tstat(trades)['t_stat']) < 3.0


def test_tstat_needs_two_trades():
    s = trade_pnl_tstat(pd.DataFrame({'pnl_per_contract': [10.0]}))
    assert math.isnan(s['t_stat']) and s['n'] == 1


# --- calendar padding -----------------------------------------------------

def test_padding_inflates_nothing_but_deflates_vol_and_sharpe():
    rng = np.random.default_rng(7)
    idx = pd.bdate_range('2015-01-01', '2025-01-01')
    equity = pd.Series(1e6 * np.cumprod(1 + rng.normal(4e-4, 0.01, len(idx))), index=idx)
    clean, padded = compute_metrics(equity), compute_metrics(pad_to_calendar_days(equity))
    assert padded['Volatility'] < clean['Volatility']
    assert padded['Sharpe'] < clean['Sharpe']
    assert padded['Sharpe'] / clean['Sharpe'] == pytest.approx(math.sqrt(252 / 365), rel=0.05)
    # Terminal value and hence CAGR are untouched by padding.
    assert padded['CAGR'] == pytest.approx(clean['CAGR'], rel=1e-6)


def test_padding_preserves_max_drawdown():
    equity = pd.Series([100.0, 120.0, 90.0, 150.0], index=pd.bdate_range('2020-01-01', periods=4))
    assert (compute_metrics(pad_to_calendar_days(equity))['Max Drawdown %']
            == pytest.approx(compute_metrics(equity)['Max Drawdown %']))


# --- legacy equity curve --------------------------------------------------

def test_legacy_equity_curve_shape():
    trades = pd.DataFrame({
        'entry_time': pd.to_datetime(['2020-01-02 10:00', '2020-01-06 10:00']),
        'exit_time': pd.to_datetime(['2020-01-03 14:30', '2020-01-07 14:30']),
        'account_value': [1_010_000.0, 1_005_000.0],
    })
    eq = legacy_equity_curve(trades, 1_000_000.0)
    # Spans first entry date to last exit date, every calendar day.
    assert eq.index[0] == pd.Timestamp('2020-01-02')
    assert eq.index[-1] == pd.Timestamp('2020-01-07')
    assert (eq.index.to_series().diff().dropna() == pd.Timedelta(days=1)).all()
    # Seeded with initial capital before the first exit, then steps.
    assert eq.loc['2020-01-02'] == 1_000_000.0
    assert eq.loc['2020-01-03'] == 1_010_000.0
    assert eq.loc['2020-01-05'] == 1_010_000.0   # weekend carries forward
    assert eq.loc['2020-01-07'] == 1_005_000.0


def test_legacy_equity_curve_empty_trades():
    assert legacy_equity_curve(pd.DataFrame(), 1e6).empty


# --- benchmark alignment --------------------------------------------------

def test_align_benchmark_starts_at_initial_capital():
    qqq = pd.DataFrame({'date': pd.bdate_range('2020-01-01', periods=50),
                        'close': np.linspace(200, 300, 50)})
    idx = pd.bdate_range('2020-01-06', periods=10)
    bench = align_benchmark(qqq, idx, 1_000_000.0)
    assert bench.iloc[0] == pytest.approx(1_000_000.0)
    assert len(bench) == 10
    assert bench.is_monotonic_increasing


def test_align_benchmark_rejects_uncovered_period():
    qqq = pd.DataFrame({'date': pd.to_datetime(['2020-01-01']), 'close': [np.nan]})
    with pytest.raises(ValueError):
        align_benchmark(qqq, pd.bdate_range('2020-01-01', periods=3), 1e6)


# --- table helpers --------------------------------------------------------

def test_metrics_table_and_formatting():
    idx = bdays(300)
    rng = np.random.default_rng(8)
    a = pd.Series(1e6 * np.cumprod(1 + rng.normal(5e-4, 0.01, 300)), index=idx)
    b = pd.Series(1e6 * np.cumprod(1 + rng.normal(1e-4, 0.02, 300)), index=idx)
    table = metrics_table({'A': a, 'B': b})
    assert list(table.index) == ['A', 'B']
    assert set(table.columns) == {'Sharpe', 'Volatility', 'Max Drawdown %',
                                  'Max Drawdown $', 'CAGR'}
    formatted = format_metrics(table)
    assert formatted.loc['A', 'Volatility'].endswith('%')
    assert '%' not in formatted.loc['A', 'Sharpe']
    # Drawdown % is displayed as a positive magnitude.
    assert not formatted.loc['A', 'Max Drawdown %'].startswith('-')
