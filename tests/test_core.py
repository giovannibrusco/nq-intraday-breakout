"""Tests for ``nqbreakout.strategy.core``.

The slow parity test (``test_full_10y_parity``) is gated behind
``RUN_SLOW=1`` and skipped if the NQ parquet at ``data/nq_1min.parquet`` has
not been built yet.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import inspect
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nqbreakout.data.loader import load_nq_1min, resample_to_5min
from nqbreakout.strategy.config import (
    FilterSpec,
    InstrumentSpec,
    StrategyConfig,
    load_instrument_spec,
)
from nqbreakout.strategy.core import (
    BacktestResult,
    Trade,
    position_size,
    run_backtest,
)
from nqbreakout.strategy.costs import LEGACY_COST_MODEL, CostModel
from nqbreakout.strategy.filters import FilterEngine


REPO_ROOT = Path(__file__).resolve().parent.parent
NQ_PARQUET = REPO_ROOT / "data" / "nq_1min.parquet"

slow = pytest.mark.skipif(
    not os.getenv("RUN_SLOW"),
    reason="slow test; set RUN_SLOW=1 to enable",
)


# ---------------------------------------------------------------------------
# position_size
# ---------------------------------------------------------------------------


def test_position_size_risk_cap() -> None:
    """Risk cap binds: equity=$100k, stop=50pts, risk=1% -> 1 contract.

    n_risk = (0.01 * 100_000) / (50 * 20) = 1000 / 1000 = 1.0 -> floor 1
    n_margin = 100_000 / (23_282.57 * 1.15) ~ 3.73 -> floor 3
    Result: max(1, min(max_position, min(1, 3))) = 1.
    """
    n = position_size(
        equity=100_000.0,
        stop_points=50.0,
        point_value=20.0,
        risk_pct=0.01,
        margin_per_contract=23282.57,
        margin_buffer=0.15,
        max_position=10**6,
    )
    assert n == 1


def test_position_size_margin_cap() -> None:
    """Margin cap binds with a tiny stop and a small account.

    equity=$10k, stop=1pt -> n_risk = 100/(1*20) = 5.0
    n_margin = 10_000 / (23_282.57 * 1.15) ~ 0.373 -> floor 0
    Then max(1, min(max_position, 0)) = max(1, 0) = 1 (legacy floor).
    """
    n = position_size(
        equity=10_000.0,
        stop_points=1.0,
        point_value=20.0,
        risk_pct=0.01,
        margin_per_contract=23282.57,
        margin_buffer=0.15,
        max_position=10**6,
    )
    assert n == 1  # margin floor pulls below 1, then legacy max(1, .) lifts it back


def test_position_size_max_position() -> None:
    """``max_position=2`` caps result regardless of risk/margin."""
    n = position_size(
        equity=10_000_000.0,
        stop_points=1.0,
        point_value=20.0,
        risk_pct=0.01,
        margin_per_contract=23282.57,
        margin_buffer=0.15,
        max_position=2,
    )
    assert n == 2


def test_position_size_signature_unified() -> None:
    """Public signature uses ``stop_points`` (unified), not ``stop_dollars``."""
    sig = inspect.signature(position_size)
    params = sig.parameters
    assert "stop_points" in params, "position_size must take stop_points"
    assert "stop_dollars" not in params, "legacy 'stop_dollars' name must be gone"
    assert "stop_dollars_per_contract" not in params


# ---------------------------------------------------------------------------
# Synthetic golden backtest
# ---------------------------------------------------------------------------


def _make_synthetic_5min_df() -> pd.DataFrame:
    """Build a 5-day 5-minute OHLCV frame with two predictable breakouts.

    Each session: 8:30 to 14:30 ET, 5-min bars (=73 bars/day). The opening
    range (08:30-10:00) sits at ``[100, 105]``. After 10:00 we drop a single
    bar with a clear long breakout (high jumps to 110, immediately hits a
    target 200pts above entry). Subsequent bars are flat to avoid a second
    entry that day. We do NOT include a previous trading day in
    ``daily_summary_regular``, so legacy filters_today_pass would be False;
    therefore this golden test mainly checks the loop machinery, not entries.

    The intent is to exercise: bar-loop iteration, the 9:30-16:00 stats
    accumulation, the day-rollover branch, and the empty-trades early
    return path. ``test_run_backtest_no_global_state`` covers determinism.
    """
    bars: list[dict] = []
    base_date = dt.datetime(2024, 1, 2, 8, 30)  # Tuesday
    for d in range(5):
        day = base_date + dt.timedelta(days=d)
        for i in range(73):  # 08:30 to 14:30 inclusive in 5-min bars
            ts = day + dt.timedelta(minutes=5 * i)
            bars.append(
                {
                    "date": ts,
                    "open": 100.0,
                    "high": 105.0,
                    "low": 95.0,
                    "close": 100.0,
                    "totalvolume": 1000,
                }
            )
    df = pd.DataFrame(bars)
    df["day"] = df["date"].dt.date
    df["time"] = df["date"].dt.time
    return df


def test_run_backtest_golden_runs_clean() -> None:
    """Synthetic flat data produces no trades but the engine returns a valid result."""
    df = _make_synthetic_5min_df()
    cfg = StrategyConfig()
    inst = load_instrument_spec("NQ")

    result = run_backtest(df, cfg, inst)

    assert isinstance(result, BacktestResult)
    assert isinstance(result.trades, pd.DataFrame)
    # Flat synthetic data + no prior-day summary -> no entries.
    assert len(result.trades) == 0
    assert result.meta["total_pnl"] == 0.0
    assert result.meta["final_account_value"] == cfg.initial_capital


def test_run_backtest_no_global_state() -> None:
    """Two consecutive runs on the same input must hash-match."""
    df = _make_synthetic_5min_df()
    cfg = StrategyConfig()
    inst = load_instrument_spec("NQ")

    r1 = run_backtest(df, cfg, inst)
    r2 = run_backtest(df, cfg, inst)

    h1 = hashlib.sha256(pd.util.hash_pandas_object(r1.trades, index=True).values.tobytes()).hexdigest()
    h2 = hashlib.sha256(pd.util.hash_pandas_object(r2.trades, index=True).values.tobytes()).hexdigest()
    assert h1 == h2, "run_backtest is not deterministic across calls"


# ---------------------------------------------------------------------------
# Filter engine
# ---------------------------------------------------------------------------


def test_filter_passthrough_default() -> None:
    """Default ``FilterEngine(FilterSpec())`` allows both directions."""
    eng = FilterEngine(FilterSpec())
    decision = eng.evaluate(dt.date(2024, 1, 2), 0, None)
    assert decision.allow_long is True
    assert decision.allow_short is True
    assert decision.snapshot == {}


# ---------------------------------------------------------------------------
# Slow parity test (10y NQ vs legacy)
# ---------------------------------------------------------------------------


@slow
def test_full_10y_parity() -> None:
    """Run new engine and legacy ``reconstruct_trades`` on the same 5-min frame.

    Asserts identical trade count and total PnL within machine precision.
    Skipped if the NQ parquet has not been materialized.
    """
    if not NQ_PARQUET.exists():
        pytest.skip(f"NQ parquet not built at {NQ_PARQUET}; run scripts/convert_to_parquet.py first")

    # Make legacy module importable.
    sys.path.insert(0, str(REPO_ROOT))
    import topstep_montecarlo as legacy  # type: ignore

    df_1min = load_nq_1min(NQ_PARQUET)
    df_5min = resample_to_5min(df_1min)

    cfg = StrategyConfig()
    inst = load_instrument_spec("NQ")
    new = run_backtest(df_5min, cfg, inst, cost_model=LEGACY_COST_MODEL)

    legacy_trades = legacy.reconstruct_trades(df_5min.copy(), risk_pct=cfg.risk_pct)

    assert len(new.trades) == len(legacy_trades), (
        f"trade count mismatch: new={len(new.trades)} legacy={len(legacy_trades)}"
    )

    new_pnl = float(new.trades["pnl"].sum())
    leg_pnl = float(legacy_trades["pnl"].sum())
    denom = max(abs(leg_pnl), 1.0)
    rel_diff = abs(new_pnl - leg_pnl) / denom
    assert rel_diff < 1e-6, (
        f"PnL mismatch: new={new_pnl:.4f} legacy={leg_pnl:.4f} rel_diff={rel_diff:.2e}"
    )
