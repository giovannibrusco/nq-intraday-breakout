"""Regime filter engine - 4 filters individually toggleable.

Phase 4 replaces the Phase 1 passthrough. The legacy notebook hard-coded
``cond1 = cond2 = cond3 = cond4 = True`` inline in the bar loop; this module
delivers the real checks while preserving the evaluator contract that
``core.run_backtest`` depends on.

Design:
- All filters precompute per-day features once (vectorized) before the
  backtest bar loop, via ``compute_daily_features``.
- Per-day decisions are O(1) lookups in the precomputed frame.
- Each filter is a pure function of a single day's feature row and the
  ``FilterSpec`` thresholds; ``FilterEngine.evaluate`` conjoins decisions
  across the enabled filters.
- Default behavior when ``daily_features is None``: passthrough (preserves
  the Phase 1 parity contract for ``run_backtest`` callers that do not
  supply features).
- All features use ``.shift(1)`` discipline: the feature row for day T uses
  only data available strictly before T's session opens. No look-ahead.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .config import FilterSpec


# ---------------------------------------------------------------------------
# Decision object
# ---------------------------------------------------------------------------


@dataclass
class FilterDecision:
    """Result of evaluating filters for one trading day.

    - ``allow_long`` / ``allow_short``: whether a long (resp. short) breakout
      may be taken today.
    - ``snapshot``: diagnostic dict for traceability (gap %, range percentile,
      trend slope, volume ratio, etc.).
    """

    allow_long: bool
    allow_short: bool
    snapshot: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Daily feature computation
# ---------------------------------------------------------------------------


def compute_daily_features(
    df_5min: pd.DataFrame,
    qqq_daily: pd.DataFrame | None = None,
    *,
    atr_window: int = 20,
    range_window_days: int = 60,
    trend_ema_window: int = 50,
    trend_source: str = "NQ",
    liquidity_session_start: dt.time = dt.time(8, 30),
    liquidity_session_end: dt.time = dt.time(10, 0),
    liquidity_median_window_days: int = 20,
) -> pd.DataFrame:
    """Precompute per-day features used by filters.

    Returns a DataFrame indexed by ``date`` (Python ``datetime.date``) with
    columns:

    - ``prev_close``: regular-session close of the prior day
    - ``open_price``: first bar's open at or after ``liquidity_session_start``
      on day T (effectively today's session open)
    - ``gap_pct``: ``(open_price - prev_close) / prev_close``
    - ``gap_atr``: ``(open_price - prev_close) / ATR(atr_window)`` where ATR
      is the mean absolute daily range over the prior ``atr_window`` days
    - ``range_pctile``: percentile rank (0..1) of the prior day's range
      within the previous ``range_window_days`` daily ranges (excluding today)
    - ``ema_slope_sign``: +1 / 0 / -1 from the slope of EMA-``trend_ema_window``
      on the configured daily close series (NQ derived from 5-min, or QQQ)
    - ``session_vol``: volume during ``[liquidity_session_start,
      liquidity_session_end]`` on day T
    - ``session_vol_median_20d``: rolling median of ``session_vol`` over the
      prior ``liquidity_median_window_days`` days (excluding today)
    - ``session_vol_ratio``: ``session_vol / session_vol_median_20d``

    All features except ``session_vol`` (today's known at bar close 10:00)
    are strictly lagged: day T's feature row depends only on data available
    before day T's entry window.

    Notes on the liquidity feature: ``session_vol`` is the volume accumulated
    during the opening window (08:30..10:00). By the time the trade entry
    window opens at ``config.trade_start`` (10:00 by default) this is known
    and not look-ahead biased. ``session_vol_median_20d`` is the rolling
    median of that same quantity over prior days (``.shift(1)``).
    """
    if "date" not in df_5min.columns:
        raise ValueError("df_5min must have a 'date' column of Timestamps")

    work = df_5min.copy()
    if "day" not in work.columns:
        work["day"] = work["date"].dt.date
    if "time" not in work.columns:
        work["time"] = work["date"].dt.time

    # Regular-session (09:30..16:00) daily OHLC for gap/range/trend features.
    # We intentionally use the regular session high/low for the prior-day
    # range (not the full 24h span) to match how breakout literature measures
    # range and to align with ``core.run_backtest``'s regular-session summary.
    regular = work[
        (work["time"] >= dt.time(9, 30)) & (work["time"] <= dt.time(16, 0))
    ]
    daily = regular.groupby("day").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    )

    # Intraday opening-session volume for the liquidity filter.
    session_mask = (work["time"] >= liquidity_session_start) & (
        work["time"] < liquidity_session_end
    )
    if "totalvolume" in work.columns:
        session_vol = (
            work.loc[session_mask].groupby("day")["totalvolume"].sum().rename("session_vol")
        )
    else:
        # If volume is not available, emit NaN so the liquidity filter becomes
        # a no-op (passthrough) rather than silently rejecting every day.
        session_vol = pd.Series(dtype=float, name="session_vol")

    features = daily.copy()
    features["prev_close"] = features["close"].shift(1)
    features["open_price"] = features["open"]  # today's regular-session open

    # Gap (overnight) diagnostics. All use prev_close, which is lagged by 1 day.
    features["gap_pct"] = (features["open_price"] - features["prev_close"]) / features[
        "prev_close"
    ]

    # ATR proxy: mean absolute daily range over the prior atr_window days
    # (shifted so today's value is unknown to the feature). We use the simple
    # range (high-low) not Wilder's ATR: this matches the plan's spec and is
    # robust under missing gaps.
    daily_range = (features["high"] - features["low"]).abs()
    features["atr_range"] = daily_range.shift(1).rolling(atr_window, min_periods=atr_window).mean()
    # Gap expressed in ATR units: |open - prev_close| / ATR, signed by gap_pct.
    features["gap_atr"] = (features["open_price"] - features["prev_close"]) / features["atr_range"]

    # Prior-day range percentile rank over the previous range_window_days days.
    # We want: "today's classification of the PRIOR day's range" against the
    # preceding window. Using .shift(1) on the range series, then .rolling
    # with a custom percentile-of-latest function.
    prior_range = daily_range.shift(1)

    def _pctile_of_last(window: np.ndarray) -> float:
        if np.isnan(window).any():
            return np.nan
        last = window[-1]
        # Percentile of last value within the whole window (inclusive).
        return (window <= last).mean()

    features["range_pctile"] = prior_range.rolling(
        range_window_days, min_periods=range_window_days
    ).apply(_pctile_of_last, raw=True)

    # Trend: EMA slope sign on the daily close series. Source can be NQ (from
    # the resampled 5-min closes, regular session last) or QQQ (external
    # daily). Either way we .shift(1) after computing so today's row reflects
    # "trend as of yesterday's close".
    if trend_source.upper() == "QQQ" and qqq_daily is not None:
        qqq = qqq_daily.copy()
        qqq["day"] = pd.to_datetime(qqq["date"]).dt.date
        qqq_close = qqq.set_index("day")["close"].astype(float)
        ema = qqq_close.ewm(span=trend_ema_window, adjust=False).mean()
        ema_slope = ema.diff()
        ema_slope = ema_slope.reindex(features.index).ffill()
    else:
        nq_close = features["close"].astype(float)
        ema = nq_close.ewm(span=trend_ema_window, adjust=False).mean()
        ema_slope = ema.diff()

    # Shift so that today's trend decision uses yesterday's slope.
    features["ema_slope"] = ema_slope.shift(1)
    features["ema_slope_sign"] = np.sign(features["ema_slope"]).fillna(0).astype(int)

    # Liquidity: today's session volume / rolling median of prior days.
    if len(session_vol) > 0:
        features = features.join(session_vol, how="left")
        features["session_vol_median_20d"] = (
            features["session_vol"]
            .shift(1)
            .rolling(liquidity_median_window_days, min_periods=liquidity_median_window_days)
            .median()
        )
        features["session_vol_ratio"] = (
            features["session_vol"] / features["session_vol_median_20d"]
        )
    else:
        features["session_vol"] = np.nan
        features["session_vol_median_20d"] = np.nan
        features["session_vol_ratio"] = np.nan

    features.index.name = "date"
    return features


# ---------------------------------------------------------------------------
# Individual filter functions (exposed for significance testing)
# ---------------------------------------------------------------------------


def _is_missing(value: Any) -> bool:
    """Return True if ``value`` is NaN or None."""
    if value is None:
        return True
    try:
        return bool(np.isnan(value))
    except TypeError:
        return False


def filter_overnight_gap(features_row: pd.Series, spec: FilterSpec) -> FilterDecision:
    """Overnight gap / drift filter.

    Reject both directions if the gap is extreme (``|gap_atr| > gap_atr_max_both``).
    Otherwise block longs if the gap is too negative (``gap_pct <
    gap_pct_min_long`` or ``gap_atr < -gap_atr_max_long``), and block shorts
    symmetrically for positive gaps.
    """
    gap_pct = features_row.get("gap_pct", np.nan)
    gap_atr = features_row.get("gap_atr", np.nan)

    if _is_missing(gap_pct) or _is_missing(gap_atr):
        # Insufficient history: do not filter. Preserves parity in warmup.
        return FilterDecision(True, True, {"gap_pct": gap_pct, "gap_atr": gap_atr})

    snapshot = {"gap_pct": float(gap_pct), "gap_atr": float(gap_atr)}

    if abs(gap_atr) > spec.gap_atr_max_both:
        return FilterDecision(False, False, snapshot)

    allow_long = gap_pct >= spec.gap_pct_min_long and gap_atr >= -spec.gap_atr_max_long
    allow_short = gap_pct <= spec.gap_pct_max_short and gap_atr <= spec.gap_atr_max_long
    return FilterDecision(bool(allow_long), bool(allow_short), snapshot)


def filter_prior_range(features_row: pd.Series, spec: FilterSpec) -> FilterDecision:
    """Prior-day range percentile filter.

    Reject both directions if yesterday's range was in the top tail of the
    trailing distribution (``range_pctile > range_percentile_max``). The idea
    is that regime is "exhausted" after an outsized range day and mean
    reversion dominates.
    """
    pctile = features_row.get("range_pctile", np.nan)
    if _is_missing(pctile):
        return FilterDecision(True, True, {"range_pctile": pctile})

    snapshot = {"range_pctile": float(pctile)}
    if pctile > spec.range_percentile_max:
        return FilterDecision(False, False, snapshot)
    return FilterDecision(True, True, snapshot)


def filter_trend(features_row: pd.Series, spec: FilterSpec) -> FilterDecision:
    """Daily trend proxy filter.

    Asymmetric: allows longs only in an uptrend (slope > 0) and shorts only
    in a downtrend (slope < 0). Flat regime (slope == 0) blocks both, but
    this is rare in practice since EMA slopes are almost never exactly zero.
    """
    slope_sign = features_row.get("ema_slope_sign", np.nan)
    slope = features_row.get("ema_slope", np.nan)
    if _is_missing(slope_sign):
        return FilterDecision(True, True, {"ema_slope_sign": slope_sign})

    sign = int(slope_sign)
    snapshot = {"ema_slope": float(slope) if not _is_missing(slope) else np.nan, "ema_slope_sign": sign}
    return FilterDecision(sign > 0, sign < 0, snapshot)


def filter_liquidity(features_row: pd.Series, spec: FilterSpec) -> FilterDecision:
    """Session liquidity filter.

    Reject both directions if today's opening-window volume is thin relative
    to the trailing median (``session_vol_ratio < liquidity_min_ratio``).
    Rationale: breakouts on low-volume days are more likely to fake and
    reverse, since institutional flow is absent.
    """
    ratio = features_row.get("session_vol_ratio", np.nan)
    if _is_missing(ratio):
        return FilterDecision(True, True, {"session_vol_ratio": ratio})

    snapshot = {"session_vol_ratio": float(ratio)}
    if ratio < spec.liquidity_min_ratio:
        return FilterDecision(False, False, snapshot)
    return FilterDecision(True, True, snapshot)


# ---------------------------------------------------------------------------
# Engine (conjunctive combiner)
# ---------------------------------------------------------------------------


class FilterEngine:
    """Filter evaluator with conjunctive combiner across enabled filters.

    Each filter returns its own ``FilterDecision`` (allow_long, allow_short);
    the engine ANDs them together. A disabled filter contributes
    ``(True, True)`` so it is a no-op.

    If ``daily_features is None`` (Phase 1 callers) the engine returns
    ``FilterDecision(True, True, {})`` unconditionally, preserving parity.
    """

    def __init__(
        self,
        spec: FilterSpec,
        daily_features: pd.DataFrame | None = None,
    ) -> None:
        self.spec = spec
        self.daily_features = daily_features

    def evaluate(self, day: Any, bar_idx: int, df_day: Any) -> FilterDecision:
        """Return the combined per-day decision for ``day``.

        Filters are day-scoped (not bar-scoped): the same decision applies to
        every bar on the given day, so this is safe to call once per day and
        cache, or call per-bar (cheap lookup).
        """
        if self.daily_features is None:
            return FilterDecision(True, True, {})

        try:
            row = self.daily_features.loc[day]
        except KeyError:
            # Day outside the feature frame (e.g. first day before any
            # history). Treat as warmup: allow both directions with empty
            # snapshot so the bar loop behaves like Phase 1.
            return FilterDecision(True, True, {})

        snapshot: dict = {}
        allow_long = True
        allow_short = True

        if self.spec.enable_overnight_gap:
            d = filter_overnight_gap(row, self.spec)
            allow_long &= d.allow_long
            allow_short &= d.allow_short
            snapshot.update({f"gap_{k}": v for k, v in d.snapshot.items()})
        if self.spec.enable_prior_range:
            d = filter_prior_range(row, self.spec)
            allow_long &= d.allow_long
            allow_short &= d.allow_short
            snapshot.update({f"range_{k}": v for k, v in d.snapshot.items()})
        if self.spec.enable_trend:
            d = filter_trend(row, self.spec)
            allow_long &= d.allow_long
            allow_short &= d.allow_short
            snapshot.update({f"trend_{k}": v for k, v in d.snapshot.items()})
        if self.spec.enable_liquidity:
            d = filter_liquidity(row, self.spec)
            allow_long &= d.allow_long
            allow_short &= d.allow_short
            snapshot.update({f"liquidity_{k}": v for k, v in d.snapshot.items()})

        return FilterDecision(bool(allow_long), bool(allow_short), snapshot)
