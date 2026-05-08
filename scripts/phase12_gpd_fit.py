"""Phase 12.1 v2: GPD threshold selection + fit on left tail of daily PnL.

For each of 8 Apex products at Phase 11E aggressive setup:
  - Load daily PnL series.
  - Build losses = -daily_pnl[daily_pnl < 0]  (positive losses).
  - MRL plot: E[X - u | X > u] vs u, scan u in pctiles [70, 95] of losses.
  - Hill plot: alpha = 1/xi vs k (number of order stats in tail).
  - Pick threshold (default 90th pctile of losses).
  - Fit GPD via scipy.stats.genpareto.fit(excess, floc=0) -> xi, beta.
  - Bootstrap CI 95% on (xi, beta) with 1000 resamples.
  - GoF: KS test on tail vs fitted GPD, Q-Q plot, exceedance prob plot.

Output:
  reports/phase12_gpd_fat_tails/diagnostics_{product}.png  x 8
  reports/phase12_gpd_fat_tails/gpd_fits.csv               (8 rows)
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
    factor = TIER_TO_PARQUET[tier][1]
    return pd.read_parquet(path)["daily_pnl"] * factor


def mrl(losses: np.ndarray, thresholds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mean Residual Life: E[L - u | L > u] for each u."""
    means = []
    counts = []
    for u in thresholds:
        excess = losses[losses > u] - u
        if len(excess) >= 2:
            means.append(excess.mean())
            counts.append(len(excess))
        else:
            means.append(np.nan)
            counts.append(len(excess))
    return np.array(means), np.array(counts)


def hill_estimator(losses_sorted_desc: np.ndarray, k_range: np.ndarray) -> np.ndarray:
    """Hill estimator of tail index xi = 1/alpha for k order stats.

    losses_sorted_desc: losses sorted descending.
    For each k: xi_hat = (1/k) * sum_{i=1..k} log(L_(i) / L_(k+1)).
    """
    out = np.full(len(k_range), np.nan)
    n = len(losses_sorted_desc)
    for j, k in enumerate(k_range):
        if k < 2 or k + 1 >= n:
            continue
        top_k = losses_sorted_desc[:k]
        ref = losses_sorted_desc[k]
        if ref <= 0:
            continue
        out[j] = np.mean(np.log(top_k / ref))
    return out


def fit_gpd_with_ci(excess: np.ndarray, n_boot: int = 1000, seed: int = 42) -> dict:
    """Fit GPD to excess + bootstrap CI 95% on (xi, scale)."""
    xi, loc, scale = stats.genpareto.fit(excess, floc=0)
    # KS GoF
    ks_stat, ks_p = stats.kstest(excess, "genpareto", args=(xi, 0, scale))
    # Bootstrap
    rng = np.random.default_rng(seed)
    xis, scales = [], []
    for _ in range(n_boot):
        sample = rng.choice(excess, size=len(excess), replace=True)
        try:
            xi_b, _, scale_b = stats.genpareto.fit(sample, floc=0)
            xis.append(xi_b)
            scales.append(scale_b)
        except Exception:
            continue
    xi_lo, xi_hi = np.percentile(xis, [2.5, 97.5]) if xis else (np.nan, np.nan)
    sc_lo, sc_hi = np.percentile(scales, [2.5, 97.5]) if scales else (np.nan, np.nan)
    return {
        "xi": xi, "scale": scale, "ks_stat": ks_stat, "ks_p": ks_p,
        "xi_ci": (xi_lo, xi_hi), "scale_ci": (sc_lo, sc_hi),
        "n_boot_ok": len(xis),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--elbow-summary", default="reports/phase11e_sl_elbow/elbow_summary.csv")
    parser.add_argument("--out-dir", default="reports/phase12_gpd_fat_tails")
    parser.add_argument("--threshold-pctile", type=float, default=90.0,
                        help="Default percentile of LOSSES to use as threshold (90 = top 10%)")
    parser.add_argument("--n-boot", type=int, default=1000)
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    elbow = pd.read_csv(args.elbow_summary)

    rows = []
    diagnostics_data = {}  # for plotting later

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
        # Losses = -PnL for negative days only (positive numbers)
        losses = -data[data < 0]
        n_days = len(data)
        n_loss_days = len(losses)

        print(f"\n[Phase 12.1] {product} (TP={tp}, SL={sl})")
        print(f"  n_days={n_days}  n_loss_days={n_loss_days}  "
              f"worst_loss=${losses.max():,.0f}  median_loss=${np.median(losses):,.0f}")

        # Threshold scan: 70-95 pctile of losses
        pct_grid = np.arange(70, 96, 2.5)
        thresh_grid = np.percentile(losses, pct_grid)
        mrl_means, mrl_counts = mrl(losses, thresh_grid)

        # Hill estimator
        losses_sorted = np.sort(losses)[::-1]  # descending
        k_grid = np.arange(5, max(6, min(len(losses)//2, 100)))
        hill = hill_estimator(losses_sorted, k_grid)

        # Pick threshold = chosen pctile
        threshold = float(np.percentile(losses, args.threshold_pctile))
        excess = losses[losses > threshold] - threshold
        n_tail = len(excess)
        print(f"  threshold (pctile={args.threshold_pctile})=${threshold:,.0f}  n_tail={n_tail}")

        if n_tail < 10:
            print(f"  WARN: tail too small (n={n_tail}); skipping fit")
            rows.append({
                "product": product, "tier": tier, "tp": tp, "sl": sl,
                "threshold_pctile": args.threshold_pctile, "threshold": round(threshold, 0),
                "n_tail": n_tail,
                "xi": None, "scale": None,
                "xi_ci_low": None, "xi_ci_high": None,
                "scale_ci_low": None, "scale_ci_high": None,
                "ks_stat": None, "ks_p": None,
                "status": "skipped_small_tail",
            })
            continue

        fit = fit_gpd_with_ci(excess, n_boot=args.n_boot)
        xi = fit["xi"]
        scale = fit["scale"]
        print(f"  GPD fit: xi={xi:+.3f} (CI95 [{fit['xi_ci'][0]:+.3f}, {fit['xi_ci'][1]:+.3f}])  "
              f"scale=${scale:,.0f} (CI95 [${fit['scale_ci'][0]:,.0f}, ${fit['scale_ci'][1]:,.0f}])")
        print(f"  KS_stat={fit['ks_stat']:.3f}  KS_p={fit['ks_p']:.4f}  n_boot_ok={fit['n_boot_ok']}")

        if xi > 0:
            tail_class = "fat_tail (xi>0)"
        elif xi > -0.05:
            tail_class = "exponential (xi~=0)"
        else:
            tail_class = "bounded (xi<0)"
        print(f"  -> tail class: {tail_class}")

        rows.append({
            "product": product, "tier": tier, "tp": tp, "sl": sl,
            "threshold_pctile": args.threshold_pctile,
            "threshold": round(threshold, 0),
            "n_tail": n_tail,
            "xi": round(xi, 4),
            "scale": round(scale, 1),
            "xi_ci_low": round(fit["xi_ci"][0], 4),
            "xi_ci_high": round(fit["xi_ci"][1], 4),
            "scale_ci_low": round(fit["scale_ci"][0], 1),
            "scale_ci_high": round(fit["scale_ci"][1], 1),
            "ks_stat": round(fit["ks_stat"], 4),
            "ks_p": round(fit["ks_p"], 4),
            "tail_class": tail_class,
            "status": "ok",
        })

        diagnostics_data[product] = {
            "losses": losses,
            "threshold": threshold,
            "excess": excess,
            "xi": xi, "scale": scale,
            "pct_grid": pct_grid, "thresh_grid": thresh_grid,
            "mrl_means": mrl_means, "mrl_counts": mrl_counts,
            "k_grid": k_grid, "hill": hill,
            "tier": tier, "tp": tp, "sl": sl,
        }

    fits_df = pd.DataFrame(rows)
    fits_path = out / "gpd_fits.csv"
    fits_df.to_csv(fits_path, index=False)
    print(f"\n[Phase 12.1] Fits -> {fits_path}")

    # Plotting: 4-panel diagnostics per product
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        for product, d in diagnostics_data.items():
            fig, axes = plt.subplots(2, 2, figsize=(14, 10))

            # Panel 1: MRL plot
            ax = axes[0, 0]
            ax.plot(d["thresh_grid"], d["mrl_means"], marker="o", color="tab:blue")
            ax.axvline(d["threshold"], color="red", linestyle="--", label=f"chosen u=${d['threshold']:,.0f}")
            for i, (u, n) in enumerate(zip(d["thresh_grid"], d["mrl_counts"])):
                ax.annotate(f"n={n}", (u, d["mrl_means"][i]), fontsize=7,
                            textcoords="offset points", xytext=(3, 3))
            ax.set_xlabel("Threshold u ($)")
            ax.set_ylabel("E[L - u | L > u] ($)")
            ax.set_title(f"{product}: MRL plot (linear region = GPD valid)")
            ax.legend()
            ax.grid(True, alpha=0.3)

            # Panel 2: Hill plot
            ax = axes[0, 1]
            ax.plot(d["k_grid"], d["hill"], marker=".", color="tab:green")
            ax.axhline(d["xi"], color="red", linestyle="--", label=f"GPD xi={d['xi']:+.3f}")
            ax.set_xlabel("k (order stats from top)")
            ax.set_ylabel("Hill xi estimate")
            ax.set_title(f"{product}: Hill plot (plateau = stable tail index)")
            ax.legend()
            ax.grid(True, alpha=0.3)

            # Panel 3: Q-Q plot empirical excess vs GPD
            ax = axes[1, 0]
            xi, scale = d["xi"], d["scale"]
            n = len(d["excess"])
            theoretical_q = stats.genpareto.ppf((np.arange(1, n+1) - 0.5) / n, xi, loc=0, scale=scale)
            empirical_q = np.sort(d["excess"])
            ax.scatter(theoretical_q, empirical_q, s=20, alpha=0.7)
            lim_max = max(theoretical_q.max(), empirical_q.max())
            ax.plot([0, lim_max], [0, lim_max], color="red", linestyle="--", label="y=x")
            ax.set_xlabel("Theoretical GPD quantile ($)")
            ax.set_ylabel("Empirical excess ($)")
            ax.set_title(f"{product}: Q-Q plot tail vs GPD (xi={xi:+.3f}, scale=${scale:,.0f})")
            ax.legend()
            ax.grid(True, alpha=0.3)

            # Panel 4: exceedance probability (log-log)
            ax = axes[1, 1]
            sorted_excess = np.sort(d["excess"])
            n = len(sorted_excess)
            emp_surv = 1 - (np.arange(1, n+1) - 0.5) / n
            theo_surv = 1 - stats.genpareto.cdf(sorted_excess, xi, loc=0, scale=scale)
            ax.loglog(sorted_excess, emp_surv, "o", markersize=4, label="empirical", alpha=0.7)
            ax.loglog(sorted_excess, theo_surv, "-", color="red", label=f"GPD fit", linewidth=2)
            ax.set_xlabel("Excess over threshold ($, log)")
            ax.set_ylabel("P(L - u > x | L > u)  [log]")
            ax.set_title(f"{product}: Exceedance probability (tail decay)")
            ax.legend()
            ax.grid(True, alpha=0.3, which="both")

            fig.suptitle(
                f"{product} | Phase 11E aggressive (TP={d['tp']}, SL={d['sl']}, tier={d['tier']}) "
                f"| threshold ${d['threshold']:,.0f} (pct={args.threshold_pctile}) "
                f"| n_tail={len(d['excess'])}",
                fontsize=11
            )
            plt.tight_layout()
            plt.savefig(out / f"diagnostics_{product}.png", dpi=80)
            plt.close()
        print(f"[Phase 12.1] Diagnostics plots -> {out}/diagnostics_*.png")
    except ImportError:
        print("[Phase 12.1] matplotlib not installed; skipping plots.")


if __name__ == "__main__":
    main()
