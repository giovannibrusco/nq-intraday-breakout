"""Phase E rev regression tests: TIER_TO_PARQUET scaling factor.

Asserts the per-tier conversion factors in `scripts/phase9c_lifecycle_mc.py`.
**All tiers now backtest at 1 NQ direct ($20/pt) = NQ-equivalent scale, so
factor=1.0 uniform across tiers (no MNQ→NQ conversion needed).**

History:
  - Phase 0-13: 25K backtested at 1 MNQ ($2/pt) due to legacy margin check
    (NQ_margin > 25K capital). Required factor=10 to convert MNQ→NQ-eq.
    Other tiers (50K+) at 1 NQ direct → factor=1.
  - Phase 14: incorrectly tried factor=1 for 25K WITHOUT changing backtest
    scale → broke per-capital leverage parity. Reverted in Phase 14R.
  - Phase 14R: factor=10 restored as correct under MNQ backtest. Regression
    test added asserting 25K=10.0 (under Phase 0-13 convention).
  - **Phase E rev (current):** root-cause fix → backtest 25K changed to 1 NQ
    direct (matches live PA L0 trader scale). Factor=1.0 now consistent with
    NEW backtest scale. DLL applied at backtest level via run_backtest.

These tests prevent both regression directions:
  1. NQ tiers must stay 1.0 (no accidental scaling).
  2. 25K must stay 1.0 under Phase E rev convention (no reverting to MNQ scale).
  3. `load_pnl_per_mini` returns the series multiplied by factor (sanity).

Per-capital leverage at PA L0 remains identical by Apex design:
  25K: 1 NQ × $20/pt / $25K = 0.08%
  50K: 2 NQ × $20/pt / $50K = 0.08%
  Both achieved via factor=1.0 with backtest at 1 NQ direct.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# scripts/ is not a package — add to sys.path for import
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from phase9c_lifecycle_mc import TIER_TO_PARQUET, load_pnl_per_mini  # noqa: E402


EXPECTED_FACTORS = {
    "25K":   1.0,  # Phase E rev: NQ direct backtest, no MNQ conversion needed
    "50K":   1.0,  # NQ backtest, already in NQ-equivalent units
    "100K":  1.0,
    "150K":  1.0,
}


def test_tier_to_parquet_factors_match_apex_rules():
    """Regression: per-tier factors must match Apex 25K MNQ→NQ-equivalent
    convention. Catches both directions of accidental change:
      - NQ tiers accidentally inflated (factor != 1.0)
      - 25K accidentally "fixed" to 1.0 (the Phase 14 false alarm)
    """
    for tier, (_fname, factor) in TIER_TO_PARQUET.items():
        expected = EXPECTED_FACTORS[tier]
        assert factor == expected, (
            f"TIER_TO_PARQUET[{tier!r}] factor = {factor} != {expected}. "
            f"See test docstring for Apex convention rationale."
        )


def test_load_pnl_per_mini_returns_unchanged_at_factor_one(tmp_path, monkeypatch):
    """load_pnl_per_mini at factor=1.0 must return the parquet's daily_pnl
    column unchanged (no scaling). NQ tier semantics."""
    pnl_values = np.array([-50.0, 30.0, 0.0, 75.5, -120.3])
    df = pd.DataFrame({"daily_pnl": pnl_values})
    parquet_path = tmp_path / "test_tier.parquet"
    df.to_parquet(parquet_path)

    monkeypatch.setitem(TIER_TO_PARQUET, "TEST", ("test_tier.parquet", 1.0))
    series = load_pnl_per_mini(tmp_path, "TEST")

    np.testing.assert_array_equal(series.values, pnl_values)


def test_load_pnl_per_mini_scales_when_factor_not_one(tmp_path, monkeypatch):
    """Sanity: factor != 1.0 multiplies series. Verifies the scaling mechanic
    used by 25K (factor=10 for MNQ→NQ-equivalent conversion)."""
    pnl_values = np.array([10.0, -20.0, 5.0])
    df = pd.DataFrame({"daily_pnl": pnl_values})
    parquet_path = tmp_path / "scaled_tier.parquet"
    df.to_parquet(parquet_path)

    monkeypatch.setitem(TIER_TO_PARQUET, "SCALED", ("scaled_tier.parquet", 3.0))
    series = load_pnl_per_mini(tmp_path, "SCALED")

    np.testing.assert_array_almost_equal(series.values, pnl_values * 3.0)


def test_load_pnl_per_mini_25k_factor_one_phase_e_rev(tmp_path, monkeypatch):
    """Phase E rev: 25K backtest at 1 NQ direct → factor=1.0 (no conversion).

    Pre Phase E rev (Phase 0-13): 25K backtested at 1 MNQ → factor=10 needed
    to convert MNQ→NQ-eq for MC scaling-tier logic.

    Phase E rev: backtest at 1 NQ direct, daily_pnl already in NQ-eq units,
    factor=1.0 returns series unchanged. This test asserts the new convention
    and prevents accidental revert to MNQ-based scaling.
    """
    nq_pnl = np.array([200.0, -500.0, 1000.0])  # raw 1 NQ daily PnL (NQ-eq scale)
    df = pd.DataFrame({"daily_pnl": nq_pnl})
    parquet_path = tmp_path / "apex_25k_longonly.parquet"
    df.to_parquet(parquet_path)

    monkeypatch.setitem(TIER_TO_PARQUET, "25K", ("apex_25k_longonly.parquet", 1.0))
    series = load_pnl_per_mini(tmp_path, "25K")

    # factor=1.0 → series unchanged
    np.testing.assert_array_almost_equal(series.values, nq_pnl)


def test_load_pnl_per_mini_missing_parquet_raises(tmp_path, monkeypatch):
    monkeypatch.setitem(TIER_TO_PARQUET, "MISSING", ("nonexistent.parquet", 1.0))
    with pytest.raises(FileNotFoundError):
        load_pnl_per_mini(tmp_path, "MISSING")
