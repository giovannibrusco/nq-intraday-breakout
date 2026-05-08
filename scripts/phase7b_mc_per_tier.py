"""Phase 7b: Monte Carlo sweep using per-tier daily PnL series.

Reads daily PnL parquets produced by phase7b_per_tier_backtest.py
(one file per tier, each computed with that tier's instrument + sizing).
Runs run_mc_sweep for each tier against its own ApexAccount.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from nqbreakout.prop.apex import load_apex_accounts
from nqbreakout.prop.montecarlo import run_mc_sweep


def main():
    parser = argparse.ArgumentParser(
        description="Monte Carlo sweep using per-tier daily PnL (Phase 7b)."
    )
    parser.add_argument("--daily-pnl-dir", default="data/daily_pnl_per_tier")
    parser.add_argument("--accounts", default="configs/apex_accounts.yaml")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--max-days", type=int, default=60)
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", default="reports/phase7b_mc_per_tier")
    args = parser.parse_args()

    accounts = load_apex_accounts(args.accounts)
    in_dir = Path(args.daily_pnl_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for acc in accounts:
        pnl_path = in_dir / f"{acc.name}_longonly.parquet"
        if not pnl_path.exists():
            print(f"SKIP {acc.name}: no daily PnL at {pnl_path}")
            continue

        daily_pnl = pd.read_parquet(pnl_path)["daily_pnl"]

        if len(daily_pnl) < args.block_size:
            print(
                f"SKIP {acc.name}: only {len(daily_pnl)} days of history "
                f"(need >= block_size={args.block_size})"
            )
            continue

        df = run_mc_sweep(
            daily_pnl,
            acc,
            n_sims=args.sims,
            max_days=args.max_days,
            block_size=args.block_size,
            seed=args.seed,
        )
        df.to_parquet(out_dir / f"{acc.name}_sims.parquet")

        passed = df["passed"]
        dd_violated = df["reason"] == "drawdown_violated"
        timeout = df["reason"] == "timeout"
        median_days = (
            float(df.loc[passed, "days_traded"].median())
            if passed.any()
            else float("nan")
        )

        rows.append(
            {
                "tier": acc.name,
                "sims": len(df),
                "pass_rate": float(passed.mean()),
                "median_days_to_pass": median_days,
                "drawdown_violated_pct": float(dd_violated.mean()),
                "timeout_pct": float(timeout.mean()),
                "n_days_history": len(daily_pnl),
                "daily_std": float(daily_pnl.std()),
                "daily_mean": float(daily_pnl.mean()),
                "trailing_threshold": acc.trailing_threshold,
                "profit_target": acc.profit_target,
            }
        )
        print(
            f"{acc.name}: pass_rate={passed.mean():.3f} | "
            f"DD_viol={dd_violated.mean():.3f} | "
            f"timeout={timeout.mean():.3f} | "
            f"daily_std=${daily_pnl.std():,.0f} | "
            f"trailing=${acc.trailing_threshold:,.0f}"
        )

    summary = pd.DataFrame(rows)
    summary_path = out_dir / "summary.csv"
    summary.to_csv(summary_path, index=False)
    print(f"\nSummary saved to {summary_path}\n")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
