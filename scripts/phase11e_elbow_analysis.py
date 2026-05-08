"""Phase 11E.3: Elbow analysis across full grid (11B + 11D + 11E).

Concatenates all available MC summaries, dedups, and produces:
  - elbow_curves_{product}.png (multi-line E[net] vs SL, one line per TP)
  - heatmap_full_grid_{product}.png (full TP × SL heatmap E[net] + pass rate)
  - elbow_summary.csv (best (TP, SL) per product on multiple criteria)

Elbow detection: Kneedle algorithm-style — point of max curvature in E[net] vs SL curve.
Plateau detection: where Δ E[net] / Δ SL drops below threshold ($50/Δ100pt).
Safety zone: SL ≤ tier_capital × 0.10 (max 10% capital loss per single trade).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# Tier capital for safety zone calculation
TIER_CAPITAL = {
    "25K": 25_000,
    "50K": 50_000,
    "100K": 100_000,
    "150K": 150_000,
}

# Per-tier instrument and point value (for SL_dollars calculation)
TIER_INSTRUMENT = {
    "25K": ("MNQ", 2.0),
    "50K": ("NQ", 20.0),
    "100K": ("NQ", 20.0),
    "150K": ("NQ", 20.0),
}


def load_combined_summary(*paths: Path) -> pd.DataFrame:
    """Concatenate MC summaries, dedup on (product, TP, SL)."""
    dfs = []
    for p in paths:
        if p.exists():
            dfs.append(pd.read_csv(p))
    if not dfs:
        raise RuntimeError("No summaries found.")
    df = pd.concat(dfs, ignore_index=True)
    df["dollar_per_day_promo"] = df["expected_net_per_cycle_promo"] / df["expected_cycle_days"]
    return df.drop_duplicates(subset=["product", "tp_points", "sl_points"], keep="last")


def kneedle_elbow(x: np.ndarray, y: np.ndarray) -> int:
    """Simple Kneedle algorithm: point of max distance from line connecting endpoints.

    Returns INDEX of elbow point in sorted (x, y) arrays.
    """
    if len(x) < 3:
        return int(np.argmax(y))
    # Sort by x
    order = np.argsort(x)
    xs = x[order]
    ys = y[order]
    # Normalize to [0, 1]
    xn = (xs - xs.min()) / (xs.max() - xs.min() + 1e-9)
    yn = (ys - ys.min()) / (ys.max() - ys.min() + 1e-9)
    # Distance from line through (xn[0], yn[0]) and (xn[-1], yn[-1])
    dx = xn[-1] - xn[0]
    dy = yn[-1] - yn[0]
    norm = np.sqrt(dx**2 + dy**2)
    distances = np.abs(dy * xn - dx * yn + (dx * yn[0] - dy * xn[0])) / (norm + 1e-9)
    elbow_idx = int(np.argmax(distances))
    return order[elbow_idx]


def detect_plateau(sls: np.ndarray, e_nets: np.ndarray, threshold: float = 50.0) -> int | None:
    """Find first SL where E[net] curve plateaus (gradient < threshold per +100pt SL)."""
    order = np.argsort(sls)
    xs = sls[order]
    ys = e_nets[order]
    # Compute gradient: ΔE[net] / ΔSL × 100pt
    grad = np.diff(ys) / np.diff(xs) * 100.0
    # Find first 3 consecutive points where |gradient| < threshold
    for i in range(len(grad) - 2):
        if all(abs(grad[i + k]) < threshold for k in range(3)):
            return int(xs[order[i]])
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase11b", default="reports/phase11b_grid_tp_sl/grid_summary.csv")
    parser.add_argument("--phase11d", default="reports/phase11d_extended_sl/extended_mc_summary.csv")
    parser.add_argument("--phase11e", default="reports/phase11e_sl_elbow/full_grid_mc_summary.csv")
    parser.add_argument("--out-dir", default="reports/phase11e_sl_elbow")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    df = load_combined_summary(Path(args.phase11b), Path(args.phase11d), Path(args.phase11e))
    print(f"[Phase 11E.3] Loaded {len(df)} (product, TP, SL) cells from combined summaries")

    # Per-product analysis
    rows = []
    for product, sub in df.groupby("product"):
        tier = sub["tier"].iloc[0]
        capital = TIER_CAPITAL[tier]
        inst, point_value = TIER_INSTRUMENT[tier]
        safety_sl_max = capital * 0.10 / point_value  # Max SL in points such that SL$ ≤ 10% capital

        # Best (TP, SL) global
        best_global = sub.sort_values("expected_net_per_cycle_promo", ascending=False).iloc[0]
        best_tp_global = int(best_global["tp_points"])
        best_sl_global = int(best_global["sl_points"])
        best_e_global = float(best_global["expected_net_per_cycle_promo"])

        # Best within safety zone
        sub_safe = sub[sub["sl_points"] <= safety_sl_max]
        if len(sub_safe):
            best_safe = sub_safe.sort_values("expected_net_per_cycle_promo", ascending=False).iloc[0]
            safe_tp = int(best_safe["tp_points"])
            safe_sl = int(best_safe["sl_points"])
            safe_e = float(best_safe["expected_net_per_cycle_promo"])
        else:
            safe_tp = safe_sl = -1
            safe_e = float("nan")

        # Elbow detection: per TP curve, find elbow then take best
        elbow_records = []
        for tp_val, tp_sub in sub.groupby("tp_points"):
            sls = tp_sub["sl_points"].values
            e_nets = tp_sub["expected_net_per_cycle_promo"].values
            if len(sls) < 3:
                continue
            elbow_orig_idx = kneedle_elbow(sls, e_nets)
            elbow_records.append({
                "tp": int(tp_val),
                "sl": int(sls[elbow_orig_idx - sls.argsort()[0] if False else elbow_orig_idx]),
                "e_net": float(e_nets[elbow_orig_idx]),
            })
        # Pick best elbow across TPs
        if elbow_records:
            elbow_best = max(elbow_records, key=lambda r: r["e_net"])
            elbow_tp = elbow_best["tp"]
            elbow_sl = elbow_best["sl"]
            elbow_e = elbow_best["e_net"]
        else:
            elbow_tp = elbow_sl = -1
            elbow_e = float("nan")

        # Plateau detection at the best TP
        sub_best_tp = sub[sub["tp_points"] == best_tp_global]
        plateau_sl = detect_plateau(
            sub_best_tp["sl_points"].values,
            sub_best_tp["expected_net_per_cycle_promo"].values,
        )

        rows.append({
            "product": product,
            "tier": tier,
            "tier_capital": capital,
            "safety_sl_max_points": int(safety_sl_max),
            "best_tp_global": best_tp_global,
            "best_sl_global": best_sl_global,
            "best_E_net_global": round(best_e_global, 0),
            "elbow_tp": elbow_tp,
            "elbow_sl": elbow_sl,
            "elbow_E_net": round(elbow_e, 0),
            "safe_tp": safe_tp,
            "safe_sl": safe_sl,
            "safe_E_net": round(safe_e, 0) if pd.notna(safe_e) else "n/a",
            "plateau_start_sl_at_best_tp": plateau_sl if plateau_sl is not None else "no_plateau",
            "delta_global_vs_safe_pct": round(
                100 * (best_e_global / safe_e - 1), 1
            ) if pd.notna(safe_e) else "n/a",
        })

    elbow_summary = pd.DataFrame(rows).sort_values("best_E_net_global", ascending=False)
    elbow_path = out / "elbow_summary.csv"
    elbow_summary.to_csv(elbow_path, index=False)
    print(f"[Phase 11E.3] Elbow summary -> {elbow_path}\n")
    print(elbow_summary.to_string(index=False))

    # Plotting
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        for product, sub in df.groupby("product"):
            # Multi-line elbow curve plot (E[net] vs SL, one line per TP)
            fig, axes = plt.subplots(1, 2, figsize=(15, 6))
            tps = sorted(sub["tp_points"].unique())
            colors = plt.cm.viridis(np.linspace(0.1, 0.9, len(tps)))
            for tp, color in zip(tps, colors):
                tp_sub = sub[sub["tp_points"] == tp].sort_values("sl_points")
                axes[0].plot(tp_sub["sl_points"], tp_sub["expected_net_per_cycle_promo"],
                             marker="o", label=f"TP={int(tp)}", color=color)
                axes[1].plot(tp_sub["sl_points"], tp_sub["pass_rate"],
                             marker="o", label=f"TP={int(tp)}", color=color)
            axes[0].set_xscale("log")
            axes[0].set_xlabel("SL (points, log scale)")
            axes[0].set_ylabel("E[net]/cycle promo ($)")
            axes[0].set_title(f"{product}: E[net]/cycle vs SL (per TP)")
            axes[0].legend(loc="best")
            axes[0].grid(True, alpha=0.3)

            axes[1].set_xscale("log")
            axes[1].set_xlabel("SL (points, log scale)")
            axes[1].set_ylabel("Pass rate")
            axes[1].set_title(f"{product}: Pass rate vs SL (per TP)")
            axes[1].legend(loc="best")
            axes[1].grid(True, alpha=0.3)

            tier = sub["tier"].iloc[0]
            inst, pv = TIER_INSTRUMENT[tier]
            safety_sl = TIER_CAPITAL[tier] * 0.10 / pv
            for ax in axes:
                ax.axvline(x=safety_sl, color="red", linestyle="--", alpha=0.5,
                           label=f"Safety cap (SL ≤ 10% capital = {int(safety_sl)}pt)")

            plt.tight_layout()
            plt.savefig(out / f"elbow_curves_{product}.png", dpi=80)
            plt.close()

            # Heatmap full grid E[net]
            pivot_net = sub.pivot_table(index="sl_points", columns="tp_points",
                                          values="expected_net_per_cycle_promo")
            pivot_pass = sub.pivot_table(index="sl_points", columns="tp_points",
                                            values="pass_rate")
            fig, axes = plt.subplots(1, 2, figsize=(14, 8))
            for ax, pivot, title in [(axes[0], pivot_net, "E[net]/cycle promo"),
                                      (axes[1], pivot_pass, "Pass rate")]:
                im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn", origin="lower")
                ax.set_xticks(range(len(pivot.columns)))
                ax.set_xticklabels([f"{c:.0f}" for c in pivot.columns])
                ax.set_yticks(range(len(pivot.index)))
                ax.set_yticklabels([f"{r:.0f}" for r in pivot.index])
                ax.set_xlabel("TP (points)")
                ax.set_ylabel("SL (points)")
                ax.set_title(f"{product}: {title}")
                for i in range(pivot.shape[0]):
                    for j in range(pivot.shape[1]):
                        v = pivot.values[i, j]
                        if pd.isna(v):
                            ax.text(j, i, "—", ha="center", va="center", color="gray", fontsize=6)
                        else:
                            ax.text(j, i, f"{v:.2f}" if v < 10 else f"{v:.0f}",
                                    ha="center", va="center", color="black", fontsize=6)
                plt.colorbar(im, ax=ax)
            plt.tight_layout()
            plt.savefig(out / f"heatmap_full_grid_{product}.png", dpi=80)
            plt.close()
        print(f"\n[Phase 11E.3] Plots -> {out}/elbow_curves_*.png + heatmap_full_grid_*.png")
    except ImportError:
        print("[Phase 11E.3] matplotlib not installed; skipping plots.")


if __name__ == "__main__":
    main()
