"""Phase 11E.2: Full grid lifecycle MC across 11B + 11D + 11E cells.

For all (TP, SL) cells across the extended grid, runs lifecycle MC for each
of 8 Apex products. Skips cells where MC was already computed in 11B/11D
(based on grid_summary.csv and extended_mc_summary.csv).

Output:
  - reports/phase11e_sl_elbow/full_grid_mc_summary.csv (only NEW MC runs)
  - reports/phase11e_sl_elbow/lifecycle_mc_tp{TP}_sl{SL}/{product}_lifecycle.parquet
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

TPS = [40, 60, 82, 115, 168]
SLS = [2, 4, 6, 8, 10, 12, 15, 18, 20, 22, 25, 28, 30, 40, 56, 88, 100, 137, 200, 250, 300, 400, 500, 600, 800, 1000, 1500, 2000, 10000]

# Existing MC summary CSVs (use to dedup)
EXISTING_SUMMARIES = [
    Path("reports/phase11b_grid_tp_sl/grid_summary.csv"),
    Path("reports/phase11d_extended_sl/extended_mc_summary.csv"),
]

# Backtest dirs (where to find daily PnL parquets, with different SL paddings)
BACKTEST_DIRS = [
    (Path("reports/phase11b_grid_tp_sl"), 3),  # 3-digit SL
    (Path("reports/phase11d_extended_sl"), 3),
    (Path("reports/phase11e_sl_elbow"), 5),    # 5-digit SL
]


def find_pnl_parquet(tp: int, sl: int, tier_short: str) -> Path | None:
    """Locate per-tier daily PnL parquet across 11B/11D/11E dirs."""
    fname = TIER_TO_PARQUET[tier_short][0]
    for base, pad in BACKTEST_DIRS:
        if pad == 3:
            cand = base / f"daily_pnl_tp{tp:03d}_sl{sl:03d}" / fname
        else:
            cand = base / f"daily_pnl_tp{tp:03d}_sl{sl:05d}" / fname
        if cand.exists():
            return cand
    return None


def load_pnl(tp: int, sl: int, tier_short: str) -> pd.Series:
    path = find_pnl_parquet(tp, sl, tier_short)
    if path is None:
        raise FileNotFoundError(f"No daily PnL parquet for TP={tp} SL={sl} {tier_short}")
    factor = TIER_TO_PARQUET[tier_short][1]
    return pd.read_parquet(path)["daily_pnl"] * factor


def already_computed(tp: int, sl: int, product_name: str) -> bool:
    """Check if MC for (TP, SL, product) was already in 11B or 11D summary."""
    for sumpath in EXISTING_SUMMARIES:
        if not sumpath.exists():
            continue
        df = pd.read_csv(sumpath)
        match = df[
            (df["tp_points"] == tp)
            & (df["sl_points"] == sl)
            & (df["product"] == product_name)
        ]
        if len(match) > 0:
            return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", default="configs/apex_products_2026.yaml")
    parser.add_argument("--out-dir", default="reports/phase11e_sl_elbow")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--pa-max-days", type=int, default=504)
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-contracts-mini", type=float, default=1.0)
    args = parser.parse_args()

    products = load_apex_products(args.products)
    out = Path(args.out_dir)

    # Build all combos and filter existing
    all_combos = [
        (tp, sl, product) for tp in TPS for sl in SLS for product in products
    ]
    new_combos = [
        (tp, sl, p) for (tp, sl, p) in all_combos
        if not already_computed(tp, sl, p.name)
    ]
    n_existing = len(all_combos) - len(new_combos)
    print(f"[Phase 11E.2] Total grid MC combos: {len(all_combos)}")
    print(f"[Phase 11E.2] Already in 11B/11D summaries: {n_existing}")
    print(f"[Phase 11E.2] NEW MC runs to compute: {len(new_combos)}")

    rows = []
    for i, (tp, sl, product) in enumerate(new_combos, 1):
        try:
            pnl = load_pnl(tp, sl, product.eval.tier)
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
        cell_dir = out / f"lifecycle_mc_tp{tp:03d}_sl{sl:05d}"
        cell_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cell_dir / f"{product.name}_lifecycle.parquet")

        summary = summarize(df, product)
        summary["tp_points"] = tp
        summary["sl_points"] = sl
        rows.append(summary)

        if i % 100 == 0 or i == len(new_combos):
            print(
                f"  [{i:4d}/{len(new_combos)}] TP={tp:5d} SL={sl:5d} {product.name:20s} "
                f"pass={summary['pass_rate']:.3f} E[net]=${summary.get('expected_net_per_cycle_promo', 0):,.0f}"
            )

    grid_df = pd.DataFrame(rows)
    grid_path = out / "full_grid_mc_summary.csv"
    grid_df.to_csv(grid_path, index=False)
    print(f"\n[Phase 11E.2] MC summary -> {grid_path} ({len(grid_df)} rows)")


if __name__ == "__main__":
    main()
