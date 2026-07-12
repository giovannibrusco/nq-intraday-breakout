"""Unit tests for the backtest engine, on hand-built synthetic 5-minute bars.

Every scenario is constructed so the expected fill and PnL can be computed
by hand: flat days at a known price, with specific bars overridden.
"""

import datetime as dt

import pandas as pd
import pytest

from backtest import Config, position_size, run_backtest

BASE = Config()  # risk 1%, entry_mode='stop'
# round turn: 2 * 2.50 commission + 2 * 1.5 ticks * $5 = $20
RT_COST = BASE.round_turn_cost


def flat_day(day: str, price: float) -> pd.DataFrame:
    """One session of 5-min bars 08:30-14:30, flat at `price` (range +-1)."""
    idx = pd.date_range(f'{day} 08:30', f'{day} 14:30', freq='5min')
    return pd.DataFrame({
        'date': idx,
        'open': float(price),
        'high': float(price) + 1,
        'low': float(price) - 1,
        'close': float(price),
        'totalvolume': 100,
    })


def set_bar(df: pd.DataFrame, ts: str, **values) -> None:
    mask = df['date'] == pd.Timestamp(ts)
    assert mask.any(), f'no bar at {ts}'
    for col, val in values.items():
        df.loc[mask, col] = float(val)


def set_from(df: pd.DataFrame, ts: str, price: float) -> None:
    """Flatten every bar strictly after `ts` at `price`."""
    mask = df['date'] > pd.Timestamp(ts)
    df.loc[mask, ['open', 'close']] = float(price)
    df.loc[mask, 'high'] = float(price) + 1
    df.loc[mask, 'low'] = float(price) - 1


def breakout_day(day: str = '2023-01-02') -> pd.DataFrame:
    """Flat at 15000 (window high 15001), upside breakout on the 10:05 bar."""
    df = flat_day(day, 15000)
    set_bar(df, f'{day} 10:05', open=15000.5, high=15010, low=15000, close=15008)
    set_from(df, f'{day} 10:05', 15005)
    return df


# ---------------------------------------------------------------------------
# Entry fills
# ---------------------------------------------------------------------------

def test_stop_mode_fills_at_breakout_level():
    result = run_backtest(breakout_day(), BASE)
    trade = result.trades.iloc[0]
    assert trade['direction'] == 'long'
    assert trade['entry_time'] == pd.Timestamp('2023-01-02 10:05')
    assert trade['entry_price'] == 15001.0  # window high, not the bar close


def test_close_mode_fills_at_bar_close():
    result = run_backtest(breakout_day(), Config(entry_mode='close'))
    assert result.trades.iloc[0]['entry_price'] == 15008.0


def test_stop_mode_entry_gap_fills_at_open():
    # Bar opens already above the window high: a resting stop fills at the open.
    day = '2023-01-02'
    df = flat_day(day, 15000)
    set_bar(df, f'{day} 10:05', open=15020, high=15030, low=15015, close=15025)
    set_from(df, f'{day} 10:05', 15020)
    result = run_backtest(df, BASE)
    assert result.trades.iloc[0]['entry_price'] == 15020.0


def test_ten_oclock_bar_is_tradable_and_outside_window():
    # The 10:00 bar must not feed the window (else its high would raise
    # window_high and suppress the trigger) and must be tradable.
    day = '2023-01-02'
    df = flat_day(day, 15000)
    set_bar(df, f'{day} 10:00', open=15000, high=15005, low=15000, close=15004)
    result = run_backtest(df, BASE)
    trade = result.trades.iloc[0]
    assert trade['entry_time'] == pd.Timestamp(f'{day} 10:00')
    assert trade['entry_price'] == 15001.0


def test_no_entry_on_last_bar_of_day():
    day = '2023-01-02'
    df = flat_day(day, 15000)
    set_bar(df, f'{day} 14:30', open=15000, high=15010, low=15000, close=15008)
    result = run_backtest(df, BASE)
    assert result.trades.empty


# ---------------------------------------------------------------------------
# Exit fills
# ---------------------------------------------------------------------------

def test_target_exit_and_costs():
    # Entry 15001, target 15201; a later bar trades through it.
    day = '2023-01-02'
    df = breakout_day(day)
    set_bar(df, f'{day} 11:00', open=15100, high=15250, low=15090, close=15200)
    set_from(df, f'{day} 11:00', 15200)
    result = run_backtest(df, BASE)
    trade = result.trades.iloc[0]
    assert trade['exit_reason'] == 'target'
    assert trade['exit_price'] == 15201.0
    assert trade['contracts'] == 5  # 1% of $1M / ($2000 risk per contract)
    assert trade['pnl_per_contract'] == pytest.approx(200 * 20 - RT_COST)  # 3980
    assert trade['pnl'] == pytest.approx(5 * (200 * 20 - RT_COST))


def test_overnight_gap_through_stop_fills_at_open():
    # Long held overnight; next session opens 200 pts below the stop.
    df = pd.concat([breakout_day('2023-01-02'), flat_day('2023-01-03', 14800)],
                   ignore_index=True)
    result = run_backtest(df, BASE)
    trade = result.trades.iloc[0]
    assert trade['exit_reason'] == 'stop'
    assert trade['exit_time'] == pd.Timestamp('2023-01-03 08:30')
    assert trade['exit_price'] == 14800.0  # the open, NOT the 14901 stop level
    assert trade['pnl_per_contract'] == pytest.approx((14800 - 15001) * 20 - RT_COST)


def test_stop_and_target_same_bar_assumes_stop_first():
    day = '2023-01-02'
    df = breakout_day(day)
    set_bar(df, f'{day} 11:00', open=15000, high=15250, low=14890, close=15000)
    result = run_backtest(df, BASE)
    trade = result.trades.iloc[0]
    assert trade['exit_reason'] == 'stop'
    assert trade['exit_price'] == 14901.0  # entry 15001 - 100 pts


def test_short_flattened_at_session_end():
    day = '2023-01-02'
    df = flat_day(day, 15000)
    set_bar(df, f'{day} 10:05', open=14999.5, high=15000, low=14990, close=14995)
    result = run_backtest(df, BASE)
    trade = result.trades.iloc[0]
    assert trade['direction'] == 'short'
    assert trade['entry_price'] == 14999.0  # window low
    assert trade['exit_reason'] == 'eod_short'
    assert trade['exit_time'] == pd.Timestamp(f'{day} 14:30')
    assert trade['exit_price'] == 15000.0
    assert trade['pnl_per_contract'] == pytest.approx((14999 - 15000) * 20 - RT_COST)


def test_max_hold_exits_at_session_close_of_fifth_day():
    days = ['2023-01-02', '2023-01-03', '2023-01-04', '2023-01-05', '2023-01-06']
    frames = [breakout_day(days[0])] + [flat_day(d, 15005) for d in days[1:]]
    result = run_backtest(pd.concat(frames, ignore_index=True), BASE)
    trade = result.trades.iloc[0]
    assert trade['exit_reason'] == 'max_hold'
    assert trade['days_in_trade'] == 5
    # At the 14:30 close of day 5, not on its first (pre-market) bar.
    assert trade['exit_time'] == pd.Timestamp('2023-01-06 14:30')


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------

def test_position_size_risk_budget():
    assert position_size(1_000_000, 100, BASE) == 5


def test_position_size_margin_cap_binds():
    cfg = Config(risk_pct=0.02, margin_per_contract=200_000)
    # risk allows 10, margin allows 1e6 / (200k * 1.15) = 4.34 -> 4
    assert position_size(1_000_000, 100, cfg) == 4


def test_position_size_returns_zero_when_unaffordable():
    assert position_size(20_000, 100, BASE) == 0
    assert position_size(-5_000, 100, BASE) == 0


def test_engine_skips_trade_when_size_is_zero():
    result = run_backtest(breakout_day(), Config(initial_capital=20_000))
    assert result.trades.empty


# ---------------------------------------------------------------------------
# Mark-to-market equity
# ---------------------------------------------------------------------------

def test_open_position_is_marked_at_session_close():
    df = pd.concat([breakout_day('2023-01-02'), flat_day('2023-01-03', 15050)],
                   ignore_index=True)
    result = run_backtest(df, BASE)
    # Day 1: long 5 contracts from 15001, session close 15005.
    day1 = result.equity.loc[pd.Timestamp('2023-01-02')]
    assert day1 == pytest.approx(1_000_000 + (15005 - 15001) * 20 * 5)
    # Day 2: force-closed at the final bar close 15050 (realized, net of costs).
    trade = result.trades.iloc[0]
    assert trade['exit_reason'] == 'force_close'
    day2 = result.equity.loc[pd.Timestamp('2023-01-03')]
    assert day2 == pytest.approx(1_000_000 + ((15050 - 15001) * 20 - RT_COST) * 5)


def test_equity_indexed_by_trading_days():
    df = pd.concat([breakout_day('2023-01-02'), flat_day('2023-01-03', 15005)],
                   ignore_index=True)
    result = run_backtest(df, BASE)
    assert list(result.equity.index) == [pd.Timestamp('2023-01-02'),
                                         pd.Timestamp('2023-01-03')]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_round_turn_cost():
    assert RT_COST == pytest.approx(2 * 2.50 + 2 * 1.5 * 5.0)  # $20


def test_invalid_entry_mode_rejected():
    with pytest.raises(ValueError):
        Config(entry_mode='limit')
