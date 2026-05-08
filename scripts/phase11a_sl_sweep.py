"""Phase 11A.3: Per-tier backtest sweep across SL candidates at fixed TP.

For each unique (tier, TP) combo from Phase 10A frontier (best TP per product),
runs backtest with each SL candidate (5 values from MAE percentiles).

Output structure:
    reports/phase11a_mae_sl/daily_pnl_tp{TP:03d}_sl{SL:03d}/{tier}_longonly.parquet

Phase 11A.4 lifecycle MC will then point at these daily-PnL parquets.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd

from nqbreakout.data.loader import load_nq_1min, resample_to_5min
from nqbreakout.prop.apex import load_apex_accounts
from nqbreakout.strategy.config import load_instrument_spec, load_strategy_config
from nqbreakout.strategy.core import run_backtest

NQ_MARGIN = 23282.57
MNQ_MARGIN_ASSUMPTION = 100.0

APEX_4_TIER_NAMES = {"apex_25k", "apex_50k", "apex_100k", "apex_150k"}

# Unique (tier_name, TP_points) combos from Phase 10A frontier (best TP per product)
# Determined by inspecting reports/phase10a_mfe_tp/frontier.csv
TIER_TP_COMBOS = [
    ("apex_25k",  82),   # 25K_intraday best TP
    ("apex_25k",  115),  # 25K_eod best TP
    ("apex_50k",  168),  # 50K_intra + 50K_eod best TP (same)
    ("apex_100k", 115),  # 100K_intra best TP
    ("apex_100k", 168),  # 100K_eod best TP
    ("apex_150k", 115),  # 150K_intra + 150K_eod best TP (same)
]


def choose_instrument(tier, nq_margin: float, mnq_margin: float):
    required_nq = nq_margin * 1.20
    if tier.starting_balance >= required_nq:
        return "NQ", tier.max_contracts_nq, nq_margin
    return "MNQ", tier.max_contracts_mnq, mnq_margin


def daily_pnl_from_trades(trades: pd.DataFrame) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float, name="daily_pnl")
    s = trades.copy()
    s["exit_date"] = pd.to_datetime(s["exit_time"]).dt.normalize()
    return s.groupby("exit_date")["pnl"].sum().rename("daily_pnl")


def main():
    parser = argparse.ArgumentParser(
        description="Per-tier backtest sweep across SL candidates at fixed TP."
    )
    parser.add_argument("--data", default="data/nq_1min.parquet")
    parser.add_argument("--config", default="configs/strategy_default.yaml")
    parser.add_argument("--accounts", default="configs/apex_accounts.yaml")
    parser.add_argument("--out-dir", default="reports/phase11a_mae_sl")
    parser.add_argument(
        "--sls",
        nargs="+",
        type=float,
        default=[56.0, 88.0, 100.0, 137.0, 200.0],
        help="SL candidate values in points (MAE percentiles + baseline 100).",
    )
    parser.add_argument("--mnq-margin", type=float, default=MNQ_MARGIN_ASSUMPTION)
    parser.add_argument("--nq-margin", type=float, default=NQ_MARGIN)
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[Phase 11A.3] Loading data: {args.data}")
    df_5min = resample_to_5min(load_nq_1min(args.data))
    base_config = load_strategy_config(args.config)
    accounts_by_name = {
        t.name: t for t in load_apex_accounts(args.accounts) if t.name in APEX_4_TIER_NAMES
    }

    print(f"[Phase 11A.3] (tier, TP) combos: {TIER_TP_COMBOS}")
    print(f"[Phase 11A.3] SL candidates: {args.sls}")

    summary_rows = []
    for tier_name, tp in TIER_TP_COMBOS:
        tier = accounts_by_name[tier_name]
        inst_name, max_contracts, margin = choose_instrument(
            tier, args.nq_margin, args.mnq_margin
        )
        instr = load_instrument_spec(inst_name)

        for sl in args.sls:
            tp_int = int(round(tp))
            sl_int = int(round(sl))
            cell_dir = out / f"daily_pnl_tp{tp_int:03d}_sl{sl_int:03d}"
            cell_dir.mkdir(parents=True, exist_ok=True)

            cfg = replace(
                base_config,
                target_points=float(tp),
                stop_points=float(sl),
                initial_capital=tier.starting_balance,
                max_position=max_contracts,
                margin_per_contract=margin,
                flatten_at_eod=True,
                eod_cutoff_time=tier.flatten_cutoff,
                long_only=True,
            )

            res = run_backtest(df_5min, cfg, instr)
            pnl = daily_pnl_from_trades(res.trades)
            pnl_path = cell_dir / f"{tier_name}_longonly.parquet"
            pnl.to_frame().to_parquet(pnl_path)

            n_trades = len(res.trades)
            total_pnl = float(res.trades["pnl"].sum()) if not res.trades.empty else 0.0
            daily_std = float(pnl.std()) if len(pnl) > 1 else 0.0
            target_hits = (
                int((res.trades["exit_reason"] == "target").sum()) if not res.trades.empty else 0
            )
            stop_hits = (
                int((res.trades["exit_reason"] == "stop").sum()) if not res.trades.empty else 0
            )

            summary_rows.append({
                "tier": tier_name,
                "tp_points": tp,
                "sl_points": sl,
                "instrument": inst_name,
                "max_contracts": max_contracts,
                "trades": n_trades,
                "target_hits": target_hits,
                "target_hit_rate": round(target_hits / n_trades, 3) if n_trades else 0.0,
                "stop_hits": stop_hits,
                "stop_hit_rate": round(stop_hits / n_trades, 3) if n_trades else 0.0,
                "total_pnl": round(total_pnl, 0),
                "days_traded": len(pnl),
                "daily_std": round(daily_std, 0),
            })
            print(
                f"  {tier_name:10s} TP={tp:5.1f} SL={sl:6.1f} {inst_name} {max_contracts}c | "
                f"trades={n_trades:4d} tgt={target_hits:4d}({target_hits/n_trades*100:5.1f}%) "
                f"stp={stop_hits:4d}({stop_hits/n_trades*100:5.1f}%) "
                f"total=${total_pnl:>9,.0f} std=${daily_std:>6,.0f}"
            )

    summary = pd.DataFrame(summary_rows)
    summary_path = out / "tp_sl_sweep_backtest_summary.csv"
    summary.to_csv(summary_path, index=False)
    print(f"\n[Phase 11A.3] Sweep summary -> {summary_path}\n")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
