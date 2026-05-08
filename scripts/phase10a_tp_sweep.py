"""Phase 10A.3: Per-tier backtest sweep across multiple TP candidates.

Replicates phase7b_per_tier_backtest.py but parametrizes target_points (TP).
For each TP value, runs per-tier backtests for the 4 Apex 4.0 tiers
(25K, 50K, 100K, 150K) and saves daily PnL parquets.

Output structure:
    reports/phase10a_mfe_tp/daily_pnl_tp{TP_int}/apex_{tier}_longonly.parquet

Each TP_int dir mirrors data/daily_pnl_per_tier/ schema so the existing
phase9c_lifecycle_mc.py can be pointed at any of them via --daily-pnl-dir.

TP candidates default to MFE percentiles {60, 82, 115, 168, 245} + baseline 200,
but can be overridden via --tps.
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

# Only the 4 tiers that map to Apex 4.0 products
APEX_4_TIER_NAMES = {"apex_25k", "apex_50k", "apex_100k", "apex_150k"}


def choose_instrument(tier, nq_margin: float, mnq_margin: float):
    """Mirror phase7b: NQ if balance >= NQ_MARGIN * 1.20, else MNQ."""
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
        description="Per-tier backtest sweep across multiple TP candidates."
    )
    parser.add_argument("--data", default="data/nq_1min.parquet")
    parser.add_argument("--config", default="configs/strategy_default.yaml")
    parser.add_argument("--accounts", default="configs/apex_accounts.yaml")
    parser.add_argument("--out-dir", default="reports/phase10a_mfe_tp")
    parser.add_argument(
        "--tps",
        nargs="+",
        type=float,
        default=[60.0, 82.0, 115.0, 168.0, 200.0, 245.0],
        help="TP candidate values in points (default: MFE percentiles + baseline 200).",
    )
    parser.add_argument("--mnq-margin", type=float, default=MNQ_MARGIN_ASSUMPTION)
    parser.add_argument("--nq-margin", type=float, default=NQ_MARGIN)
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[Phase 10A.3] Loading data: {args.data}")
    df_5min = resample_to_5min(load_nq_1min(args.data))
    base_config = load_strategy_config(args.config)
    accounts = [t for t in load_apex_accounts(args.accounts) if t.name in APEX_4_TIER_NAMES]
    print(f"[Phase 10A.3] Tiers (Apex 4.0 only): {[t.name for t in accounts]}")
    print(f"[Phase 10A.3] TP candidates: {args.tps}")

    summary_rows = []
    for tp in args.tps:
        tp_int = int(round(tp))
        tp_dir = out / f"daily_pnl_tp{tp_int:03d}"
        tp_dir.mkdir(parents=True, exist_ok=True)

        for tier in accounts:
            inst_name, max_contracts, margin = choose_instrument(
                tier, args.nq_margin, args.mnq_margin
            )
            instr = load_instrument_spec(inst_name)

            cfg = replace(
                base_config,
                target_points=float(tp),
                initial_capital=tier.starting_balance,
                max_position=max_contracts,
                margin_per_contract=margin,
                flatten_at_eod=True,
                eod_cutoff_time=tier.flatten_cutoff,
                long_only=True,
            )

            res = run_backtest(df_5min, cfg, instr)
            pnl = daily_pnl_from_trades(res.trades)

            pnl_path = tp_dir / f"{tier.name}_longonly.parquet"
            pnl.to_frame().to_parquet(pnl_path)

            n_trades = len(res.trades)
            total_pnl = float(res.trades["pnl"].sum()) if not res.trades.empty else 0.0
            daily_std = float(pnl.std()) if len(pnl) > 1 else 0.0
            target_hits = (
                int((res.trades["exit_reason"] == "target").sum())
                if not res.trades.empty
                else 0
            )

            summary_rows.append(
                {
                    "tp_points": tp,
                    "tier": tier.name,
                    "instrument": inst_name,
                    "max_contracts": max_contracts,
                    "trades": n_trades,
                    "target_hits": target_hits,
                    "target_hit_rate": round(target_hits / n_trades, 3) if n_trades else 0.0,
                    "total_pnl": round(total_pnl, 0),
                    "days_traded": len(pnl),
                    "daily_std": round(daily_std, 0),
                }
            )
            print(
                f"  TP={tp:6.1f}  {tier.name:10s} {inst_name} {max_contracts}c  "
                f"trades={n_trades:4d} target_hits={target_hits:4d} "
                f"({target_hits/n_trades*100:5.1f}%)  total=${total_pnl:>10,.0f}  "
                f"std=${daily_std:>7,.0f}"
            )

    summary = pd.DataFrame(summary_rows)
    summary_path = out / "tp_sweep_backtest_summary.csv"
    summary.to_csv(summary_path, index=False)
    print(f"\n[Phase 10A.3] Sweep summary -> {summary_path}\n")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
