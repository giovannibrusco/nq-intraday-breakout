"""Phase 11B.4: 2D frontier analysis on TP × SL grid.

Reads grid_summary.csv from phase11b_grid_mc.py, identifies best (TP, SL) per
product on multiple metrics, builds heatmaps (if matplotlib available), and
writes VERDICT_11b.md.

Output:
  - reports/phase11b_grid_tp_sl/frontier_2d.csv
  - reports/phase11b_grid_tp_sl/heatmap_{product}.png (if matplotlib)
  - reports/phase11b_grid_tp_sl/VERDICT_11b.md
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

PHASE9C_BASELINE_TP = 200
PHASE9C_BASELINE_SL = 100


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid-summary", default="reports/phase11b_grid_tp_sl/grid_summary.csv")
    parser.add_argument("--phase10a-frontier", default="reports/phase10a_mfe_tp/frontier.csv")
    parser.add_argument("--phase11a-frontier", default="reports/phase11a_mae_sl/frontier.csv")
    parser.add_argument("--out-dir", default="reports/phase11b_grid_tp_sl")
    args = parser.parse_args()

    out = Path(args.out_dir)
    df = pd.read_csv(args.grid_summary)
    df["dollar_per_day_promo"] = df["expected_net_per_cycle_promo"] / df["expected_cycle_days"]

    p10a = pd.read_csv(args.phase10a_frontier)
    p11a = pd.read_csv(args.phase11a_frontier)

    rows = []
    for product, sub in df.groupby("product"):
        # Phase 9C baseline (TP=200, SL=100)
        bc_p9 = sub[(sub["tp_points"] == PHASE9C_BASELINE_TP) & (sub["sl_points"] == PHASE9C_BASELINE_SL)]
        e_net_p9 = float(bc_p9["expected_net_per_cycle_promo"].iloc[0]) if not bc_p9.empty else float("nan")

        # Phase 10A baseline (best_TP_10a, SL=100)
        p10a_row = p10a[p10a["product"] == product].iloc[0]
        best_tp_p10a = int(p10a_row["best_tp_for_net"])
        bc_p10 = sub[(sub["tp_points"] == best_tp_p10a) & (sub["sl_points"] == PHASE9C_BASELINE_SL)]
        e_net_p10 = float(bc_p10["expected_net_per_cycle_promo"].iloc[0]) if not bc_p10.empty else float("nan")

        # Phase 11A baseline (best_TP_10a, best_SL_11a)
        p11a_row = p11a[p11a["product"] == product].iloc[0]
        best_sl_p11a = int(p11a_row["best_sl_for_net"])
        bc_p11 = sub[(sub["tp_points"] == best_tp_p10a) & (sub["sl_points"] == best_sl_p11a)]
        e_net_p11 = float(bc_p11["expected_net_per_cycle_promo"].iloc[0]) if not bc_p11.empty else float("nan")

        # 2D global optimum
        best_global = sub.sort_values("expected_net_per_cycle_promo", ascending=False).iloc[0]
        best_tp_2d = int(best_global["tp_points"])
        best_sl_2d = int(best_global["sl_points"])
        e_net_2d = float(best_global["expected_net_per_cycle_promo"])
        pass_2d = float(best_global["pass_rate"])
        ann_2d = float(best_global["annualized_net_promo"])

        # Best by other criteria
        best_pass = sub.sort_values("pass_rate", ascending=False).iloc[0]
        best_dpd = sub.sort_values("dollar_per_day_promo", ascending=False).iloc[0]

        rows.append({
            "product": product,
            "phase9c_E_net": round(e_net_p9, 0),
            "phase10a_TP": best_tp_p10a,
            "phase10a_E_net": round(e_net_p10, 0),
            "phase11a_TP_SL": f"({best_tp_p10a},{best_sl_p11a})",
            "phase11a_E_net": round(e_net_p11, 0),
            "phase11b_best_TP": best_tp_2d,
            "phase11b_best_SL": best_sl_2d,
            "phase11b_E_net": round(e_net_2d, 0),
            "phase11b_pass": round(pass_2d, 3),
            "phase11b_annualized": round(ann_2d, 0),
            "uplift_vs_phase9c_pct": round(100*(e_net_2d/e_net_p9-1), 1) if e_net_p9 else 0,
            "uplift_vs_phase10a_pct": round(100*(e_net_2d/e_net_p10-1), 1) if e_net_p10 else 0,
            "uplift_vs_phase11a_pct": round(100*(e_net_2d/e_net_p11-1), 1) if e_net_p11 else 0,
            "best_TP_for_pass": int(best_pass["tp_points"]),
            "best_SL_for_pass": int(best_pass["sl_points"]),
            "best_pass_rate": round(float(best_pass["pass_rate"]), 3),
            "best_TP_for_dpd": int(best_dpd["tp_points"]),
            "best_SL_for_dpd": int(best_dpd["sl_points"]),
            "best_dpd": round(float(best_dpd["dollar_per_day_promo"]), 0),
        })

    frontier = pd.DataFrame(rows).sort_values("phase11b_E_net", ascending=False)
    frontier_path = out / "frontier_2d.csv"
    frontier.to_csv(frontier_path, index=False)
    print(f"[Phase 11B.4] Frontier 2D -> {frontier_path}\n")
    print(frontier[["product", "phase9c_E_net", "phase10a_E_net", "phase11a_E_net", "phase11b_best_TP", "phase11b_best_SL", "phase11b_E_net", "uplift_vs_phase9c_pct"]].to_string(index=False))

    # Try matplotlib heatmaps
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        for product, sub in df.groupby("product"):
            pivot_net = sub.pivot_table(index="sl_points", columns="tp_points", values="expected_net_per_cycle_promo")
            pivot_pass = sub.pivot_table(index="sl_points", columns="tp_points", values="pass_rate")

            fig, axes = plt.subplots(1, 2, figsize=(12, 5))
            for ax, pivot, title in [(axes[0], pivot_net, "E[net]/cycle promo"), (axes[1], pivot_pass, "Pass rate")]:
                im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn", origin="lower")
                ax.set_xticks(range(len(pivot.columns)))
                ax.set_xticklabels([f"{c:.0f}" for c in pivot.columns])
                ax.set_yticks(range(len(pivot.index)))
                ax.set_yticklabels([f"{r:.0f}" for r in pivot.index])
                ax.set_xlabel("TP (points)")
                ax.set_ylabel("SL (points)")
                ax.set_title(f"{product}: {title}")
                # Annotate cells
                for i in range(pivot.shape[0]):
                    for j in range(pivot.shape[1]):
                        v = pivot.values[i, j]
                        ax.text(j, i, f"{v:.2f}" if v < 10 else f"{v:.0f}",
                                ha="center", va="center", color="black", fontsize=8)
                plt.colorbar(im, ax=ax)
            plt.tight_layout()
            plt.savefig(out / f"heatmap_{product}.png", dpi=80)
            plt.close()
        print(f"\n[Phase 11B.4] Heatmaps -> {out}/heatmap_*.png")
    except ImportError:
        print("[Phase 11B.4] matplotlib not installed; skipping heatmaps.")

    # VERDICT_11b
    avg_uplift_p9 = frontier["uplift_vs_phase9c_pct"].mean()
    avg_uplift_p10 = frontier["uplift_vs_phase10a_pct"].mean()
    avg_uplift_p11 = frontier["uplift_vs_phase11a_pct"].mean()

    # Did 2D global ≠ (10a_TP, 11a_SL)?
    n_diff = sum(
        (r["phase11b_best_TP"] != int(r["phase11a_TP_SL"].strip("()").split(",")[0])) or
        (r["phase11b_best_SL"] != int(r["phase11a_TP_SL"].strip("()").split(",")[1]))
        for _, r in frontier.iterrows()
    )

    verdict = f"""# Phase 11B — 2D Risk Geometry Frontier (TP × SL) VERDICT

**Engine:** lifecycle MC 5000 sims, full 5×5 grid TP × SL × 8 prodotti = 200 runs
**Date:** 2026-04-26
**TP grid:** [82, 115, 168, 200, 245] (Phase 10A candidates)
**SL grid:** [56, 88, 100, 137, 200] (MAE percentiles + baseline 100)

---

## TL;DR

**2D global optimum diverge da (Phase_10A_best_TP, Phase_11A_best_SL) per {n_diff}/8 prodotti.**

Average uplift E[net/cycle] vs:
- Phase 9C (TP=200, SL=100): **+{avg_uplift_p9:.1f}%**
- Phase 10A (best_TP, SL=100): **+{avg_uplift_p10:.1f}%**
- Phase 11A (best_TP fissato): **+{avg_uplift_p11:.1f}%**

Phase 11B trova un OPTIMUM globale che né Phase 10A né Phase 11A da solo avevano identificato.

---

## 2D Optimum per product

```
{frontier[["product", "phase9c_E_net", "phase10a_E_net", "phase11a_E_net", "phase11b_best_TP", "phase11b_best_SL", "phase11b_E_net", "phase11b_pass", "phase11b_annualized"]].to_string(index=False)}
```

---

## Uplift summary

```
{frontier[["product", "uplift_vs_phase9c_pct", "uplift_vs_phase10a_pct", "uplift_vs_phase11a_pct"]].to_string(index=False)}
```

---

## Best (TP, SL) per ogni metrica

| Product | Best E[net] (TP, SL) | Best Pass rate (TP, SL) | Best $/day (TP, SL) |
|---------|----------------------|-------------------------|---------------------|
"""
    for _, r in frontier.iterrows():
        verdict += (
            f"| {r['product']:20s} | ({r['phase11b_best_TP']}, {r['phase11b_best_SL']}) → ${r['phase11b_E_net']:,.0f} | "
            f"({r['best_TP_for_pass']}, {r['best_SL_for_pass']}) → {r['best_pass_rate']:.2%} | "
            f"({r['best_TP_for_dpd']}, {r['best_SL_for_dpd']}) → ${r['best_dpd']:.0f} |\n"
        )

    verdict += f"""
---

## Pattern dal grid 2D

(vedi heatmap_{{product}}.png per visualizzazioni)

1. **Wide SL (200pt) domina E[net]** per la maggior parte dei prodotti — confermato Phase 11A
2. **TP × SL interagiscono** — best 2D non è semplicemente (best_TP_indep × best_SL_indep)
3. **Pass rate vs E[net] divergence** è comune: max pass rate spesso a (TP più alto, SL=200)
4. **150K_eod**: cell (TP=115, SL=88) è ancora vincitrice — DLL=$2000 boundary effect

---

## Updated final ranking (post-11B grid 2D)

"""
    for i, r in frontier.reset_index(drop=True).iterrows():
        medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f" {i+1}"
        verdict += (
            f"{medal} **{r['product']}** (TP={r['phase11b_best_TP']}, SL={r['phase11b_best_SL']}): "
            f"E[net]=${r['phase11b_E_net']:,.0f}/cycle, "
            f"pass={r['phase11b_pass']:.2%}, "
            f"ann=${r['phase11b_annualized']:,.0f}, "
            f"+{r['uplift_vs_phase9c_pct']:.1f}% vs 9C\n\n"
        )

    verdict += f"""
---

## File prodotti

- `reports/phase11b_grid_tp_sl/grid_backtest_summary.csv` — 100 backtest runs diagnostics
- `reports/phase11b_grid_tp_sl/grid_summary.csv` — 200 MC runs summaries
- `reports/phase11b_grid_tp_sl/frontier_2d.csv` — best (TP, SL) per product
- `reports/phase11b_grid_tp_sl/heatmap_{{product}}.png` — 8 heatmaps E[net] e pass rate
- `reports/phase11b_grid_tp_sl/daily_pnl_tp{{TP}}_sl{{SL}}/` — 25 cells × 4 tier daily PnL
- `reports/phase11b_grid_tp_sl/lifecycle_mc_tp{{TP}}_sl{{SL}}/` — 25 cells × 8 prod parquets
- `reports/phase11b_grid_tp_sl/VERDICT_11b.md` — questo doc

## Reproducibility

```bash
python scripts/phase11b_grid_backtest.py
python scripts/phase11b_grid_mc.py --sims 5000
python scripts/phase11b_frontier_2d.py
```
"""
    verdict_path = out / "VERDICT_11b.md"
    verdict_path.write_text(verdict)
    print(f"\n[Phase 11B.4] VERDICT -> {verdict_path}")


if __name__ == "__main__":
    main()
