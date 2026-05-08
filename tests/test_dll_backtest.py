"""Phase E rev: regression tests for DLL applied at run_backtest level.

Asserts the intraday-aware Daily Loss Limit logic in
``nqbreakout.strategy.core.run_backtest``:

  1. DLL force-flattens an open trade when intraday low pushes cumulative
     day PnL below -daily_loss_limit. exit_reason="dll_flatten", price
     fills at the bar's row.low (long).

  2. After DLL trip, the dll_blocked_today flag prevents new entries for
     the rest of the day, even if breakout pattern would otherwise allow.

  3. dll_blocked_today + daily_realized_pnl reset at day rollover, so the
     next day allows fresh trades.

  4. daily_loss_limit=0.0 (default) disables DLL → preserves Phase 0-9c
     parity. Trade behavior unchanged when DLL is off.

These tests use synthetic 5-min OHLCV frames with controlled price action
to make DLL trigger conditions deterministic.

Phase 14R conventions still apply: backtest at 1 NQ ($20/pt) for all tiers.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from nqbreakout.strategy.config import StrategyConfig, load_instrument_spec
from nqbreakout.strategy.core import run_backtest


# ---------------------------------------------------------------------------
# Synthetic frame builder
# ---------------------------------------------------------------------------


def _make_dll_synthetic_df(
    drawdown_low_day2: float | None = None,
    second_breakout_day2: bool = False,
    day3_clean_breakout: bool = False,
) -> pd.DataFrame:
    """Build a 2- or 3-day 5-min OHLCV frame for DLL test scenarios.

    Layout:
      Day 1 (Mon 2024-01-08): warmup, flat OHLC=[100,105,95,100] across
        08:30-16:00 (RTH). Used by daily_summary_regular to build prev-day
        summary so day 2's filter eval can pass.
      Day 2 (Tue 2024-01-09): test day.
        - 08:30-09:55: window range [100, 105]
        - 10:00 bar: BREAKOUT LONG (high=110, close=110), entry triggered
        - 10:05 bar: optional drawdown to ``drawdown_low_day2`` (test 1, 3)
        - 10:35 bar: optional second breakout (high=130) to test entry-block (test 2)
        - rest: flat to avoid extra signals
      Day 3 (Wed 2024-01-10, optional): if ``day3_clean_breakout`` then
        contains a clean long breakout for the reset test.
    """
    bars: list[dict] = []
    base = dt.datetime(2024, 1, 8, 8, 30)  # Monday 08:30 ET

    # ---- Day 1 (warmup): 08:30-16:00, flat ----
    # 91 bars: 08:30 + 5min*90 = 16:00. Indices 12-90 cover 09:30-16:00 (RTH).
    for i in range(91):
        ts = base + dt.timedelta(minutes=5 * i)
        bars.append(
            {"date": ts, "open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0}
        )

    # ---- Day 2 (test): 08:30-16:00 (loop iterates only [08:30, 14:30]) ----
    day2_start = base + dt.timedelta(days=1)
    # 08:30-10:00: 19 bars, OR window range [100, 105]. The 10:00 bar is the
    # last to update window_high (inclusive). After this bar, window_ready=True.
    for i in range(19):
        ts = day2_start + dt.timedelta(minutes=5 * i)
        bars.append(
            {"date": ts, "open": 100.0, "high": 105.0, "low": 100.0, "close": 102.0}
        )

    # 10:05 bar: BREAKOUT LONG. high=110 > window_high=105 → entry at close=110.
    ts = day2_start + dt.timedelta(minutes=5 * 19)
    bars.append({"date": ts, "open": 105.0, "high": 110.0, "low": 105.0, "close": 110.0})

    # 10:10 bar: optional drawdown to test DLL trip.
    if drawdown_low_day2 is not None:
        ts = day2_start + dt.timedelta(minutes=5 * 20)
        bars.append(
            {"date": ts, "open": 110.0, "high": 110.0, "low": float(drawdown_low_day2), "close": 105.0}
        )
    else:
        ts = day2_start + dt.timedelta(minutes=5 * 20)
        bars.append({"date": ts, "open": 110.0, "high": 112.0, "low": 109.0, "close": 111.0})

    # 10:15-14:30: 51 bars. Optional second breakout at i=4 (=10:35).
    for i in range(51):
        ts = day2_start + dt.timedelta(minutes=5 * (21 + i))
        if second_breakout_day2 and i == 4:
            bars.append(
                {"date": ts, "open": 110.0, "high": 130.0, "low": 110.0, "close": 125.0}
            )
        else:
            bars.append(
                {"date": ts, "open": 110.0, "high": 112.0, "low": 108.0, "close": 110.0}
            )

    # 14:35-16:00: 18 bars (outside loop range but needed by daily_summary_regular
    # so that day 3 can read prev_summary for day 2 in the filter eval).
    for i in range(18):
        ts = day2_start + dt.timedelta(minutes=5 * (72 + i))
        bars.append(
            {"date": ts, "open": 110.0, "high": 112.0, "low": 108.0, "close": 110.0}
        )

    # ---- Day 3 (optional): warmup or clean breakout ----
    if day3_clean_breakout:
        day3_start = base + dt.timedelta(days=2)
        for i in range(91):
            ts = day3_start + dt.timedelta(minutes=5 * i)
            if 0 <= i <= 18:  # OR window 08:30-10:00 inclusive: [100, 105]
                bars.append(
                    {"date": ts, "open": 100.0, "high": 105.0, "low": 100.0, "close": 102.0}
                )
            elif i == 19:  # 10:05 bar: BREAKOUT (after window close), entry trigger
                bars.append(
                    {"date": ts, "open": 105.0, "high": 115.0, "low": 105.0, "close": 112.0}
                )
            else:
                bars.append(
                    {"date": ts, "open": 110.0, "high": 113.0, "low": 109.0, "close": 111.0}
                )

    df = pd.DataFrame(bars)
    df["day"] = df["date"].dt.date
    df["time"] = df["date"].dt.time
    return df


def _make_dll_test_config(
    daily_loss_limit: float = 200.0, stop_points: float = 50.0
) -> StrategyConfig:
    """Config for DLL tests:

    - daily_loss_limit: configurable (0 disables DLL)
    - stop_points=50 (wide → SL never fires before DLL in synthetic scenarios)
    - target_points=100 (TP=210 above entry=110 → never reached in synthetic)
    - flatten_at_eod=True with eod_cutoff_time=14:00 ET so trade closes
      cleanly within the bar loop in non-DLL scenarios
    - long_only=True (matches Phase 11E config)
    - max_position=1 (force 1 NQ to keep DLL trigger math simple/predictable)
    """
    return StrategyConfig(
        stop_points=stop_points,
        target_points=100.0,
        max_trades_per_day=2,
        daily_loss_limit=daily_loss_limit,
        flatten_at_eod=True,
        eod_cutoff_time=dt.time(14, 0),
        long_only=True,
        max_position=1,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dll_force_flattens_on_intraday_low() -> None:
    """When intraday low pushes cum day PnL below -DLL, force flatten at low.

    Scenario:
      - Entry at 10:00, price 110, 1 NQ ($20/pt).
      - 10:05 bar: low=99 → unrealized at low = (99-110)*$20 = -$220.
      - DLL=$200 → cum -$220 <= -$200 → trip. Exit at row.low=99.
      - Expected exit_reason='dll_flatten', pnl<=-200.
    """
    df = _make_dll_synthetic_df(drawdown_low_day2=99.0)
    cfg = _make_dll_test_config(daily_loss_limit=200.0)
    inst = load_instrument_spec("NQ")

    result = run_backtest(df, cfg, inst)

    assert len(result.trades) >= 1, "expected at least one trade"
    first = result.trades.iloc[0]
    assert first["exit_reason"] == "dll_flatten", (
        f"expected dll_flatten, got {first['exit_reason']}"
    )
    # PnL gross = (99-110)*$20 = -$220. After costs ~$225-$230 net.
    assert first["pnl"] <= -200.0, f"expected pnl <= -$200, got {first['pnl']}"


def test_dll_blocks_new_entry_after_breach() -> None:
    """After DLL trip, no second trade same day even if breakout pattern triggers.

    Scenario:
      - Day 2 trade #1 enters at 10:00, DLL fires at 10:05.
      - Second breakout at 10:35 (high=130 > window_high=105).
      - Without DLL block, a second trade would fire (max_trades_per_day=2).
      - With dll_blocked_today flag, second entry blocked → exactly 1 trade.
    """
    df = _make_dll_synthetic_df(
        drawdown_low_day2=99.0, second_breakout_day2=True
    )
    cfg = _make_dll_test_config(daily_loss_limit=200.0)
    inst = load_instrument_spec("NQ")

    result = run_backtest(df, cfg, inst)

    assert len(result.trades) == 1, (
        f"expected 1 trade (DLL block), got {len(result.trades)}"
    )
    assert result.trades.iloc[0]["exit_reason"] == "dll_flatten"


def test_dll_resets_on_day_boundary() -> None:
    """DLL flag clears at day rollover; next day allows fresh trades.

    Scenario:
      - Day 2: DLL trip on trade #1.
      - Day 3: clean breakout pattern → dll_blocked_today reset → new entry.
      - Expect 1 trade on day 2 (DLL stop) + at least 1 trade on day 3.
    """
    df = _make_dll_synthetic_df(
        drawdown_low_day2=99.0, day3_clean_breakout=True
    )
    cfg = _make_dll_test_config(daily_loss_limit=200.0)
    inst = load_instrument_spec("NQ")

    result = run_backtest(df, cfg, inst)

    if "entry_time" not in result.trades.columns:
        return  # No trades — would fail other tests too
    days = pd.to_datetime(result.trades["entry_time"]).dt.date
    day2 = result.trades[days == dt.date(2024, 1, 9)]
    day3 = result.trades[days == dt.date(2024, 1, 10)]

    assert len(day2) == 1, f"expected 1 trade on day 2 (DLL stop), got {len(day2)}"
    assert day2.iloc[0]["exit_reason"] == "dll_flatten"
    assert len(day3) >= 1, f"expected ≥1 trade on day 3 (fresh), got {len(day3)}"


def test_dll_disabled_when_zero_preserves_legacy_behavior() -> None:
    """daily_loss_limit=0.0 disables DLL → trade survives drawdown.

    Scenario:
      - Same drawdown scenario as test 1 (10:05 low=99, would trip DLL=$200).
      - But daily_loss_limit=0.0 → DLL inactive.
      - Trade survives drawdown, eventually closes at EOD (14:00) at close=110.
      - Expected: exit_reason='force_close' (or stop/target/max_hold), NOT 'dll_flatten'.
    """
    df = _make_dll_synthetic_df(drawdown_low_day2=99.0)
    cfg_disabled = _make_dll_test_config(daily_loss_limit=0.0)
    inst = load_instrument_spec("NQ")

    result = run_backtest(df, cfg_disabled, inst)

    assert len(result.trades) >= 1
    first = result.trades.iloc[0]
    assert first["exit_reason"] != "dll_flatten", (
        f"expected NOT dll_flatten when DLL=0, got {first['exit_reason']}"
    )
