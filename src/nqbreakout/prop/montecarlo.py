"""Monte Carlo simulation for Apex evaluation pass-rate AND PA lifecycle EV.

Two modes:
    1. Legacy 7-tier MC (`simulate_evaluation` + `run_mc_sweep`) — preserved
       for parity with Phase 0-8.
    2. New 4.0 lifecycle MC (`simulate_eval_4`, `simulate_pa_lifecycle`,
       `simulate_full_lifecycle`, `run_lifecycle_sweep`) for ApexProduct.

Both modes use Kunsch moving-block bootstrap on historical daily PnL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .apex import (
    ApexAccount,
    ApexDrawdownTracker,
    ApexProduct,
    ApexEval,
    ApexPA,
    ApexTrailingTracker,
    ApexQualifyingDayTracker,
    ApexInactivityTracker,
    consistency_ok,
    enough_trading_days,
)


# =============================================================================
# Block bootstrap (shared)
# =============================================================================


def block_bootstrap_days(
    historical_daily_pnl: pd.Series,
    n_days: int,
    block_size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Kunsch moving-block bootstrap on daily PnL."""
    vals = np.asarray(historical_daily_pnl.values, dtype=float)
    n = len(vals)
    if n < block_size:
        raise ValueError(f"historical length {n} < block_size {block_size}")
    n_blocks = int(np.ceil(n_days / block_size))
    starts = rng.integers(0, n - block_size + 1, size=n_blocks)
    blocks = [vals[s:s + block_size] for s in starts]
    sampled = np.concatenate(blocks)[:n_days]
    return sampled


def block_bootstrap_regime_aware(
    historical_daily_pnl: pd.Series,
    regimes: pd.Series,
    transition_matrix: np.ndarray,
    stationary_dist: np.ndarray,
    state_labels: list[str],
    n_days: int,
    block_size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Regime-conditional moving-block bootstrap (Phase 10B).

    Sample regime sequence via discrete Markov chain (transition_matrix);
    for each transition, draw a block of `block_size` consecutive days from
    the historical series whose START day matches the current regime label.

    Args:
        historical_daily_pnl: 1-D series of daily PnL (length N).
        regimes: 1-D series of regime labels aligned to historical_daily_pnl.
        transition_matrix: K x K row-stochastic matrix; rows ordered as
            state_labels.
        stationary_dist: length-K vector summing to 1 (initial regime distribution).
        state_labels: ordered list of K regime labels (must match matrix rows).
        n_days: total days to sample.
        block_size: bootstrap block length.
        rng: numpy Generator.

    Returns:
        1-D ndarray of length n_days with sampled daily PnL.

    Raises:
        ValueError: if a regime has no valid block-start days, or if input
            length < block_size.
    """
    vals = np.asarray(historical_daily_pnl.values, dtype=float)
    regs = np.asarray(regimes.values)
    n = len(vals)
    if n < block_size:
        raise ValueError(f"historical length {n} < block_size {block_size}")
    if len(regs) != n:
        raise ValueError(f"regimes length {len(regs)} != historical length {n}")

    n_states = len(state_labels)
    if transition_matrix.shape != (n_states, n_states):
        raise ValueError(
            f"transition_matrix shape {transition_matrix.shape} != ({n_states}, {n_states})"
        )
    if len(stationary_dist) != n_states:
        raise ValueError(f"stationary_dist length {len(stationary_dist)} != {n_states}")

    # Pre-compute valid block starts per regime (start day's regime == label)
    starts_by_regime: dict[str, np.ndarray] = {}
    for label in state_labels:
        valid_starts = np.where(regs[: n - block_size + 1] == label)[0]
        if len(valid_starts) == 0:
            raise ValueError(f"No valid block starts for regime '{label}'")
        starts_by_regime[label] = valid_starts

    out = np.empty(n_days, dtype=float)
    cursor = 0
    state_idx = int(rng.choice(n_states, p=stationary_dist))

    while cursor < n_days:
        label = state_labels[state_idx]
        starts = starts_by_regime[label]
        start = int(starts[rng.integers(0, len(starts))])
        block = vals[start : start + block_size]
        n_take = min(block_size, n_days - cursor)
        out[cursor : cursor + n_take] = block[:n_take]
        cursor += n_take
        state_idx = int(rng.choice(n_states, p=transition_matrix[state_idx]))

    return out


def block_bootstrap_with_extremes(
    historical_extremes: pd.DataFrame,
    n_days: int,
    block_size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Block bootstrap returning per-day (pnl, intraday_low_delta, intraday_high_delta) (Phase 13).

    Block bootstrap on the joint historical (pnl, low, high) triplet so that
    the intraday extremes for each sampled day are the actual extremes from
    that historical day (preserving correlation).

    Args:
        historical_extremes: DataFrame with columns
            ``daily_pnl``, ``intraday_low_delta``, ``intraday_high_delta``
            (low_delta ≤ 0, high_delta ≥ 0; both relative to start-of-day).
        n_days: total days to sample.
        block_size: bootstrap block length.
        rng: numpy Generator.

    Returns:
        2-D ndarray of shape ``(n_days, 3)``; columns are
        (pnl, intraday_low_delta, intraday_high_delta).

    Raises:
        ValueError: if historical length < block_size or required columns missing.
    """
    cols = ["daily_pnl", "intraday_low_delta", "intraday_high_delta"]
    missing = [c for c in cols if c not in historical_extremes.columns]
    if missing:
        raise ValueError(f"historical_extremes missing columns: {missing}")
    vals = historical_extremes[cols].values.astype(float)
    n = len(vals)
    if n < block_size:
        raise ValueError(f"historical length {n} < block_size {block_size}")
    n_blocks = int(np.ceil(n_days / block_size))
    starts = rng.integers(0, n - block_size + 1, size=n_blocks)
    blocks = [vals[s : s + block_size] for s in starts]
    return np.concatenate(blocks)[:n_days]


def block_bootstrap_with_shock_days(
    historical_daily_pnl: pd.Series,
    shock_pnl: float,
    shock_prob: float,
    n_days: int,
    block_size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Block bootstrap with multi-SL shock-day injection (Phase 12).

    Standard block bootstrap on historical daily PnL, then with probability
    `shock_prob` each sampled day is REPLACED by a single fixed `shock_pnl`
    value. Models the empirically-unobserved-but-theoretically-possible
    scenario of 2+ SL hits in the same trading day under future regime change.

    Calibration:
        - `shock_pnl`: typically -k * SL_points * point_value for k stops in
          one day. Example: SL=1000 pt, NQ point_value=$20, k=2 stops -> -$40k.
        - `shock_prob`:
            - 0.000 = baseline empirical (no shock injection; matches history
              where 0/904 days had 2 SL hits).
            - 0.005 = mild stress: ~1.25 multi-SL events per 252 trading days.
            - 0.020 = severe stress: ~5 multi-SL events per 252 trading days
              (COVID-style cluster).

    Args:
        historical_daily_pnl: 1-D series of daily PnL (length N).
        shock_pnl: PnL value used for shock days (typically large negative).
        shock_prob: per-day probability of substitution. Must be in [0, 1].
        n_days: total days to sample.
        block_size: bootstrap block length for base sampling.
        rng: numpy Generator.

    Returns:
        1-D ndarray of length n_days with sampled (and possibly shock-injected)
        daily PnL.

    Raises:
        ValueError: if shock_prob not in [0, 1].

    Note:
        With shock_prob == 0.0 the uniform draw is skipped and the result is
        bitwise identical to `block_bootstrap_days` for the same seed.
    """
    if not (0.0 <= shock_prob <= 1.0):
        raise ValueError(f"shock_prob {shock_prob} not in [0, 1]")
    base = block_bootstrap_days(historical_daily_pnl, n_days, block_size, rng)
    if shock_prob == 0.0:
        return base
    mask = rng.uniform(size=n_days) < shock_prob
    if mask.any():
        base[mask] = shock_pnl
    return base


# =============================================================================
# LEGACY (7-tier) — DO NOT MODIFY (parity tests)
# =============================================================================


@dataclass
class SimResult:
    passed: bool
    reason: str
    final_balance: float
    days_traded: int
    floor_at_end: float
    daily_pnl: list[float] = field(default_factory=list)


def simulate_evaluation(
    historical_daily_pnl: pd.Series,
    account: ApexAccount,
    max_days: int = 60,
    block_size: int = 10,
    rng: Optional[np.random.Generator] = None,
) -> SimResult:
    """LEGACY 7-tier eval simulation. Kept for Phase 0-8 parity."""
    if rng is None:
        rng = np.random.default_rng()
    sampled = block_bootstrap_days(historical_daily_pnl, max_days, block_size, rng)
    tracker = ApexDrawdownTracker(account)
    balance = account.starting_balance
    daily_pnl_realized: list[float] = []

    for day_idx, pnl in enumerate(sampled, start=1):
        intraday_low = balance + min(float(pnl), 0.0)
        if tracker.violated(intraday_low):
            return SimResult(False, "drawdown_violated", balance + float(pnl), day_idx, tracker.floor, daily_pnl_realized)
        balance += float(pnl)
        daily_pnl_realized.append(float(pnl))
        tracker.update_eod(balance)

        target_balance = account.starting_balance + account.profit_target
        if balance >= target_balance:
            if (
                enough_trading_days(day_idx, account.min_trading_days)
                and consistency_ok(daily_pnl_realized, account.consistency_threshold)
            ):
                return SimResult(True, "passed", balance, day_idx, tracker.floor, daily_pnl_realized)

    return SimResult(False, "timeout", balance, max_days, tracker.floor, daily_pnl_realized)


def run_mc_sweep(
    historical_daily_pnl: pd.Series,
    account: ApexAccount,
    n_sims: int = 1000,
    max_days: int = 60,
    block_size: int = 10,
    seed: int = 42,
) -> pd.DataFrame:
    """LEGACY MC sweep. Kept for Phase 0-8 parity."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_sims):
        result = simulate_evaluation(historical_daily_pnl, account, max_days, block_size, rng)
        rows.append({
            "sim": i, "account": account.name, "passed": result.passed,
            "reason": result.reason, "final_balance": result.final_balance,
            "days_traded": result.days_traded, "floor_at_end": result.floor_at_end,
        })
    return pd.DataFrame(rows)


# =============================================================================
# 4.0 — Eval simulation
# =============================================================================


@dataclass
class EvalResult:
    passed: bool
    reason: str
    final_balance: float
    days_traded: int
    floor_at_end: float
    daily_pnl: list[float] = field(default_factory=list)


def simulate_eval_4(
    pnl_per_mini: pd.Series,
    eval_cfg: ApexEval,
    contracts_mini: float,
    block_size: int = 10,
    rng: Optional[np.random.Generator] = None,
    sampler_fn=None,
) -> EvalResult:
    """Simulate one Apex 4.0 evaluation run for one product variant.

    Args:
        pnl_per_mini: Historical daily PnL per 1 NQ mini contract (or equivalent).
            For MNQ usage, divide values by 10 before passing.
        eval_cfg: ApexEval config (tier × variant).
        contracts_mini: Constant contracts (mini equivalent) used during eval.
            Capped by eval_cfg.max_contracts_mini.
        block_size: Bootstrap block length (ignored if sampler_fn provided).
        rng: Random generator.
        sampler_fn: Optional callable (n_days, rng) -> ndarray. If None, uses
            block_bootstrap_days. Phase 10B: pass a regime-aware sampler here.

    Notes:
        - Intraday eval has NO DLL (eval_cfg.daily_loss_limit is None).
        - Eval has NO consistency rule (4.0 specific).
        - Trailing freeze at start + $100 (via safety_net_buffer in tracker).
        - Time limit = eval_cfg.time_limit_days (default 30).
    """
    if rng is None:
        rng = np.random.default_rng()
    contracts = min(float(contracts_mini), float(eval_cfg.max_contracts_mini))
    if sampler_fn is None:
        sampled_per_mini = block_bootstrap_days(
            pnl_per_mini, eval_cfg.time_limit_days, block_size, rng
        )
    else:
        sampled_per_mini = sampler_fn(eval_cfg.time_limit_days, rng)
    tracker = ApexTrailingTracker(
        starting_balance=eval_cfg.starting_balance,
        max_drawdown=eval_cfg.max_drawdown,
        safety_net_buffer=100.0,
        trail_type=eval_cfg.variant,
    )
    balance = eval_cfg.starting_balance
    daily_pnl_realized: list[float] = []
    target_balance = eval_cfg.starting_balance + eval_cfg.profit_target

    # Phase 13: detect 2D sampler (with intraday extremes) vs legacy 1D (pnl-only)
    sampled_arr = np.asarray(sampled_per_mini)
    has_extremes = sampled_arr.ndim == 2

    for day_idx in range(1, len(sampled_arr) + 1):
        if has_extremes:
            pnl_unit = float(sampled_arr[day_idx - 1, 0])
            low_unit = float(sampled_arr[day_idx - 1, 1])
            high_unit = float(sampled_arr[day_idx - 1, 2])
        else:
            pnl_unit = float(sampled_arr[day_idx - 1])
            low_unit = min(pnl_unit, 0.0)
            high_unit = max(pnl_unit, 0.0)

        pnl = pnl_unit * contracts
        # DLL (EOD eval only) — kept on EOD pnl per Apex docs section 2.3
        if eval_cfg.daily_loss_limit is not None and pnl < -eval_cfg.daily_loss_limit:
            return EvalResult(False, "dll_violated", balance + pnl, day_idx, tracker.floor, daily_pnl_realized)

        # Drawdown violation check: depends on trail variant.
        #   - intraday trail: violation if intraday low < floor (real-time check)
        #   - eod trail: violation if EOD balance < floor (snapshot only)
        if eval_cfg.variant == "intraday":
            intraday_low = balance + low_unit * contracts
            if tracker.violated(intraday_low):
                return EvalResult(False, "drawdown_violated", balance + pnl, day_idx, tracker.floor, daily_pnl_realized)
            # Intraday-trail peak update with actual intraday high BEFORE applying pnl
            intraday_high = balance + high_unit * contracts
            tracker.update_intraday_high(intraday_high)
        else:
            # EOD trail: violation only on post-EOD balance
            if tracker.violated(balance + pnl):
                return EvalResult(False, "drawdown_violated", balance + pnl, day_idx, tracker.floor, daily_pnl_realized)

        balance += pnl
        daily_pnl_realized.append(pnl)
        tracker.update_eod(balance)

        if balance >= target_balance and enough_trading_days(day_idx, eval_cfg.min_days_to_pass):
            return EvalResult(True, "passed", balance, day_idx, tracker.floor, daily_pnl_realized)

    return EvalResult(False, "timeout", balance, eval_cfg.time_limit_days, tracker.floor, daily_pnl_realized)


# =============================================================================
# 4.0 — PA lifecycle simulation
# =============================================================================


@dataclass
class PALifecycleResult:
    closed_reason: str  # "max_payouts" | "trailing_violated" | "inactivity_closed" | "dll_violated" | "timeout"
    payouts_taken: list[float] = field(default_factory=list)
    final_balance: float = 0.0
    days_traded: int = 0
    days_to_first_payout: Optional[int] = None
    floor_at_end: float = 0.0

    @property
    def total_payouts(self) -> float:
        return float(sum(self.payouts_taken))

    @property
    def n_payouts(self) -> int:
        return len(self.payouts_taken)


def simulate_pa_lifecycle(
    pnl_per_mini: pd.Series,
    pa: ApexPA,
    max_days: int = 504,
    block_size: int = 10,
    rng: Optional[np.random.Generator] = None,
    sampler_fn=None,
) -> PALifecycleResult:
    """Simulate one PA lifetime (post-eval), to close.

    Args:
        pnl_per_mini: Historical daily PnL per 1 mini contract.
        pa: ApexPA config (tier × variant).
        max_days: Max trading days to simulate (default 2 years).
        block_size: Bootstrap block length.
        rng: Random generator.

    Process per day:
        1. Determine scaling level from profit_so_far → contracts cap + DLL.
        2. Sample per-mini PnL, scale by current contracts.
        3. Clamp loss to -DLL (DLL is hard cap by broker).
        4. Apply to balance; update trailing.
        5. Check trailing violation.
        6. Update qualifying day count + inactivity tracker.
        7. Check inactivity closure.
        8. Trigger payout when qualifying≥5 AND balance≥min_balance_to_request
           AND consistency rule satisfied.
        9. Close PA after 6th payout.
    """
    if rng is None:
        rng = np.random.default_rng()
    if sampler_fn is None:
        sampled_per_mini = block_bootstrap_days(pnl_per_mini, max_days, block_size, rng)
    else:
        sampled_per_mini = sampler_fn(max_days, rng)

    balance = pa.starting_balance
    profit_so_far = 0.0
    tracker = ApexTrailingTracker(
        starting_balance=pa.starting_balance,
        max_drawdown=pa.max_drawdown,
        safety_net_buffer=pa.safety_net_buffer,
        trail_type=pa.variant,
    )
    qual_tracker = ApexQualifyingDayTracker(
        min_daily_profit=pa.qualifying_min_daily_profit,
        days_required=pa.qualifying_days_required,
    )
    inactivity = ApexInactivityTracker(
        min_profit=pa.inactivity_min_profit,
        dormant_days=pa.inactivity_dormant_days,
        close_days=pa.inactivity_close_days,
    )

    payouts_taken: list[float] = []
    payout_idx = 0
    days_to_first_payout: Optional[int] = None
    daily_pnl_history: list[float] = []

    # Phase 13: detect 2D sampler (with intraday extremes) vs legacy 1D
    sampled_arr = np.asarray(sampled_per_mini)
    has_extremes = sampled_arr.ndim == 2

    for day_idx in range(len(sampled_arr)):
        if has_extremes:
            pnl_unit = float(sampled_arr[day_idx, 0])
            low_unit = float(sampled_arr[day_idx, 1])
            high_unit = float(sampled_arr[day_idx, 2])
        else:
            pnl_unit = float(sampled_arr[day_idx])
            low_unit = min(pnl_unit, 0.0)
            high_unit = max(pnl_unit, 0.0)

        # 1. Scaling level
        level = pa.scaling_level_for_profit(profit_so_far)
        scaling_cap_mini = level.max_contracts_mini
        contracts_today = min(scaling_cap_mini, pa.max_contracts_mini)
        dll_today = level.daily_loss_limit

        # 2. Scale PnL by today's contracts
        pnl = pnl_unit * contracts_today

        # 3. DLL clamp (broker stops trading at DLL hit)
        if pnl < -dll_today:
            pnl = -dll_today
            # If broker auto-flatten at DLL, intraday low cannot go below -DLL either
            low_unit_scaled = -dll_today
        else:
            low_unit_scaled = low_unit * contracts_today
            # If extreme crosses DLL, broker would flatten there
            if low_unit_scaled < -dll_today:
                low_unit_scaled = -dll_today

        # Drawdown violation check: depends on trail variant.
        #   - intraday trail: real-time check on intraday low (MAE-aware)
        #   - eod trail: snapshot only on EOD balance
        if pa.variant == "intraday":
            intraday_low = balance + low_unit_scaled
            if tracker.violated(intraday_low):
                return PALifecycleResult(
                    closed_reason="trailing_violated",
                    payouts_taken=payouts_taken,
                    final_balance=balance + pnl,
                    days_traded=day_idx + 1,
                    days_to_first_payout=days_to_first_payout,
                    floor_at_end=tracker.floor,
                )
            # Intraday-trail peak update with actual intraday high BEFORE applying pnl
            intraday_high = balance + high_unit * contracts_today
            tracker.update_intraday_high(intraday_high)
        else:
            # EOD trail: violation only on post-EOD balance
            if tracker.violated(balance + pnl):
                return PALifecycleResult(
                    closed_reason="trailing_violated",
                    payouts_taken=payouts_taken,
                    final_balance=balance + pnl,
                    days_traded=day_idx + 1,
                    days_to_first_payout=days_to_first_payout,
                    floor_at_end=tracker.floor,
                )

        # 4. Apply
        balance += pnl
        profit_so_far = balance - pa.starting_balance
        daily_pnl_history.append(pnl)

        tracker.update_eod(balance)

        # 6. Qualifying day + inactivity
        qual_tracker.update(pnl)
        inactivity.update(day_idx, pnl)

        # 7. Inactivity closure
        if inactivity.is_closed(day_idx):
            return PALifecycleResult(
                closed_reason="inactivity_closed",
                payouts_taken=payouts_taken,
                final_balance=balance,
                days_traded=day_idx + 1,
                days_to_first_payout=days_to_first_payout,
                floor_at_end=tracker.floor,
            )

        # 8. Payout request
        if (
            qual_tracker.can_request_payout()
            and balance >= pa.min_balance_to_request
            and payout_idx < pa.max_payout_requests
            and consistency_ok(daily_pnl_history, pa.consistency_threshold)
        ):
            cap = pa.payout_ladder.cap_for_request(payout_idx)
            available = balance - pa.safety_net
            payout_amount = min(cap, available)
            if payout_amount >= pa.min_payout:
                balance -= payout_amount
                profit_so_far = balance - pa.starting_balance
                payouts_taken.append(payout_amount)
                if days_to_first_payout is None:
                    days_to_first_payout = day_idx + 1
                payout_idx += 1
                qual_tracker.reset()

                # 9. Close after 6 payouts
                if payout_idx >= pa.max_payout_requests:
                    return PALifecycleResult(
                        closed_reason="max_payouts",
                        payouts_taken=payouts_taken,
                        final_balance=balance,
                        days_traded=day_idx + 1,
                        days_to_first_payout=days_to_first_payout,
                        floor_at_end=tracker.floor,
                    )

    # Timeout
    return PALifecycleResult(
        closed_reason="timeout",
        payouts_taken=payouts_taken,
        final_balance=balance,
        days_traded=max_days,
        days_to_first_payout=days_to_first_payout,
        floor_at_end=tracker.floor,
    )


# =============================================================================
# 4.0 — Full lifecycle (Eval → PA)
# =============================================================================


@dataclass
class FullLifecycleResult:
    eval_passed: bool
    eval_days: int
    eval_reason: str
    pa_result: Optional[PALifecycleResult] = None

    @property
    def total_payouts(self) -> float:
        return self.pa_result.total_payouts if self.pa_result else 0.0

    @property
    def total_days(self) -> int:
        if self.pa_result is None:
            return self.eval_days
        return self.eval_days + self.pa_result.days_traded


def simulate_full_lifecycle(
    pnl_per_mini: pd.Series,
    product: ApexProduct,
    eval_contracts_mini: float,
    pa_max_days: int = 504,
    block_size: int = 10,
    rng: Optional[np.random.Generator] = None,
    sampler_fn=None,
    pnl_per_mini_pa: Optional[pd.Series] = None,
    sampler_fn_pa=None,
) -> FullLifecycleResult:
    """One full eval → (if passed) PA → close cycle.

    Returns FullLifecycleResult with eval pass + (optional) PA outcome.

    Args:
        pnl_per_mini: daily PnL series used for the EVAL phase.
        product: ApexProduct (eval_cfg + pa_cfg).
        eval_contracts_mini: constant contracts during eval.
        pa_max_days: max trading days for PA simulation.
        block_size: bootstrap block length (ignored if sampler_fn provided).
        rng: random generator.
        sampler_fn: optional callable for eval phase. If None, defaults to
            block_bootstrap_days on `pnl_per_mini`.
        pnl_per_mini_pa: optional separate daily PnL for PA phase (Phase 10C
            dual-geometry). If None, PA uses same `pnl_per_mini` as eval.
        sampler_fn_pa: optional callable for PA phase. If None, falls back to
            `sampler_fn` (or block_bootstrap_days on `pnl_per_mini_pa`/`pnl_per_mini`).
    """
    if rng is None:
        rng = np.random.default_rng()
    eval_res = simulate_eval_4(
        pnl_per_mini, product.eval, eval_contracts_mini, block_size, rng,
        sampler_fn=sampler_fn,
    )
    if not eval_res.passed:
        return FullLifecycleResult(False, eval_res.days_traded, eval_res.reason, None)

    pa_pnl = pnl_per_mini_pa if pnl_per_mini_pa is not None else pnl_per_mini
    pa_sampler = sampler_fn_pa if sampler_fn_pa is not None else sampler_fn
    pa_res = simulate_pa_lifecycle(
        pa_pnl, product.pa, pa_max_days, block_size, rng,
        sampler_fn=pa_sampler,
    )
    return FullLifecycleResult(True, eval_res.days_traded, eval_res.reason, pa_res)


def run_lifecycle_sweep(
    pnl_per_mini: pd.Series,
    product: ApexProduct,
    eval_contracts_mini: float,
    n_sims: int = 1000,
    pa_max_days: int = 504,
    block_size: int = 10,
    seed: int = 42,
    sampler_fn=None,
    pnl_per_mini_pa: Optional[pd.Series] = None,
    sampler_fn_pa=None,
) -> pd.DataFrame:
    """Aggregate full-lifecycle MC over n_sims. One row per sim.

    sampler_fn (optional): callable (n_days, rng) -> ndarray; if None defaults
    to block_bootstrap_days. Phase 10B uses this to inject regime-aware sampling.

    pnl_per_mini_pa / sampler_fn_pa (optional, Phase 10C): use separate PnL
    series and/or sampler for the PA phase, enabling dual-geometry strategies
    (e.g., tight TP for eval to maximize pass rate, wide TP for PA to maximize
    payouts).
    """
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_sims):
        res = simulate_full_lifecycle(
            pnl_per_mini, product, eval_contracts_mini, pa_max_days, block_size, rng,
            sampler_fn=sampler_fn,
            pnl_per_mini_pa=pnl_per_mini_pa,
            sampler_fn_pa=sampler_fn_pa,
        )
        rows.append({
            "sim": i,
            "product": product.name,
            "eval_passed": res.eval_passed,
            "eval_days": res.eval_days,
            "eval_reason": res.eval_reason,
            "pa_closed_reason": res.pa_result.closed_reason if res.pa_result else None,
            "n_payouts": res.pa_result.n_payouts if res.pa_result else 0,
            "total_payouts": res.total_payouts,
            "pa_days": res.pa_result.days_traded if res.pa_result else 0,
            "total_days": res.total_days,
            "days_to_first_payout": (
                res.pa_result.days_to_first_payout if res.pa_result else None
            ),
            "final_balance": (
                res.pa_result.final_balance if res.pa_result else None
            ),
        })
    return pd.DataFrame(rows)
