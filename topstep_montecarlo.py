from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

# Notebook-aligned strategy constants
POINT_VALUE = 20.0
STOP_DOLLARS = 2000.0
SLIPPAGE_PER_SIDE_TICKS = 1.5
SLIPPAGE_PER_TICK_VALUE = 5.0
TARGET_DOLLARS = 4000.0
STOP_POINTS = STOP_DOLLARS / POINT_VALUE
TARGET_POINTS = TARGET_DOLLARS / POINT_VALUE
INITIAL_CAPITAL_NOTEBOOK = 1_000_000.0
MARGIN_PER_CONTRACT = 23282.57
MARGIN_BUFFER = 0.15
FEE_PER_LEG = 0.85

TRADE_START = dt.time(10, 0)
TRADE_END = dt.time(14, 30)
REGULAR_START = dt.time(9, 30)
REGULAR_END = dt.time(16, 0)
WINDOW_START = dt.time(8, 30)
WINDOW_END = dt.time(10, 0)
MAX_TRADES_PER_DAY = 2
MAX_LONG_HOLD_DAYS = 5


@dataclass(frozen=True)
class DayProfile:
    date: dt.date
    pnl_per_contract_list: list[float]
    stop_dollars_per_contract_list: list[float]


@dataclass(frozen=True)
class RuleSet:
    name: str
    profit_target: float
    max_position: int
    max_loss_limit: float
    daily_loss_limit: Optional[float]
    consistency_threshold: float


@dataclass(frozen=True)
class SimConfig:
    n_sims: int = 1000
    seed: int = 42
    risk_pct: float = 0.01
    starting_balance: float = 50_000.0
    max_sim_days: int = 1000
    conservative_intraday: bool = True
    margin_per_contract: float = MARGIN_PER_CONTRACT
    margin_buffer: float = MARGIN_BUFFER


@dataclass(frozen=True)
class RunResult:
    run_id: int
    rule_set: str
    status: str
    reason: str
    days: int
    final_balance: float
    best_day_profit: float
    total_profit: float


@dataclass(frozen=True)
class TradeRecord:
    direction: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    contracts: int
    stop_points: float
    pnl_per_contract: float
    pnl: float
    fees: float
    exit_reason: str
    days_in_trade: int
    account_value: float
    risk_points: float
    risk_pct: float
    risk_dollars: float
    account_value_entry: float


class TradeBuilderState:
    def __init__(self) -> None:
        self.trades: list[TradeRecord] = []
        self.open_trade: Optional[dict] = None
        self.account_value = INITIAL_CAPITAL_NOTEBOOK
        self.current_day: Optional[dt.date] = None
        self.prev_day: Optional[dt.date] = None
        self.daily_trade_count = 0
        self.window_high: Optional[float] = None
        self.window_low: Optional[float] = None
        self.window_ready = False
        self.current_stats = {"open": None, "high": None, "low": None}
        self.filters_today_pass = False
        self.prev_row_timestamp: Optional[pd.Timestamp] = None
        self.prev_row_close: Optional[float] = None
        self.day_to_index: dict[dt.date, int] = {}

    def ensure_day_index(self, day: dt.date) -> int:
        if day not in self.day_to_index:
            self.day_to_index[day] = len(self.day_to_index)
        return self.day_to_index[day]


def position_size(
    equity: float,
    stop_dollars_per_contract: float,
    risk_pct: float,
    margin_per_contract: float,
    margin_buffer: float,
    max_position: int,
) -> int:
    stop_dollars_per_contract = max(stop_dollars_per_contract, 1e-9)
    margin_per_contract = max(margin_per_contract, 1e-9)
    n_risk = (risk_pct * equity) / stop_dollars_per_contract
    n_margin = equity / (margin_per_contract * (1.0 + margin_buffer))
    n = math.floor(min(n_risk, n_margin))
    return max(1, min(max_position, n))


def load_intraday_data(data_path: Path) -> pd.DataFrame:
    if not data_path.exists():
        raise FileNotFoundError(f"Data file not found: {data_path}")

    raw_df = pd.read_csv(data_path)
    required = {"Date", "Time", "Open", "High", "Low", "Close", "TotalVolume"}
    missing = required.difference(raw_df.columns)
    if missing:
        raise ValueError(f"Missing required columns in {data_path}: {sorted(missing)}")

    raw_df["date"] = pd.to_datetime(
        raw_df["Date"] + " " + raw_df["Time"],
        format="%d/%m/%Y %H:%M:%S",
        errors="coerce",
    )
    raw_df = raw_df.dropna(subset=["date"]).copy()
    raw_df = raw_df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "TotalVolume": "totalvolume",
        }
    )

    df_full = (
        raw_df.sort_values("date")
        .set_index("date")
        .resample("5min")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "totalvolume": "sum",
            }
        )
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )

    df_full["day"] = df_full["date"].dt.date
    df_full["time"] = df_full["date"].dt.time
    return df_full


def close_trade(state: TradeBuilderState, exit_time: pd.Timestamp, exit_price: float, exit_reason: str, exit_day: dt.date) -> None:
    assert state.open_trade is not None
    entry_idx = state.ensure_day_index(state.open_trade["entry_day"])
    exit_idx = state.ensure_day_index(exit_day)
    days_in_trade = exit_idx - entry_idx + 1

    contracts = int(state.open_trade.get("contracts", 1))
    if state.open_trade["direction"] == "long":
        pnl_per_contract = (exit_price - state.open_trade["entry_price"]) * POINT_VALUE
    else:
        pnl_per_contract = (state.open_trade["entry_price"] - exit_price) * POINT_VALUE

    fee_per_contract = FEE_PER_LEG * 2.0
    slippage_per_contract = SLIPPAGE_PER_SIDE_TICKS * SLIPPAGE_PER_TICK_VALUE
    pnl_per_contract -= fee_per_contract + slippage_per_contract

    pnl = pnl_per_contract * contracts
    state.account_value += pnl

    state.trades.append(
        TradeRecord(
            direction=state.open_trade["direction"],
            entry_time=state.open_trade["entry_time"],
            exit_time=exit_time,
            entry_price=float(state.open_trade["entry_price"]),
            exit_price=float(exit_price),
            contracts=contracts,
            stop_points=float(state.open_trade.get("stop_pts", STOP_POINTS)),
            pnl_per_contract=float(pnl_per_contract),
            pnl=float(pnl),
            fees=float(fee_per_contract * contracts),
            exit_reason=exit_reason,
            days_in_trade=int(days_in_trade),
            account_value=float(state.account_value),
            risk_points=float(state.open_trade.get("risk_points", 0.0)),
            risk_pct=float(state.open_trade.get("risk_pct", np.nan)),
            risk_dollars=float(state.open_trade.get("risk_dollars", 0.0)),
            account_value_entry=float(state.open_trade.get("account_value_entry", state.account_value)),
        )
    )
    state.open_trade = None


def reconstruct_trades(df_full: pd.DataFrame, risk_pct: float = 0.01) -> pd.DataFrame:
    df_regular = df_full[(df_full["time"] >= REGULAR_START) & (df_full["time"] <= REGULAR_END)].copy()
    daily_summary_regular = df_regular.groupby("day").agg({"open": "first", "high": "max", "low": "min", "close": "last"})

    df = df_full[(df_full["time"] >= WINDOW_START) & (df_full["time"] <= TRADE_END)].copy()
    state = TradeBuilderState()

    for row in df.itertuples(index=False):
        row_timestamp = pd.Timestamp(row.date)
        row_day = row_timestamp.date()
        row_time = row_timestamp.time()
        state.ensure_day_index(row_day)

        if state.current_day is None:
            state.current_day = row_day

        if row_day != state.current_day:
            if state.open_trade is not None and state.open_trade["direction"] == "short":
                assert state.prev_row_timestamp is not None and state.prev_row_close is not None
                close_trade(state, state.prev_row_timestamp, float(state.prev_row_close), "eod_short", state.current_day)

            state.prev_day = state.current_day
            state.current_day = row_day
            state.daily_trade_count = 0
            state.window_high = None
            state.window_low = None
            state.window_ready = False
            state.current_stats = {"open": None, "high": None, "low": None}
            state.filters_today_pass = False

        if WINDOW_START <= row_time <= WINDOW_END:
            state.window_high = float(row.high) if state.window_high is None else max(state.window_high, float(row.high))
            state.window_low = float(row.low) if state.window_low is None else min(state.window_low, float(row.low))

        if (not state.window_ready) and row_time >= WINDOW_END and state.window_high is not None and state.window_low is not None:
            state.window_ready = True

        if REGULAR_START <= row_time <= REGULAR_END:
            if state.current_stats["open"] is None:
                state.current_stats["open"] = float(row.open)
            state.current_stats["high"] = float(row.high) if state.current_stats["high"] is None else max(state.current_stats["high"], float(row.high))
            state.current_stats["low"] = float(row.low) if state.current_stats["low"] is None else min(state.current_stats["low"], float(row.low))

        if state.window_ready:
            prev_summary = daily_summary_regular.loc[state.prev_day] if state.prev_day in daily_summary_regular.index else None
            if prev_summary is not None and None not in (state.current_stats["open"], state.current_stats["high"], state.current_stats["low"]):
                # Notebook placeholders kept as always True
                cond1 = True
                cond2 = True
                cond3 = True
                cond4 = True
                state.filters_today_pass = bool(cond1 and cond2 and cond3 and cond4)
            else:
                state.filters_today_pass = False

        if state.open_trade is not None:
            exit_price = None
            exit_reason = None
            entry_idx = state.ensure_day_index(state.open_trade["entry_day"])
            current_idx = state.ensure_day_index(row_day)
            days_in_trade = current_idx - entry_idx + 1

            if state.open_trade["direction"] == "long":
                if float(row.low) <= float(state.open_trade["stop"]):
                    exit_price = float(state.open_trade["stop"])
                    exit_reason = "stop"
                elif float(row.high) >= float(state.open_trade["target"]):
                    exit_price = float(state.open_trade["target"])
                    exit_reason = "target"
                elif days_in_trade >= MAX_LONG_HOLD_DAYS:
                    exit_price = float(row.close)
                    exit_reason = "max_hold"
            else:
                if float(row.high) >= float(state.open_trade["stop"]):
                    exit_price = float(state.open_trade["stop"])
                    exit_reason = "stop"
                elif float(row.low) <= float(state.open_trade["target"]):
                    exit_price = float(state.open_trade["target"])
                    exit_reason = "target"
                elif row_time >= TRADE_END:
                    exit_price = float(row.close)
                    exit_reason = "eod_short"

            if exit_price is not None and exit_reason is not None:
                close_trade(state, row_timestamp, exit_price, exit_reason, row_day)
                state.prev_row_timestamp = row_timestamp
                state.prev_row_close = float(row.close)
                continue

        if (
            state.open_trade is None
            and state.window_ready
            and state.filters_today_pass
            and state.window_high is not None
            and state.window_low is not None
            and state.daily_trade_count < MAX_TRADES_PER_DAY
            and TRADE_START <= row_time <= TRADE_END
        ):
            direction = None
            entry_price = None
            stop = None
            target = None

            if float(row.high) > state.window_high:
                direction = "long"
                entry_price = float(row.close)
                stop = entry_price - STOP_POINTS
                target = entry_price + TARGET_POINTS
            elif float(row.low) < state.window_low:
                direction = "short"
                entry_price = float(row.close)
                stop = entry_price + STOP_POINTS
                target = entry_price - TARGET_POINTS

            if direction is not None and entry_price is not None and stop is not None and target is not None:
                stop_pts = abs(entry_price - stop)
                stop_dollars_per_contract = stop_pts * POINT_VALUE
                contracts = position_size(
                    equity=float(state.account_value),
                    stop_dollars_per_contract=float(stop_dollars_per_contract),
                    risk_pct=risk_pct,
                    margin_per_contract=MARGIN_PER_CONTRACT,
                    margin_buffer=MARGIN_BUFFER,
                    max_position=10**6,
                )
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
                    "risk_pct": (stop_dollars_per_contract * contracts / state.account_value * 100.0) if state.account_value else float("nan"),
                }
                state.daily_trade_count += 1

        state.prev_row_timestamp = row_timestamp
        state.prev_row_close = float(row.close)

    if state.open_trade is not None:
        last_timestamp = pd.Timestamp(df.iloc[-1]["date"])
        last_day = last_timestamp.date()
        state.ensure_day_index(last_day)
        close_trade(state, last_timestamp, float(df.iloc[-1]["close"]), "force_close", last_day)

    if not state.trades:
        raise RuntimeError("No trades generated by reconstructed strategy.")

    trades_df = pd.DataFrame([asdict(t) for t in state.trades])
    trades_df["exit_time"] = pd.to_datetime(trades_df["exit_time"])
    trades_df = trades_df.sort_values("exit_time").reset_index(drop=True)
    trades_df["cumulative_pnl"] = trades_df["pnl"].cumsum()
    trades_df["account_value"] = INITIAL_CAPITAL_NOTEBOOK + trades_df["cumulative_pnl"]
    trades_df["stop_dollars_per_contract"] = trades_df["stop_points"].fillna(STOP_POINTS) * POINT_VALUE
    return trades_df


def build_day_profiles(trades_df: pd.DataFrame) -> list[DayProfile]:
    if trades_df.empty:
        return []

    temp = trades_df.copy()
    temp["exit_time"] = pd.to_datetime(temp["exit_time"])
    temp["exit_date"] = temp["exit_time"].dt.date
    temp = temp.sort_values(["exit_date", "exit_time"]).reset_index(drop=True)

    profiles: list[DayProfile] = []
    for exit_date, grp in temp.groupby("exit_date", sort=True):
        pnl_list = [float(x) for x in grp["pnl_per_contract"].tolist()]
        stop_list = [float(x) if pd.notna(x) else STOP_DOLLARS for x in grp["stop_dollars_per_contract"].tolist()]
        if not pnl_list:
            continue
        profiles.append(DayProfile(date=exit_date, pnl_per_contract_list=pnl_list, stop_dollars_per_contract_list=stop_list))

    if not profiles:
        raise RuntimeError("No day profiles built from trades.")
    return profiles


def _mll_floor(starting_balance: float, peak_eod_balance: float, max_loss_limit: float) -> float:
    return min(starting_balance, peak_eod_balance - max_loss_limit)


def _consistency_ok(best_day_profit: float, total_profit: float, threshold: float) -> bool:
    if total_profit <= 0:
        return False
    return best_day_profit < (threshold * total_profit)


def simulate_one_run(
    run_id: int,
    rule_set: RuleSet,
    config: SimConfig,
    day_profiles: list[DayProfile],
    rng: np.random.Generator,
) -> RunResult:
    balance = float(config.starting_balance)
    peak_eod_balance = float(config.starting_balance)
    mll_floor = _mll_floor(config.starting_balance, peak_eod_balance, rule_set.max_loss_limit)

    target_balance = config.starting_balance + rule_set.profit_target
    trading_days = 0
    best_day_profit = float("-inf")

    for day in range(1, config.max_sim_days + 1):
        profile = day_profiles[int(rng.integers(0, len(day_profiles)))]
        day_start_balance = balance
        day_profit = 0.0

        for pnl_pc, stop_pc in zip(profile.pnl_per_contract_list, profile.stop_dollars_per_contract_list, strict=True):
            contracts = position_size(
                equity=balance,
                stop_dollars_per_contract=stop_pc,
                risk_pct=config.risk_pct,
                margin_per_contract=config.margin_per_contract,
                margin_buffer=config.margin_buffer,
                max_position=rule_set.max_position,
            )

            if config.conservative_intraday:
                intraday_low = balance - (stop_pc * contracts)
                if intraday_low < mll_floor - 1e-9:
                    return RunResult(
                        run_id=run_id,
                        rule_set=rule_set.name,
                        status="fail",
                        reason="mll_intraday",
                        days=trading_days + 1,
                        final_balance=balance,
                        best_day_profit=(best_day_profit if best_day_profit != float("-inf") else 0.0),
                        total_profit=balance - config.starting_balance,
                    )
                if rule_set.daily_loss_limit is not None and (intraday_low - day_start_balance) < rule_set.daily_loss_limit - 1e-9:
                    return RunResult(
                        run_id=run_id,
                        rule_set=rule_set.name,
                        status="fail",
                        reason="dll_intraday",
                        days=trading_days + 1,
                        final_balance=balance,
                        best_day_profit=(best_day_profit if best_day_profit != float("-inf") else 0.0),
                        total_profit=balance - config.starting_balance,
                    )

            realized = pnl_pc * contracts
            balance += realized
            day_profit += realized

            if balance < mll_floor - 1e-9:
                return RunResult(
                    run_id=run_id,
                    rule_set=rule_set.name,
                    status="fail",
                    reason="mll_realized",
                    days=trading_days + 1,
                    final_balance=balance,
                    best_day_profit=(best_day_profit if best_day_profit != float("-inf") else 0.0),
                    total_profit=balance - config.starting_balance,
                )

            if rule_set.daily_loss_limit is not None and (balance - day_start_balance) < rule_set.daily_loss_limit - 1e-9:
                return RunResult(
                    run_id=run_id,
                    rule_set=rule_set.name,
                    status="fail",
                    reason="dll_realized",
                    days=trading_days + 1,
                    final_balance=balance,
                    best_day_profit=(best_day_profit if best_day_profit != float("-inf") else 0.0),
                    total_profit=balance - config.starting_balance,
                )

        trading_days = day
        best_day_profit = max(best_day_profit, day_profit)
        total_profit = balance - config.starting_balance

        if (
            balance >= target_balance
            and trading_days >= 2
            and _consistency_ok(best_day_profit, total_profit, rule_set.consistency_threshold)
        ):
            return RunResult(
                run_id=run_id,
                rule_set=rule_set.name,
                status="pass",
                reason="pass_all_rules",
                days=trading_days,
                final_balance=balance,
                best_day_profit=best_day_profit,
                total_profit=total_profit,
            )

        peak_eod_balance = max(peak_eod_balance, balance)
        mll_floor = _mll_floor(config.starting_balance, peak_eod_balance, rule_set.max_loss_limit)

    return RunResult(
        run_id=run_id,
        rule_set=rule_set.name,
        status="timeout",
        reason="max_days_reached",
        days=trading_days,
        final_balance=balance,
        best_day_profit=(best_day_profit if best_day_profit != float("-inf") else 0.0),
        total_profit=balance - config.starting_balance,
    )


def simulate_many(rule_set: RuleSet, config: SimConfig, day_profiles: list[DayProfile], seed_offset: int = 0) -> list[RunResult]:
    rng = np.random.default_rng(config.seed + seed_offset)
    results: list[RunResult] = []
    for run_id in range(1, config.n_sims + 1):
        results.append(simulate_one_run(run_id=run_id, rule_set=rule_set, config=config, day_profiles=day_profiles, rng=rng))
    return results


def _quantiles(values: Iterable[float]) -> dict[str, float]:
    arr = np.array(list(values), dtype=float)
    if arr.size == 0:
        return {"mean": 0.0, "median": 0.0, "p05": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
    return {
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "p05": float(np.quantile(arr, 0.05)),
        "p95": float(np.quantile(arr, 0.95)),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }


def summarize_results(results: list[RunResult]) -> dict[str, object]:
    n = len(results)
    status_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    for r in results:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1
        reason_counts[r.reason] = reason_counts.get(r.reason, 0) + 1

    pass_count = status_counts.get("pass", 0)
    days_arr = [r.days for r in results]
    final_bal_arr = [r.final_balance for r in results]
    total_profit_arr = [r.total_profit for r in results]
    best_day_arr = [r.best_day_profit for r in results]

    return {
        "n_sims": n,
        "pass_rate": float(pass_count / n) if n else 0.0,
        "status_counts": status_counts,
        "reason_counts": reason_counts,
        "days_stats": _quantiles(days_arr),
        "final_balance_stats": _quantiles(final_bal_arr),
        "total_profit_stats": _quantiles(total_profit_arr),
        "best_day_profit_stats": _quantiles(best_day_arr),
    }


def write_runs_csv(output_path: Path, all_results: list[RunResult]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "run_id",
                "rule_set",
                "status",
                "reason",
                "days",
                "final_balance",
                "best_day_profit",
                "total_profit",
            ],
        )
        writer.writeheader()
        for row in all_results:
            writer.writerow(asdict(row))


def write_summary_json(output_path: Path, payload: dict[str, object]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=True)


def default_rule_sets() -> list[RuleSet]:
    return [
        RuleSet(
            name="topstepx_no_dll",
            profit_target=3000.0,
            max_position=5,
            max_loss_limit=2000.0,
            daily_loss_limit=None,
            consistency_threshold=0.5,
        ),
        RuleSet(
            name="legacy_with_dll",
            profit_target=3000.0,
            max_position=5,
            max_loss_limit=2000.0,
            daily_loss_limit=-1000.0,
            consistency_threshold=0.5,
        ),
    ]


def scale_day_profiles(
    day_profiles: list[DayProfile],
    pnl_scale: float,
    stop_scale: float,
) -> list[DayProfile]:
    scaled: list[DayProfile] = []
    for p in day_profiles:
        scaled.append(
            DayProfile(
                date=p.date,
                pnl_per_contract_list=[x * pnl_scale for x in p.pnl_per_contract_list],
                stop_dollars_per_contract_list=[x * stop_scale for x in p.stop_dollars_per_contract_list],
            )
        )
    return scaled


def instrument_setup(instrument: str, base_rules: list[RuleSet]) -> tuple[list[RuleSet], float, float, float]:
    instrument = instrument.lower().strip()
    if instrument == "nq":
        return base_rules, 1.0, 1.0, MARGIN_PER_CONTRACT

    if instrument == "mnq":
        # MNQ is 1/10th of NQ notional: approximate by scaling per-contract economics and margin by 0.1.
        scaled_rules = [
            RuleSet(
                name=f"{r.name}_mnq",
                profit_target=r.profit_target,
                max_position=r.max_position * 10,  # 5 NQ equivalent => 50 MNQ
                max_loss_limit=r.max_loss_limit,
                daily_loss_limit=r.daily_loss_limit,
                consistency_threshold=r.consistency_threshold,
            )
            for r in base_rules
        ]
        return scaled_rules, 0.1, 0.1, MARGIN_PER_CONTRACT * 0.1

    raise ValueError(f"Unsupported instrument: {instrument}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Topstep 50K Monte Carlo simulator from NQ_breakout strategy trades")
    parser.add_argument("--data-path", type=Path, default=Path("Data/nq-10y-1min.csv"), help="Path to NQ 1-minute CSV")
    parser.add_argument("--sims", type=int, default=1000, help="Number of Monte Carlo runs")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed")
    parser.add_argument("--risk-pct", type=float, default=0.01, help="Risk percentage for dynamic sizing")
    parser.add_argument("--instrument", choices=["nq", "mnq"], default="nq", help="Contract type for simulation")
    parser.add_argument("--starting-balance", type=float, default=50_000.0, help="Starting account balance")
    parser.add_argument("--max-sim-days", type=int, default=1000, help="Guardrail max simulated days per run")
    parser.add_argument(
        "--intraday-model",
        choices=["conservative", "realized-only"],
        default="conservative",
        help="Conservative checks worst-case intratrade stop risk; realized-only checks closed trade equity only.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("results"), help="Output directory")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rules_raw = default_rule_sets()
    rules, pnl_scale, stop_scale, margin_per_contract = instrument_setup(args.instrument, rules_raw)

    config = SimConfig(
        n_sims=int(args.sims),
        seed=int(args.seed),
        risk_pct=float(args.risk_pct),
        starting_balance=float(args.starting_balance),
        max_sim_days=int(args.max_sim_days),
        conservative_intraday=(args.intraday_model == "conservative"),
        margin_per_contract=float(margin_per_contract),
        margin_buffer=MARGIN_BUFFER,
    )

    print("[1/4] Loading and resampling intraday data...")
    df_full = load_intraday_data(args.data_path)

    print("[2/4] Reconstructing strategy trades from notebook logic...")
    trades_df = reconstruct_trades(df_full, risk_pct=0.01)
    day_profiles_raw = build_day_profiles(trades_df)
    day_profiles = scale_day_profiles(day_profiles_raw, pnl_scale=pnl_scale, stop_scale=stop_scale)

    print("[3/4] Running Monte Carlo simulations...")
    all_results: list[RunResult] = []
    summary_rules: dict[str, object] = {}

    for idx, rule in enumerate(rules):
        results = simulate_many(rule_set=rule, config=config, day_profiles=day_profiles, seed_offset=idx * 10_000)
        all_results.extend(results)
        summary_rules[rule.name] = summarize_results(results)

    print("[4/4] Writing output files...")
    summary_path = args.output_dir / "topstep_montecarlo_summary.json"
    runs_path = args.output_dir / "topstep_montecarlo_runs.csv"

    summary_payload: dict[str, object] = {
        "meta": {
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "config": asdict(config),
            "data_path": str(args.data_path),
            "intraday_model": args.intraday_model,
            "instrument": args.instrument,
            "instrument_pnl_scale": pnl_scale,
            "instrument_stop_scale": stop_scale,
        },
        "trade_reconstruction": {
            "n_trades": int(len(trades_df)),
            "n_day_profiles": int(len(day_profiles)),
            "pnl_per_contract_mean": float(trades_df["pnl_per_contract"].mean()),
            "pnl_per_contract_std": float(trades_df["pnl_per_contract"].std(ddof=0)),
            "date_start": str(trades_df["exit_time"].min().date()),
            "date_end": str(trades_df["exit_time"].max().date()),
        },
        "rules": summary_rules,
    }

    write_summary_json(summary_path, summary_payload)
    write_runs_csv(runs_path, all_results)

    print(f"Summary: {summary_path}")
    print(f"Runs CSV: {runs_path}")


if __name__ == "__main__":
    main()
