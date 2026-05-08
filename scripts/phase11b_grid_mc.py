"""Phase 11B.3: Lifecycle MC on full 2D grid TP × SL.

For each (TP, SL) cell from phase11b_grid_backtest.py, runs lifecycle MC for
all 8 Apex 4.0 products. Total: 25 cells × 8 products = 200 MC runs.

Output:
  - reports/phase11b_grid_tp_sl/grid_summary.csv (200 rows: TP × SL × product)
  - reports/phase11b_grid_tp_sl/lifecycle_mc_tp{TP}_sl{SL}/{product}_lifecycle.parquet
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


def load_pnl(grid_dir: Path, tp: int, sl: int, tier: str) -> pd.Series:
    fname, factor = TIER_TO_PARQUET[tier]
    path = grid_dir / f"daily_pnl_tp{tp:03d}_sl{sl:03d}" / fname
    if not path.exists():
        raise FileNotFoundError(f"Missing TP={tp} SL={sl} parquet for {tier}: {path}")
    return pd.read_parquet(path)["daily_pnl"] * factor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", default="configs/apex_products_2026.yaml")
    parser.add_argument("--grid-dir", default="reports/phase11b_grid_tp_sl")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--pa-max-days", type=int, default=504)
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-contracts-mini", type=float, default=1.0)
    parser.add_argument("--tps", nargs="+", type=int, default=[82, 115, 168, 200, 245])
    parser.add_argument("--sls", nargs="+", type=int, default=[56, 88, 100, 137, 200])
    args = parser.parse_args()

    products = load_apex_products(args.products)
    grid_dir = Path(args.grid_dir)
    n_total = len(args.tps) * len(args.sls) * len(products)
    print(f"[Phase 11B.3] Total MC runs: {n_total} (5 TP × 5 SL × 8 products)")

    rows = []
    counter = 0
    for tp in args.tps:
        for sl in args.sls:
            cell_dir = grid_dir / f"lifecycle_mc_tp{tp:03d}_sl{sl:03d}"
            cell_dir.mkdir(parents=True, exist_ok=True)
            for product in products:
                counter += 1
                try:
                    pnl = load_pnl(grid_dir, tp, sl, product.eval.tier)
                except FileNotFoundError as e:
                    print(f"  SKIP {product.name} TP={tp} SL={sl}: {e}")
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
                df.to_parquet(cell_dir / f"{product.name}_lifecycle.parquet")

                summary = summarize(df, product)
                summary["tp_points"] = tp
                summary["sl_points"] = sl
                rows.append(summary)
            print(
                f"  [{counter:3d}/{n_total}] TP={tp:3d} SL={sl:3d} done"
            )

    grid_df = pd.DataFrame(rows)
    grid_path = grid_dir / "grid_summary.csv"
    grid_df.to_csv(grid_path, index=False)
    print(f"\n[Phase 11B.3] Grid summary -> {grid_path} ({len(grid_df)} rows)")


if __name__ == "__main__":
    main()
