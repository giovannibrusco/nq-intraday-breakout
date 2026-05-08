"""Phase 11D.2: Lifecycle MC on extended (TP, SL) cells.

For each cell from Phase 11D.1, runs lifecycle MC × 8 prodotti × 5000 sims.

Output:
  - reports/phase11d_extended_sl/lifecycle_mc_tp{TP}_sl{SL}/{product}_lifecycle.parquet
  - reports/phase11d_extended_sl/extended_mc_summary.csv
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

# Cells from Phase 11D.1 (must mirror)
WIDER_TP_SL = [(tp, sl) for tp in [82, 115, 168] for sl in [250, 300, 400]]
TIGHTER_TP_SL = [(115, 30), (115, 40)]
ALL_CELLS = WIDER_TP_SL + TIGHTER_TP_SL


def load_pnl(grid_dir: Path, tp: int, sl: int, tier: str) -> pd.Series:
    fname, factor = TIER_TO_PARQUET[tier]
    path = grid_dir / f"daily_pnl_tp{tp:03d}_sl{sl:03d}" / fname
    if not path.exists():
        raise FileNotFoundError(f"Missing TP={tp} SL={sl} parquet for {tier}: {path}")
    return pd.read_parquet(path)["daily_pnl"] * factor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", default="configs/apex_products_2026.yaml")
    parser.add_argument("--grid-dir", default="reports/phase11d_extended_sl")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--pa-max-days", type=int, default=504)
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-contracts-mini", type=float, default=1.0)
    args = parser.parse_args()

    products = load_apex_products(args.products)
    grid_dir = Path(args.grid_dir)

    # Tighter cells only apply to 100K (eod)
    tighter_only_100k = {(115, 30), (115, 40)}

    # Compute total runs
    n_total = 0
    for tp, sl in ALL_CELLS:
        if (tp, sl) in tighter_only_100k:
            n_total += sum(1 for p in products if p.eval.tier == "100K")
        else:
            n_total += len(products)
    print(f"[Phase 11D.2] Total MC runs: {n_total}")

    rows = []
    counter = 0
    for tp, sl in ALL_CELLS:
        cell_dir = grid_dir / f"lifecycle_mc_tp{tp:03d}_sl{sl:03d}"
        cell_dir.mkdir(parents=True, exist_ok=True)

        # Tighter cells skip non-100K products (no parquet exists)
        applicable = (
            [p for p in products if p.eval.tier == "100K"]
            if (tp, sl) in tighter_only_100k
            else products
        )

        for product in applicable:
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
        print(f"  [{counter:3d}/{n_total}] TP={tp:3d} SL={sl:3d} done")

    grid_df = pd.DataFrame(rows)
    grid_path = grid_dir / "extended_mc_summary.csv"
    grid_df.to_csv(grid_path, index=False)
    print(f"\n[Phase 11D.2] Extended MC summary -> {grid_path} ({len(grid_df)} rows)")


if __name__ == "__main__":
    main()
