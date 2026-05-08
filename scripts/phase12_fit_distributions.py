"""Phase 12.1: Fit Normal, t-Student, and skew-Normal distributions to daily PnL.

For each of 8 Apex products at Phase 11E aggressive setup, fit 3 distributions
to the daily PnL series. Compare AIC, KS test, and visual Q-Q plots.

Output:
  - reports/phase12_fat_tails/distribution_fits.csv (24 rows)
  - reports/phase12_fat_tails/qq_plot_{product}.png x 8
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

import sys
sys.path.insert(0, str(Path(__file__).parent))
from phase9c_lifecycle_mc import TIER_TO_PARQUET  # noqa: E402

# Daily PnL parquet lookup dirs (different SL paddings)
LOOKUP_DIRS = [
    (Path("reports/phase11b_grid_tp_sl"), 3),
    (Path("reports/phase11d_extended_sl"), 3),
    (Path("reports/phase11e_sl_elbow"), 5),
]


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
    return pd.read_parquet(path)["daily_pnl"]


def fit_normal(data: np.ndarray) -> tuple[dict, float, float]:
    mu, sigma = stats.norm.fit(data)
    loglik = stats.norm.logpdf(data, mu, sigma).sum()
    aic = 2 * 2 - 2 * loglik  # k=2 params
    ks_stat, ks_p = stats.kstest(data, "norm", args=(mu, sigma))
    return {"loc": mu, "scale": sigma}, aic, ks_p


def fit_t(data: np.ndarray) -> tuple[dict, float, float]:
    df, loc, scale = stats.t.fit(data)
    loglik = stats.t.logpdf(data, df, loc=loc, scale=scale).sum()
    aic = 2 * 3 - 2 * loglik
    ks_stat, ks_p = stats.kstest(data, "t", args=(df, loc, scale))
    return {"df": df, "loc": loc, "scale": scale}, aic, ks_p


def fit_skewnorm(data: np.ndarray) -> tuple[dict, float, float]:
    """Skew-normal (proxy for skew-t since scipy lacks direct skew-t)."""
    a, loc, scale = stats.skewnorm.fit(data)
    loglik = stats.skewnorm.logpdf(data, a, loc=loc, scale=scale).sum()
    aic = 2 * 3 - 2 * loglik
    ks_stat, ks_p = stats.kstest(data, "skewnorm", args=(a, loc, scale))
    return {"a": a, "loc": loc, "scale": scale}, aic, ks_p


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--elbow-summary", default="reports/phase11e_sl_elbow/elbow_summary.csv")
    parser.add_argument("--out-dir", default="reports/phase12_fat_tails")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    elbow = pd.read_csv(args.elbow_summary)

    rows = []
    fits_per_product = {}
    for _, e in elbow.iterrows():
        product = e["product"]
        tier = e["tier"]
        tp = int(e["best_tp_global"])
        sl = int(e["best_sl_global"])

        try:
            pnl = load_pnl(tp, sl, tier)
        except FileNotFoundError as ex:
            print(f"SKIP {product}: {ex}")
            continue

        data = np.asarray(pnl.values, dtype=float)
        # Filter out exact zeros (no trade days) which dominate body
        # Actually keep zeros — they're real "no trade" days, part of the distribution
        n_days = len(data)
        print(f"\n[Phase 12.1] {product} (TP={tp}, SL={sl}): {n_days} days")
        print(f"  mean=${data.mean():,.0f}  std=${data.std():,.0f}  "
              f"skew={stats.skew(data):.2f}  kurt={stats.kurtosis(data):.2f}")
        print(f"  min=${data.min():,.0f}  max=${data.max():,.0f}")

        fits_per_product[product] = {}
        for name, fit_fn in [("normal", fit_normal), ("t", fit_t), ("skewnorm", fit_skewnorm)]:
            try:
                params, aic, ks_p = fit_fn(data)
            except Exception as ex:
                print(f"  {name} fit failed: {ex}")
                continue
            fits_per_product[product][name] = {"params": params, "aic": aic, "ks_p": ks_p}
            rows.append({
                "product": product,
                "tier": tier,
                "tp": tp,
                "sl": sl,
                "distribution": name,
                "aic": round(aic, 1),
                "ks_p_value": round(ks_p, 4),
                "params_json": str(params),
            })
            print(f"  {name:9s}: AIC={aic:8.1f}  KS_p={ks_p:.4f}  params={params}")

    fits_df = pd.DataFrame(rows)
    fits_path = out / "distribution_fits.csv"
    fits_df.to_csv(fits_path, index=False)
    print(f"\n[Phase 12.1] Fits -> {fits_path}")

    # Q-Q plots
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        for product, fits in fits_per_product.items():
            tier = elbow[elbow["product"] == product].iloc[0]["tier"]
            tp = int(elbow[elbow["product"] == product].iloc[0]["best_tp_global"])
            sl = int(elbow[elbow["product"] == product].iloc[0]["best_sl_global"])
            try:
                pnl = load_pnl(tp, sl, tier)
            except FileNotFoundError:
                continue
            data = np.asarray(pnl.values, dtype=float)

            fig, axes = plt.subplots(1, 3, figsize=(15, 5))
            for ax, (name, dist_obj, args_lambda) in zip(
                axes,
                [
                    ("normal", stats.norm, lambda p: (p["loc"], p["scale"])),
                    ("t", stats.t, lambda p: (p["df"], p["loc"], p["scale"])),
                    ("skewnorm", stats.skewnorm, lambda p: (p["a"], p["loc"], p["scale"])),
                ],
            ):
                if name not in fits:
                    ax.text(0.5, 0.5, f"{name} fit failed", ha="center")
                    continue
                p = fits[name]["params"]
                args_t = args_lambda(p)
                if name == "normal":
                    dist_frozen = dist_obj(loc=args_t[0], scale=args_t[1])
                else:
                    dist_frozen = dist_obj(*args_t[:-2], loc=args_t[-2], scale=args_t[-1])
                stats.probplot(data, dist=dist_frozen, plot=ax)
                ax.set_title(f"{product}\n{name} (KS_p={fits[name]['ks_p']:.3f}, AIC={fits[name]['aic']:.0f})")
                ax.grid(True, alpha=0.3)
            plt.tight_layout()
            plt.savefig(out / f"qq_plot_{product}.png", dpi=80)
            plt.close()
        print(f"[Phase 12.1] Q-Q plots -> {out}/qq_plot_*.png")
    except ImportError:
        print("[Phase 12.1] matplotlib not installed; skipping Q-Q plots.")


if __name__ == "__main__":
    main()
