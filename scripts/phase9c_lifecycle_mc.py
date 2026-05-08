"""Phase 9C: Apex 4.0 lifecycle Monte Carlo (Eval + PA full lifecycle).

Runs `run_lifecycle_sweep` for each of the 8 products (4 tier × 2 variants).
Aggregates pass rate, expected lifetime payouts, days to close, ROI.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from nqbreakout.prop.apex import load_apex_products, ApexProduct
from nqbreakout.prop.montecarlo import run_lifecycle_sweep

# Map tier → (per-tier parquet base name, conversion factor to per-1-mini-NQ-equivalent)
# Convention: "1 mini" inside MC = 1 NQ-equivalent unit ($20/pt). Apex yaml uses
# `max_contracts_mini` in NQ-equivalents across all tiers for unified scaling-tier logic.
#
# Phase E rev (post Phase 14R): all tiers backtest at 1 NQ direct ($20/pt) =
# NQ-equivalent scale. TIER_TO_PARQUET factor=1.0 uniform. DLL applied at
# run_backtest level (live-nominal $ amount, no per-contract scaling).
#
# History:
#   - Phase 0-13: 25K backtested at 1 MNQ ($2/pt) due to legacy margin check
#     (NQ_margin > 25K capital). Required factor=10 to convert MNQ→NQ-eq.
#     Other tiers (50K+) at 1 NQ direct → factor=1.
#   - Phase 14: incorrectly tried factor=1 for 25K WITHOUT changing backtest
#     scale → broke per-capital leverage parity. Reverted in Phase 14R.
#   - Phase E rev (current): root-cause fix → backtest 25K at 1 NQ direct
#     (matches live PA L0 trader scale). Factor=1.0 now consistent with NEW
#     backtest scale. DLL applied at backtest level via run_backtest.
TIER_TO_PARQUET = {
    "25K":  ("apex_25k_longonly.parquet",   1.0),  # Phase E rev: NQ direct backtest
    "50K":  ("apex_50k_longonly.parquet",   1.0),
    "100K": ("apex_100k_longonly.parquet",  1.0),
    "150K": ("apex_150k_longonly.parquet",  1.0),
}


def load_pnl_per_mini(parquet_dir: Path, tier: str) -> pd.Series:
    fname, factor = TIER_TO_PARQUET[tier]
    path = parquet_dir / fname
    if not path.exists():
        raise FileNotFoundError(f"Per-tier daily PnL missing: {path}")
    s = pd.read_parquet(path)["daily_pnl"]
    return s * factor


def summarize(df: pd.DataFrame, product: ApexProduct) -> dict:
    eval_passed = df["eval_passed"]
    pass_rate = float(eval_passed.mean())

    pa_df = df[eval_passed]
    n_pa = len(pa_df)

    if n_pa == 0:
        return {
            "product": product.name,
            "tier": product.eval.tier,
            "variant": product.eval.variant,
            "pass_rate": pass_rate,
            "n_sims": len(df),
            "n_pa_runs": 0,
        }

    avg_payouts_given_pass = float(pa_df["total_payouts"].mean())
    avg_n_payouts_given_pass = float(pa_df["n_payouts"].mean())
    avg_pa_days_given_pass = float(pa_df["pa_days"].mean())
    avg_total_days_given_pass = float(pa_df["total_days"].mean())

    eval_cost = product.eval.eval_fee
    eval_cost_promo = product.eval.eval_coupon_fee
    pa_cost = product.pa.activation_fee

    # Cost per cycle = eval + activation (paid only if eval passes for activation;
    # eval cost paid regardless)
    # E[cost per cycle attempt] = eval_cost + P(pass) * pa_cost
    expected_cost = eval_cost + pass_rate * pa_cost
    expected_cost_promo = eval_cost_promo + pass_rate * pa_cost
    # E[gross per cycle attempt] = P(pass) * E[total_payouts | pass]
    expected_gross = pass_rate * avg_payouts_given_pass
    expected_net = expected_gross - expected_cost
    expected_net_promo = expected_gross - expected_cost_promo

    # Cycle time: eval_time (for losers ~30, for winners variable) + PA time (only if pass)
    avg_eval_days_loser = float(df.loc[~eval_passed, "eval_days"].mean()) if (~eval_passed).any() else float("nan")
    avg_eval_days_winner = float(pa_df["eval_days"].mean())
    expected_cycle_days = (
        pass_rate * avg_total_days_given_pass + (1 - pass_rate) * avg_eval_days_loser
    )

    # ROI annualized: net per cycle / cycle days * 252 trading days
    annual_factor = 252.0 / expected_cycle_days if expected_cycle_days > 0 else 0.0
    annualized_net = expected_net * annual_factor
    annualized_net_promo = expected_net_promo * annual_factor

    # Closure reasons
    reasons = pa_df["pa_closed_reason"].value_counts(normalize=True).to_dict()

    return {
        "product": product.name,
        "tier": product.eval.tier,
        "variant": product.eval.variant,
        "n_sims": len(df),
        "pass_rate": pass_rate,
        "lifetime_cap": product.pa.payout_ladder.lifetime_cap,
        "avg_payouts_given_pass": avg_payouts_given_pass,
        "avg_n_payouts_given_pass": avg_n_payouts_given_pass,
        "avg_pa_days_given_pass": avg_pa_days_given_pass,
        "avg_total_days_given_pass": avg_total_days_given_pass,
        "eval_cost_full": eval_cost,
        "eval_cost_promo": eval_cost_promo,
        "pa_activation": pa_cost,
        "expected_cost_per_cycle_full": expected_cost,
        "expected_cost_per_cycle_promo": expected_cost_promo,
        "expected_gross_per_cycle": expected_gross,
        "expected_net_per_cycle_full": expected_net,
        "expected_net_per_cycle_promo": expected_net_promo,
        "expected_cycle_days": expected_cycle_days,
        "annualized_net_full": annualized_net,
        "annualized_net_promo": annualized_net_promo,
        "pct_max_payouts": reasons.get("max_payouts", 0.0),
        "pct_trailing_violated": reasons.get("trailing_violated", 0.0),
        "pct_inactivity": reasons.get("inactivity_closed", 0.0),
        "pct_timeout": reasons.get("timeout", 0.0),
    }


def main():
    parser = argparse.ArgumentParser(description="Apex 4.0 lifecycle MC sweep.")
    parser.add_argument("--products", default="configs/apex_products_2026.yaml")
    parser.add_argument("--daily-pnl-dir", default="data/daily_pnl_per_tier")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--pa-max-days", type=int, default=504)  # ~2 trading years
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", default="reports/phase9c_lifecycle_mc")
    parser.add_argument(
        "--eval-contracts-mini",
        type=float,
        default=1.0,
        help="Constant contracts (mini equivalent) used during eval.",
    )
    args = parser.parse_args()

    products = load_apex_products(args.products)
    parquet_dir = Path(args.daily_pnl_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    for product in products:
        try:
            pnl_per_mini = load_pnl_per_mini(parquet_dir, product.eval.tier)
        except FileNotFoundError as e:
            print(f"SKIP {product.name}: {e}")
            continue

        df = run_lifecycle_sweep(
            pnl_per_mini=pnl_per_mini,
            product=product,
            eval_contracts_mini=args.eval_contracts_mini,
            n_sims=args.sims,
            pa_max_days=args.pa_max_days,
            block_size=args.block_size,
            seed=args.seed,
        )
        df.to_parquet(out_dir / f"{product.name}_lifecycle.parquet")

        summary = summarize(df, product)
        summaries.append(summary)
        print(
            f"{product.name}: pass={summary['pass_rate']:.3f} | "
            f"avg_payouts|pass=${summary.get('avg_payouts_given_pass', 0):,.0f} | "
            f"E[net|full]=${summary.get('expected_net_per_cycle_full', 0):,.0f} | "
            f"E[net|promo]=${summary.get('expected_net_per_cycle_promo', 0):,.0f} | "
            f"ann|promo=${summary.get('annualized_net_promo', 0):,.0f}"
        )

    summary_df = pd.DataFrame(summaries)
    summary_path = out_dir / "summary.csv"
    summary_df.to_csv(summary_path, index=False)
    print(f"\nSummary -> {summary_path}\n")
    cols_print = [
        "product", "pass_rate", "avg_payouts_given_pass", "avg_total_days_given_pass",
        "expected_net_per_cycle_promo", "annualized_net_promo",
        "pct_max_payouts", "pct_trailing_violated",
    ]
    print(summary_df[cols_print].to_string(index=False))


if __name__ == "__main__":
    main()
