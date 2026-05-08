"""Phase 12.3: Shock-augmented lifecycle MC across 3 stress regimes.

For each of 8 Apex products at Phase 11E aggressive setup, run lifecycle MC
with shock-day sampler at 3 stress levels:
  - baseline:  shock_prob=0.000  (matches empirical 0/904 multi-SL days)
  - mild:      shock_prob=0.005  (~1.25 multi-SL events/year)
  - severe:    shock_prob=0.020  (~5 multi-SL events/year, COVID-style cluster)

Shock value: -k * SL_points * $20 (NQ-equivalent), k=2 (two SL hits same day).

Output:
  reports/phase12_gpd_fat_tails/shock_mc_summary.csv         (24 rows)
  reports/phase12_gpd_fat_tails/lifecycle_mc_{product}_{regime}.parquet x 24
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from nqbreakout.prop.apex import load_apex_products
from nqbreakout.prop.montecarlo import (
    block_bootstrap_with_shock_days,
    run_lifecycle_sweep,
)

import sys
sys.path.insert(0, str(Path(__file__).parent))
from phase9c_lifecycle_mc import TIER_TO_PARQUET, summarize  # noqa: E402

LOOKUP_DIRS = [
    (Path("reports/phase11b_grid_tp_sl"), 3),
    (Path("reports/phase11d_extended_sl"), 3),
    (Path("reports/phase11e_sl_elbow"), 5),
]

NQ_POINT_VALUE = 20.0  # pnl_per_mini is NQ-equivalent after TIER_TO_PARQUET factor

REGIMES = [
    ("baseline", 0.000),
    ("mild",     0.005),
    ("severe",   0.020),
]
SHOCK_K = 2  # number of SL hits modeled per shock day


def find_pnl_parquet(tp: int, sl: int, tier: str) -> Path | None:
    fname = TIER_TO_PARQUET[tier][0]
    for base, pad in LOOKUP_DIRS:
        if pad == 3:
            cand = base / f"daily_pnl_tp{tp:03d}_sl{sl:03d}" / fname
        else:
            cand = base / f"daily_pnl_tp{tp:03d}_sl{sl:05d}" / fname
        if cand.exists():
            return cand
    return None


def load_pnl(tp: int, sl: int, tier: str) -> pd.Series:
    path = find_pnl_parquet(tp, sl, tier)
    if path is None:
        raise FileNotFoundError(f"No parquet for TP={tp} SL={sl} {tier}")
    factor = TIER_TO_PARQUET[tier][1]
    return pd.read_parquet(path)["daily_pnl"] * factor


def make_shock_sampler(historical_pnl: pd.Series, shock_pnl: float,
                       shock_prob: float, block_size: int):
    """Closure for run_lifecycle_sweep's sampler_fn(n_days, rng)."""
    def sampler(n_days: int, rng: np.random.Generator) -> np.ndarray:
        return block_bootstrap_with_shock_days(
            historical_pnl, shock_pnl, shock_prob,
            n_days, block_size, rng,
        )
    return sampler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", default="configs/apex_products_2026.yaml")
    parser.add_argument("--elbow-summary",
                        default="reports/phase11e_sl_elbow/elbow_summary.csv")
    parser.add_argument("--out-dir", default="reports/phase12_gpd_fat_tails")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--pa-max-days", type=int, default=504)
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-contracts-mini", type=float, default=1.0)
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    products_by_name = {p.name: p for p in load_apex_products(args.products)}
    elbow = pd.read_csv(args.elbow_summary)

    rows = []
    n_total = len(elbow) * len(REGIMES)
    i = 0
    for _, e in elbow.iterrows():
        product_name = e["product"]
        tier = e["tier"]
        tp = int(e["best_tp_global"])
        sl = int(e["best_sl_global"])

        if product_name not in products_by_name:
            print(f"SKIP {product_name}: not in products yaml")
            continue
        product = products_by_name[product_name]

        try:
            pnl = load_pnl(tp, sl, tier)
        except FileNotFoundError as ex:
            print(f"SKIP {product_name}: {ex}")
            continue

        # Shock value: k SL hits @ NQ point value (pnl_per_mini already NQ-equivalent)
        shock_pnl = -SHOCK_K * sl * NQ_POINT_VALUE

        print(f"\n[Phase 12.3] {product_name} (TP={tp}, SL={sl}, tier={tier})")
        print(f"  shock_pnl = -{SHOCK_K} * {sl} * ${NQ_POINT_VALUE:.0f} = ${shock_pnl:,.0f}")

        for regime_name, shock_prob in REGIMES:
            i += 1
            sampler = make_shock_sampler(pnl, shock_pnl, shock_prob, args.block_size)
            df = run_lifecycle_sweep(
                pnl_per_mini=pnl,
                product=product,
                eval_contracts_mini=args.eval_contracts_mini,
                n_sims=args.sims,
                pa_max_days=args.pa_max_days,
                block_size=args.block_size,
                seed=args.seed,
                sampler_fn=sampler,
            )
            cell_path = out / f"lifecycle_mc_{product_name}_{regime_name}.parquet"
            df.to_parquet(cell_path)

            summary = summarize(df, product)
            summary["product"] = product_name
            summary["tier"] = tier
            summary["tp_points"] = tp
            summary["sl_points"] = sl
            summary["regime"] = regime_name
            summary["shock_prob"] = shock_prob
            summary["shock_pnl"] = shock_pnl
            summary["expected_shocks_per_252d"] = round(shock_prob * 252, 2)
            rows.append(summary)

            print(
                f"  [{i:2d}/{n_total}] {regime_name:8s} (p={shock_prob:.3f}, "
                f"~{shock_prob*252:.1f}/yr): "
                f"pass={summary['pass_rate']:.3f}  "
                f"E[net]=${summary.get('expected_net_per_cycle_promo', 0):,.0f}  "
                f"E[days]={summary.get('expected_cycle_days', 0):.0f}"
            )

    summary_df = pd.DataFrame(rows)
    summary_path = out / "shock_mc_summary.csv"
    summary_df.to_csv(summary_path, index=False)
    print(f"\n[Phase 12.3] Summary -> {summary_path} ({len(summary_df)} rows)")


if __name__ == "__main__":
    main()
