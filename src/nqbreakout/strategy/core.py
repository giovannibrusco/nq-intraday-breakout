"""Unified backtest engine.

This module replaces the duplicated trade-reconstruction logic that previously
lived both in ``NQ_breakout.ipynb`` (cells In[42], In[44], In[45]) and in
``topstep_montecarlo.reconstruct_trades``. The new ``run_backtest`` entry
point is deterministic, has zero I/O, holds no global state, and routes every
filter decision through ``FilterEngine`` and every cost charge through
``CostModel``.

Parity contract (Phase 1):
- With ``FilterEngine(FilterSpec())`` (passthrough) and ``LEGACY_COST_MODEL``,
  ``run_backtest`` produces the same trades as ``reconstruct_trades`` on the
  same 5-minute DataFrame, to machine precision. Verified by the slow parity
  test in ``tests/test_core.py``.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .config import FilterSpec, InstrumentSpec, StrategyConfig
from .costs import (
    DEFAULT_COST_MODEL,
    LEGACY_COST_MODEL,
    CostModel,
    LegacyCostModel,
    make_cost_model_for_instrument,
)
from .filters import FilterEngine


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Trade:
    """One closed trade. Fields mirror legacy ``TradeRecord`` plus the new
    cost split (gross vs net) and Phase 6 hooks (mae/mfe).
    """

    direction: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    contracts: int
    stop_points: float
    target_points: float
    pnl_per_contract_gross: float
    pnl_per_contract_net: float
    slippage_per_contract: float
    fees_per_contract: float
    pnl: float
    exit_reason: str
    days_in_trade: int
    account_value_entry: float
    account_value_exit: float
    risk_dollars: float
    risk_pct_realized: float
    mae_points: float = 0.0  # max adverse excursion, populated in Phase 6
    mfe_points: float = 0.0  # max favorable excursion, populated in Phase 6
    filters_snapshot: dict = field(default_factory=dict)


@dataclass
class BacktestResult:
    """Bundle of artifacts returned by ``run_backtest``."""

    trades: pd.DataFrame
    daily_equity: pd.Series
    config: StrategyConfig
    instrument: InstrumentSpec
    meta: dict


# ---------------------------------------------------------------------------
# Position sizing (unified signature)
# ---------------------------------------------------------------------------


def position_size(
    equity: float,
    stop_points: float,
    *,
    point_value: float,
    risk_pct: float,
    margin_per_contract: float,
    margin_buffer: float,
    max_position: int,
) -> int:
    """Compute contract count using the unified ``stop_points`` interface.

    Internally converts ``stop_points`` to dollars (``stop_points * point_value``)
    so the behavior matches the legacy ``topstep_montecarlo.position_size``,
    which takes ``stop_dollars_per_contract`` directly. The legacy code uses
    ``math.floor(min(n_risk, n_margin))`` then ``max(1, min(max_position, n))``,
    which we reproduce verbatim to preserve parity in the slow 10y test.
    """
    stop_dollars = max(stop_points * point_value, 1e-9)
    margin = max(margin_per_contract, 1e-9)
    n_risk = (risk_pct * equity) / stop_dollars
    n_margin = equity / (margin * (1.0 + margin_buffer))
    n = math.floor(min(n_risk, n_margin))
    return max(1, min(max_position, n))


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


@dataclass
class _BacktestState:
    """Per-run mutable state. Lives only inside ``run_backtest``."""

    trades: list[Trade] = field(default_factory=list)
    open_trade: dict | None = None
    account_value: float = 0.0
    current_day: dt.date | None = None
    prev_day: dt.date | None = None
    daily_trade_count: int = 0
    window_high: float | None = None
    window_low: float | None = None
    window_ready: bool = False
    current_stats: dict = field(
        default_factory=lambda: {"open": None, "high": None, "low": None}
    )
    filters_today_pass: bool = False
    allow_long_today: bool = False
    allow_short_today: bool = False
    prev_row_timestamp: pd.Timestamp | None = None
    prev_row_close: float | None = None
    day_to_index: dict[dt.date, int] = field(default_factory=dict)
    # Phase E rev: Apex Daily Loss Limit tracking (active when config.daily_loss_limit > 0)
    daily_realized_pnl: float = 0.0   # Sum closed-trade pnl_after_costs within current day
    dll_blocked_today: bool = False    # Flag set when cum day PnL hits -DLL; blocks new entries

    def ensure_day_index(self, day: dt.date) -> int:
        if day not in self.day_to_index:
            self.day_to_index[day] = len(self.day_to_index)
        return self.day_to_index[day]


# ---------------------------------------------------------------------------
# Trade closing
# ---------------------------------------------------------------------------


def _close_trade(
    state: _BacktestState,
    exit_time: pd.Timestamp,
    exit_price: float,
    exit_reason: str,
    exit_day: dt.date,
    *,
    config: StrategyConfig,
    instrument: InstrumentSpec,
    cost_model: CostModel | LegacyCostModel,
) -> None:
    """Realize the current open trade and append to ``state.trades``."""
    assert state.open_trade is not None
    ot = state.open_trade

    entry_idx = state.ensure_day_index(ot["entry_day"])
    exit_idx = state.ensure_day_index(exit_day)
    days_in_trade = exit_idx - entry_idx + 1

    contracts = int(ot.get("contracts", 1))

    # Gross PnL per contract in dollars (no costs yet).
    if ot["direction"] == "long":
        pnl_per_contract_gross = (exit_price - ot["entry_price"]) * instrument.point_value
    else:
        pnl_per_contract_gross = (ot["entry_price"] - exit_price) * instrument.point_value

    # Phase 2: cost split by exit reason via total_round_trip_cost.
    total_cost = cost_model.total_round_trip_cost(exit_reason)
    fees_per_contract = getattr(cost_model, "fee_round_trip_usd", total_cost)
    slippage_per_contract = total_cost - fees_per_contract
    pnl_per_contract_net = pnl_per_contract_gross - total_cost

    pnl = pnl_per_contract_net * contracts
    account_value_exit = state.account_value + pnl

    state.trades.append(
        Trade(
            direction=ot["direction"],
            entry_time=ot["entry_time"],
            exit_time=exit_time,
            entry_price=float(ot["entry_price"]),
            exit_price=float(exit_price),
            contracts=contracts,
            stop_points=float(ot.get("stop_pts", config.stop_points)),
            target_points=float(config.target_points),
            pnl_per_contract_gross=float(pnl_per_contract_gross),
            pnl_per_contract_net=float(pnl_per_contract_net),
            slippage_per_contract=float(slippage_per_contract),
            fees_per_contract=float(fees_per_contract),
            pnl=float(pnl),
            exit_reason=exit_reason,
            days_in_trade=int(days_in_trade),
            account_value_entry=float(ot.get("account_value_entry", state.account_value)),
            account_value_exit=float(account_value_exit),
            risk_dollars=float(ot.get("risk_dollars", 0.0)),
            risk_pct_realized=float(ot.get("risk_pct_realized", float("nan"))),
            mae_points=float(ot.get("mae_points", 0.0)),
            mfe_points=float(ot.get("mfe_points", 0.0)),
            filters_snapshot=dict(ot.get("filters_snapshot", {})),
        )
    )

    state.account_value = account_value_exit
    # Phase E rev: track cumulative realized PnL within current day for DLL check
    state.daily_realized_pnl += float(pnl)
    state.open_trade = None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_backtest(
    df_5min: pd.DataFrame,
    config: StrategyConfig,
    instrument: InstrumentSpec,
    filters: FilterEngine | None = None,
    cost_model: CostModel | LegacyCostModel | None = None,
) -> BacktestResult:
    """Run the breakout strategy on a 5-minute OHLCV DataFrame.

    Expected input schema (matching the legacy loader):
        columns: ``date`` (Timestamp), ``open``, ``high``, ``low``, ``close``
        plus optional ``totalvolume``. ``day`` and ``time`` columns are
        derived if absent.

    The function is pure: same input, same output, no globals, no I/O.
    """
    if filters is None:
        filters = FilterEngine(config.filter_spec)
    if cost_model is None:
        # Phase 13: derive Apex Rithmic cost model from instrument symbol
        # (NQ $1.99/leg, MNQ $0.51/leg). Falls back to NQ default for
        # unknown symbols rather than raising, to preserve back-compat.
        try:
            cost_model = make_cost_model_for_instrument(instrument)
        except ValueError:
            cost_model = DEFAULT_COST_MODEL

    # Derive day/time helpers if the caller did not pre-compute them.
    df_full = df_5min.copy()
    if "day" not in df_full.columns:
        df_full["day"] = df_full["date"].dt.date
    if "time" not in df_full.columns:
        df_full["time"] = df_full["date"].dt.time

    # Daily summary across the regular session, used by the (still passthrough)
    # filter logic. Kept for parity with legacy structure even though Phase 1
    # filters do not consult it.
    df_regular = df_full[
        (df_full["time"] >= config.regular_start) & (df_full["time"] <= config.regular_end)
    ].copy()
    daily_summary_regular = df_regular.groupby("day").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    )

    # Restrict the bar loop to the [window_start, trade_end] window.
    df = df_full[
        (df_full["time"] >= config.window_start) & (df_full["time"] <= config.trade_end)
    ].copy()

    state = _BacktestState(account_value=float(config.initial_capital))

    stop_points = float(config.stop_points)
    target_points = float(config.target_points)

    for row in df.itertuples(index=False):
        row_timestamp = pd.Timestamp(row.date)
        row_day = row_timestamp.date()
        row_time = row_timestamp.time()
        state.ensure_day_index(row_day)

        if state.current_day is None:
            state.current_day = row_day

        if row_day != state.current_day:
            # On day rollover, force-close any still-open short before resetting.
            if state.open_trade is not None and state.open_trade["direction"] == "short":
                assert state.prev_row_timestamp is not None and state.prev_row_close is not None
                _close_trade(
                    state,
                    state.prev_row_timestamp,
                    float(state.prev_row_close),
                    "eod_short",
                    state.current_day,
                    config=config,
                    instrument=instrument,
                    cost_model=cost_model,
                )

            state.prev_day = state.current_day
            state.current_day = row_day
            state.daily_trade_count = 0
            state.window_high = None
            state.window_low = None
            state.window_ready = False
            state.current_stats = {"open": None, "high": None, "low": None}
            state.filters_today_pass = False
            state.allow_long_today = False
            state.allow_short_today = False
            # Phase E rev: reset DLL state at day rollover
            state.daily_realized_pnl = 0.0
            state.dll_blocked_today = False

        # Build opening-range high/low.
        if config.window_start <= row_time <= config.window_end:
            state.window_high = (
                float(row.high) if state.window_high is None else max(state.window_high, float(row.high))
            )
            state.window_low = (
                float(row.low) if state.window_low is None else min(state.window_low, float(row.low))
            )

        if (
            not state.window_ready
            and row_time >= config.window_end
            and state.window_high is not None
            and state.window_low is not None
        ):
            state.window_ready = True

        # Track regular-session OHL stats (used by filter pre-checks).
        if config.regular_start <= row_time <= config.regular_end:
            if state.current_stats["open"] is None:
                state.current_stats["open"] = float(row.open)
            state.current_stats["high"] = (
                float(row.high)
                if state.current_stats["high"] is None
                else max(state.current_stats["high"], float(row.high))
            )
            state.current_stats["low"] = (
                float(row.low)
                if state.current_stats["low"] is None
                else min(state.current_stats["low"], float(row.low))
            )

        # Filter evaluation. Mirrors legacy: requires a valid prev-day summary
        # and complete current-day OHL to even consider passing.
        if state.window_ready:
            prev_summary = (
                daily_summary_regular.loc[state.prev_day]
                if state.prev_day in daily_summary_regular.index
                else None
            )
            if prev_summary is not None and None not in (
                state.current_stats["open"],
                state.current_stats["high"],
                state.current_stats["low"],
            ):
                decision = filters.evaluate(row_day, 0, None)
                # Phase 4: split long/short gating so asymmetric filters
                # (e.g. trend) can block one direction without the other.
                # filters_today_pass remains True iff EITHER direction is
                # allowed, so the outer entry branch still gates on filter
                # readiness but the inner branches pick the direction.
                state.allow_long_today = bool(decision.allow_long)
                state.allow_short_today = bool(decision.allow_short)
                state.filters_today_pass = bool(decision.allow_long or decision.allow_short)
                state.filters_snapshot = dict(decision.snapshot)
            else:
                state.filters_today_pass = False
                state.allow_long_today = False
                state.allow_short_today = False
                state.filters_snapshot = {}

        # Open-trade exit checks.
        if state.open_trade is not None:
            # Update MAE/MFE for the current bar while the trade is still open.
            # This runs before the exit check so that the closing bar is included
            # in the excursion calculation.
            ot = state.open_trade
            if ot["direction"] == "long":
                bar_favorable = float(row.high) - float(ot["entry_price"])
                bar_adverse = float(ot["entry_price"]) - float(row.low)
            else:
                bar_favorable = float(ot["entry_price"]) - float(row.low)
                bar_adverse = float(row.high) - float(ot["entry_price"])
            ot["mfe_points"] = max(ot["mfe_points"], bar_favorable)
            ot["mae_points"] = max(ot["mae_points"], bar_adverse)

            exit_price = None
            exit_reason = None
            entry_idx = state.ensure_day_index(state.open_trade["entry_day"])
            current_idx = state.ensure_day_index(row_day)
            days_in_trade = current_idx - entry_idx + 1

            # EOD force-flatten (Phase 3). Applied first, before max_long_hold_days,
            # so that Apex 16:59 cutoff takes precedence over multi-day hold logic.
            # Only active when flatten_at_eod=True; default False preserves parity.
            if (
                config.flatten_at_eod
                and row_time >= config.eod_cutoff_time
                and exit_price is None
            ):
                exit_price = float(row.close)
                exit_reason = "force_close"

            # Phase E rev: DLL force-flatten (Apex Daily Loss Limit, intraday-aware).
            # Checks intraday low (long) / high (short) of open trade against the
            # cumulative day PnL threshold. If realized + unrealized at extreme
            # crosses -daily_loss_limit, flatten at the EXACT trigger price
            # (where cum_day_pnl = -DLL) + block re-entry rest of day.
            # Gap handling: if bar opens past trigger, fill at row.open (worse).
            # Models live Apex tick-by-tick DLL trip semantics.
            # Priority: EOD > DLL > SL > TP > max_hold (matches live precedence).
            # Only active when daily_loss_limit > 0 (default 0 disables, preserves parity).
            if (
                config.daily_loss_limit > 0.0
                and exit_price is None
                and not state.dll_blocked_today
            ):
                ot_for_dll = state.open_trade
                contracts_dll = float(ot_for_dll.get("contracts", 1.0))
                entry_price_dll = float(ot_for_dll["entry_price"])
                pv_dll = float(instrument.point_value) * contracts_dll
                # Compute cum day PnL at intraday extreme
                if ot_for_dll["direction"] == "long":
                    unrealized_at_extreme = (
                        float(row.low) - entry_price_dll
                    ) * pv_dll
                else:
                    unrealized_at_extreme = (
                        entry_price_dll - float(row.high)
                    ) * pv_dll
                cum_day_pnl_at_extreme = (
                    state.daily_realized_pnl + unrealized_at_extreme
                )
                if cum_day_pnl_at_extreme <= -config.daily_loss_limit:
                    # Compute exact trigger price (where cum_day_pnl = -DLL).
                    # unrealized_at_trigger = -DLL - daily_realized_pnl
                    # For long: (trigger - entry) * pv = unrealized_at_trigger
                    #         → trigger = entry + unrealized_at_trigger / pv
                    # For short: (entry - trigger) * pv = unrealized_at_trigger
                    #         → trigger = entry - unrealized_at_trigger / pv
                    unrealized_at_trigger = (
                        -config.daily_loss_limit - state.daily_realized_pnl
                    )
                    if ot_for_dll["direction"] == "long":
                        trigger_price = entry_price_dll + unrealized_at_trigger / pv_dll
                        # Gap-down: if open ≤ trigger, fill at open (worse than trigger)
                        exit_price = min(float(row.open), trigger_price)
                    else:
                        trigger_price = entry_price_dll - unrealized_at_trigger / pv_dll
                        # Gap-up for short: if open ≥ trigger, fill at open (worse)
                        exit_price = max(float(row.open), trigger_price)
                    exit_reason = "dll_flatten"
                    state.dll_blocked_today = True

            if exit_price is None and state.open_trade["direction"] == "long":
                if float(row.low) <= float(state.open_trade["stop"]):
                    exit_price = float(state.open_trade["stop"])
                    exit_reason = "stop"
                elif float(row.high) >= float(state.open_trade["target"]):
                    exit_price = float(state.open_trade["target"])
                    exit_reason = "target"
                elif days_in_trade >= config.max_long_hold_days:
                    exit_price = float(row.close)
                    exit_reason = "max_hold"
            elif exit_price is None:
                if float(row.high) >= float(state.open_trade["stop"]):
                    exit_price = float(state.open_trade["stop"])
                    exit_reason = "stop"
                elif float(row.low) <= float(state.open_trade["target"]):
                    exit_price = float(state.open_trade["target"])
                    exit_reason = "target"
                elif row_time >= config.trade_end:
                    exit_price = float(row.close)
                    exit_reason = "eod_short"

            if exit_price is not None and exit_reason is not None:
                _close_trade(
                    state,
                    row_timestamp,
                    exit_price,
                    exit_reason,
                    row_day,
                    config=config,
                    instrument=instrument,
                    cost_model=cost_model,
                )
                state.prev_row_timestamp = row_timestamp
                state.prev_row_close = float(row.close)
                continue

        # New-trade entry checks.
        if (
            state.open_trade is None
            and state.window_ready
            and state.filters_today_pass
            and state.window_high is not None
            and state.window_low is not None
            and state.daily_trade_count < config.max_trades_per_day
            and config.trade_start <= row_time <= config.trade_end
            and not state.dll_blocked_today  # Phase E rev: DLL gate
        ):
            direction = None
            entry_price = None
            stop = None
            target = None

            if float(row.high) > state.window_high and state.allow_long_today:
                direction = "long"
                entry_price = float(row.close)
                stop = entry_price - stop_points
                target = entry_price + target_points
            elif (
                not config.long_only
                and float(row.low) < state.window_low
                and state.allow_short_today
            ):
                direction = "short"
                entry_price = float(row.close)
                stop = entry_price + stop_points
                target = entry_price - target_points

            if direction is not None and entry_price is not None and stop is not None and target is not None:
                stop_pts = abs(entry_price - stop)
                contracts = position_size(
                    equity=float(state.account_value),
                    stop_points=float(stop_pts),
                    point_value=instrument.point_value,
                    risk_pct=config.risk_pct,
                    margin_per_contract=config.margin_per_contract,
                    margin_buffer=config.margin_buffer,
                    max_position=config.max_position,
                )
                stop_dollars_per_contract = stop_pts * instrument.point_value
                state.open_trade = {
                    "direction": direction,
                    "entry_time": row_timestamp,
                    "entry_price": entry_price,
                    "stop": stop,
                    "target": target,
                    "entry_day": row_day,
                    "stop_pts": stop_pts,
                    "contracts": contracts,
                    "risk_per_contract": stop_dollars_per_contract,
                    "risk_dollars": stop_dollars_per_contract * contracts,
                    "risk_points": stop_pts * contracts,
                    "account_value_entry": state.account_value,
                    "risk_pct_realized": (
                        stop_dollars_per_contract * contracts / state.account_value * 100.0
                        if state.account_value
                        else float("nan")
                    ),
                    "mae_points": 0.0,
                    "mfe_points": 0.0,
                    "filters_snapshot": getattr(state, "filters_snapshot", {}),
                }
                state.daily_trade_count += 1

        state.prev_row_timestamp = row_timestamp
        state.prev_row_close = float(row.close)

    # Force-close any trade still open at the end of the dataframe.
    if state.open_trade is not None:
        last_timestamp = pd.Timestamp(df.iloc[-1]["date"])
        last_day = last_timestamp.date()
        state.ensure_day_index(last_day)
        _close_trade(
            state,
            last_timestamp,
            float(df.iloc[-1]["close"]),
            "force_close",
            last_day,
            config=config,
            instrument=instrument,
            cost_model=cost_model,
        )

    # Build trades DataFrame.
    if not state.trades:
        trades_df = pd.DataFrame(
            columns=[
                "direction",
                "entry_time",
                "exit_time",
                "entry_price",
                "exit_price",
                "contracts",
                "stop_points",
                "target_points",
                "pnl_per_contract_gross",
                "pnl_per_contract_net",
                "slippage_per_contract",
                "fees_per_contract",
                "pnl",
                "exit_reason",
                "days_in_trade",
                "account_value_entry",
                "account_value_exit",
                "risk_dollars",
                "risk_pct_realized",
                "mae_points",
                "mfe_points",
            ]
        )
        daily_equity = pd.Series(dtype=float, name="equity")
    else:
        trades_df = pd.DataFrame([_trade_to_dict(t) for t in state.trades])
        trades_df["exit_time"] = pd.to_datetime(trades_df["exit_time"])
        trades_df["entry_time"] = pd.to_datetime(trades_df["entry_time"])
        trades_df = trades_df.sort_values("exit_time").reset_index(drop=True)
        trades_df["cumulative_pnl"] = trades_df["pnl"].cumsum()
        trades_df["account_value"] = float(config.initial_capital) + trades_df["cumulative_pnl"]
        # Legacy compat alias (used by topstep_montecarlo.build_day_profiles).
        trades_df["pnl_per_contract"] = trades_df["pnl_per_contract_net"]
        trades_df["stop_dollars_per_contract"] = (
            trades_df["stop_points"].fillna(stop_points) * instrument.point_value
        )

        # Daily equity = end-of-day account value, indexed by exit date.
        exit_dates = trades_df["exit_time"].dt.normalize()
        daily_equity = (
            pd.DataFrame({"date": exit_dates, "account_value": trades_df["account_value"]})
            .groupby("date")["account_value"]
            .last()
        )
        daily_equity.name = "equity"

    meta = {
        "n_trades": int(len(trades_df)),
        "total_pnl": float(trades_df["pnl"].sum()) if not trades_df.empty else 0.0,
        "final_account_value": (
            float(trades_df["account_value"].iloc[-1]) if not trades_df.empty else float(config.initial_capital)
        ),
        "cost_model": repr(cost_model),
    }

    return BacktestResult(
        trades=trades_df,
        daily_equity=daily_equity,
        config=config,
        instrument=instrument,
        meta=meta,
    )


def _trade_to_dict(t: Trade) -> dict[str, Any]:
    """Convert ``Trade`` to a flat dict (used to build the trades DataFrame).

    We avoid ``dataclasses.asdict`` because it would deep-copy the
    ``filters_snapshot`` dict for every trade; we keep snapshots out of the
    main DataFrame columns and store them as Python objects in a single column.
    """
    return {
        "direction": t.direction,
        "entry_time": t.entry_time,
        "exit_time": t.exit_time,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "contracts": t.contracts,
        "stop_points": t.stop_points,
        "target_points": t.target_points,
        "pnl_per_contract_gross": t.pnl_per_contract_gross,
        "pnl_per_contract_net": t.pnl_per_contract_net,
        "slippage_per_contract": t.slippage_per_contract,
        "fees_per_contract": t.fees_per_contract,
        "pnl": t.pnl,
        "exit_reason": t.exit_reason,
        "days_in_trade": t.days_in_trade,
        "account_value_entry": t.account_value_entry,
        "account_value_exit": t.account_value_exit,
        "risk_dollars": t.risk_dollars,
        "risk_pct_realized": t.risk_pct_realized,
        "mae_points": t.mae_points,
        "mfe_points": t.mfe_points,
    }
