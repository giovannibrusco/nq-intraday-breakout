"""Tests for ``nqbreakout.strategy.costs``."""

from __future__ import annotations

import inspect

import pytest

from nqbreakout.strategy.costs import (
    CostModel,
    DEFAULT_COST_MODEL,
    LEGACY_COST_MODEL,
    LegacyCostModel,
    NQ_RITHMIC_COST_MODEL,
    MNQ_RITHMIC_COST_MODEL,
    make_cost_model_for_instrument,
)

# CostModel defaults (NQ Apex Rithmic, post-Phase 13):
#   fee_round_trip_usd  = 2 * 1.99 = 3.98
#   entry_slippage_ticks = 1.5  -> 1.5 * 5 = 7.50
#   stop_slippage_ticks  = 1.5  -> 7.50
#   target_slippage_ticks = 0.0 -> 0.00
#   eod_slippage_ticks   = 1.0  -> 5.00


def test_target_hit_cost() -> None:
    """Target (resting limit): entry slip + 0 exit slip + fee = 3.98 + 7.50 + 0.00 = $11.48."""
    cost = CostModel().total_round_trip_cost("target")
    assert cost == pytest.approx(11.48)


def test_stop_hit_cost() -> None:
    """Stop (market): entry slip + stop slip + fee = 3.98 + 7.50 + 7.50 = $18.98."""
    cost = CostModel().total_round_trip_cost("stop")
    assert cost == pytest.approx(18.98)


def test_eod_cost() -> None:
    """EOD: entry slip + eod slip + fee = 3.98 + 7.50 + 5.00 = $16.48."""
    cost = CostModel().total_round_trip_cost("eod")
    assert cost == pytest.approx(16.48)


def test_max_hold_cost() -> None:
    """max_hold routes to eod exit: same $16.48 as eod."""
    cost = CostModel().total_round_trip_cost("max_hold")
    assert cost == pytest.approx(16.48)


def test_force_close_cost() -> None:
    """force_close routes to force (eod_slippage): $16.48."""
    cost = CostModel().total_round_trip_cost("force_close")
    assert cost == pytest.approx(16.48)


def test_eod_short_reason_cost() -> None:
    """eod_short exit_reason (used internally by core) maps to eod: $16.48."""
    cost = CostModel().total_round_trip_cost("eod_short")
    assert cost == pytest.approx(16.48)


def test_gap_penalty_above_trigger() -> None:
    """gap_size=5 ticks > trigger=4: gap penalty of 2 ticks * $5 = $10 added to target cost."""
    model = CostModel(gap_slippage_ticks=2.0)
    base = model.total_round_trip_cost("target")
    gapped = model.total_round_trip_cost("target", gap_size_ticks=5.0)
    assert gapped == pytest.approx(base + 10.0)


def test_gap_penalty_below_trigger() -> None:
    """gap_size=3 ticks <= trigger=4: no gap penalty."""
    model = CostModel(gap_slippage_ticks=2.0)
    base = model.total_round_trip_cost("target")
    no_gap = model.total_round_trip_cost("target", gap_size_ticks=3.0)
    assert no_gap == pytest.approx(base)


def test_gap_penalty_at_trigger_boundary() -> None:
    """gap_size == trigger (4 ticks): no gap penalty (strictly greater required)."""
    model = CostModel(gap_slippage_ticks=2.0)
    base = model.total_round_trip_cost("target")
    at_trigger = model.total_round_trip_cost("target", gap_size_ticks=4.0)
    assert at_trigger == pytest.approx(base)


def test_legacy_cost_model_flat() -> None:
    """LegacyCostModel returns $9.20 regardless of exit reason."""
    assert LEGACY_COST_MODEL.total_round_trip_cost("target") == pytest.approx(9.20)
    assert LEGACY_COST_MODEL.total_round_trip_cost("stop") == pytest.approx(9.20)
    assert LEGACY_COST_MODEL.total_round_trip_cost("eod") == pytest.approx(9.20)
    assert LEGACY_COST_MODEL.total_round_trip_cost("force_close") == pytest.approx(9.20)


def test_legacy_signature_compatible() -> None:
    """LegacyCostModel.total_round_trip_cost accepts same call signature as CostModel."""
    new_sig = inspect.signature(CostModel.total_round_trip_cost)
    leg_sig = inspect.signature(LegacyCostModel.total_round_trip_cost)
    new_params = set(new_sig.parameters) - {"self"}
    leg_params = set(leg_sig.parameters) - {"self"}
    assert new_params == leg_params, (
        f"Signature mismatch: CostModel={new_params}, LegacyCostModel={leg_params}"
    )


def test_default_cost_model_is_new() -> None:
    """DEFAULT_COST_MODEL is a CostModel instance, not LegacyCostModel."""
    assert isinstance(DEFAULT_COST_MODEL, CostModel)
    assert not isinstance(DEFAULT_COST_MODEL, LegacyCostModel)


def test_fee_round_trip_property() -> None:
    """fee_round_trip_usd = 2 * fee_per_leg_usd."""
    m = CostModel(fee_per_leg_usd=0.85)
    assert m.fee_round_trip_usd == pytest.approx(1.70)


# =============================================================================
# Phase 13: Apex Rithmic instrument-aware cost models.
# =============================================================================


def test_nq_rithmic_fees() -> None:
    """NQ Apex Rithmic: $1.99/leg, $5/tick, $3.98 RT fees."""
    m = NQ_RITHMIC_COST_MODEL
    assert m.fee_per_leg_usd == pytest.approx(1.99)
    assert m.fee_round_trip_usd == pytest.approx(3.98)
    assert m.tick_value == pytest.approx(5.0)


def test_mnq_rithmic_fees() -> None:
    """MNQ Apex Rithmic: $0.51/leg, $0.50/tick, $1.02 RT fees."""
    m = MNQ_RITHMIC_COST_MODEL
    assert m.fee_per_leg_usd == pytest.approx(0.51)
    assert m.fee_round_trip_usd == pytest.approx(1.02)
    assert m.tick_value == pytest.approx(0.50)


def test_nq_target_total() -> None:
    """NQ target RT: $3.98 fee + $7.50 entry slip + $0 target = $11.48."""
    assert NQ_RITHMIC_COST_MODEL.total_round_trip_cost("target") == pytest.approx(11.48)


def test_mnq_target_total() -> None:
    """MNQ target RT: $1.02 fee + $0.75 entry slip (1.5 tick * $0.5) + $0 target = $1.77."""
    assert MNQ_RITHMIC_COST_MODEL.total_round_trip_cost("target") == pytest.approx(1.77)


def test_mnq_stop_total() -> None:
    """MNQ stop RT: $1.02 fee + $0.75 entry + $0.75 stop = $2.52."""
    assert MNQ_RITHMIC_COST_MODEL.total_round_trip_cost("stop") == pytest.approx(2.52)


def test_factory_nq() -> None:
    """Factory returns NQ_RITHMIC for NQ instrument."""
    from nqbreakout.strategy.config import NQ_DEFAULT
    m = make_cost_model_for_instrument(NQ_DEFAULT)
    assert m is NQ_RITHMIC_COST_MODEL


def test_factory_mnq() -> None:
    """Factory returns MNQ_RITHMIC for MNQ instrument."""
    from nqbreakout.strategy.config import MNQ_DEFAULT
    m = make_cost_model_for_instrument(MNQ_DEFAULT)
    assert m is MNQ_RITHMIC_COST_MODEL


def test_factory_unknown_raises() -> None:
    """Factory raises ValueError for unknown symbol."""
    class FakeInstr:
        symbol = "XYZ"
    with pytest.raises(ValueError, match="Unknown instrument"):
        make_cost_model_for_instrument(FakeInstr())


def test_factory_string_input() -> None:
    """Factory accepts plain string symbols too."""
    assert make_cost_model_for_instrument("NQ") is NQ_RITHMIC_COST_MODEL
    assert make_cost_model_for_instrument("MNQ") is MNQ_RITHMIC_COST_MODEL
