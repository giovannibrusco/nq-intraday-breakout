"""Phase 11A.4: Lifecycle MC sweep across SL variants at fixed TP per product.

For each Apex 4.0 product (8 total), uses the best TP from Phase 10A frontier
and varies SL across candidate values. Runs lifecycle MC and aggregates.

Output:
  - reports/phase11a_mae_sl/lifecycle_mc_tp{TP}_sl{SL}/{product}_lifecycle.parquet
  - reports/phase11a_mae_sl/sweep_summary.csv (40 rows: 5 SL × 8 products)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from nqbreakout.prop.apex import load_apex_products
from nqbreakout.prop.montecarlo import run_lifecycle_sweep

import sys
sys.path.insert(0, str(Path(__file__).parent))
from phase9c_lifecycle_mc import TIER_TO_PARQUET, summarize  # noqa: E402

# Best TP per product from Phase 10A frontier (single-net optimum, no dual)
PRODUCT_BEST_TP = {
    "apex_25k_intraday":  82,
    "apex_25k_eod":       115,
    "apex_50k_intraday":  168,
    "apex_50k_eod":       168,
    "apex_100k_intraday": 115,  # dual eval=168 ignored for 11A simplicity
    "apex_100k_eod":      168,
    "apex_150k_intraday": 115,
    "apex_150k_eod":      115,  # dual eval=245 ignored for 11A simplicity
}


def load_pnl_for_combo(sweep_dir: Path, tp: int, sl: int, tier: str) -> pd.Series:
    fname, factor = TIER_TO_PARQUET[tier]
    path = sweep_dir / f"daily_pnl_tp{tp:03d}_sl{sl:03d}" / fname
    if not path.exists():
        raise FileNotFoundError(f"Missing TP={tp} SL={sl} parquet for {tier}: {path}")
    return pd.read_parquet(path)["daily_pnl"] * factor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", default="configs/apex_products_2026.yaml")
    parser.add_argument("--sweep-dir", default="reports/phase11a_mae_sl")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--pa-max-days", type=int, default=504)
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-contracts-mini", type=float, default=1.0)
    parser.add_argument(
        "--sls",
        nargs="+",
        type=int,
        default=[56, 88, 100, 137, 200],
        help="SL candidates to sweep (must match dirs daily_pnl_tp{TP}_sl{SL}/).",
    )
    args = parser.parse_args()

    products = load_apex_products(args.products)
    sweep_dir = Path(args.sweep_dir)

    all_rows = []
    for product in products:
        if product.name not in PRODUCT_BEST_TP:
            print(f"SKIP {product.name}: no TP mapping")
            continue
        tp = PRODUCT_BEST_TP[product.name]
        tier = product.eval.tier
        tier_name = f"apex_{tier.lower()}"  # e.g., "25K" -> "apex_25k"

        print(f"\n[Phase 11A.4] {product.name} (tier={tier}, TP={tp}) -> sweep SL")
        for sl in args.sls:
            try:
                pnl = load_pnl_for_combo(sweep_dir, tp, sl, tier)
            except FileNotFoundError as e:
                print(f"  SKIP SL={sl}: {e}")
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

            cell_dir = sweep_dir / f"lifecycle_mc_tp{tp:03d}_sl{sl:03d}"
            cell_dir.mkdir(parents=True, exist_ok=True)
            df.to_parquet(cell_dir / f"{product.name}_lifecycle.parquet")

            summary = summarize(df, product)
            summary["tp_points"] = tp
            summary["sl_points"] = sl
            all_rows.append(summary)
            print(
                f"  SL={sl:3d}: pass={summary['pass_rate']:.3f} "
                f"E[net|promo]=${summary.get('expected_net_per_cycle_promo', 0):,.0f} "
                f"ann=${summary.get('annualized_net_promo', 0):,.0f}"
            )

    sweep_df = pd.DataFrame(all_rows)
    sweep_path = sweep_dir / "sweep_summary.csv"
    sweep_df.to_csv(sweep_path, index=False)
    print(f"\n[Phase 11A.4] Sweep summary -> {sweep_path}")

    print("\n[Phase 11A.4] Pass rate matrix (SL × product):")
    print(
        sweep_df.pivot_table(
            index="sl_points", columns="product", values="pass_rate"
        ).round(3).to_string()
    )
    print("\n[Phase 11A.4] E[net/cycle] promo matrix (SL × product):")
    print(
        sweep_df.pivot_table(
            index="sl_points", columns="product", values="expected_net_per_cycle_promo"
        ).round(0).to_string()
    )


if __name__ == "__main__":
    main()
