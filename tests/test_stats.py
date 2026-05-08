"""Comprehensive tests for Phase 5 stats modules.

Tests cover: metrics, bootstrap, walk-forward, grid search.
Slow tests (requiring real parquet data) are gated behind @pytest.mark.slow.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nqbreakout.stats.bootstrap import (
    bootstrap_all_metrics,
    bootstrap_statistic_ci,
    moving_block_bootstrap,
)
from nqbreakout.stats.gridsearch import (
    load_grid,
    pick_robust_config,
    run_grid,
    stability_score,
)
from nqbreakout.stats.metrics import (
    calmar,
    max_drawdown,
    max_drawdown_pct,
    profit_factor,
    sharpe,
    sortino,
    summary,
    win_rate,
)
from nqbreakout.stats.walkforward import WFResult, WFFold, generate_folds, run_walkforward
from nqbreakout.strategy.config import InstrumentSpec, StrategyConfig, load_instrument_spec

REPO_ROOT = Path(__file__).resolve().parent.parent
NQ_PARQUET = REPO_ROOT / "data" / "nq_1min.parquet"
GRID_YAML = REPO_ROOT / "configs" / "grid.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pnl(n: int = 200, seed: int = 0) -> pd.Series:
    """Synthetic daily PnL with alternating +100 / -50."""
    rng = np.random.default_rng(seed)
    vals = rng.choice([100.0, -50.0], size=n)
    return pd.Series(vals, dtype=float)


def _make_equity(pnl: pd.Series, start: float = 100_000.0) -> pd.Series:
    return pd.Series(start + pnl.cumsum().values, dtype=float)


def _synthetic_5min_df(start: str = "2020-01-02", n_days: int = 30) -> pd.DataFrame:
    """Create a minimal synthetic 5-min DataFrame for walk-forward tests."""
    rows = []
    day = pd.Timestamp(start)
    for _ in range(n_days):
        if day.weekday() < 5:  # mon-fri only
            for hour in range(9, 15):
                for minute in range(0, 60, 5):
                    ts = day.replace(hour=hour, minute=minute, second=0)
                    rows.append(
                        {
                            "date": ts,
                            "open": 15000.0,
                            "high": 15010.0,
                            "low": 14990.0,
                            "close": 15005.0,
                            "totalvolume": 1000,
                        }
                    )
        day += pd.Timedelta(days=1)
    df = pd.DataFrame(rows)
    df["day"] = df["date"].dt.date
    df["time"] = df["date"].dt.time
    return df


# ---------------------------------------------------------------------------
# metrics.py
# ---------------------------------------------------------------------------


def test_sharpe_known_values():
    """Verify Sharpe formula against hand-computed value."""
    pnl = pd.Series([100.0, -50.0] * 50)  # 100 values
    mean = pnl.mean()
    std = pnl.std(ddof=1)
    expected = mean / std * np.sqrt(252.0)
    result = sharpe(pnl)
    assert abs(result - expected) < 1e-10, f"sharpe={result}, expected={expected}"


def test_sharpe_empty_returns_nan():
    assert np.isnan(sharpe(pd.Series([], dtype=float)))


def test_sharpe_zero_std_returns_nan():
    assert np.isnan(sharpe(pd.Series([50.0] * 20)))


def test_sortino_uses_downside_only():
    """Sortino should differ from Sharpe when distribution is asymmetric."""
    pnl = pd.Series([200.0, -10.0] * 50)
    s = sharpe(pnl)
    so = sortino(pnl)
    # Large positive returns inflate Sharpe denominator but not Sortino
    assert so > s, f"sortino={so} should > sharpe={s} for right-skewed returns"


def test_sortino_no_downside_returns_nan():
    """All positive returns => downside std = 0 => nan."""
    pnl = pd.Series([50.0, 100.0, 75.0])
    assert np.isnan(sortino(pnl))


def test_max_drawdown_simple():
    """equity [100, 120, 90, 110] => max drawdown = 30."""
    equity = pd.Series([100.0, 120.0, 90.0, 110.0])
    assert max_drawdown(equity) == pytest.approx(30.0)


def test_max_drawdown_monotone_up_is_zero():
    equity = pd.Series([100.0, 110.0, 120.0, 130.0])
    assert max_drawdown(equity) == pytest.approx(0.0)


def test_max_drawdown_pct_simple():
    equity = pd.Series([100.0, 120.0, 90.0, 110.0])
    # Peak = 120, trough = 90 => 30/120 = 0.25
    assert max_drawdown_pct(equity) == pytest.approx(0.25)


def test_calmar_positive():
    pnl = _make_pnl(200)
    equity = _make_equity(pnl)
    c = calmar(pnl, equity)
    assert not np.isnan(c)
    # Calmar = ann_return / mdd_pct; both should be positive for mixed pnl
    assert isinstance(c, float)


def test_profit_factor_known():
    trade_pnl = pd.Series([100.0, 200.0, -50.0, -100.0])
    # winners = 300, losers = 150 => pf = 2.0
    assert profit_factor(trade_pnl) == pytest.approx(2.0)


def test_profit_factor_no_losers_returns_nan():
    assert np.isnan(profit_factor(pd.Series([100.0, 200.0])))


def test_win_rate_known():
    trade_pnl = pd.Series([100.0, -50.0, 200.0, -30.0])
    assert win_rate(trade_pnl) == pytest.approx(0.5)


def test_summary_returns_all_keys():
    pnl = _make_pnl(100)
    equity = _make_equity(pnl)
    result = summary(pnl, equity, trade_pnl=pnl)
    for k in ("sharpe", "sortino", "max_dd", "max_dd_pct", "calmar",
               "total_pnl", "n_days", "profit_factor", "win_rate", "n_trades"):
        assert k in result, f"Key '{k}' missing from summary"


# ---------------------------------------------------------------------------
# bootstrap.py
# ---------------------------------------------------------------------------


def test_moving_block_bootstrap_shape():
    rng = np.random.default_rng(42)
    x = pd.Series(np.arange(100, dtype=float))
    result = moving_block_bootstrap(x, block_size=10, n_resamples=50, rng=rng)
    assert result.shape == (50, 100), f"Expected (50, 100), got {result.shape}"


def test_moving_block_bootstrap_deterministic():
    x = pd.Series(np.random.default_rng(0).random(100))
    r1 = moving_block_bootstrap(x, block_size=10, n_resamples=20, rng=np.random.default_rng(42))
    r2 = moving_block_bootstrap(x, block_size=10, n_resamples=20, rng=np.random.default_rng(42))
    np.testing.assert_array_equal(r1, r2)


def test_moving_block_bootstrap_values_from_original():
    """Every resampled value must come from the original series."""
    x = pd.Series(np.arange(50, dtype=float) * 10)
    rng = np.random.default_rng(0)
    result = moving_block_bootstrap(x, block_size=5, n_resamples=10, rng=rng)
    original_set = set(x.values.tolist())
    for row in result:
        for val in row:
            assert val in original_set, f"Value {val} not in original series"


def test_bootstrap_ci_contains_point_estimate():
    """With large n_resamples the CI should bracket the point estimate."""
    pnl = pd.Series(np.random.default_rng(42).normal(loc=50, scale=200, size=500))
    ci = bootstrap_statistic_ci(pnl, sharpe, block_size=10, n_resamples=500, alpha=0.05, seed=42)
    # Point estimate should be within [ci_lower, ci_upper] for bootstrapped metric
    # (percentile CI -- not guaranteed for all distributions but holds here)
    assert ci["ci_lower"] <= ci["point"] <= ci["ci_upper"], (
        f"Point {ci['point']:.4f} outside CI [{ci['ci_lower']:.4f}, {ci['ci_upper']:.4f}]"
    )


def test_bootstrap_ci_on_constant_series():
    """Constant PnL => zero std => sharpe=nan; CI should reflect degenerate case."""
    pnl = pd.Series([100.0] * 50)
    ci = bootstrap_statistic_ci(pnl, sharpe, block_size=5, n_resamples=100, seed=42)
    # sharpe of constant series is nan; bootstrap of constant series should also yield nan
    assert np.isnan(ci["point"])


def test_bootstrap_all_metrics_returns_dataframe():
    pnl = _make_pnl(200)
    equity = _make_equity(pnl)
    df = bootstrap_all_metrics(pnl, equity, block_size=10, n_resamples=100, seed=42)
    assert isinstance(df, pd.DataFrame)
    assert set(df["metric"]) >= {"sharpe", "sortino", "calmar", "max_dd_pct"}
    assert "ci_lower" in df.columns
    assert "ci_upper" in df.columns


# ---------------------------------------------------------------------------
# walkforward.py
# ---------------------------------------------------------------------------


def test_generate_folds_count():
    """10-year date range, train=5y, test=1y, step=1y -> 5 folds."""
    rows = [{"date": pd.Timestamp("2010-01-01") + pd.Timedelta(days=i)} for i in range(3650 + 365)]
    df = pd.DataFrame(rows)
    df["open"] = df["high"] = df["low"] = df["close"] = 1.0
    folds = generate_folds(df, train_years=5, test_years=1, step_years=1)
    assert len(folds) == 5, f"Expected 5 folds, got {len(folds)}"


def test_generate_folds_no_overlap_test():
    """Test windows must be non-overlapping."""
    rows = [{"date": pd.Timestamp("2010-01-01") + pd.Timedelta(days=i)} for i in range(3650 + 365)]
    df = pd.DataFrame(rows)
    df["open"] = df["high"] = df["low"] = df["close"] = 1.0
    folds = generate_folds(df, train_years=5, test_years=1, step_years=1)
    for i in range(len(folds) - 1):
        assert folds[i + 1].test_start > folds[i].test_end, (
            f"Fold {i} and {i + 1} test windows overlap"
        )


def test_generate_folds_train_before_test():
    """Train must always end before test starts."""
    rows = [{"date": pd.Timestamp("2010-01-01") + pd.Timedelta(days=i)} for i in range(3650 + 365)]
    df = pd.DataFrame(rows)
    df["open"] = df["high"] = df["low"] = df["close"] = 1.0
    folds = generate_folds(df, train_years=5, test_years=1, step_years=1)
    for fold in folds:
        assert fold.train_end < fold.test_start, f"Fold {fold.fold_id}: train overlaps test"


def test_run_walkforward_no_optimizer_smoke():
    """Walk-forward on synthetic 3-year data runs clean and returns WFResult."""
    # Build a 3-year synthetic 5-min dataset: 600 trading days ~ 2.4 calendar years
    rows = []
    day = pd.Timestamp("2020-01-02")
    days_added = 0
    while days_added < 600:
        if day.weekday() < 5:
            for h in [9, 10, 11, 12, 13, 14]:
                ts = day.replace(hour=h, minute=0, second=0)
                rows.append({
                    "date": ts,
                    "open": 15000.0,
                    "high": 15020.0,
                    "low": 14980.0,
                    "close": 15005.0,
                    "totalvolume": 5000,
                })
            days_added += 1
        day += pd.Timedelta(days=1)
    df = pd.DataFrame(rows)
    df["day"] = df["date"].dt.date
    df["time"] = df["date"].dt.time

    config = StrategyConfig()
    instr = load_instrument_spec("NQ")
    wf = run_walkforward(df, config, instr, train_years=1, test_years=1, step_years=1)
    assert isinstance(wf, WFResult)
    assert len(wf.folds) >= 1
    assert isinstance(wf.summary, pd.DataFrame)
    required_cols = {
        "fold_id", "train_sharpe", "train_pnl", "train_trades",
        "test_sharpe", "test_pnl", "test_trades", "oos_degradation",
    }
    assert required_cols.issubset(set(wf.summary.columns)), (
        f"Missing columns: {required_cols - set(wf.summary.columns)}"
    )


def test_run_walkforward_insufficient_data_raises():
    """Less data than train+test window should raise ValueError."""
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=100, freq="5min")})
    df["open"] = df["high"] = df["low"] = df["close"] = 15000.0
    df["day"] = df["date"].dt.date
    df["time"] = df["date"].dt.time
    config = StrategyConfig()
    instr = load_instrument_spec("NQ")
    with pytest.raises(ValueError, match="No folds"):
        run_walkforward(df, config, instr, train_years=5, test_years=1, step_years=1)


# ---------------------------------------------------------------------------
# gridsearch.py
# ---------------------------------------------------------------------------


def test_load_grid_yaml():
    """Grid YAML loads successfully with expected keys."""
    grid = load_grid(GRID_YAML)
    assert "stop_points" in grid
    assert "target_points" in grid
    assert isinstance(grid["stop_points"], list)
    assert len(grid["stop_points"]) > 0


def test_run_grid_returns_all_combos():
    """2 params x 2 values = 4 rows."""
    df = _synthetic_5min_df(n_days=60)
    config = StrategyConfig()
    instr = load_instrument_spec("NQ")
    grid = {"stop_points": [75.0, 125.0], "target_points": [150.0, 250.0]}
    result = run_grid(df, config, instr, grid)
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 4, f"Expected 4 rows, got {len(result)}"
    assert "sharpe" in result.columns
    assert "stop_points" in result.columns


def test_run_grid_columns_complete():
    """All required metric columns must be present."""
    df = _synthetic_5min_df(n_days=60)
    config = StrategyConfig()
    instr = load_instrument_spec("NQ")
    grid = {"stop_points": [100.0], "target_points": [200.0]}
    result = run_grid(df, config, instr, grid)
    for col in ("total_pnl", "n_trades", "sharpe", "sortino", "calmar",
                "max_dd_pct", "profit_factor", "win_rate"):
        assert col in result.columns, f"Column '{col}' missing"


def test_stability_score_adds_column():
    df = _synthetic_5min_df(n_days=60)
    config = StrategyConfig()
    instr = load_instrument_spec("NQ")
    grid = {"stop_points": [75.0, 100.0, 125.0], "target_points": [150.0, 200.0]}
    grid_results = run_grid(df, config, instr, grid)
    scored = stability_score(grid_results, metric="sharpe", param="stop_points", neighbors=1)
    assert "stability_sharpe_stop_points" in scored.columns


def test_stability_score_neighbors_are_means():
    """For a 3-point axis with values [A, B, C], middle row's stability = mean(A, B, C)."""
    # Build a minimal grid result by hand.
    df_manual = pd.DataFrame({
        "stop_points": [75.0, 100.0, 125.0],
        "target_points": [200.0, 200.0, 200.0],
        "sharpe": [0.5, 1.0, 0.7],
        "total_pnl": [0.0, 0.0, 0.0],
        "n_trades": [0, 0, 0],
        "sortino": [float("nan")] * 3,
        "calmar": [float("nan")] * 3,
        "max_dd_pct": [float("nan")] * 3,
        "profit_factor": [float("nan")] * 3,
        "win_rate": [float("nan")] * 3,
    })
    scored = stability_score(df_manual, metric="sharpe", param="stop_points", neighbors=1)
    middle_row = scored[scored["stop_points"] == 100.0].iloc[0]
    # Middle row neighbors on stop_points: [75, 100, 125] => mean sharpe = (0.5+1.0+0.7)/3
    expected = (0.5 + 1.0 + 0.7) / 3
    assert abs(middle_row["stability_sharpe_stop_points"] - expected) < 1e-10


def test_pick_robust_config_returns_series():
    df = _synthetic_5min_df(n_days=60)
    config = StrategyConfig()
    instr = load_instrument_spec("NQ")
    grid = {"stop_points": [75.0, 100.0, 125.0], "target_points": [150.0, 200.0]}
    grid_results = run_grid(df, config, instr, grid)
    scored = stability_score(grid_results, metric="sharpe", param="stop_points")
    best = pick_robust_config(scored, primary_metric="sharpe", stability_weight=0.5)
    assert isinstance(best, pd.Series)
    assert "stop_points" in best.index
    assert "target_points" in best.index


def test_pick_robust_config_without_stability_column():
    """pick_robust_config falls back to pure metric when no stability column exists."""
    data = pd.DataFrame({
        "stop_points": [75.0, 100.0, 125.0],
        "sharpe": [0.3, 0.9, 0.6],
        "total_pnl": [1000.0, 2000.0, 1500.0],
        "n_trades": [10, 15, 12],
        "sortino": [float("nan")] * 3,
        "calmar": [float("nan")] * 3,
        "max_dd_pct": [float("nan")] * 3,
        "profit_factor": [float("nan")] * 3,
        "win_rate": [float("nan")] * 3,
    })
    best = pick_robust_config(data, primary_metric="sharpe")
    assert best["stop_points"] == 100.0  # highest sharpe


# ---------------------------------------------------------------------------
# Slow tests (require real parquet)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_run_walkforward_real_data():
    """WF on 4-year real data slice (smoke test, requires parquet)."""
    from nqbreakout.data.loader import load_nq_1min, resample_to_5min

    if not NQ_PARQUET.exists():
        pytest.skip("NQ parquet not found")

    df = resample_to_5min(load_nq_1min(NQ_PARQUET))
    df_sample = df.loc[(df["date"] >= "2021-01-01") & (df["date"] <= "2024-12-31")]
    config = StrategyConfig()
    instr = load_instrument_spec("NQ")
    wf = run_walkforward(df_sample, config, instr, train_years=1, test_years=1, step_years=1)
    assert isinstance(wf.summary, pd.DataFrame)
    assert len(wf.summary) >= 1


@pytest.mark.slow
def test_run_grid_real_small():
    """Grid search on 2 combos using real data (smoke test)."""
    from nqbreakout.data.loader import load_nq_1min, resample_to_5min

    if not NQ_PARQUET.exists():
        pytest.skip("NQ parquet not found")

    df = resample_to_5min(load_nq_1min(NQ_PARQUET))
    df_sample = df.loc[(df["date"] >= "2023-01-01") & (df["date"] <= "2023-06-30")]
    config = StrategyConfig()
    instr = load_instrument_spec("NQ")
    grid = {"stop_points": [75.0, 125.0]}
    result = run_grid(df_sample, config, instr, grid)
    assert len(result) == 2
    assert "sharpe" in result.columns
