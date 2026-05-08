import numpy as np
import pandas as pd
import pytest

from nqbreakout.prop.apex import ApexAccount
from nqbreakout.prop.montecarlo import (
    block_bootstrap_days,
    block_bootstrap_regime_aware,
    block_bootstrap_with_extremes,
    block_bootstrap_with_shock_days,
    simulate_evaluation,
    run_mc_sweep,
    SimResult,
)


def make_account(**overrides):
    base = dict(
        name="test",
        starting_balance=50000,
        profit_target=3000,
        trailing_threshold=2500,
        max_contracts_nq=6,
        max_contracts_mnq=60,
    )
    base.update(overrides)
    return ApexAccount(**base)


def test_block_bootstrap_length():
    rng = np.random.default_rng(0)
    hist = pd.Series(np.arange(100, dtype=float))
    out = block_bootstrap_days(hist, n_days=30, block_size=5, rng=rng)
    assert len(out) == 30


def test_block_bootstrap_deterministic():
    hist = pd.Series(np.arange(100, dtype=float))
    a = block_bootstrap_days(hist, 50, 10, np.random.default_rng(123))
    b = block_bootstrap_days(hist, 50, 10, np.random.default_rng(123))
    np.testing.assert_array_equal(a, b)


def test_block_bootstrap_raises_on_short_history():
    hist = pd.Series([1.0, 2.0, 3.0])
    with pytest.raises(ValueError):
        block_bootstrap_days(hist, 10, block_size=5, rng=np.random.default_rng(0))


def test_simulate_evaluation_pass_positive_drift():
    acc = make_account(min_trading_days=7)
    # daily pnl mean > 0, passes quickly after ~10 days
    rng = np.random.default_rng(42)
    hist = pd.Series(rng.normal(loc=500, scale=200, size=200))
    result = simulate_evaluation(hist, acc, max_days=60, block_size=10, rng=rng)
    assert isinstance(result, SimResult)
    # with that drift, pass rate should be non-trivial; either pass or a known reason
    assert result.reason in {"passed", "drawdown_violated", "timeout"}


def test_simulate_evaluation_drawdown_on_big_loss():
    acc = make_account(profit_target=10000, trailing_threshold=2500)
    # history dominated by big losses
    hist = pd.Series([-3000.0] * 50)
    rng = np.random.default_rng(0)
    result = simulate_evaluation(hist, acc, max_days=60, block_size=10, rng=rng)
    assert result.passed is False
    assert result.reason == "drawdown_violated"


def test_run_mc_sweep_returns_dataframe():
    acc = make_account()
    rng = np.random.default_rng(0)
    hist = pd.Series(rng.normal(loc=100, scale=500, size=300))
    df = run_mc_sweep(hist, acc, n_sims=20, max_days=40, block_size=5, seed=42)
    assert len(df) == 20
    assert set(df.columns) >= {"sim", "account", "passed", "reason", "final_balance", "days_traded", "floor_at_end"}


def test_pass_rate_in_unit_interval():
    acc = make_account()
    rng = np.random.default_rng(0)
    hist = pd.Series(rng.normal(loc=100, scale=500, size=300))
    df = run_mc_sweep(hist, acc, n_sims=50, seed=42)
    pr = df["passed"].mean()
    assert 0.0 <= pr <= 1.0


# =============================================================================
# Phase 10B — Regime-aware bootstrap tests
# =============================================================================


def _make_regime_inputs(n=300, seed=0):
    """Build a synthetic series with two distinct regimes for testing.

    Regime "low" days have mean=0, std=1; regime "high" days have mean=10, std=1.
    Sequence: 50 low, 50 high, 50 low, 50 high, ...  (sticky regimes).
    """
    rng = np.random.default_rng(seed)
    blocks = []
    regimes = []
    for i in range(n // 50):
        label = "low" if i % 2 == 0 else "high"
        mean = 0.0 if label == "low" else 10.0
        blocks.append(rng.normal(mean, 1.0, size=50))
        regimes.extend([label] * 50)
    pnl = pd.Series(np.concatenate(blocks))
    regs = pd.Series(regimes)
    return pnl, regs


def test_regime_aware_length_and_seed_determinism():
    pnl, regs = _make_regime_inputs(n=300, seed=1)
    state_labels = ["low", "high"]
    # Identity transition (sticky regimes)
    T = np.array([[1.0, 0.0], [0.0, 1.0]])
    stat = np.array([0.5, 0.5])
    out_a = block_bootstrap_regime_aware(
        pnl, regs, T, stat, state_labels, n_days=50, block_size=10,
        rng=np.random.default_rng(42),
    )
    out_b = block_bootstrap_regime_aware(
        pnl, regs, T, stat, state_labels, n_days=50, block_size=10,
        rng=np.random.default_rng(42),
    )
    assert len(out_a) == 50
    np.testing.assert_array_equal(out_a, out_b)


def test_regime_aware_sticky_low_yields_low_mean():
    """If we lock into 'low' regime, sample mean should match low-regime mean (~0)."""
    pnl, regs = _make_regime_inputs(n=300, seed=2)
    state_labels = ["low", "high"]
    # Force always start in low, never transition out
    T = np.array([[1.0, 0.0], [1.0, 0.0]])
    stat = np.array([1.0, 0.0])
    out = block_bootstrap_regime_aware(
        pnl, regs, T, stat, state_labels, n_days=500, block_size=5,
        rng=np.random.default_rng(7),
    )
    # Mean should be near 0 (low regime mean), not near 5 (overall mean)
    assert abs(np.mean(out)) < 1.0


def test_regime_aware_sticky_high_yields_high_mean():
    pnl, regs = _make_regime_inputs(n=300, seed=3)
    state_labels = ["low", "high"]
    T = np.array([[0.0, 1.0], [0.0, 1.0]])
    stat = np.array([0.0, 1.0])
    out = block_bootstrap_regime_aware(
        pnl, regs, T, stat, state_labels, n_days=500, block_size=5,
        rng=np.random.default_rng(11),
    )
    # Mean should be near 10 (high regime mean)
    assert abs(np.mean(out) - 10.0) < 1.0


def test_regime_aware_raises_on_short_history():
    pnl = pd.Series([1.0, 2.0, 3.0])
    regs = pd.Series(["low", "low", "low"])
    T = np.eye(2)
    stat = np.array([1.0, 0.0])
    with pytest.raises(ValueError):
        block_bootstrap_regime_aware(
            pnl, regs, T, stat, ["low", "high"], n_days=5, block_size=10,
            rng=np.random.default_rng(0),
        )


def test_regime_aware_raises_on_missing_regime_starts():
    """If a state has no valid block starts in history, must raise."""
    pnl = pd.Series(np.arange(50, dtype=float))
    regs = pd.Series(["low"] * 50)  # No 'high' days at all
    T = np.array([[0.5, 0.5], [0.5, 0.5]])
    stat = np.array([0.5, 0.5])
    with pytest.raises(ValueError, match="No valid block starts"):
        block_bootstrap_regime_aware(
            pnl, regs, T, stat, ["low", "high"], n_days=20, block_size=5,
            rng=np.random.default_rng(0),
        )


def test_regime_aware_uniform_transition_approx_block_bootstrap():
    """With uniform transition, sample mean should approximate full-series mean."""
    pnl, regs = _make_regime_inputs(n=300, seed=4)
    state_labels = ["low", "high"]
    T = np.array([[0.5, 0.5], [0.5, 0.5]])
    stat = np.array([0.5, 0.5])
    out = block_bootstrap_regime_aware(
        pnl, regs, T, stat, state_labels, n_days=2000, block_size=5,
        rng=np.random.default_rng(13),
    )
    # Overall mean of inputs is ~5; sample mean should be close
    assert abs(np.mean(out) - pnl.mean()) < 1.0


# =============================================================================
# Phase 12: shock-day injected sampler
# =============================================================================


def test_shock_sampler_zero_prob_matches_block_bootstrap():
    """shock_prob=0 must be bitwise identical to block_bootstrap_days."""
    hist = pd.Series(np.arange(200, dtype=float))
    a = block_bootstrap_with_shock_days(
        hist, shock_pnl=-99999.0, shock_prob=0.0,
        n_days=300, block_size=10, rng=np.random.default_rng(7),
    )
    b = block_bootstrap_days(hist, n_days=300, block_size=10,
                             rng=np.random.default_rng(7))
    np.testing.assert_array_equal(a, b)


def test_shock_sampler_deterministic():
    hist = pd.Series(np.arange(200, dtype=float))
    a = block_bootstrap_with_shock_days(
        hist, shock_pnl=-50000.0, shock_prob=0.05,
        n_days=500, block_size=10, rng=np.random.default_rng(42),
    )
    b = block_bootstrap_with_shock_days(
        hist, shock_pnl=-50000.0, shock_prob=0.05,
        n_days=500, block_size=10, rng=np.random.default_rng(42),
    )
    np.testing.assert_array_equal(a, b)


def test_shock_sampler_length():
    hist = pd.Series(np.arange(200, dtype=float))
    out = block_bootstrap_with_shock_days(
        hist, shock_pnl=-50000.0, shock_prob=0.10,
        n_days=123, block_size=7, rng=np.random.default_rng(1),
    )
    assert len(out) == 123


def test_shock_sampler_injects_shock_value():
    """Large shock_prob produces samples below the historical minimum."""
    hist = pd.Series(np.arange(200, dtype=float))  # min = 0
    out = block_bootstrap_with_shock_days(
        hist, shock_pnl=-99999.0, shock_prob=0.5,
        n_days=2000, block_size=10, rng=np.random.default_rng(0),
    )
    n_shock = int((out == -99999.0).sum())
    # Expected ~1000 shocks; allow generous margin
    assert 800 <= n_shock <= 1200
    # Sample minimum equals shock value
    assert out.min() == -99999.0


# =============================================================================
# Phase 13: MAE-aware extremes sampler
# =============================================================================


def _make_extremes_df(n=200, seed=0):
    rng = np.random.default_rng(seed)
    pnl = rng.normal(50, 200, size=n)
    # intraday_low_delta: more negative than min(pnl, 0); add extra MAE
    intraday_low = np.minimum(pnl, 0) - rng.uniform(100, 500, size=n)
    intraday_high = np.maximum(pnl, 0) + rng.uniform(50, 300, size=n)
    return pd.DataFrame({
        "daily_pnl": pnl,
        "intraday_low_delta": intraday_low,
        "intraday_high_delta": intraday_high,
    })


def test_extremes_sampler_shape_and_columns():
    df = _make_extremes_df(n=200)
    out = block_bootstrap_with_extremes(
        df, n_days=300, block_size=10, rng=np.random.default_rng(0)
    )
    assert out.shape == (300, 3)


def test_extremes_sampler_deterministic():
    df = _make_extremes_df(n=200)
    a = block_bootstrap_with_extremes(df, 100, 5, np.random.default_rng(7))
    b = block_bootstrap_with_extremes(df, 100, 5, np.random.default_rng(7))
    np.testing.assert_array_equal(a, b)


def test_extremes_sampler_preserves_triplet_correlation():
    """Sampled day's (pnl, low, high) must be JOINT — same row from history."""
    df = _make_extremes_df(n=200)
    out = block_bootstrap_with_extremes(df, 500, 1, np.random.default_rng(1))
    # Each sampled triplet must appear as a row in the historical data
    hist_set = set(map(tuple, df[["daily_pnl", "intraday_low_delta",
                                    "intraday_high_delta"]].values.round(8)))
    sampled_set = set(map(tuple, out.round(8)))
    assert sampled_set.issubset(hist_set)


def test_extremes_sampler_low_le_zero_high_ge_zero():
    """All sampled low_deltas should be <= 0, all high_deltas >= 0 by construction."""
    df = _make_extremes_df(n=200)
    out = block_bootstrap_with_extremes(df, 300, 5, np.random.default_rng(2))
    assert (out[:, 1] <= 0).all()
    assert (out[:, 2] >= 0).all()


def test_extremes_sampler_missing_column_raises():
    df = pd.DataFrame({"daily_pnl": np.arange(50.0),
                        "intraday_low_delta": -np.arange(50.0)})  # missing high
    with pytest.raises(ValueError, match="missing columns"):
        block_bootstrap_with_extremes(df, 30, 5, np.random.default_rng(0))


def test_extremes_sampler_short_history_raises():
    df = _make_extremes_df(n=3)
    with pytest.raises(ValueError, match="< block_size"):
        block_bootstrap_with_extremes(df, 10, 5, np.random.default_rng(0))


def test_shock_sampler_invalid_prob_raises():
    hist = pd.Series(np.arange(50, dtype=float))
    for bad in (-0.1, 1.5):
        with pytest.raises(ValueError):
            block_bootstrap_with_shock_days(
                hist, shock_pnl=0.0, shock_prob=bad,
                n_days=10, block_size=5, rng=np.random.default_rng(0),
            )
