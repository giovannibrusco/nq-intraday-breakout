"""CLI: run Monte Carlo sweep across Apex account tiers.

Example:
    python -m nqbreakout.cli.mc_sweep \\
        --daily-pnl reports/daily_pnl.parquet \\
        --sims 1000
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from nqbreakout.prop.apex import load_apex_accounts
from nqbreakout.prop.montecarlo import run_mc_sweep


def _load_daily_pnl(path: str) -> pd.Series:
    p = Path(path)
    if p.suffix == ".parquet":
        df = pd.read_parquet(p)
    else:
        df = pd.read_csv(p)
    if "daily_pnl" in df.columns:
        return df["daily_pnl"]
    if df.shape[1] == 1:
        return df.iloc[:, 0]
    raise ValueError(f"Cannot find daily_pnl column in {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apex Monte Carlo sweep")
    parser.add_argument("--daily-pnl", required=True, help="Path to parquet/csv with daily_pnl")
    parser.add_argument("--accounts-config", default="configs/apex_accounts.yaml")
    parser.add_argument("--account", default="all", help="account name or 'all'")
    parser.add_argument("--sims", type=int, default=1000)
    parser.add_argument("--max-days", type=int, default=60)
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", default="reports/mc_sweep")
    args = parser.parse_args()

    daily_pnl = _load_daily_pnl(args.daily_pnl)
    accounts = load_apex_accounts(args.accounts_config)
    if args.account != "all":
        accounts = [a for a in accounts if a.name == args.account]
        if not accounts:
            raise SystemExit(f"No account matched '{args.account}'")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    for acc in accounts:
        df = run_mc_sweep(
            daily_pnl, acc,
            n_sims=args.sims, max_days=args.max_days,
            block_size=args.block_size, seed=args.seed,
        )
        df.to_parquet(out_dir / f"{acc.name}_sims.parquet")
        passed = df["passed"]
        summary_rows.append({
            "account": acc.name,
            "sims": len(df),
            "pass_rate": float(passed.mean()),
            "median_days_to_pass": float(df.loc[passed, "days_traded"].median()) if passed.any() else float("nan"),
            "drawdown_violated_pct": float((df["reason"] == "drawdown_violated").mean()),
            "timeout_pct": float((df["reason"] == "timeout").mean()),
        })
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(out_dir / "summary.csv", index=False)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
