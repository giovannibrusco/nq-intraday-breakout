"""Tests for ``nqbreakout.strategy.filters`` - Phase 4 regime filters.

Covers:
- passthrough contract when ``daily_features is None``;
- each individual filter function under representative inputs;
- conjunction semantics across enabled filters in ``FilterEngine``;
- look-ahead discipline on ``compute_daily_features`` (day T uses only
  data strictly prior to day T's session open).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from nqbreakout.strategy.config import FilterSpec
from nqbreakout.strategy.filters import (
    FilterDecision,
    FilterEngine,
    compute_daily_features,
    filter_liquidity,
    filter_overnight_gap,
    filter_prior_range,
    filter_trend,
)


# ---------------------------------------------------------------------------
# Passthrough contract
# ---------------------------------------------------------------------------


def test_filter_engine_passthrough_when_no_features() -> None:
    """Without ``daily_features`` the engine allows both directions."""
    eng = FilterEngine(FilterSpec(enable_overnight_gap=True, enable_trend=True))
    decision = eng.evaluate(dt.date(2024, 1, 2), 0, None)
    assert decision.allow_long is True
    assert decision.allow_short is True
    assert decision.snapshot == {}


# ---------------------------------------------------------------------------
# Overnight gap filter
# ---------------------------------------------------------------------------


def test_filter_overnight_gap_accepts_small_gap() -> None:
    """Small +0.10% gap, well within ATR, passes both directions."""
    spec = FilterSpec()
    row = pd.Series({"gap_pct": 0.001, "gap_atr": 0.5})
    d = filter_overnight_gap(row, spec)
    assert d.allow_long is True
    assert d.allow_short is True
    assert d.snapshot["gap_atr"] == 0.5


def test_filter_overnight_gap_rejects_huge_gap() -> None:
    """|gap_atr|=3.0 exceeds gap_atr_max_both=2.0 -> rejects both directions."""
    spec = FilterSpec()
    row = pd.Series({"gap_pct": 0.01, "gap_atr": 3.0})
    d = filter_overnight_gap(row, spec)
    assert d.allow_long is False
    assert d.allow_short is False


def test_filter_overnight_gap_asymmetric() -> None:
    """Large negative gap (-1% price, -1.0 ATR) blocks longs; shorts ok.

    gap_pct=-0.01 < gap_pct_min_long=-0.003 -> long blocked.
    gap_pct=-0.01 <= gap_pct_max_short=+0.003 -> short ok.
    gap_atr=-1.0, abs() < gap_atr_max_both=2.0, so not both-block.
    """
    spec = FilterSpec()
    row = pd.Series({"gap_pct": -0.01, "gap_atr": -1.0})
    d = filter_overnight_gap(row, spec)
    assert d.allow_long is False
    assert d.allow_short is True


def test_filter_overnight_gap_nan_passthrough() -> None:
    """Missing feature (warmup) -> passthrough."""
    spec = FilterSpec()
    row = pd.Series({"gap_pct": np.nan, "gap_atr": np.nan})
    d = filter_overnight_gap(row, spec)
    assert d.allow_long is True
    assert d.allow_short is True


# ---------------------------------------------------------------------------
# Prior-day range filter
# ---------------------------------------------------------------------------


def test_filter_prior_range_rejects_at_percentile() -> None:
    """Range percentile 0.90 > 0.75 threshold -> rejects both."""
    spec = FilterSpec()
    row = pd.Series({"range_pctile": 0.90})
    d = filter_prior_range(row, spec)
    assert d.allow_long is False
    assert d.allow_short is False


def test_filter_prior_range_accepts_median_regime() -> None:
    """Range percentile 0.5 -> within normal regime -> allows both."""
    spec = FilterSpec()
    row = pd.Series({"range_pctile": 0.5})
    d = filter_prior_range(row, spec)
    assert d.allow_long is True
    assert d.allow_short is True


# ---------------------------------------------------------------------------
# Trend filter
# ---------------------------------------------------------------------------


def test_filter_trend_long_only_uptrend() -> None:
    """EMA slope sign +1 -> longs allowed, shorts blocked."""
    spec = FilterSpec()
    row = pd.Series({"ema_slope_sign": 1, "ema_slope": 0.5})
    d = filter_trend(row, spec)
    assert d.allow_long is True
    assert d.allow_short is False


def test_filter_trend_short_only_downtrend() -> None:
    """EMA slope sign -1 -> shorts allowed, longs blocked."""
    spec = FilterSpec()
    row = pd.Series({"ema_slope_sign": -1, "ema_slope": -0.4})
    d = filter_trend(row, spec)
    assert d.allow_long is False
    assert d.allow_short is True


# ---------------------------------------------------------------------------
# Liquidity filter
# ---------------------------------------------------------------------------


def test_filter_liquidity_rejects_low_vol() -> None:
    """Session volume 30% of 20d median -> below min_ratio=0.5 -> reject both."""
    spec = FilterSpec()
    row = pd.Series({"session_vol_ratio": 0.3})
    d = filter_liquidity(row, spec)
    assert d.allow_long is False
    assert d.allow_short is False


def test_filter_liquidity_accepts_normal_vol() -> None:
    """Session volume 1.2x 20d median -> allow both."""
    spec = FilterSpec()
    row = pd.Series({"session_vol_ratio": 1.2})
    d = filter_liquidity(row, spec)
    assert d.allow_long is True
    assert d.allow_short is True


# ---------------------------------------------------------------------------
# Engine conjunction across enabled filters
# ---------------------------------------------------------------------------


def test_filter_engine_conjunction() -> None:
    """Two filters enabled, one rejects -> combined rejects.

    Trend filter in downtrend -> long blocked.
    Liquidity filter fine. Enabled together, longs must be blocked.
    """
    spec = FilterSpec(
        enable_trend=True,
        enable_liquidity=True,
    )
    features = pd.DataFrame(
        {
            "ema_slope_sign": [-1],
            "ema_slope": [-0.5],
            "session_vol_ratio": [1.5],
            # Other features required by full engine - NaN for unused filters
            "gap_pct": [np.nan],
            "gap_atr": [np.nan],
            "range_pctile": [np.nan],
        },
        index=[dt.date(2024, 1, 2)],
    )
    features.index.name = "date"
    eng = FilterEngine(spec, daily_features=features)
    d = eng.evaluate(dt.date(2024, 1, 2), 0, None)
    assert d.allow_long is False, "trend downtrend must block longs"
    assert d.allow_short is True, "shorts should still be allowed by trend+liquidity"


def test_filter_engine_all_enabled_rejects_hard_day() -> None:
    """All 4 enabled, hostile regime (huge gap + exhausted range + thin vol + flat trend) -> both blocked."""
    spec = FilterSpec(
        enable_overnight_gap=True,
        enable_prior_range=True,
        enable_trend=True,
        enable_liquidity=True,
    )
    features = pd.DataFrame(
        {
            "gap_pct": [0.02],
            "gap_atr": [3.0],           # > 2.0 both-block
            "range_pctile": [0.95],     # > 0.75 both-block
            "ema_slope_sign": [0],      # flat -> both-block
            "ema_slope": [0.0],
            "session_vol_ratio": [0.3], # < 0.5 both-block
        },
        index=[dt.date(2024, 1, 2)],
    )
    features.index.name = "date"
    eng = FilterEngine(spec, daily_features=features)
    d = eng.evaluate(dt.date(2024, 1, 2), 0, None)
    assert d.allow_long is False
    assert d.allow_short is False


def test_filter_engine_unknown_day_is_warmup() -> None:
    """Day not in feature frame -> warmup pass (both True)."""
    spec = FilterSpec(enable_trend=True)
    features = pd.DataFrame(
        {"ema_slope_sign": [1], "ema_slope": [0.5]},
        index=[dt.date(2024, 1, 2)],
    )
    features.index.name = "date"
    eng = FilterEngine(spec, daily_features=features)
    d = eng.evaluate(dt.date(1999, 1, 1), 0, None)
    assert d.allow_long is True
    assert d.allow_short is True


# ---------------------------------------------------------------------------
# No look-ahead in compute_daily_features
# ---------------------------------------------------------------------------


def _make_synthetic_5min(n_days: int = 120, seed: int = 7) -> pd.DataFrame:
    """Synthesize a multi-day 5-minute NQ-like frame with weekdays only."""
    rng = np.random.default_rng(seed)
    base = dt.datetime(2023, 1, 2, 8, 30)
    rows: list[dict] = []
    price = 15000.0
    day_counter = 0
    offset = 0
    while day_counter < n_days:
        day = base + dt.timedelta(days=offset)
        offset += 1
        if day.weekday() >= 5:
            continue
        day_start = day.replace(hour=8, minute=30)
        daily_drift = rng.normal(0, 30)
        for i in range(73):  # 08:30..14:30 in 5-min bars
            ts = day_start + dt.timedelta(minutes=5 * i)
            price += rng.normal(0, 5) + daily_drift / 73
            high = price + abs(rng.normal(0, 3))
            low = price - abs(rng.normal(0, 3))
            rows.append(
                {
                    "date": ts,
                    "open": float(price),
                    "high": float(high),
                    "low": float(low),
                    "close": float(price),
                    "totalvolume": int(rng.integers(500, 5000)),
                }
            )
        # tack on a 09:30 bar and 16:00 bar to make regular-session stats valid
        for extra_hour in (9, 10, 11, 12, 13, 14, 15, 16):
            ts = day.replace(hour=extra_hour, minute=0)
            rows.append(
                {
                    "date": ts,
                    "open": float(price),
                    "high": float(price + 1),
                    "low": float(price - 1),
                    "close": float(price),
                    "totalvolume": int(rng.integers(100, 1000)),
                }
            )
        day_counter += 1
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return df


def test_compute_daily_features_no_lookahead() -> None:
    """Day T's feature row must not depend on day T's prior_range / gap / trend components.

    We assemble features on a 120-day synthetic frame, then pick a day T
    in the middle and mutate day T's own OHLC and volume. We recompute and
    assert that the feature row for day T is unchanged (only features that
    depend on today's session volume can move, and we assert that ``gap_pct``,
    ``range_pctile``, ``ema_slope_sign`` are all unchanged).
    """
    df = _make_synthetic_5min(120)
    feats = compute_daily_features(df)
    assert feats.index.name == "date"

    # Pick a day well past the 60d warmup.
    test_day = feats.index[80]
    baseline = feats.loc[test_day]

    # Mutate the OHLC / volume of test_day (and ONLY test_day) dramatically.
    df2 = df.copy()
    mask = df2["date"].dt.date == test_day
    df2.loc[mask, "high"] = df2.loc[mask, "high"] + 1000.0
    df2.loc[mask, "low"] = df2.loc[mask, "low"] - 1000.0
    df2.loc[mask, "close"] = df2.loc[mask, "close"] + 500.0
    df2.loc[mask, "totalvolume"] = df2.loc[mask, "totalvolume"] * 100

    feats2 = compute_daily_features(df2)
    mutated = feats2.loc[test_day]

    # Look-ahead safe columns: these must NOT depend on today's mutation.
    for col in ("gap_pct", "gap_atr", "range_pctile", "ema_slope", "ema_slope_sign"):
        b = baseline[col]
        m = mutated[col]
        if pd.isna(b) and pd.isna(m):
            continue
        assert b == pytest.approx(m, nan_ok=False), (
            f"{col} leaked today's data: baseline={b}, mutated={m}"
        )

    # ``session_vol`` is today's opening-window volume: it is known by 10:00,
    # i.e. BEFORE trade_start, so it's not considered look-ahead for entries
    # after 10:00. We expect it to change.
    assert baseline["session_vol"] != mutated["session_vol"]
    # The rolling median is strictly prior-day -> unchanged by today's mutation.
    assert baseline["session_vol_median_20d"] == pytest.approx(
        mutated["session_vol_median_20d"], nan_ok=True
    )


def test_compute_daily_features_qqq_trend_source() -> None:
    """QQQ trend source merges on date and yields a finite slope series."""
    df = _make_synthetic_5min(60)
    dates = sorted({d.date() for d in df["date"]})
    qqq = pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "close": np.linspace(300, 350, len(dates)),  # monotone uptrend
        }
    )
    feats = compute_daily_features(df, qqq_daily=qqq, trend_source="QQQ")
    # After warmup, slope sign should be +1 (increasing series).
    late = feats.iloc[-1]
    assert late["ema_slope_sign"] == 1
