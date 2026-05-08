from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from topstep_montecarlo import (
    MARGIN_BUFFER,
    MARGIN_PER_CONTRACT,
    DayProfile,
    RuleSet,
    SimConfig,
    build_day_profiles,
    default_rule_sets,
    instrument_setup,
    load_intraday_data,
    position_size,
    reconstruct_trades,
    scale_day_profiles,
)


@dataclass(frozen=True)
class CurveResult:
    run_id: int
    rule_set: str
    status: str
    reason: str
    days: int
    equity_curve: list[float]


def _mll_floor(starting_balance: float, peak_eod_balance: float, max_loss_limit: float) -> float:
    return min(starting_balance, peak_eod_balance - max_loss_limit)


def _consistency_ok(best_day_profit: float, total_profit: float, threshold: float) -> bool:
    if total_profit <= 0:
        return False
    return best_day_profit < threshold * total_profit


def simulate_curve(
    run_id: int,
    rule_set: RuleSet,
    config: SimConfig,
    day_profiles: list[DayProfile],
    rng: np.random.Generator,
) -> CurveResult:
    balance = float(config.starting_balance)
    peak_eod_balance = float(config.starting_balance)
    mll_floor = _mll_floor(config.starting_balance, peak_eod_balance, rule_set.max_loss_limit)
    target_balance = config.starting_balance + rule_set.profit_target

    equity_curve = [balance]
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
                    equity_curve.append(intraday_low)
                    return CurveResult(run_id, rule_set.name, "fail", "mll_intraday", trading_days + 1, equity_curve)
                if rule_set.daily_loss_limit is not None and (intraday_low - day_start_balance) < rule_set.daily_loss_limit - 1e-9:
                    equity_curve.append(intraday_low)
                    return CurveResult(run_id, rule_set.name, "fail", "dll_intraday", trading_days + 1, equity_curve)

            realized = pnl_pc * contracts
            balance += realized
            day_profit += realized

            if balance < mll_floor - 1e-9:
                equity_curve.append(balance)
                return CurveResult(run_id, rule_set.name, "fail", "mll_realized", trading_days + 1, equity_curve)
            if rule_set.daily_loss_limit is not None and (balance - day_start_balance) < rule_set.daily_loss_limit - 1e-9:
                equity_curve.append(balance)
                return CurveResult(run_id, rule_set.name, "fail", "dll_realized", trading_days + 1, equity_curve)

        trading_days = day
        equity_curve.append(balance)
        best_day_profit = max(best_day_profit, day_profit)
        total_profit = balance - config.starting_balance

        if (
            balance >= target_balance
            and trading_days >= 2
            and _consistency_ok(best_day_profit, total_profit, rule_set.consistency_threshold)
        ):
            return CurveResult(run_id, rule_set.name, "pass", "pass_all_rules", trading_days, equity_curve)

        peak_eod_balance = max(peak_eod_balance, balance)
        mll_floor = _mll_floor(config.starting_balance, peak_eod_balance, rule_set.max_loss_limit)

    return CurveResult(run_id, rule_set.name, "timeout", "max_days_reached", trading_days, equity_curve)


def simulate_curves(rule_set: RuleSet, config: SimConfig, day_profiles: list[DayProfile], seed_offset: int) -> list[CurveResult]:
    rng = np.random.default_rng(config.seed + seed_offset)
    return [simulate_curve(i + 1, rule_set, config, day_profiles, rng) for i in range(config.n_sims)]


def _curve_stats(curves: list[list[float]]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    max_len = max(len(c) for c in curves)
    mat = np.full((len(curves), max_len), np.nan, dtype=float)
    for i, c in enumerate(curves):
        mat[i, : len(c)] = c

    x = np.arange(max_len)
    med = np.nanmedian(mat, axis=0)
    p10 = np.nanquantile(mat, 0.10, axis=0)
    p90 = np.nanquantile(mat, 0.90, axis=0)
    return x, med, p10, p90


def make_plot(all_rule_curves: dict[str, list[CurveResult]], output_path: Path, max_lines: int = 300) -> None:
    n_rules = len(all_rule_curves)
    fig, axes = plt.subplots(1, n_rules, figsize=(9 * n_rules, 6), sharey=True)
    if n_rules == 1:
        axes = [axes]

    for ax, (rule_name, results) in zip(axes, all_rule_curves.items(), strict=True):
        pass_rate = sum(1 for r in results if r.status == "pass") / len(results)

        # draw a subset of curves for readability
        shown = results[: max_lines]
        for r in shown:
            ax.plot(np.arange(len(r.equity_curve)), r.equity_curve, color="#94a3b8", alpha=0.12, linewidth=0.8)

        curves = [r.equity_curve for r in results]
        x, med, p10, p90 = _curve_stats(curves)
        ax.plot(x, med, color="#0f172a", linewidth=2.0, label="Median")
        ax.fill_between(x, p10, p90, color="#2563eb", alpha=0.20, label="P10-P90")

        ax.set_title(f"{rule_name}\npass_rate={pass_rate:.1%} (n={len(results)})")
        ax.set_xlabel("Trading day")
        ax.grid(alpha=0.25)
        ax.axhline(results[0].equity_curve[0], color="#16a34a", linestyle="--", linewidth=1.2, label="Start balance")

    axes[0].set_ylabel("Equity ($)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=min(4, len(labels)), frameon=False)
    fig.suptitle("Topstep Monte Carlo Equity Lines", y=1.02, fontsize=14)
    fig.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot Monte Carlo equity lines for Topstep simulation")
    parser.add_argument("--data-path", type=Path, default=Path("Data/nq-10y-1min.csv"))
    parser.add_argument("--sims", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--risk-pct", type=float, default=0.01)
    parser.add_argument("--instrument", choices=["nq", "mnq"], default="nq")
    parser.add_argument("--starting-balance", type=float, default=50_000.0)
    parser.add_argument("--max-sim-days", type=int, default=1000)
    parser.add_argument("--max-lines", type=int, default=300, help="Max spaghetti lines drawn per rule set")
    parser.add_argument(
        "--intraday-model",
        choices=["conservative", "realized-only"],
        default="conservative",
    )
    parser.add_argument("--output-path", type=Path, default=Path("results/topstep_montecarlo_equity_lines.png"))
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

    print("[1/3] Rebuilding trades and day profiles...")
    df_full = load_intraday_data(args.data_path)
    trades_df = reconstruct_trades(df_full, risk_pct=0.01)
    day_profiles_raw = build_day_profiles(trades_df)
    day_profiles = scale_day_profiles(day_profiles_raw, pnl_scale=pnl_scale, stop_scale=stop_scale)

    print("[2/3] Simulating equity curves...")
    all_rule_curves: dict[str, list[CurveResult]] = {}
    for idx, rule in enumerate(rules):
        all_rule_curves[rule.name] = simulate_curves(rule, config, day_profiles, seed_offset=idx * 10_000)

    print("[3/3] Plotting...")
    make_plot(all_rule_curves, args.output_path, max_lines=int(args.max_lines))
    print(f"Saved plot: {args.output_path}")


if __name__ == "__main__":
    main()
