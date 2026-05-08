"""Phase 11B.1+2: 2D grid backtest TP × SL across all 4 Apex 4.0 tiers.

Generates per-tier daily PnL parquets for all combinations of TP × SL
candidates. Total: 5 TP × 5 SL × 4 tier = 100 backtests.

Output structure:
    reports/phase11b_grid_tp_sl/daily_pnl_tp{TP:03d}_sl{SL:03d}/{tier}_longonly.parquet
    reports/phase11b_grid_tp_sl/grid_backtest_summary.csv (100 rows)

This is the input for phase11b_grid_mc.py which runs lifecycle MC for each cell.
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
    parser = argparse.ArgumentParser(description="2D grid backtest TP × SL.")
    parser.add_argument("--data", default="data/nq_1min.parquet")
    parser.add_argument("--config", default="configs/strategy_default.yaml")
    parser.add_argument("--accounts", default="configs/apex_accounts.yaml")
    parser.add_argument("--out-dir", default="reports/phase11b_grid_tp_sl")
    parser.add_argument(
        "--tps", nargs="+", type=float, default=[82, 115, 168, 200, 245],
        help="TP candidates",
    )
    parser.add_argument(
        "--sls", nargs="+", type=float, default=[56, 88, 100, 137, 200],
        help="SL candidates",
    )
    parser.add_argument("--mnq-margin", type=float, default=MNQ_MARGIN_ASSUMPTION)
    parser.add_argument("--nq-margin", type=float, default=NQ_MARGIN)
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[Phase 11B.1+2] Loading data: {args.data}")
    df_5min = resample_to_5min(load_nq_1min(args.data))
    base_config = load_strategy_config(args.config)
    accounts = [t for t in load_apex_accounts(args.accounts) if t.name in APEX_4_TIER_NAMES]
    print(f"[Phase 11B] Tiers: {[t.name for t in accounts]}")
    print(f"[Phase 11B] Grid: {len(args.tps)} TP × {len(args.sls)} SL = {len(args.tps)*len(args.sls)} cells")
    print(f"[Phase 11B] TP: {args.tps}")
    print(f"[Phase 11B] SL: {args.sls}")

    summary_rows = []
    n_total = len(args.tps) * len(args.sls) * len(accounts)
    counter = 0
    for tp in args.tps:
        for sl in args.sls:
            tp_int = int(round(tp))
            sl_int = int(round(sl))
            cell_dir = out / f"daily_pnl_tp{tp_int:03d}_sl{sl_int:03d}"
            cell_dir.mkdir(parents=True, exist_ok=True)

            for tier in accounts:
                counter += 1
                inst_name, max_contracts, margin = choose_instrument(
                    tier, args.nq_margin, args.mnq_margin
                )
                instr = load_instrument_spec(inst_name)

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
                pnl_path = cell_dir / f"{tier.name}_longonly.parquet"
                pnl.to_frame().to_parquet(pnl_path)

                n_trades = len(res.trades)
                total_pnl = float(res.trades["pnl"].sum()) if not res.trades.empty else 0.0
                daily_std = float(pnl.std()) if len(pnl) > 1 else 0.0
                target_hits = int((res.trades["exit_reason"] == "target").sum()) if not res.trades.empty else 0
                stop_hits = int((res.trades["exit_reason"] == "stop").sum()) if not res.trades.empty else 0

                summary_rows.append({
                    "tier": tier.name,
                    "tp_points": tp,
                    "sl_points": sl,
                    "instrument": inst_name,
                    "max_contracts": max_contracts,
                    "trades": n_trades,
                    "target_hits": target_hits,
                    "stop_hits": stop_hits,
                    "target_hit_rate": round(target_hits/n_trades, 3) if n_trades else 0.0,
                    "stop_hit_rate": round(stop_hits/n_trades, 3) if n_trades else 0.0,
                    "total_pnl": round(total_pnl, 0),
                    "days_traded": len(pnl),
                    "daily_std": round(daily_std, 0),
                })
            print(
                f"  [{counter:3d}/{n_total}] TP={tp:5.1f} SL={sl:5.1f} done"
            )

    summary = pd.DataFrame(summary_rows)
    summary_path = out / "grid_backtest_summary.csv"
    summary.to_csv(summary_path, index=False)
    print(f"\n[Phase 11B.1+2] Summary -> {summary_path} ({len(summary)} rows)")


if __name__ == "__main__":
    main()
