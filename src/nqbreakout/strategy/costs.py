"""Trading cost model — Phase 2: split slippage per exit reason.

Legacy flat model ($9.20/contract regardless of exit) preserved as
``LegacyCostModel`` / ``LEGACY_COST_MODEL`` for Phase 1 parity tests.

Phase 13 update (2026-04-27): instrument-aware Apex Rithmic fees.
- NQ Rithmic Apex: $1.99/leg ($3.98 RT)
- MNQ Rithmic Apex: $0.51/leg ($1.02 RT)
Source: docs/apex_rules_2026.md section 4 (verified from apextraderfunding.com).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ExitReason = Literal["target", "stop", "eod", "max_hold", "force_close"]


@dataclass(frozen=True)
class CostModel:
    """Split slippage cost model (Apex Rithmic NQ default post-Phase 13).

    Target = resting limit order, slippage = 0.
    Stop = market order, full tick slippage.
    Entry = market on breakout bar close.
    EOD/force = market close at 1 tick.

    Default: NQ Apex Rithmic ($1.99/leg, $5/tick).
    Use ``make_cost_model_for_instrument()`` to obtain instrument-specific
    model that adjusts fee + tick_value for MNQ vs NQ.
    """

    fee_per_leg_usd: float = 1.99           # NQ Apex Rithmic per leg (was 0.85 IB legacy)
    entry_slippage_ticks: float = 1.5
    stop_slippage_ticks: float = 1.5
    target_slippage_ticks: float = 0.0
    eod_slippage_ticks: float = 1.0
    gap_slippage_ticks: float = 0.0         # additive when gap > trigger
    gap_trigger_ticks: float = 4.0
    tick_size: float = 0.25
    tick_value: float = 5.0                 # NQ default ($5/tick)

    @property
    def fee_round_trip_usd(self) -> float:
        return 2 * self.fee_per_leg_usd

    def slippage_per_contract(self, side: Literal["entry", "stop", "target", "eod", "force"]) -> float:
        ticks_map = {
            "entry": self.entry_slippage_ticks,
            "stop": self.stop_slippage_ticks,
            "target": self.target_slippage_ticks,
            "eod": self.eod_slippage_ticks,
            "force": self.eod_slippage_ticks,
        }
        return ticks_map[side] * self.tick_value

    def total_round_trip_cost(self, exit_reason: ExitReason, gap_size_ticks: float = 0.0) -> float:
        """Dollar cost per contract for a full round trip given exit reason."""
        entry_slip = self.entry_slippage_ticks * self.tick_value
        exit_side_map = {
            "target": "target",
            "stop": "stop",
            "eod": "eod",
            "eod_short": "eod",
            "max_hold": "eod",
            "force_close": "force",
        }
        exit_side = exit_side_map.get(exit_reason, "eod")
        exit_slip = self.slippage_per_contract(exit_side)
        gap_penalty = (
            self.gap_slippage_ticks * self.tick_value
            if abs(gap_size_ticks) > self.gap_trigger_ticks else 0.0
        )
        return self.fee_round_trip_usd + entry_slip + exit_slip + gap_penalty


# ---------------------------------------------------------------------------
# Legacy shim -- preserves $9.20/contract flat for Phase 1 parity test.
# DO NOT TOUCH. Used by tests/test_core.py::test_full_10y_parity.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LegacyCostModel:
    """Flat cost model matching legacy topstep_montecarlo behavior.

    Despite the SLIPPAGE_PER_SIDE_TICKS=1.5 variable name, legacy code applies
    slippage once per round trip (not per side). Total $9.20/contract.
    """

    fee_round_trip_usd: float = 1.70
    slippage_round_trip_usd: float = 7.50

    def total_round_trip_cost(self, exit_reason: str = "any", gap_size_ticks: float = 0.0) -> float:
        return self.fee_round_trip_usd + self.slippage_round_trip_usd


LEGACY_COST_MODEL = LegacyCostModel()
DEFAULT_COST_MODEL = CostModel()  # NQ Apex Rithmic post-Phase 13


# ---------------------------------------------------------------------------
# Phase 13: Instrument-aware Apex Rithmic cost models.
# ---------------------------------------------------------------------------


NQ_RITHMIC_COST_MODEL = CostModel(
    fee_per_leg_usd=1.99,
    tick_value=5.0,
    tick_size=0.25,
)

MNQ_RITHMIC_COST_MODEL = CostModel(
    fee_per_leg_usd=0.51,
    tick_value=0.50,
    tick_size=0.25,
)


def make_cost_model_for_instrument(instrument) -> CostModel:
    """Return Apex Rithmic CostModel matching instrument symbol.

    Args:
        instrument: object with ``symbol`` attribute (e.g. InstrumentSpec).

    Returns:
        CostModel with NQ ($1.99/leg) or MNQ ($0.51/leg) Apex Rithmic fees
        and matching tick_value.

    Raises:
        ValueError: if instrument.symbol is not "NQ" or "MNQ".
    """
    sym = getattr(instrument, "symbol", None) or instrument
    if sym == "NQ":
        return NQ_RITHMIC_COST_MODEL
    if sym == "MNQ":
        return MNQ_RITHMIC_COST_MODEL
    raise ValueError(f"Unknown instrument symbol for cost model: {sym!r}")
