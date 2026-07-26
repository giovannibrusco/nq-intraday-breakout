"""Prove the legacy code path reproduces the ORIGINAL notebook engine.

``reference_engine`` below is a deliberately literal transcription of the
backtest loop in ``NQ_breakout.ipynb`` as it stands on ``main`` — the code
that produced the published Sharpe 0.95 / $3,287,392 / 1,728-trade result
set. It is intentionally un-refactored, globals and all, so that it can be
diffed against the notebook by eye.

The tests then assert that ``run_backtest(..., LEGACY_FLAGS)`` produces a
byte-identical trade list. That equivalence is what licenses the claim
that the packaged engine can still reproduce the published numbers; it
cannot be shown directly here because the market data is not in the repo.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from nq_breakout import LEGACY_CONFIG_KWARGS, LEGACY_FLAGS, Config, replace, run_backtest

from .synthetic import make_globex_feed

# --- constants exactly as in the original notebook -------------------------
POINT_VALUE = 20
STOP_DOLLARS = 2000
TARGET_DOLLARS = 4000
SLIPPAGE_PER_SIDE_TICKS = 1.5
SLIPPAGE_PER_TICK_VALUE = 5.0
STOP_POINTS = STOP_DOLLARS / POINT_VALUE
TARGET_POINTS = TARGET_DOLLARS / POINT_VALUE
INITIAL_CAPITAL = 1_000_000
MARGIN_PER_CONTRACT = 23282.57
MARGIN_BUFFER = 0.15
FEE_PER_LEG = 0.85
TRADE_START = dt.time(10, 0)
TRADE_END = dt.time(14, 30)
REGULAR_START = dt.time(9, 30)
REGULAR_END = dt.time(16, 0)
WINDOW_START = dt.time(8, 30)
WINDOW_END = dt.time(10, 0)
MAX_TRADES_PER_DAY = 2
MAX_LONG_HOLD_DAYS = 5


def reference_engine(df_full: pd.DataFrame, risk_pct: float = 0.01) -> pd.DataFrame:
    """Literal transcription of the original notebook loop."""

    def position_size(equity, stop_pts):
        stop_pts = max(stop_pts, 1e-9)
        n_risk = (risk_pct * equity) / (stop_pts * POINT_VALUE)
        n_margin = equity / (MARGIN_PER_CONTRACT * (1 + MARGIN_BUFFER))
        return max(1, int(min(n_risk, n_margin)))

    df_full = df_full.copy()
    df_full['day'] = df_full['date'].dt.date
    df_full['time'] = df_full['date'].dt.time

    df_regular = df_full[(df_full['time'] >= REGULAR_START) & (df_full['time'] <= REGULAR_END)]
    daily_summary_regular = df_regular.groupby('day').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    df = df_full[(df_full['time'] >= WINDOW_START) & (df_full['time'] <= TRADE_END)].copy()
    day_to_index = {day: idx for idx, day in enumerate(daily_summary_regular.index)}

    trades, open_trade = [], None
    account_value = INITIAL_CAPITAL
    current_day = prev_day = None
    daily_trade_count = 0
    window_high = window_low = None
    window_ready = False
    current_stats = {'open': None, 'high': None, 'low': None}
    filters_today_pass = False
    prev_row_timestamp = prev_row_close = None

    def ensure_day_index(day):
        if day not in day_to_index:
            day_to_index[day] = len(day_to_index)
        return day_to_index[day]

    def close_trade(exit_time, exit_price, exit_reason, exit_day):
        nonlocal open_trade, account_value
        days_in_trade = ensure_day_index(exit_day) - ensure_day_index(open_trade['entry_day']) + 1
        contracts = open_trade['contracts']
        if open_trade['direction'] == 'long':
            pnl_per_contract = (exit_price - open_trade['entry_price']) * POINT_VALUE
        else:
            pnl_per_contract = (open_trade['entry_price'] - exit_price) * POINT_VALUE
        pnl_per_contract -= (FEE_PER_LEG * 2 + SLIPPAGE_PER_SIDE_TICKS * SLIPPAGE_PER_TICK_VALUE)
        pnl = pnl_per_contract * contracts
        account_value += pnl
        trades.append({
            'direction': open_trade['direction'], 'entry_time': open_trade['entry_time'],
            'exit_time': exit_time, 'entry_price': open_trade['entry_price'],
            'exit_price': exit_price, 'contracts': contracts,
            'pnl_per_contract': pnl_per_contract, 'pnl': pnl,
            'exit_reason': exit_reason, 'days_in_trade': days_in_trade,
        })
        open_trade = None

    for row in df.itertuples(index=False):
        row_timestamp = pd.Timestamp(row.date)
        row_day = row_timestamp.date()
        row_time = row_timestamp.time()
        ensure_day_index(row_day)

        if current_day is None:
            current_day = row_day
        if row_day != current_day:
            if open_trade is not None and open_trade['direction'] == 'short':
                close_trade(prev_row_timestamp, prev_row_close, 'eod_short', current_day)
            prev_day, current_day = current_day, row_day
            daily_trade_count = 0
            window_high = window_low = None
            window_ready = False
            current_stats = {'open': None, 'high': None, 'low': None}
            filters_today_pass = False

        if WINDOW_START <= row_time <= WINDOW_END:
            window_high = row.high if window_high is None else max(window_high, row.high)
            window_low = row.low if window_low is None else min(window_low, row.low)
        if not window_ready and row_time >= WINDOW_END and window_high is not None:
            window_ready = True

        if REGULAR_START <= row_time <= REGULAR_END:
            if current_stats['open'] is None:
                current_stats['open'] = row.open
            current_stats['high'] = row.high if current_stats['high'] is None else max(current_stats['high'], row.high)
            current_stats['low'] = row.low if current_stats['low'] is None else min(current_stats['low'], row.low)

        if window_ready:
            prev_summary = daily_summary_regular.loc[prev_day] if prev_day in daily_summary_regular.index else None
            if prev_summary is not None and None not in (current_stats['open'], current_stats['high'], current_stats['low']):
                # cond1..cond4 were all hard-coded True in the notebook.
                filters_today_pass = True
            else:
                filters_today_pass = False

        if open_trade is not None:
            exit_price = exit_reason = None
            days_in_trade = ensure_day_index(row_day) - ensure_day_index(open_trade['entry_day']) + 1
            if open_trade['direction'] == 'long':
                if row.low <= open_trade['stop']:
                    exit_price, exit_reason = open_trade['stop'], 'stop'
                elif row.high >= open_trade['target']:
                    exit_price, exit_reason = open_trade['target'], 'target'
                elif days_in_trade >= MAX_LONG_HOLD_DAYS:
                    exit_price, exit_reason = row.close, 'max_hold'
            else:
                if row.high >= open_trade['stop']:
                    exit_price, exit_reason = open_trade['stop'], 'stop'
                elif row.low <= open_trade['target']:
                    exit_price, exit_reason = open_trade['target'], 'target'
                elif row_time >= TRADE_END:
                    exit_price, exit_reason = row.close, 'eod_short'
            if exit_price is not None:
                close_trade(row_timestamp, exit_price, exit_reason, row_day)
                prev_row_timestamp, prev_row_close = row_timestamp, row.close
                continue

        if (open_trade is None and window_ready and filters_today_pass
                and window_high is not None and window_low is not None
                and daily_trade_count < MAX_TRADES_PER_DAY
                and TRADE_START <= row_time <= TRADE_END):
            direction = entry_price = None
            if row.high > window_high:
                direction, entry_price = 'long', row.close
                stop, target = entry_price - STOP_POINTS, entry_price + TARGET_POINTS
            elif row.low < window_low:
                direction, entry_price = 'short', row.close
                stop, target = entry_price + STOP_POINTS, entry_price - TARGET_POINTS
            if direction is not None:
                open_trade = {
                    'direction': direction, 'entry_time': row_timestamp,
                    'entry_price': entry_price, 'stop': stop, 'target': target,
                    'entry_day': row_day, 'contracts': int(position_size(account_value, STOP_POINTS)),
                }
                daily_trade_count += 1

        prev_row_timestamp, prev_row_close = row_timestamp, row.close

    if open_trade is not None:
        last = df.iloc[-1]
        close_trade(pd.Timestamp(last['date']), last['close'], 'force_close',
                    pd.Timestamp(last['date']).date())

    return pd.DataFrame(trades)


COMPARE = ['direction', 'entry_time', 'exit_time', 'entry_price', 'exit_price',
           'contracts', 'pnl_per_contract', 'pnl', 'exit_reason', 'days_in_trade']


@pytest.fixture(scope='module')
def feed():
    return make_globex_feed(n_days=300, seed=11)


@pytest.mark.parametrize('risk_pct', [0.01, 0.02])
def test_legacy_flags_match_original_notebook_engine(feed, risk_pct):
    """The packaged legacy path == the original notebook, trade for trade."""
    expected = reference_engine(feed, risk_pct=risk_pct)
    cfg = replace(Config(risk_pct=risk_pct), **LEGACY_CONFIG_KWARGS)
    got = run_backtest(feed, cfg, LEGACY_FLAGS).trades

    assert len(got) == len(expected), f'{len(got)} trades vs reference {len(expected)}'
    pd.testing.assert_frame_equal(
        got[COMPARE].reset_index(drop=True),
        expected[COMPARE].reset_index(drop=True),
        check_dtype=False, atol=1e-9,
    )


def test_legacy_path_generates_trades(feed):
    """Guard against the equivalence test passing on two empty frames."""
    cfg = replace(Config(), **LEGACY_CONFIG_KWARGS)
    assert len(run_backtest(feed, cfg, LEGACY_FLAGS).trades) > 50


def test_legacy_round_turn_cost_is_9_20():
    """The original charged slippage on ONE side despite documenting two."""
    cfg = replace(Config(), **LEGACY_CONFIG_KWARGS)
    assert cfg.round_turn_cost == pytest.approx(0.85 * 2 + 1.5 * 5.0)
    assert cfg.round_turn_cost == pytest.approx(9.20)


def test_corrected_round_turn_cost_is_20_00():
    assert Config().round_turn_cost == pytest.approx(2.50 * 2 + 2 * 1.5 * 5.0)
    assert Config().round_turn_cost == pytest.approx(20.00)


def test_cost_delta_per_contract_is_exactly_10_80(feed):
    """Cost model alone moves PnL/contract by a constant $10.80."""
    legacy_cfg = replace(Config(), **LEGACY_CONFIG_KWARGS)
    costed_cfg = replace(legacy_cfg, commission_per_side=2.50, slippage_sides=2)
    a = run_backtest(feed, legacy_cfg, LEGACY_FLAGS).trades
    b = run_backtest(feed, costed_cfg, LEGACY_FLAGS).trades
    assert len(a) == len(b)
    delta = a['pnl_per_contract'].mean() - b['pnl_per_contract'].mean()
    assert delta == pytest.approx(10.80, abs=1e-9)


def test_calendar_padding_deflates_sharpe_by_sqrt_252_over_365():
    """The padding bias, isolated on a series with no strategy involved."""
    from nq_breakout import compute_metrics, pad_to_calendar_days

    rng = np.random.default_rng(3)
    days = pd.bdate_range('2015-01-01', '2025-01-01')
    equity = pd.Series(1e6 * np.cumprod(1 + rng.normal(3e-4, 0.01, len(days))), index=days)

    clean = compute_metrics(equity)
    padded = compute_metrics(pad_to_calendar_days(equity))
    ratio = padded['Sharpe'] / clean['Sharpe']
    assert ratio == pytest.approx(np.sqrt(252 / 365), rel=0.04)
