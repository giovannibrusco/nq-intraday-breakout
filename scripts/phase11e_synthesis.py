"""Phase 11E.4: Final synthesis with elbow analysis results.

Produces:
  - Updated reports/phase11_summary/VERDICT.md (with backup pre-11e)
  - Updated reports/phase11_summary/operational_setup.csv (3-tier: aggressive/elbow/conservative)
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--elbow-summary", default="reports/phase11e_sl_elbow/elbow_summary.csv")
    parser.add_argument("--phase9c", default="reports/phase9c_lifecycle_mc/summary.csv")
    parser.add_argument("--out-dir", default="reports/phase11_summary")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Backup existing VERDICT
    verdict_path = out / "VERDICT.md"
    if verdict_path.exists():
        shutil.copy(verdict_path, out / "VERDICT_pre11e.md")
        print(f"[Phase 11E.4] Backup: VERDICT.md -> VERDICT_pre11e.md")

    elbow = pd.read_csv(args.elbow_summary)
    p9 = pd.read_csv(args.phase9c)

    # Merge baseline numbers
    rows = []
    for _, e in elbow.iterrows():
        prod = e["product"]
        p9_row = p9[p9["product"] == prod].iloc[0]
        baseline_e_net = float(p9_row["expected_net_per_cycle_promo"])
        rows.append({
            "product": prod,
            "tier": e["tier"],
            "tier_capital": e["tier_capital"],
            "phase9c_E_net": round(baseline_e_net, 0),
            "aggressive_TP": e["best_tp_global"],
            "aggressive_SL": e["best_sl_global"],
            "aggressive_E_net": e["best_E_net_global"],
            "aggressive_uplift_vs_9c_pct": round(100 * (e["best_E_net_global"]/baseline_e_net - 1), 1),
            "elbow_TP": e["elbow_tp"],
            "elbow_SL": e["elbow_sl"],
            "elbow_E_net": e["elbow_E_net"],
            "elbow_uplift_vs_9c_pct": round(100 * (e["elbow_E_net"]/baseline_e_net - 1), 1),
            "conservative_TP": e["safe_tp"],
            "conservative_SL": e["safe_sl"],
            "conservative_E_net": e["safe_E_net"] if e["safe_E_net"] != "n/a" else None,
            "safety_sl_max_points": e["safety_sl_max_points"],
            "plateau_start_sl": e["plateau_start_sl_at_best_tp"],
            "delta_aggressive_vs_conservative_pct": e["delta_global_vs_safe_pct"],
        })
    df = pd.DataFrame(rows).sort_values("aggressive_E_net", ascending=False)

    ops_path = out / "operational_setup.csv"
    df.to_csv(ops_path, index=False)
    print(f"[Phase 11E.4] Operational setup -> {ops_path}")

    # Build VERDICT.md
    avg_aggressive_uplift = df["aggressive_uplift_vs_9c_pct"].mean()
    avg_elbow_uplift = df["elbow_uplift_vs_9c_pct"].mean()
    n_at_max_sl = (df["aggressive_SL"] == 10000).sum()

    verdict = f"""# Phase 11 — Full Risk Geometry VERDICT (Post-11E elbow discovery)

**Date:** 2026-04-26
**Engine:** lifecycle MC 5000 sims, full grid 5 TP × 17 SL × 8 prodotti
**Phases:** 9C → 10A → 11A → 11B → 11D → **11E (full grid + elbow)**

---

## TL;DR

**Elbow trovato esplicitamente per ogni prodotto.** Differenza tra max teorico (aggressive)
e setup safety-capped (conservative) quantificata per supportare scelta operativa.

Average E[net/cycle] uplift vs Phase 9C baseline:
- **Aggressive (theoretical max):** +{avg_aggressive_uplift:.1f}%
- **Elbow (Kneedle sweet spot):** +{avg_elbow_uplift:.1f}%
- **Conservative (10% capital safety cap):** vedere tabella sotto

Boundary check finale: **{n_at_max_sl}/8 prodotti** hanno aggressive optimum a SL=10000 ("no SL").
Per questi, il MC plateau implica che strategicamente il SL non aggiunge valore — solo TP exit dominano.

---

## 3-tier setup recommendations

| Product | Tier | Aggressive (max E[net]) | Elbow (sweet spot) | Conservative (safety) | Δ aggr vs cons |
|---------|------|-------------------------|---------------------|------------------------|-----------------|
"""
    for _, r in df.iterrows():
        cons_txt = f"TP={r['conservative_TP']}, SL={r['conservative_SL']} → ${r['conservative_E_net']:,}" if r['conservative_E_net'] else "n/a"
        verdict += (
            f"| {r['product']:20s} | {r['tier']} | "
            f"TP={r['aggressive_TP']}, SL={r['aggressive_SL']} → ${r['aggressive_E_net']:,} (+{r['aggressive_uplift_vs_9c_pct']:+.1f}%) | "
            f"TP={r['elbow_TP']}, SL={r['elbow_SL']} → ${r['elbow_E_net']:,} (+{r['elbow_uplift_vs_9c_pct']:+.1f}%) | "
            f"{cons_txt} | "
            f"{r['delta_aggressive_vs_conservative_pct']}% |\n"
        )

    verdict += f"""
---

## Decision support

**Aggressive (theoretical max):**
- Pros: massimizza E[net] su backtest storico 2015-2025
- Cons: SL=10000 (no SL) o SL >> 10% capital → single-day blow risk REAL
- Consiglio: usare solo dopo Phase 12 fat tails validation

**Elbow (Kneedle sweet spot):**
- Pros: identifica punto di max curvatura — oltre questo, marginal gain è basso
- Cons: ancora potenzialmente sopra safety cap
- Consiglio: balanced setup statisticamente robusto

**Conservative (10% capital safety cap):**
- Pros: max single-trade loss limitato a 10% del capital tier (operationally survivable)
- Cons: lascia E[net] uplift sul tavolo (vedere Δ %)
- Consiglio: setup prudente per Phase 1 deploy / paper trading

---

## File prodotti (Phase 11E)

```
reports/phase11e_sl_elbow/
  full_grid_backtest_summary.csv
  full_grid_mc_summary.csv
  daily_pnl_tp{{TP}}_sl{{SL}}/ x ~242 cells
  lifecycle_mc_tp{{TP}}_sl{{SL}}/
  elbow_curves_{{product}}.png x 8
  heatmap_full_grid_{{product}}.png x 8
  elbow_summary.csv
reports/phase11_summary/
  VERDICT.md (questo doc)
  VERDICT_pre11e.md (backup)
  operational_setup.csv (aggiornato 3-tier)
```

## Reproducibility

```bash
python scripts/phase11e_full_grid_backtest.py
python scripts/phase11e_full_grid_mc.py --sims 5000
python scripts/phase11e_elbow_analysis.py
python scripts/phase11e_synthesis.py
```
"""
    verdict_path.write_text(verdict)
    print(f"[Phase 11E.4] VERDICT -> {verdict_path}")


if __name__ == "__main__":
    main()
