"""End-to-end backtest on synthetic data, with no external files.

This is the test that proves the whole pipeline runs: generate bars, run
the engine, build an equity curve, compute metrics, and check the result
is internally coherent (accounting identities, not recorded outputs).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nq_breakout import (LEGACY_CONFIG_KWARGS, LEGACY_FLAGS, Config,
                         align_benchmark, compute_metrics, metrics_table,
                         replace, run_backtest, trade_pnl_tstat)

from .synthetic import make_globex_feed


@pytest.fixture(scope='module')
def feed():
    return make_globex_feed(n_days=400, seed=42)


@pytest.fixture(scope='module')
def result(feed):
    return run_backtest(feed, Config())


def test_pipeline_produces_trades_and_equity(result):
    assert len(result.trades) > 100
    assert len(result.equity) > 300
    assert result.equity.index.is_monotonic_increasing
    assert result.equity.notna().all()


def test_equity_reconciles_with_realised_pnl(result):
    """Final equity == initial capital + sum of trade PnL, to the cent."""
    expected = result.config.initial_capital + result.trades['pnl'].sum()
    assert result.equity.iloc[-1] == pytest.approx(expected, abs=1e-6)


def test_each_trade_pnl_matches_price_difference_and_costs(result):
    """Recompute every trade's PnL from prices; the engine must agree."""
    t = result.trades
    sign = np.where(t['direction'] == 'long', 1.0, -1.0)
    gross = sign * (t['exit_price'] - t['entry_price']) * result.config.point_value
    expected = (gross - result.config.round_turn_cost) * t['contracts']
    assert np.allclose(t['pnl'], expected, atol=1e-6)


def test_no_overlapping_trades(result):
    """The engine holds at most one position: exits must precede entries."""
    t = result.trades.sort_values('entry_time')
    assert (t['exit_time'].values[:-1] <= t['entry_time'].values[1:]).all()


def test_exit_always_after_entry(result):
    assert (result.trades['exit_time'] > result.trades['entry_time']).all()


def test_shorts_never_survive_into_the_next_trading_day(result):
    shorts = result.trades[result.trades['direction'] == 'short']
    assert len(shorts) > 0
    assert (shorts['days_in_trade'] == 1).all()


def test_longs_respect_max_hold(result):
    longs = result.trades[result.trades['direction'] == 'long']
    assert longs['days_in_trade'].max() <= result.config.max_long_hold_days


def test_max_two_trades_per_calendar_day(result):
    per_day = result.trades.groupby(result.trades['entry_time'].dt.date).size()
    assert per_day.max() <= Config().max_trades_per_day


def test_exit_reasons_are_from_the_known_set(result):
    assert set(result.trades['exit_reason']) <= {
        'stop', 'target', 'eod_short', 'max_hold', 'force_close'}


def test_position_size_tracks_the_risk_budget(result):
    """Contracts == floor(1% of equity / $2,000), subject to the margin cap."""
    t = result.trades
    cfg = result.config
    by_risk = (cfg.risk_pct * t['account_value_entry']) / cfg.stop_dollars
    by_margin = t['account_value_entry'] / (cfg.margin_per_contract * (1 + cfg.margin_buffer))
    assert (t['contracts'] == np.minimum(by_risk, by_margin).astype(int)).all()


def test_metrics_are_finite_and_signed_correctly(result):
    m = compute_metrics(result.equity)
    assert np.isfinite(m['Sharpe'])
    assert m['Volatility'] > 0
    assert m['Max Drawdown %'] <= 0
    assert m['Max Drawdown $'] <= 0


def test_doubling_risk_doubles_size_not_per_contract_edge(feed):
    """Sizing is linear, so PnL/contract is invariant to the risk budget.

    This is a mechanical identity of the simulator, worth pinning down: it
    is the reason the 1% and 2% regimes report the same PnL/contract and
    the same t-stat, and it does NOT mean size is free of market impact.
    """
    one = run_backtest(feed, Config(risk_pct=0.01))
    two = run_backtest(feed, Config(risk_pct=0.02))
    assert len(one.trades) == len(two.trades)
    assert (trade_pnl_tstat(one.trades)['mean']
            == pytest.approx(trade_pnl_tstat(two.trades)['mean']))
    assert (two.trades['contracts'] >= one.trades['contracts']).all()


def test_benchmark_comparison_runs(result):
    days = result.equity.index
    qqq = pd.DataFrame({'date': days,
                        'close': np.linspace(200, 400, len(days))})
    bench = align_benchmark(qqq, days, result.config.initial_capital)
    table = metrics_table({'strategy': result.equity, 'benchmark': bench})
    assert table.shape == (2, 5)
    assert table.notna().all().all()


def test_legacy_and_corrected_paths_both_run_on_the_same_bars(feed):
    """Both published result sets must remain regenerable from one input."""
    corrected = run_backtest(feed, Config())
    legacy = run_backtest(feed, replace(Config(), **LEGACY_CONFIG_KWARGS), LEGACY_FLAGS)
    assert len(corrected.trades) > 0 and len(legacy.trades) > 0
    # They must genuinely differ, else the flags are not wired up.
    assert len(corrected.trades) != len(legacy.trades) or not np.allclose(
        corrected.trades['pnl'].sum(), legacy.trades['pnl'].sum())


def test_engine_rejects_empty_input():
    with pytest.raises(ValueError, match='No bars'):
        run_backtest(pd.DataFrame({'date': pd.to_datetime([]), 'open': [], 'high': [],
                                   'low': [], 'close': [], 'totalvolume': []}), Config())
