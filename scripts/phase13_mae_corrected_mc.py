"""Phase 13.6: MAE-corrected lifecycle MC for 8 aggressive setups.

For each Phase 11E aggressive setup, runs lifecycle MC with the new
``block_bootstrap_with_extremes`` sampler that returns per-day
(pnl, intraday_low_delta, intraday_high_delta) triplets so the
``ApexTrailingTracker`` can detect intraday violations correctly.

Compared against Phase 13.2 (cost-corrected, proxy-based) baseline.

Output:
  reports/phase13_mae_intraday/mae_corrected_mc_summary.csv
  reports/phase13_mae_intraday/lifecycle_mc_{product}.parquet  x 8
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from nqbreakout.prop.apex import load_apex_products
from nqbreakout.prop.montecarlo import (
    block_bootstrap_with_extremes,
    run_lifecycle_sweep,
)

import sys
sys.path.insert(0, str(Path(__file__).parent))
from phase9c_lifecycle_mc import summarize  # noqa: E402

# Used to look up baseline (Phase 13.2 cost-corrected) numbers for comparison
BASELINE_CSV = Path("reports/phase13_cost_fix/cost_corrected_summary.csv")


def make_extremes_sampler(extremes_df: pd.DataFrame, block_size: int):
    def sampler(n_days: int, rng: np.random.Generator) -> np.ndarray:
        return block_bootstrap_with_extremes(extremes_df, n_days, block_size, rng)
    return sampler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", default="configs/apex_products_2026.yaml")
    parser.add_argument("--extremes-dir", default="reports/phase13_mae_intraday")
    parser.add_argument("--out-dir", default="reports/phase13_mae_intraday")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    products_by_name = {p.name: p for p in load_apex_products(args.products)}
    baseline = pd.read_csv(BASELINE_CSV).set_index("product")

    extremes_dir = Path(args.extremes_dir)
    rows = []
    for product_name in baseline.index:
        ext_path = extremes_dir / f"intraday_extremes_{product_name}.parquet"
        if not ext_path.exists():
            print(f"SKIP {product_name}: no extremes parquet")
            continue
        ext_df = pd.read_parquet(ext_path)

        if product_name not in products_by_name:
            print(f"SKIP {product_name}: not in products yaml")
            continue
        product = products_by_name[product_name]

        sampler = make_extremes_sampler(ext_df, args.block_size)
        df = run_lifecycle_sweep(
            pnl_per_mini=ext_df["daily_pnl"],
            product=product,
            eval_contracts_mini=1.0,
            n_sims=args.sims,
            pa_max_days=504,
            block_size=args.block_size,
            seed=args.seed,
            sampler_fn=sampler,
        )
        cell_path = out / f"lifecycle_mc_{product_name}.parquet"
        df.to_parquet(cell_path)

        s = summarize(df, product)
        new_pass = float(s["pass_rate"])
        new_e_net = float(s.get("expected_net_per_cycle_promo", 0))
        new_cycle_days = float(s.get("expected_cycle_days", 0))
        new_annualized = float(s.get("annualized_net_promo", 0))

        b = baseline.loc[product_name]
        old_pass = float(b["pass_rate"])
        old_e_net = float(b["E_net_per_cycle_promo_NEW"])
        delta_pass = new_pass - old_pass
        delta_e_net = new_e_net - old_e_net
        delta_e_net_pct = 100 * delta_e_net / old_e_net if old_e_net else 0.0
        ratio = new_pass / old_pass if old_pass > 0 else float("nan")

        # Recommendation rule (per Phase 12 plan)
        if ratio >= 0.90:
            rec = "aggressive"
        elif ratio >= 0.70:
            rec = "elbow"
        else:
            rec = "conservative"

        rows.append({
            "product": product_name,
            "tier": b["tier"],
            "variant": b["variant"],
            "tp": int(b["tp"]),
            "sl": int(b["sl"]),
            "instrument": b["instrument"],
            "pass_baseline_proxy": round(old_pass, 4),
            "pass_mae_corrected": round(new_pass, 4),
            "pass_delta_pp": round(100 * delta_pass, 1),
            "pass_ratio_mae_over_proxy": round(ratio, 3),
            "E_net_baseline": round(old_e_net, 0),
            "E_net_mae_corrected": round(new_e_net, 0),
            "E_net_delta_$": round(delta_e_net, 0),
            "E_net_delta_pct": round(delta_e_net_pct, 1),
            "E_cycle_days_mae": round(new_cycle_days, 1),
            "annualized_promo_mae": round(new_annualized, 0),
            "mae_robust_recommendation": rec,
        })
        print(
            f"\n[Phase 13.6] {product_name:20s} ({b['variant']}, {b['instrument']})\n"
            f"  pass: proxy={old_pass:.3f} -> mae={new_pass:.3f}  "
            f"(Δ={delta_pass*100:+.1f}pp, ratio={ratio:.2f}) -> {rec}\n"
            f"  E[net]: proxy=${old_e_net:,.0f} -> mae=${new_e_net:,.0f}  "
            f"(Δ={delta_e_net:+,.0f} = {delta_e_net_pct:+.1f}%)"
        )

    summary_df = pd.DataFrame(rows).sort_values("pass_mae_corrected", ascending=False)
    summary_path = out / "mae_corrected_mc_summary.csv"
    summary_df.to_csv(summary_path, index=False)
    print(f"\n[Phase 13.6] Summary -> {summary_path}\n")
    print(summary_df.to_string(index=False))


if __name__ == "__main__":
    main()
