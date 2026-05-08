"""Apex Trader Funding account model — New Products (2026, "4.0").

Models 4 tier × 2 variants (Intraday Trail / EOD Trail), Eval + PA lifecycle.
Legacy 7-tier classes preserved at end for backward parity tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml


# =============================================================================
# 4.0 Data classes — Eval + PA + Scaling + Payout
# =============================================================================


@dataclass(frozen=True)
class ScalingLevel:
    """One row in the PA Scaling Levels Built-in table.

    profit_max=None means "& up" (top tier).
    """
    profit_min: float
    profit_max: Optional[float]
    max_contracts_mini: int
    daily_loss_limit: float

    def contains(self, profit: float) -> bool:
        if profit < self.profit_min:
            return False
        if self.profit_max is None:
            return True
        return profit <= self.profit_max


@dataclass(frozen=True)
class PayoutLadder:
    """6-step payout cap sequence per PA lifetime."""
    payouts: tuple[float, ...]

    def __post_init__(self):
        if len(self.payouts) != 6:
            raise ValueError(f"PayoutLadder must have 6 entries, got {len(self.payouts)}")

    def cap_for_request(self, request_idx: int) -> float:
        if request_idx < 0 or request_idx >= 6:
            raise IndexError(f"request_idx {request_idx} out of range [0,5]")
        return self.payouts[request_idx]

    @property
    def lifetime_cap(self) -> float:
        return float(sum(self.payouts))


@dataclass(frozen=True)
class ApexEval:
    """Evaluation phase config (Apex 4.0)."""
    tier: str                    # "25K", "50K", "100K", "150K"
    variant: str                 # "intraday" or "eod"
    starting_balance: float
    profit_target: float
    max_drawdown: float          # == trailing threshold
    max_contracts_mini: int
    max_contracts_micro: int
    daily_loss_limit: Optional[float]  # None for intraday eval
    eval_fee: float
    eval_coupon_fee: float
    min_days_to_pass: int = 1
    time_limit_days: int = 30


@dataclass(frozen=True)
class ApexPA:
    """Performance Account phase config (Apex 4.0)."""
    tier: str
    variant: str
    starting_balance: float
    max_drawdown: float
    max_contracts_mini: int           # cap massima PA (sopra scaling)
    max_contracts_micro: int
    activation_fee: float
    scaling_levels: tuple[ScalingLevel, ...]
    payout_ladder: PayoutLadder
    qualifying_min_daily_profit: float

    consistency_threshold: float = 0.50
    safety_net_buffer: float = 100.0
    min_payout: float = 500.0
    qualifying_days_required: int = 5
    max_payout_requests: int = 6

    inactivity_min_profit: float = 50.0
    inactivity_dormant_days: int = 15
    inactivity_close_days: int = 30

    activation_deadline_days: int = 7
    max_accounts: int = 20

    flatten_cutoff: time = time(16, 59)
    allow_overnight: bool = False
    fee_per_rt_usd: float = 3.98      # NQ Rithmic; for MNQ override at sim level

    @property
    def safety_net(self) -> float:
        """Min balance required to request a payout."""
        return self.starting_balance + self.max_drawdown + self.safety_net_buffer

    @property
    def min_balance_to_request(self) -> float:
        """Balance must be >= safety_net + min_payout to actually request."""
        return self.safety_net + self.min_payout

    def scaling_level_for_profit(self, profit: float) -> ScalingLevel:
        for level in self.scaling_levels:
            if level.contains(profit):
                return level
        return self.scaling_levels[0]


@dataclass(frozen=True)
class ApexProduct:
    """One tier × variant bundle (eval + PA)."""
    name: str
    eval: ApexEval
    pa: ApexPA


# =============================================================================
# 4.0 YAML loader
# =============================================================================


def load_apex_products(path: str | Path) -> list[ApexProduct]:
    """Load list of ApexProduct from YAML config (4.0 schema)."""
    data = yaml.safe_load(Path(path).read_text())
    products_data = data["products"] if isinstance(data, dict) and "products" in data else data
    products = []
    for d in products_data:
        eval_d = d["eval"]
        pa_d = d["pa"]
        starting = float(d["starting_balance"])
        max_dd = float(d["max_drawdown"])
        eval_obj = ApexEval(
            tier=d["tier"],
            variant=d["variant"],
            starting_balance=starting,
            profit_target=float(d["profit_target"]),
            max_drawdown=max_dd,
            max_contracts_mini=int(eval_d["max_contracts_mini"]),
            max_contracts_micro=int(eval_d["max_contracts_micro"]),
            daily_loss_limit=eval_d.get("daily_loss_limit"),
            eval_fee=float(eval_d["eval_fee"]),
            eval_coupon_fee=float(eval_d["eval_coupon_fee"]),
            min_days_to_pass=int(eval_d.get("min_days_to_pass", 1)),
            time_limit_days=int(eval_d.get("time_limit_days", 30)),
        )
        scaling = tuple(
            ScalingLevel(
                profit_min=float(lv["profit_min"]),
                profit_max=lv.get("profit_max"),
                max_contracts_mini=int(lv["max_contracts_mini"]),
                daily_loss_limit=float(lv["daily_loss_limit"]),
            )
            for lv in pa_d["scaling_levels"]
        )
        ladder = PayoutLadder(payouts=tuple(float(x) for x in pa_d["payout_ladder"]))
        cutoff_str = pa_d.get("flatten_cutoff", "16:59")
        cutoff = time.fromisoformat(cutoff_str) if isinstance(cutoff_str, str) else cutoff_str
        pa_obj = ApexPA(
            tier=d["tier"],
            variant=d["variant"],
            starting_balance=starting,
            max_drawdown=max_dd,
            max_contracts_mini=int(pa_d["max_contracts_mini"]),
            max_contracts_micro=int(pa_d["max_contracts_micro"]),
            activation_fee=float(pa_d["activation_fee"]),
            scaling_levels=scaling,
            payout_ladder=ladder,
            qualifying_min_daily_profit=float(pa_d["qualifying_min_daily_profit"]),
            consistency_threshold=float(pa_d.get("consistency_threshold", 0.50)),
            safety_net_buffer=float(pa_d.get("safety_net_buffer", 100.0)),
            min_payout=float(pa_d.get("min_payout", 500.0)),
            qualifying_days_required=int(pa_d.get("qualifying_days_required", 5)),
            max_payout_requests=int(pa_d.get("max_payout_requests", 6)),
            inactivity_min_profit=float(pa_d.get("inactivity_min_profit", 50.0)),
            inactivity_dormant_days=int(pa_d.get("inactivity_dormant_days", 15)),
            inactivity_close_days=int(pa_d.get("inactivity_close_days", 30)),
            activation_deadline_days=int(pa_d.get("activation_deadline_days", 7)),
            max_accounts=int(pa_d.get("max_accounts", 20)),
            flatten_cutoff=cutoff,
            allow_overnight=bool(pa_d.get("allow_overnight", False)),
            fee_per_rt_usd=float(pa_d.get("fee_per_rt_usd", 3.98)),
        )
        products.append(ApexProduct(name=d["name"], eval=eval_obj, pa=pa_obj))
    return products


# =============================================================================
# 4.0 Trackers
# =============================================================================


class ApexTrailingTracker:
    """Trailing drawdown for Eval or PA, EOD or Intraday variant.

    Floor = peak - max_drawdown. Floor freezes when it would reach
    starting_balance + safety_net_buffer (= +$100 default), which happens
    when peak reaches starting_balance + max_drawdown + safety_net_buffer.

    EOD trail: feed update_eod() with daily close balance only.
    Intraday trail: feed update_intraday_high() with bar-level highs (or
    update_eod() if using daily proxy).
    """

    def __init__(
        self,
        starting_balance: float,
        max_drawdown: float,
        safety_net_buffer: float = 100.0,
        trail_type: str = "intraday",
    ):
        if trail_type not in ("intraday", "eod"):
            raise ValueError(f"trail_type must be 'intraday' or 'eod', got {trail_type}")
        self.starting_balance = starting_balance
        self.max_drawdown = max_drawdown
        self.safety_net_buffer = safety_net_buffer
        self.trail_type = trail_type
        self._floor = starting_balance - max_drawdown
        self._peak = starting_balance
        self._frozen = False
        self._freeze_floor = starting_balance + safety_net_buffer

    def _maybe_update(self, candidate_peak: float) -> None:
        if self._frozen:
            return
        if candidate_peak > self._peak:
            self._peak = candidate_peak
            candidate_floor = candidate_peak - self.max_drawdown
            if candidate_floor >= self._freeze_floor:
                self._floor = self._freeze_floor
                self._frozen = True
            elif candidate_floor > self._floor:
                self._floor = candidate_floor

    def update_intraday_high(self, intraday_high: float) -> None:
        if self.trail_type == "intraday":
            self._maybe_update(intraday_high)

    def update_eod(self, eod_balance: float) -> None:
        # Both trail types update on EOD; for intraday this is the lower-bound
        # update if no bar-level data is available.
        self._maybe_update(eod_balance)

    @property
    def floor(self) -> float:
        return self._floor

    @property
    def peak(self) -> float:
        return self._peak

    @property
    def frozen(self) -> bool:
        return self._frozen

    def violated(self, balance: float) -> bool:
        return balance < self._floor


class ApexQualifyingDayTracker:
    """Counts qualifying days for payout eligibility. Resets after each payout."""

    def __init__(self, min_daily_profit: float, days_required: int = 5):
        self.min_daily_profit = min_daily_profit
        self.days_required = days_required
        self.count = 0

    def update(self, daily_pnl: float) -> None:
        if daily_pnl >= self.min_daily_profit:
            self.count += 1

    def can_request_payout(self) -> bool:
        return self.count >= self.days_required

    def reset(self) -> None:
        self.count = 0


class ApexInactivityTracker:
    """Tracks rolling-window inactivity for PA closure.

    Apex rule (literal): need ≥2 qualifying days (≥$50 net) per rolling 30 cal days.
    Approximation here: track last qualifying day; flag dormant if gap ≥ 15 days
    since last qualifying, closed if gap ≥ 30 days.
    Materially equivalent for a strategy that trades regularly (not bursty).
    """

    def __init__(
        self,
        min_profit: float = 50.0,
        dormant_days: int = 15,
        close_days: int = 30,
    ):
        self.min_profit = min_profit
        self.dormant_days = dormant_days
        self.close_days = close_days
        self.last_qualifying_day_idx: int = -1

    def update(self, day_idx: int, daily_pnl: float) -> None:
        if daily_pnl >= self.min_profit:
            self.last_qualifying_day_idx = day_idx

    def days_since_qualifying(self, day_idx: int) -> int:
        if self.last_qualifying_day_idx < 0:
            return day_idx + 1   # counting from start
        return day_idx - self.last_qualifying_day_idx

    def is_dormant(self, day_idx: int) -> bool:
        return self.days_since_qualifying(day_idx) >= self.dormant_days

    def is_closed(self, day_idx: int) -> bool:
        return self.days_since_qualifying(day_idx) >= self.close_days


# =============================================================================
# 4.0 Helpers
# =============================================================================


def consistency_ok(daily_pnl: list[float], threshold: float = 0.50) -> bool:
    """No single profitable day exceeds threshold of total profit on positive days.

    Apex 4.0 PA threshold = 0.50 (vs Legacy 0.30). Eval has NO consistency rule.
    Returns False if no profitable days.
    """
    profitable = [p for p in daily_pnl if p > 0]
    if not profitable:
        return False
    total_profit = sum(profitable)
    if total_profit <= 0:
        return False
    return max(profitable) / total_profit <= threshold


def enough_trading_days(day_count: int, min_days: int) -> bool:
    return day_count >= min_days


def passes_evaluation_4(
    daily_equity: pd.Series,
    daily_pnl: pd.Series,
    eval_cfg: ApexEval,
) -> tuple[bool, str]:
    """Post-hoc Eval pass check on a historical equity curve (4.0 logic).

    Eval has NO consistency rule. DLL only for EOD variant. Time limit 30 days.
    Trailing per variant (intraday: same proxy as MC).
    """
    tracker = ApexTrailingTracker(
        starting_balance=eval_cfg.starting_balance,
        max_drawdown=eval_cfg.max_drawdown,
        trail_type=eval_cfg.variant,
    )
    days = 0
    for i, eod in enumerate(daily_equity.values):
        if i >= eval_cfg.time_limit_days:
            break
        pnl = float(daily_pnl.iloc[i])
        # DLL check (EOD eval only)
        if eval_cfg.daily_loss_limit is not None and pnl < -eval_cfg.daily_loss_limit:
            return False, "dll_violated"
        # Intraday violation proxy: balance + min(pnl, 0)
        intraday_low = float(eod) - max(pnl, 0.0)  # = balance_yesterday + min(pnl,0)
        if tracker.violated(intraday_low):
            return False, "drawdown_violated"
        tracker.update_eod(float(eod))
        days += 1
    if not enough_trading_days(days, eval_cfg.min_days_to_pass):
        return False, "min_days_failed"
    if daily_equity.iloc[-1] < eval_cfg.starting_balance + eval_cfg.profit_target:
        return False, "profit_target_not_reached"
    return True, "passed"


# =============================================================================
# LEGACY (deprecated, kept for parity tests Phase 0-8 — DO NOT MODIFY)
# =============================================================================


@dataclass(frozen=True)
class ApexAccount:
    """LEGACY 7-tier model. Kept for `test_full_10y_parity`. Use ApexProduct for 4.0."""
    name: str
    starting_balance: float
    profit_target: float
    trailing_threshold: float
    max_contracts_nq: int
    max_contracts_mnq: int
    min_trading_days: int = 7
    consistency_threshold: float = 0.30
    daily_loss_limit: Optional[float] = None
    flatten_cutoff: time = time(16, 59)
    allow_overnight: bool = False
    fee_per_rt_usd: float = 4.50


def load_apex_accounts(path: str | Path) -> list[ApexAccount]:
    """LEGACY loader. Use load_apex_products for 4.0."""
    data = yaml.safe_load(Path(path).read_text())
    accounts_data = data["accounts"] if isinstance(data, dict) and "accounts" in data else data
    accounts = []
    for d in accounts_data:
        cutoff_str = d.get("flatten_cutoff", "16:59")
        cutoff = time.fromisoformat(cutoff_str) if isinstance(cutoff_str, str) else cutoff_str
        accounts.append(ApexAccount(
            name=d["name"],
            starting_balance=float(d["starting_balance"]),
            profit_target=float(d["profit_target"]),
            trailing_threshold=float(d["trailing_threshold"]),
            max_contracts_nq=int(d["max_contracts_nq"]),
            max_contracts_mnq=int(d["max_contracts_mnq"]),
            min_trading_days=int(d.get("min_trading_days", 7)),
            consistency_threshold=float(d.get("consistency_threshold", 0.30)),
            daily_loss_limit=d.get("daily_loss_limit"),
            flatten_cutoff=cutoff,
            allow_overnight=bool(d.get("allow_overnight", False)),
            fee_per_rt_usd=float(d.get("fee_per_rt_usd", 4.50)),
        ))
    return accounts


class ApexDrawdownTracker:
    """LEGACY tracker. Floor starts at start - trailing_threshold, ratchets up
    on EOD new highs, freezes when target hit. Used by Phase 0-8 MC engine."""

    def __init__(self, account: ApexAccount):
        self.account = account
        self._floor = account.starting_balance - account.trailing_threshold
        self._peak_eod = account.starting_balance
        self._frozen = False

    def update_eod(self, equity_eod: float) -> None:
        if self._frozen:
            return
        target_balance = self.account.starting_balance + self.account.profit_target
        if equity_eod >= target_balance:
            self._floor = self.account.starting_balance
            self._frozen = True
            self._peak_eod = max(self._peak_eod, equity_eod)
            return
        if equity_eod > self._peak_eod:
            self._peak_eod = equity_eod
            candidate_floor = equity_eod - self.account.trailing_threshold
            if candidate_floor > self._floor:
                self._floor = candidate_floor

    @property
    def floor(self) -> float:
        return self._floor

    @property
    def frozen(self) -> bool:
        return self._frozen

    def violated(self, equity: float) -> bool:
        return equity < self._floor


def passes_evaluation(
    daily_equity: pd.Series,
    daily_pnl: pd.Series,
    account: ApexAccount,
) -> tuple[bool, str]:
    """LEGACY 7-tier evaluation check. Includes 30% consistency + 7-day min."""
    tracker = ApexDrawdownTracker(account)
    days = 0
    for eod in daily_equity.values:
        if tracker.violated(float(eod)):
            return False, "drawdown_violated"
        tracker.update_eod(float(eod))
        days += 1
    if not enough_trading_days(days, account.min_trading_days):
        return False, "min_days_failed"
    if daily_equity.iloc[-1] < account.starting_balance + account.profit_target:
        return False, "profit_target_not_reached"
    if not consistency_ok(list(daily_pnl.values), account.consistency_threshold):
        return False, "consistency_failed"
    return True, "passed"
