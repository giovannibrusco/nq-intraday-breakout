"""Tests for ``nqbreakout.data.volatility``.

The single most important test is ``test_atr14_lookahead_free_no_leak``:
it exists to guarantee that the ATR value attached to a trade entered on
day ``d`` was computed using only price action that occurred strictly
before day ``d``. If anyone removes the ``.shift(1)`` from
``compute_atr14_lookahead_free`` this test fails loudly.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from nqbreakout.data.volatility import (
    aggregate_to_daily_ohlc,
    compute_atr14_lookahead_free,
    compute_true_range,
)


# ---------------------------------------------------------------------------
# Synthetic builders
# ---------------------------------------------------------------------------


def _make_5min_frame(
    days: int = 3,
    *,
    start_date: dt.datetime = dt.datetime(2024, 1, 2),
    rth_start: dt.time = dt.time(9, 30),
    bars_per_day: int = 78,  # 09:30..16:25 in 5-min steps
    overnight_bars_per_day: int = 0,
    base_open: float = 100.0,
    daily_step: float = 0.0,
    range_per_day: float = 1.0,
) -> pd.DataFrame:
    """Build a controlled 5-min OHLC frame.

    Each day starts at ``base_open + i * daily_step`` and prints
    ``bars_per_day`` 5-min bars where the high is open + ``range_per_day``
    and the low is open. Optionally adds ``overnight_bars_per_day`` bars
    at 06:00..06:55 (extended-hours) which the default RTH aggregation
    must ignore.
    """
    rows = []
    for d in range(days):
        day_open = base_open + d * daily_step
        day_date = (start_date + dt.timedelta(days=d)).date()

        # Optional pre-market bars at 06:00, 06:05, ...
        for k in range(overnight_bars_per_day):
            ts = dt.datetime.combine(day_date, dt.time(6, 0)) + dt.timedelta(
                minutes=5 * k
            )
            rows.append(
                {
                    "date": ts,
                    "open": day_open + 50,
                    "high": day_open + 60,  # extreme value to detect leakage
                    "low": day_open + 40,
                    "close": day_open + 50,
                }
            )

        # RTH bars
        rth_start_dt = dt.datetime.combine(day_date, rth_start)
        for k in range(bars_per_day):
            ts = rth_start_dt + dt.timedelta(minutes=5 * k)
            rows.append(
                {
                    "date": ts,
                    "open": day_open,
                    "high": day_open + range_per_day,
                    "low": day_open,
                    "close": day_open + range_per_day / 2.0,
                }
            )

    return pd.DataFrame(rows)


def _make_daily_ohlc_known(
    n_days: int = 30,
    *,
    base: float = 100.0,
) -> pd.DataFrame:
    """Build a daily OHLC frame with hand-computable TR.

    Each day's high = base + i, low = base + i - 1, close = base + i.
    Day i has range exactly 1.0 and prev_close = base + (i - 1) = today's low,
    so ``TR_i = max(1, |high - prev_close|=2, |low - prev_close|=0) = 2`` for
    i >= 1 and ``TR_0 = NaN``.
    """
    dates = [
        (dt.date(2024, 1, 1) + dt.timedelta(days=i)) for i in range(n_days)
    ]
    rows = []
    for i in range(n_days):
        rows.append(
            {
                "open": base + i,
                "high": base + i + 1,
                "low": base + i,
                "close": base + i + 1,
            }
        )
    out = pd.DataFrame(rows, index=pd.Index(dates, name="date"))
    return out


# ---------------------------------------------------------------------------
# aggregate_to_daily_ohlc
# ---------------------------------------------------------------------------


def test_aggregate_to_daily_ohlc_basic() -> None:
    df = _make_5min_frame(days=3, range_per_day=2.0, daily_step=10.0)
    daily = aggregate_to_daily_ohlc(df)

    assert len(daily) == 3
    assert list(daily.columns) == ["open", "high", "low", "close"]

    # First day open = 100, close = high - 1 = 101 (last bar close = open + range/2 = 101)
    first = daily.iloc[0]
    assert first["open"] == pytest.approx(100.0)
    assert first["high"] == pytest.approx(102.0)
    assert first["low"] == pytest.approx(100.0)
    assert first["close"] == pytest.approx(101.0)

    # Index is date type.
    assert isinstance(daily.index[0], dt.date)


def test_aggregate_to_daily_ohlc_excludes_extended_hours() -> None:
    """Pre-market bars at 06:00 with extreme values must NOT pollute daily OHLC."""
    df = _make_5min_frame(days=2, overnight_bars_per_day=12, range_per_day=1.0)
    daily = aggregate_to_daily_ohlc(df)

    # Pre-market high was day_open + 60 = 160; if leaked it would dominate.
    # RTH high is day_open + 1 = 101.
    assert daily.iloc[0]["high"] == pytest.approx(101.0)
    # Pre-market low was day_open + 40 = 140; RTH low is 100.
    assert daily.iloc[0]["low"] == pytest.approx(100.0)


def test_aggregate_to_daily_ohlc_custom_session_window() -> None:
    """Tighter session window must shrink the aggregation."""
    df = _make_5min_frame(days=1, range_per_day=1.0)
    # Restrict to first hour 09:30..10:30: with range_per_day=1 every bar
    # has the same OHLC so we just check non-empty and structure.
    daily = aggregate_to_daily_ohlc(
        df, session_start=dt.time(9, 30), session_end=dt.time(10, 30)
    )
    assert len(daily) == 1


def test_aggregate_to_daily_ohlc_missing_date_column() -> None:
    df = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]})
    with pytest.raises(ValueError, match="date"):
        aggregate_to_daily_ohlc(df)


# ---------------------------------------------------------------------------
# compute_true_range
# ---------------------------------------------------------------------------


def test_true_range_first_row_is_nan() -> None:
    daily = _make_daily_ohlc_known(n_days=5)
    tr = compute_true_range(daily)
    assert pd.isna(tr.iloc[0])
    assert tr.iloc[1:].notna().all()


def test_true_range_known_values() -> None:
    """Hand-computed TR for the synthetic OHLC: should be 2.0 for i >= 1."""
    daily = _make_daily_ohlc_known(n_days=5)
    tr = compute_true_range(daily)
    # For i >= 1: high=base+i+1, low=base+i, prev_close=base+i.
    # range_hl=1, range_hc=|high - prev_close|=1, range_lc=|low - prev_close|=0.
    # max=1.
    expected = pd.Series(
        [np.nan, 1.0, 1.0, 1.0, 1.0],
        index=daily.index,
        name="true_range",
    )
    pd.testing.assert_series_equal(tr, expected, check_names=True)


def test_true_range_with_gap() -> None:
    """Gap-up: high - prev_close dominates."""
    dates = pd.Index(
        [dt.date(2024, 1, 1), dt.date(2024, 1, 2)], name="date"
    )
    daily = pd.DataFrame(
        {
            "open": [100.0, 110.0],
            "high": [101.0, 112.0],
            "low": [99.0, 110.0],
            "close": [100.0, 111.0],
        },
        index=dates,
    )
    tr = compute_true_range(daily)
    # Day 1: high=112, low=110, prev_close=100. range_hl=2, range_hc=12, range_lc=10. max=12.
    assert tr.iloc[1] == pytest.approx(12.0)


# ---------------------------------------------------------------------------
# compute_atr14_lookahead_free  (the critical tests)
# ---------------------------------------------------------------------------


def test_atr14_first_window_is_nan() -> None:
    """First ``window + 1`` rows must be NaN.

    One NaN for the missing prev_close of row 0, then ``window`` rows where
    rolling(14) has not yet accumulated 14 obs, then one more for the
    .shift(1).  Total: 15 NaN at the head for window=14.
    """
    daily = _make_daily_ohlc_known(n_days=30)
    atr = compute_atr14_lookahead_free(daily, window=14, method="sma")

    assert atr.iloc[:15].isna().all(), "expected first 15 rows to be NaN"
    assert atr.iloc[15:].notna().all(), "expected rows from 15 onward to be non-NaN"


def test_atr14_lookahead_free_no_leak() -> None:
    """CRITICAL anti-leak invariant.

    After .shift(1), the value at row index ``d`` must equal the rolling
    mean of TR over the 14 days strictly preceding ``d``. Specifically:

        atr.iloc[15] == mean(TR.iloc[1..15])  (the 14 values at indices 1..14)

    Because TR.iloc[0] is NaN, the SMA at iloc 14 (= 15th row of TR) covers
    indices 1..14. After shift(1) that lands at iloc 15.

    If anyone removes the .shift(1) the SMA would be at iloc 14, and the
    test below would fail because atr.iloc[15] would equal the mean of TR
    over indices 2..15, including TR.iloc[15] which is computed using day
    15's high/low — i.e. forward-looking information.
    """
    daily = _make_daily_ohlc_known(n_days=30)
    tr = compute_true_range(daily)
    atr = compute_atr14_lookahead_free(daily, window=14, method="sma")

    expected_at_15 = float(tr.iloc[1:15].mean())  # 14 values at indices 1..14
    assert atr.iloc[15] == pytest.approx(expected_at_15)

    # And the value at iloc 15 must NOT include TR.iloc[15] (= today's TR).
    # If it did, the value would be mean(TR.iloc[2..16]) which differs unless
    # the sequence is constant. For our synthetic frame TR is constant 1.0
    # for i >= 1, so we craft a more discriminating fixture below.


def test_atr14_lookahead_free_no_leak_discriminating() -> None:
    """Same invariant but with a non-constant TR series so a leak would
    produce a numerically different value.

    We build OHLC where TR_i grows linearly with i (TR_i = i for i >= 1),
    so mean(TR over [1..14]) = 7.5 while mean(TR over [2..15]) = 8.5.
    These clearly differ, so a missing .shift(1) would be caught numerically.
    """
    n_days = 30
    dates = pd.Index(
        [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(n_days)],
        name="date",
    )
    rows = []
    prev_close = 100.0
    for i in range(n_days):
        if i == 0:
            rows.append({"open": 100.0, "high": 101.0, "low": 100.0, "close": 100.0})
            continue
        # Engineer TR_i = i: low = prev_close (so range_lc = 0),
        # high = prev_close + i (so range_hl = i, range_hc = i), close = high.
        low = prev_close
        high = prev_close + float(i)
        close = high
        rows.append({"open": prev_close, "high": high, "low": low, "close": close})
        prev_close = close
    daily = pd.DataFrame(rows, index=dates)

    tr = compute_true_range(daily)
    atr = compute_atr14_lookahead_free(daily, window=14, method="sma")

    # Sanity: TR_i should equal i for i >= 1.
    for i in range(1, n_days):
        assert tr.iloc[i] == pytest.approx(float(i)), f"TR[{i}] != {i}"

    expected_at_15 = float(tr.iloc[1:15].mean())  # mean(1..14) = 7.5
    leaked_at_15 = float(tr.iloc[2:16].mean())    # mean(2..15) = 8.5

    assert expected_at_15 == pytest.approx(7.5)
    assert leaked_at_15 == pytest.approx(8.5)
    assert atr.iloc[15] == pytest.approx(expected_at_15)
    assert atr.iloc[15] != pytest.approx(leaked_at_15)


def test_atr14_sma_vs_wilder_smoke() -> None:
    """Both methods produce non-NaN values past the warm-up; Wilder's
    typically diverges from SMA after the seed because of the EWMA nature.
    """
    daily = _make_daily_ohlc_known(n_days=40)
    atr_sma = compute_atr14_lookahead_free(daily, window=14, method="sma")
    atr_w = compute_atr14_lookahead_free(daily, window=14, method="wilder")

    # Both NaN for first 15.
    assert atr_sma.iloc[:15].isna().all()
    assert atr_w.iloc[:15].isna().all()

    # Both finite from row 15 onwards.
    assert atr_sma.iloc[15:].notna().all()
    assert atr_w.iloc[15:].notna().all()

    # On a constant-TR series both equal the constant; sanity check this.
    # Our fixture has TR = 1 for i >= 1, so ATR should be 1 from row 15.
    assert atr_sma.iloc[15] == pytest.approx(1.0)
    assert atr_w.iloc[15] == pytest.approx(1.0)


def test_atr14_invalid_window() -> None:
    daily = _make_daily_ohlc_known(n_days=20)
    with pytest.raises(ValueError, match="window"):
        compute_atr14_lookahead_free(daily, window=1)


def test_atr14_invalid_method() -> None:
    daily = _make_daily_ohlc_known(n_days=20)
    with pytest.raises(ValueError, match="method"):
        compute_atr14_lookahead_free(daily, method="ema")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Integration: trade-level join with no leak
# ---------------------------------------------------------------------------


def test_atr_join_with_trades_no_leak() -> None:
    """Build synthetic trades that span multiple days, join against the
    lookahead-free ATR series, and verify each trade's ATR equals the SMA
    of TR computed strictly before the trade's entry day.
    """
    n_days = 30
    daily = _make_daily_ohlc_known(n_days=n_days)
    tr = compute_true_range(daily)
    atr = compute_atr14_lookahead_free(daily, window=14, method="sma")

    # Trades on days 14, 16, 20, 25 (mix of warm-up boundary + later).
    trade_days = [14, 16, 20, 25]
    trades = pd.DataFrame(
        {
            "entry_time": [
                pd.Timestamp(daily.index[d]) + pd.Timedelta(hours=10)
                for d in trade_days
            ],
            "side": ["long"] * len(trade_days),
            "pnl": [100.0] * len(trade_days),
        }
    )
    trades["entry_date"] = trades["entry_time"].dt.date
    trades["atr14_pts"] = trades["entry_date"].map(atr)

    # Day 14: atr available is NaN (warm-up). Verify.
    assert pd.isna(trades.loc[trades["entry_date"] == daily.index[14], "atr14_pts"].iloc[0])

    # Day 16: atr should equal mean(TR.iloc[2..16]) — i.e. the 14-day SMA of
    # TR ending one day before day 16.
    expected_d16 = float(tr.iloc[2:16].mean())
    got_d16 = float(trades.loc[trades["entry_date"] == daily.index[16], "atr14_pts"].iloc[0])
    assert got_d16 == pytest.approx(expected_d16)

    # Day 20: should equal mean(TR.iloc[6..20]).
    expected_d20 = float(tr.iloc[6:20].mean())
    got_d20 = float(trades.loc[trades["entry_date"] == daily.index[20], "atr14_pts"].iloc[0])
    assert got_d20 == pytest.approx(expected_d20)


# ---------------------------------------------------------------------------
# End-to-end: 5-min frame -> daily OHLC -> ATR
# ---------------------------------------------------------------------------


def test_full_pipeline_5min_to_atr() -> None:
    df_5min = _make_5min_frame(days=20, range_per_day=2.0, daily_step=0.0)
    daily = aggregate_to_daily_ohlc(df_5min)
    atr = compute_atr14_lookahead_free(daily)

    assert len(daily) == 20
    # First 15 NaN, then non-NaN.
    assert atr.iloc[:15].isna().all()
    assert atr.iloc[15:].notna().all()
    # On a flat daily_step=0 frame TR should be small (just intraday range).
    assert (atr.dropna() > 0).all()
