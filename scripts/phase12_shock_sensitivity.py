"""Phase 12.4: Shock rate sensitivity sweep.

Picks 2 representative products (most-sensitive + most-robust from Phase 12.3)
and sweeps shock_prob across a finer grid to map degradation curve.

Output:
  reports/phase12_gpd_fat_tails/shock_sensitivity.csv
  reports/phase12_gpd_fat_tails/sensitivity_{product}.png
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
NQ_POINT_VALUE = 20.0
SHOCK_K = 2

# Most sensitive (NQ-intraday with biggest severe degradation) + most robust (small EOD)
SENSITIVITY_PRODUCTS = ["apex_150k_intraday", "apex_25k_eod"]
SHOCK_PROBS = [0.000, 0.0025, 0.005, 0.010, 0.020, 0.040, 0.080]


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


def make_sampler(historical_pnl: pd.Series, shock_pnl: float,
                 shock_prob: float, block_size: int):
    def sampler(n_days: int, rng: np.random.Generator) -> np.ndarray:
        return block_bootstrap_with_shock_days(
            historical_pnl, shock_pnl, shock_prob, n_days, block_size, rng,
        )
    return sampler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", default="configs/apex_products_2026.yaml")
    parser.add_argument("--elbow-summary",
                        default="reports/phase11e_sl_elbow/elbow_summary.csv")
    parser.add_argument("--out-dir", default="reports/phase12_gpd_fat_tails")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    products_by_name = {p.name: p for p in load_apex_products(args.products)}
    elbow = pd.read_csv(args.elbow_summary)

    rows = []
    for prod_name in SENSITIVITY_PRODUCTS:
        e = elbow[elbow["product"] == prod_name].iloc[0]
        tier = e["tier"]
        tp = int(e["best_tp_global"])
        sl = int(e["best_sl_global"])
        product = products_by_name[prod_name]
        pnl = load_pnl(tp, sl, tier)
        shock_pnl = -SHOCK_K * sl * NQ_POINT_VALUE

        print(f"\n[Phase 12.4] {prod_name} (TP={tp}, SL={sl}, shock=${shock_pnl:,.0f})")
        for prob in SHOCK_PROBS:
            sampler = make_sampler(pnl, shock_pnl, prob, args.block_size)
            df = run_lifecycle_sweep(
                pnl_per_mini=pnl, product=product,
                eval_contracts_mini=1.0, n_sims=args.sims,
                pa_max_days=504, block_size=args.block_size,
                seed=args.seed, sampler_fn=sampler,
            )
            s = summarize(df, product)
            row = {
                "product": prod_name, "tier": tier, "tp": tp, "sl": sl,
                "shock_prob": prob,
                "shocks_per_year": round(prob * 252, 2),
                "pass_rate": round(s["pass_rate"], 4),
                "expected_net_per_cycle_promo": round(s.get("expected_net_per_cycle_promo", 0), 0),
                "expected_cycle_days": round(s.get("expected_cycle_days", 0), 1),
            }
            rows.append(row)
            print(f"  p={prob:.4f} (~{prob*252:.1f}/yr): "
                  f"pass={row['pass_rate']:.3f}  "
                  f"E[net]=${row['expected_net_per_cycle_promo']:,.0f}")

    sens_df = pd.DataFrame(rows)
    sens_path = out / "shock_sensitivity.csv"
    sens_df.to_csv(sens_path, index=False)
    print(f"\n[Phase 12.4] Sensitivity -> {sens_path}")

    # Plot
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        for prod_name in SENSITIVITY_PRODUCTS:
            sub = sens_df[sens_df["product"] == prod_name].sort_values("shock_prob")
            fig, axes = plt.subplots(1, 2, figsize=(13, 5))
            axes[0].plot(sub["shocks_per_year"], sub["pass_rate"],
                         marker="o", color="tab:red")
            axes[0].set_xlabel("Multi-SL shock days per 252 trading days")
            axes[0].set_ylabel("Pass rate")
            axes[0].set_title(f"{prod_name}: pass rate vs shock frequency")
            axes[0].grid(True, alpha=0.3)
            axes[0].axvline(1.26, color="orange", linestyle="--", alpha=0.5,
                            label="mild (1.3/yr)")
            axes[0].axvline(5.04, color="red", linestyle="--", alpha=0.5,
                            label="severe (5/yr)")
            axes[0].legend()

            axes[1].plot(sub["shocks_per_year"], sub["expected_net_per_cycle_promo"],
                         marker="o", color="tab:blue")
            axes[1].set_xlabel("Multi-SL shock days per 252 trading days")
            axes[1].set_ylabel("E[net]/cycle promo ($)")
            axes[1].set_title(f"{prod_name}: E[net] vs shock frequency")
            axes[1].grid(True, alpha=0.3)
            axes[1].axvline(1.26, color="orange", linestyle="--", alpha=0.5)
            axes[1].axvline(5.04, color="red", linestyle="--", alpha=0.5)

            plt.tight_layout()
            plt.savefig(out / f"sensitivity_{prod_name}.png", dpi=80)
            plt.close()
        print(f"[Phase 12.4] Plots -> {out}/sensitivity_*.png")
    except ImportError:
        print("[Phase 12.4] matplotlib missing; skip plots")


if __name__ == "__main__":
    main()
