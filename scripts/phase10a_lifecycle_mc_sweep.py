"""Phase 10A.4: Lifecycle MC sweep across all TP variants.

For each daily-pnl-tp{TP} dir produced by phase10a_tp_sweep.py, runs the
phase9c lifecycle MC for the 8 Apex 4.0 products and aggregates summaries.

Reuses the same montecarlo functions and summarization logic as phase9c.

Output:
  - reports/phase10a_mfe_tp/lifecycle_mc_tp{TP}/{product}_lifecycle.parquet
  - reports/phase10a_mfe_tp/lifecycle_mc_tp{TP}/summary.csv
  - reports/phase10a_mfe_tp/sweep_summary.csv (concatenated; 1 row per TP × product)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from nqbreakout.prop.apex import load_apex_products
from nqbreakout.prop.montecarlo import run_lifecycle_sweep

# Reuse phase9c logic (TIER_TO_PARQUET map and summarize fn)
import sys

sys.path.insert(0, str(Path(__file__).parent))
from phase9c_lifecycle_mc import TIER_TO_PARQUET, load_pnl_per_mini, summarize  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Lifecycle MC sweep across TP variants.")
    parser.add_argument("--products", default="configs/apex_products_2026.yaml")
    parser.add_argument("--sweep-dir", default="reports/phase10a_mfe_tp")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--pa-max-days", type=int, default=504)
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-contracts-mini", type=float, default=1.0)
    parser.add_argument(
        "--tps",
        nargs="+",
        type=int,
        default=[60, 82, 115, 168, 200, 245],
        help="TP candidates to sweep (must match dirs daily_pnl_tp{TP}/).",
    )
    args = parser.parse_args()

    products = load_apex_products(args.products)
    sweep_dir = Path(args.sweep_dir)

    all_rows = []
    for tp_int in args.tps:
        tp_dir = sweep_dir / f"daily_pnl_tp{tp_int:03d}"
        if not tp_dir.exists():
            print(f"[Phase 10A.4] SKIP TP={tp_int}: dir not found ({tp_dir})")
            continue

        out_dir = sweep_dir / f"lifecycle_mc_tp{tp_int:03d}"
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n[Phase 10A.4] TP={tp_int} -> running lifecycle MC...")
        summaries = []
        for product in products:
            try:
                pnl = load_pnl_per_mini(tp_dir, product.eval.tier)
            except FileNotFoundError as e:
                print(f"  SKIP {product.name}: {e}")
                continue

            df = run_lifecycle_sweep(
                pnl_per_mini=pnl,
                product=product,
                eval_contracts_mini=args.eval_contracts_mini,
                n_sims=args.sims,
                pa_max_days=args.pa_max_days,
                block_size=args.block_size,
                seed=args.seed,
            )
            df.to_parquet(out_dir / f"{product.name}_lifecycle.parquet")

            summary = summarize(df, product)
            summary["tp_points"] = tp_int
            summaries.append(summary)
            all_rows.append(summary)
            print(
                f"  {product.name}: pass={summary['pass_rate']:.3f} "
                f"E[net|promo]=${summary.get('expected_net_per_cycle_promo', 0):,.0f} "
                f"ann=${summary.get('annualized_net_promo', 0):,.0f}"
            )

        sdf = pd.DataFrame(summaries)
        sdf.to_csv(out_dir / "summary.csv", index=False)

    if not all_rows:
        print("\n[Phase 10A.4] No results produced.")
        return

    sweep_df = pd.DataFrame(all_rows)
    sweep_path = sweep_dir / "sweep_summary.csv"
    sweep_df.to_csv(sweep_path, index=False)
    print(f"\n[Phase 10A.4] Sweep summary -> {sweep_path}")

    # Pivot for quick scan: pass_rate by (TP, product)
    print("\n[Phase 10A.4] Pass rate matrix:")
    print(
        sweep_df.pivot_table(
            index="tp_points", columns="product", values="pass_rate"
        ).round(3).to_string()
    )
    print("\n[Phase 10A.4] E[net/cycle] promo matrix:")
    print(
        sweep_df.pivot_table(
            index="tp_points", columns="product", values="expected_net_per_cycle_promo"
        ).round(0).to_string()
    )


if __name__ == "__main__":
    main()
