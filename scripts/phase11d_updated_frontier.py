"""Phase 11D.3: Updated 2D frontier (Phase 11B + 11D combined).

Concatenates Phase 11B grid_summary.csv + Phase 11D extended_mc_summary.csv,
re-runs frontier analysis on extended grid (8 SL × 5 TP cells max).

Output:
  - reports/phase11d_extended_sl/frontier_2d_extended.csv
  - reports/phase11d_extended_sl/heatmap_extended_{product}.png
  - reports/phase11d_extended_sl/VERDICT_11d.md
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

PHASE9C_BASELINE_TP = 200
PHASE9C_BASELINE_SL = 100


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase11b-summary", default="reports/phase11b_grid_tp_sl/grid_summary.csv")
    parser.add_argument("--phase11d-summary", default="reports/phase11d_extended_sl/extended_mc_summary.csv")
    parser.add_argument("--phase11b-frontier", default="reports/phase11b_grid_tp_sl/frontier_2d.csv")
    parser.add_argument("--out-dir", default="reports/phase11d_extended_sl")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Concat both summaries
    p11b = pd.read_csv(args.phase11b_summary)
    p11d = pd.read_csv(args.phase11d_summary)
    df = pd.concat([p11b, p11d], ignore_index=True)
    df["dollar_per_day_promo"] = df["expected_net_per_cycle_promo"] / df["expected_cycle_days"]
    df_dedup = df.drop_duplicates(subset=["product", "tp_points", "sl_points"], keep="last")

    # Phase 11B frontier (for diff)
    p11b_front = pd.read_csv(args.phase11b_frontier)
    p11b_best = {
        r["product"]: (int(r["phase11b_best_TP"]), int(r["phase11b_best_SL"]),
                       float(r["phase11b_E_net"]))
        for _, r in p11b_front.iterrows()
    }

    # Find new global optimum per product
    rows = []
    for product, sub in df_dedup.groupby("product"):
        best = sub.sort_values("expected_net_per_cycle_promo", ascending=False).iloc[0]
        best_tp_ext = int(best["tp_points"])
        best_sl_ext = int(best["sl_points"])
        e_net_ext = float(best["expected_net_per_cycle_promo"])
        pass_ext = float(best["pass_rate"])
        ann_ext = float(best["annualized_net_promo"])

        # Compare to 11B
        old_tp, old_sl, old_e = p11b_best[product]
        delta = e_net_ext - old_e
        delta_pct = (e_net_ext / old_e - 1) * 100 if old_e else 0
        changed = (best_tp_ext, best_sl_ext) != (old_tp, old_sl)

        # Boundary check
        max_sl = sub["sl_points"].max()
        at_boundary = (best_sl_ext == max_sl) and (max_sl >= 400)
        min_sl = sub["sl_points"].min()
        at_lower_boundary = (best_sl_ext == min_sl) and (min_sl <= 30)

        rows.append({
            "product": product,
            "phase11b_TP": old_tp,
            "phase11b_SL": old_sl,
            "phase11b_E_net": round(old_e, 0),
            "phase11d_TP": best_tp_ext,
            "phase11d_SL": best_sl_ext,
            "phase11d_E_net": round(e_net_ext, 0),
            "phase11d_pass": round(pass_ext, 3),
            "phase11d_annualized": round(ann_ext, 0),
            "delta_E_net": round(delta, 0),
            "delta_pct": round(delta_pct, 1),
            "changed": changed,
            "at_upper_boundary": at_boundary,
            "at_lower_boundary": at_lower_boundary,
        })

    frontier = pd.DataFrame(rows).sort_values("phase11d_E_net", ascending=False)
    frontier_path = out / "frontier_2d_extended.csv"
    frontier.to_csv(frontier_path, index=False)
    print(f"[Phase 11D.3] Updated frontier -> {frontier_path}\n")
    print(frontier.to_string(index=False))

    # Heatmaps with extended grid
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        for product, sub in df_dedup.groupby("product"):
            pivot_net = sub.pivot_table(index="sl_points", columns="tp_points", values="expected_net_per_cycle_promo")
            pivot_pass = sub.pivot_table(index="sl_points", columns="tp_points", values="pass_rate")

            fig, axes = plt.subplots(1, 2, figsize=(14, 6))
            for ax, pivot, title in [(axes[0], pivot_net, "E[net]/cycle promo"), (axes[1], pivot_pass, "Pass rate")]:
                im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn", origin="lower")
                ax.set_xticks(range(len(pivot.columns)))
                ax.set_xticklabels([f"{c:.0f}" for c in pivot.columns])
                ax.set_yticks(range(len(pivot.index)))
                ax.set_yticklabels([f"{r:.0f}" for r in pivot.index])
                ax.set_xlabel("TP (points)")
                ax.set_ylabel("SL (points)")
                ax.set_title(f"{product}: {title} (Phase 11B + 11D extended)")
                for i in range(pivot.shape[0]):
                    for j in range(pivot.shape[1]):
                        v = pivot.values[i, j]
                        if pd.isna(v):
                            ax.text(j, i, "—", ha="center", va="center", color="gray", fontsize=8)
                        else:
                            ax.text(j, i, f"{v:.2f}" if v < 10 else f"{v:.0f}",
                                    ha="center", va="center", color="black", fontsize=7)
                plt.colorbar(im, ax=ax)
            plt.tight_layout()
            plt.savefig(out / f"heatmap_extended_{product}.png", dpi=80)
            plt.close()
        print(f"\n[Phase 11D.3] Heatmaps -> {out}/heatmap_extended_*.png")
    except ImportError:
        print("[Phase 11D.3] matplotlib not installed; skipping heatmaps.")

    # VERDICT_11d
    n_changed = int(frontier["changed"].sum())
    n_upper_boundary = int(frontier["at_upper_boundary"].sum())
    n_lower_boundary = int(frontier["at_lower_boundary"].sum())
    avg_delta_pct = float(frontier["delta_pct"].mean())

    verdict = f"""# Phase 11D — Extended SL Sweep VERDICT (Boundary saturation check)

**Engine:** lifecycle MC 5000 sims, extended grid 8 SL × 5 TP cells
**Date:** 2026-04-26
**Extension:** wider SL {{250, 300, 400}} + tighter SL {{30, 40}} per 100K_eod
**Total cells (Phase 11B + 11D):** {len(df_dedup)} unique (product, TP, SL) combinations

---

## TL;DR

**Best (TP, SL) cambia per {n_changed}/8 prodotti vs Phase 11B.**
Average uplift E[net/cycle] vs Phase 11B: **+{avg_delta_pct:.1f}%**

Boundary saturation:
- {n_upper_boundary}/8 prodotti hanno best_SL al boundary superiore (SL=400) → potrebbe essere ancora più largo
- {n_lower_boundary}/8 prodotti hanno best_SL al boundary inferiore (SL=30) → potrebbe essere ancora più tight
- {8 - n_upper_boundary - n_lower_boundary}/8 prodotti hanno optimum interior → 11D conferma optimum stabile

---

## Frontier 11D (extended) vs Phase 11B

```
{frontier.to_string(index=False)}
```

---

## Boundary analysis

"""
    boundary_products = frontier[frontier["at_upper_boundary"] | frontier["at_lower_boundary"]]
    if len(boundary_products) > 0:
        verdict += "**Prodotti ancora al boundary (potrebbe servire estensione ulteriore):**\n\n"
        for _, r in boundary_products.iterrows():
            side = "SUPERIOR (SL=400)" if r["at_upper_boundary"] else "INFERIOR (SL=30)"
            verdict += f"- **{r['product']}**: best_SL={r['phase11d_SL']} ({side})\n"
        verdict += "\n**Raccomandazione:** Phase 11E con SL ∈ {500, 600, 800} (se boundary superiore) o SL ∈ {15, 20, 25} (se inferiore).\n\n"
    else:
        verdict += "**Tutti i prodotti hanno optimum interior al grid esteso.** Phase 11D risolve definitivamente boundary saturation.\n\n"

    verdict += f"""
---

## Updated final ranking (post-11D)

"""
    for i, r in frontier.reset_index(drop=True).iterrows():
        medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f" {i+1}"
        marker = " 🆕" if r["changed"] else ""
        verdict += (
            f"{medal} **{r['product']}** (TP={r['phase11d_TP']}, SL={r['phase11d_SL']}){marker}: "
            f"E[net]=${r['phase11d_E_net']:,.0f}/cycle, "
            f"pass={r['phase11d_pass']:.2%}, "
            f"ann=${r['phase11d_annualized']:,.0f}\n\n"
        )

    verdict += f"""
---

## Risk operativo (SL molto wide)

I prodotti con best_SL ≥ 250pt richiedono attenzione:
- SL=250pt × $20 NQ = $5,000 single trade loss
- SL=300pt = $6,000
- SL=400pt = $8,000

**Per EOD products con DLL:**
- 25K_eod DLL=$2,000 → broker clamp loss a $2,000 anche se SL=400 → SL effettivo = $2,000
- 50K_eod DLL=$2,500 (verificare) → similar clamp
- 100K_eod DLL=$1,500 → SL effettivo = $1,500
- 150K_eod DLL=$2,000 → SL effettivo = $2,000

**MC potrebbe star sotto-stimando rischio per prodotti EOD con SL > DLL** perché simula DLL clamp invece di vero SL-out.
**Per Intraday products:** SL pieno applica, no clamp. Tail risk reale.

---

## File prodotti

- `reports/phase11d_extended_sl/extended_backtest_summary.csv` — 38 new backtests
- `reports/phase11d_extended_sl/extended_mc_summary.csv` — 76 new MC runs
- `reports/phase11d_extended_sl/daily_pnl_tp{{TP}}_sl{{SL}}/` — 38 new cells
- `reports/phase11d_extended_sl/lifecycle_mc_tp{{TP}}_sl{{SL}}/` — new MC parquets
- `reports/phase11d_extended_sl/frontier_2d_extended.csv` — updated frontier
- `reports/phase11d_extended_sl/heatmap_extended_{{product}}.png` — 8 updated heatmaps
- `reports/phase11d_extended_sl/VERDICT_11d.md` — questo doc

## Reproducibility

```bash
python scripts/phase11d_extended_backtest.py
python scripts/phase11d_extended_mc.py --sims 5000
python scripts/phase11d_updated_frontier.py
```
"""

    verdict_path = out / "VERDICT_11d.md"
    verdict_path.write_text(verdict)
    print(f"\n[Phase 11D.3] VERDICT -> {verdict_path}")


if __name__ == "__main__":
    main()
