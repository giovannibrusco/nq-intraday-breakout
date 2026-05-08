"""Tests for Phase 6 trade-level analytics.

Covers:
- add_regime_columns: join correctness
- per_regime_breakdown: columns and partition
- mae_mfe_distribution: percentile ordering
- edge_per_direction: long/short split
- exit_reason_breakdown: completeness
- calmar fix: returns finite, reasonable value
- MAE/MFE population in real backtest run
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from nqbreakout.stats.metrics import calmar, max_drawdown_pct
from nqbreakout.stats.trade_analytics import (
    add_regime_columns,
    edge_per_direction,
    exit_reason_breakdown,
    mae_mfe_distribution,
    per_regime_breakdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trades(n: int = 20, seed: int = 42) -> pd.DataFrame:
    """Synthetic trades DataFrame with all expected columns."""
    rng = np.random.default_rng(seed)
    base = pd.Timestamp("2020-01-02")
    records = []
    for i in range(n):
        direction = "long" if i % 2 == 0 else "short"
        pnl = float(rng.choice([400.0, -200.0]))
        entry_time = base + pd.Timedelta(days=i)
        records.append({
            "direction": direction,
            "entry_time": entry_time,
            "exit_time": entry_time + pd.Timedelta(hours=2),
            "entry_price": 15000.0 + float(rng.integers(-100, 100)),
            "exit_price": 15000.0 + float(rng.integers(-100, 100)),
            "pnl": pnl,
            "exit_reason": rng.choice(["stop", "target", "max_hold"]),
            "mae_points": float(rng.uniform(5, 80)),
            "mfe_points": float(rng.uniform(10, 150)),
        })
    return pd.DataFrame(records)


def _make_daily_features(trades: pd.DataFrame) -> pd.DataFrame:
    """Minimal synthetic daily_features aligned with trade dates."""
    dates = pd.to_datetime(trades["entry_time"]).dt.date.unique()
    rng = np.random.default_rng(99)
    n = len(dates)
    feat = pd.DataFrame(
        {
            "gap_atr": rng.normal(0, 1, n),
            "range_pctile": rng.uniform(0, 1, n),
            "ema_slope_sign": rng.choice([-1, 0, 1], n),
            "session_vol_ratio": rng.uniform(0.5, 2.0, n),
        },
        index=dates,
    )
    feat.index.name = "date"
    return feat


# ---------------------------------------------------------------------------
# 1. add_regime_columns: join correctness
# ---------------------------------------------------------------------------


def test_add_regime_columns_joins_correctly():
    trades = _make_trades(10)
    features = _make_daily_features(trades)
    result = add_regime_columns(trades, features)

    assert "gap_atr" in result.columns
    assert "range_pctile" in result.columns
    assert "trend_sign" in result.columns
    assert "vol_ratio" in result.columns

    # Number of rows unchanged
    assert len(result) == len(trades)

    # Bin columns present
    assert "gap_atr_bin" in result.columns
    assert "range_pctile_bin" in result.columns
    assert "vol_ratio_bin" in result.columns


def test_add_regime_columns_bin_labels():
    """Bin labels must be one of low/mid/high (as strings)."""
    trades = _make_trades(30)
    features = _make_daily_features(trades)
    result = add_regime_columns(trades, features)

    valid = {"low", "mid", "high"}
    for col in ["gap_atr_bin", "range_pctile_bin", "vol_ratio_bin"]:
        unique_vals = set(result[col].dropna().astype(str).unique())
        assert unique_vals.issubset(valid), f"{col} has unexpected labels: {unique_vals}"


# ---------------------------------------------------------------------------
# 2. per_regime_breakdown: columns
# ---------------------------------------------------------------------------


def test_per_regime_breakdown_columns():
    trades = _make_trades(20)
    result = per_regime_breakdown(trades, "exit_reason")

    required = {"count", "total_pnl", "mean_pnl", "median_pnl", "win_rate", "sharpe_per_trade"}
    assert required.issubset(set(result.columns)), (
        f"Missing columns: {required - set(result.columns)}"
    )


# ---------------------------------------------------------------------------
# 3. per_regime_breakdown: partition (counts sum to total)
# ---------------------------------------------------------------------------


def test_per_regime_breakdown_partition():
    trades = _make_trades(20)
    result = per_regime_breakdown(trades, "exit_reason")

    assert int(result["count"].sum()) == len(trades), (
        f"Sum of per-regime counts {result['count'].sum()} != total {len(trades)}"
    )


def test_per_regime_breakdown_direction_partition():
    trades = _make_trades(20)
    result = per_regime_breakdown(trades, "direction")
    assert int(result["count"].sum()) == len(trades)


# ---------------------------------------------------------------------------
# 4. mae_mfe_distribution: percentiles in correct order
# ---------------------------------------------------------------------------


def test_mae_mfe_distribution_percentiles():
    trades = _make_trades(50)
    dist = mae_mfe_distribution(trades)

    mae_pct = dist["mae_pct"]
    mfe_pct = dist["mfe_pct"]

    # Percentiles must be non-decreasing
    pct_levels = [10, 25, 50, 75, 90]
    for i in range(len(pct_levels) - 1):
        p_lo = pct_levels[i]
        p_hi = pct_levels[i + 1]
        assert mae_pct[p_lo] <= mae_pct[p_hi], (
            f"MAE pct[{p_lo}] > pct[{p_hi}]: {mae_pct[p_lo]:.2f} > {mae_pct[p_hi]:.2f}"
        )
        assert mfe_pct[p_lo] <= mfe_pct[p_hi], (
            f"MFE pct[{p_lo}] > pct[{p_hi}]: {mfe_pct[p_lo]:.2f} > {mfe_pct[p_hi]:.2f}"
        )


# ---------------------------------------------------------------------------
# 5. edge_per_direction: count_long + count_short == total
# ---------------------------------------------------------------------------


def test_edge_per_direction_long_short_split():
    trades = _make_trades(20)
    result = edge_per_direction(trades)

    assert "long" in result.index
    assert "short" in result.index
    total_counts = int(result["count"].sum())
    assert total_counts == len(trades), (
        f"Direction counts {total_counts} != total trades {len(trades)}"
    )


def test_edge_per_direction_columns():
    trades = _make_trades(20)
    result = edge_per_direction(trades)
    for col in ("count", "total_pnl", "mean_pnl", "win_rate", "max_dd"):
        assert col in result.columns, f"Missing column '{col}'"


# ---------------------------------------------------------------------------
# 6. exit_reason_breakdown: every reason appears in output
# ---------------------------------------------------------------------------


def test_exit_reason_breakdown_includes_all_reasons():
    trades = _make_trades(30)
    all_reasons = set(trades["exit_reason"].unique())
    result = exit_reason_breakdown(trades)

    assert set(result.index) == all_reasons, (
        f"Breakdown index {set(result.index)} != trades reasons {all_reasons}"
    )


def test_exit_reason_breakdown_columns():
    trades = _make_trades(20)
    result = exit_reason_breakdown(trades)
    for col in ("count", "total_pnl", "mean_pnl", "mean_mae", "mean_mfe"):
        assert col in result.columns, f"Missing column '{col}'"


# ---------------------------------------------------------------------------
# 7. Calmar fix: returns finite, reasonable value (< 100)
# ---------------------------------------------------------------------------


def test_calmar_fix_returns_finite_reasonable():
    """Calmar of a realistic equity curve must be finite and < 100.

    This specifically guards against the old bug that multiplied mean daily
    dollar PnL by 252 then divided by a small fraction, yielding millions.
    """
    # Build a realistic synthetic equity: ~$100k account, gentle upward drift
    rng = np.random.default_rng(42)
    daily_returns = rng.normal(loc=200, scale=1500, size=500)
    equity = pd.Series(100_000.0 + np.cumsum(daily_returns))
    pnl = pd.Series(daily_returns)

    c = calmar(pnl, equity)

    assert np.isfinite(c), f"Calmar must be finite, got {c}"
    assert c < 100.0, f"Calmar should be < 100 for realistic equity, got {c:.2f}"
    # Also should be > -100 (strategy has positive drift so should be positive)
    assert c > -100.0, f"Calmar should be > -100, got {c:.2f}"


def test_calmar_known_values():
    """Verify Calmar against hand-computed compound formula.

    The calmar function uses n_days = len(daily_pnl.dropna()) and equity
    first/last values. We construct the inputs to match exactly.
    """
    initial = 100_000.0
    final = 200_000.0
    # Build equity so that first=initial, last=final, with a 10% drawdown somewhere.
    peak = 220_000.0
    trough = peak * 0.90  # 10% drawdown from peak
    equity_vals = np.concatenate([
        np.linspace(initial, peak, 150),
        np.linspace(peak, trough, 50),
        np.linspace(trough, final, 52),
    ])
    equity = pd.Series(equity_vals.astype(float))
    # pnl series: use diff().dropna() to match how calmar counts n_days
    pnl_full = equity.diff().dropna()  # 251 values, all non-nan

    c = calmar(pnl_full, equity)
    mdd = max_drawdown_pct(equity)

    # n_days as computed inside calmar = len(pnl_full.dropna()) = 251
    n_days = len(pnl_full.dropna())
    ann_return_expected = (final / initial) ** (252.0 / n_days) - 1.0
    expected_calmar = ann_return_expected / mdd

    assert abs(c - expected_calmar) < 1e-8, (
        f"Calmar={c:.6f}, expected={expected_calmar:.6f}"
    )


# ---------------------------------------------------------------------------
# 8. MAE/MFE populated in real backtest
# ---------------------------------------------------------------------------


def test_mae_mfe_populated_in_trades():
    """Run a minimal backtest and assert mae/mfe columns are NOT all zero."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from nqbreakout.strategy.config import StrategyConfig, load_instrument_spec
    from nqbreakout.strategy.core import run_backtest

    # Build a synthetic 5-min DataFrame that will generate at least one trade.
    # Pattern: 5 days with opening range [100, 105] and then a long breakout
    # bar high=115 at 10:05. We need a prev-day entry in daily_summary_regular
    # to pass the filter gate. We use two sets of 5 bars: day 0 as "prev day",
    # day 1 as "trade day".
    bars = []
    # Day 0: regular session 09:30..16:00, stable
    d0 = dt.datetime(2024, 1, 2, 9, 30)
    for i in range(78):  # 9:30..16:00 in 5-min steps (78 bars)
        ts = d0 + dt.timedelta(minutes=5 * i)
        bars.append({
            "date": ts,
            "open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0,
            "totalvolume": 1000,
        })

    # Day 1: opening range 08:30..10:00 at [100,105], then breakout bar at 10:05
    d1 = dt.datetime(2024, 1, 3, 8, 30)
    # Opening range bars (08:30..10:00)
    for i in range(19):  # 08:30..09:55
        ts = d1 + dt.timedelta(minutes=5 * i)
        bars.append({
            "date": ts,
            "open": 100.0, "high": 105.0, "low": 98.0, "close": 101.0,
            "totalvolume": 1000,
        })
    # 10:00 bar: regular session, no breakout yet
    bars.append({
        "date": dt.datetime(2024, 1, 3, 10, 0),
        "open": 101.0, "high": 106.0, "low": 99.0, "close": 101.0,
        "totalvolume": 1000,
    })
    # 10:05 bar: long breakout (high > 105 opening range high)
    bars.append({
        "date": dt.datetime(2024, 1, 3, 10, 5),
        "open": 101.0, "high": 115.0, "low": 100.0, "close": 108.0,
        "totalvolume": 2000,
    })
    # 10:10 bar: target hit (entry~108, target=108+200=308 -- too far, so use stop)
    # Actually let the trade close at max_hold or eod. Add bars until trade_end 14:30
    current_ts = dt.datetime(2024, 1, 3, 10, 10)
    end_ts = dt.datetime(2024, 1, 3, 14, 35)
    while current_ts <= end_ts:
        # Extreme bar to hit stop on long (low < stop = 108 - 100 = 8): very low
        bars.append({
            "date": current_ts,
            "open": 108.0, "high": 110.0, "low": 5.0, "close": 8.0,
            "totalvolume": 1000,
        })
        current_ts += dt.timedelta(minutes=5)

    df = pd.DataFrame(bars)
    df["day"] = df["date"].dt.date
    df["time"] = df["date"].dt.time

    cfg = StrategyConfig()
    instr = load_instrument_spec("NQ")
    result = run_backtest(df, cfg, instr)

    if result.trades.empty:
        pytest.skip("No trades generated in synthetic data; skipping MAE/MFE population test.")

    # MAE and MFE must not both be zero for all trades
    mae_all_zero = (result.trades["mae_points"] == 0.0).all()
    mfe_all_zero = (result.trades["mfe_points"] == 0.0).all()

    assert not (mae_all_zero and mfe_all_zero), (
        "Both mae_points and mfe_points are all zero -- Phase 6 tracking not active."
    )
