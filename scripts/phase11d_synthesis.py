"""Phase 11D.4: Updated synthesis with 11D extension results.

Backups previous VERDICT.md, generates new one with extended grid results.
Updates comparison_phases.csv and operational_setup.csv with 11D column.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase11b-frontier", default="reports/phase11b_grid_tp_sl/frontier_2d.csv")
    parser.add_argument("--phase11d-frontier", default="reports/phase11d_extended_sl/frontier_2d_extended.csv")
    parser.add_argument("--phase11c-comparison", default="reports/phase11_summary/comparison_phases.csv")
    parser.add_argument("--out-dir", default="reports/phase11_summary")
    args = parser.parse_args()

    out = Path(args.out_dir)

    # Backup existing VERDICT.md
    verdict_path = out / "VERDICT.md"
    if verdict_path.exists():
        backup_path = out / "VERDICT_pre11d.md"
        shutil.copy(verdict_path, backup_path)
        print(f"[Phase 11D.4] Backup: {verdict_path} -> {backup_path}")

    # Load all phase data
    p11b = pd.read_csv(args.phase11b_frontier)
    p11d = pd.read_csv(args.phase11d_frontier)
    cmp = pd.read_csv(args.phase11c_comparison)

    # Merge into comparison
    p11d_renamed = p11d[["product", "phase11d_TP", "phase11d_SL", "phase11d_E_net", "phase11d_pass", "phase11d_annualized", "delta_pct", "at_upper_boundary"]].copy()
    p11d_renamed = p11d_renamed.rename(columns={
        "phase11d_TP": "11d_TP",
        "phase11d_SL": "11d_SL",
        "phase11d_E_net": "11d_E_net",
        "phase11d_pass": "11d_pass",
        "phase11d_annualized": "11d_annualized",
        "delta_pct": "delta_11b_to_11d_pct",
        "at_upper_boundary": "11d_at_boundary",
    })
    cmp_extended = cmp.merge(p11d_renamed, on="product", how="left")
    cmp_extended["uplift_9c_to_11d_pct"] = round(
        100 * (cmp_extended["11d_E_net"] / cmp_extended["9c_E_net"] - 1), 1
    )
    cmp_extended = cmp_extended.sort_values("11d_E_net", ascending=False)
    cmp_path = out / "comparison_phases.csv"
    cmp_extended.to_csv(cmp_path, index=False)
    print(f"[Phase 11D.4] Updated comparison -> {cmp_path}")

    # Updated operational_setup
    ops_rows = []
    for _, r in cmp_extended.iterrows():
        ops_rows.append({
            "product": r["product"],
            "TP_points": int(r["11d_TP"]),
            "SL_points": int(r["11d_SL"]),
            "TP_dollars_NQ": int(r["11d_TP"] * 20),
            "SL_dollars_NQ": int(r["11d_SL"] * 20),
            "RR_ratio": round(r["11d_TP"] / r["11d_SL"], 2),
            "expected_pass_rate": round(r["11d_pass"], 3),
            "expected_E_net_per_cycle": round(r["11d_E_net"], 0),
            "expected_annualized_promo": round(r["11d_annualized"], 0),
            "boundary_saturated": bool(r["11d_at_boundary"]),
        })
    ops = pd.DataFrame(ops_rows)
    ops.to_csv(out / "operational_setup.csv", index=False)
    print(f"[Phase 11D.4] Updated operational setup -> {out / 'operational_setup.csv'}")

    # Updated VERDICT.md
    avg_uplift_9c = cmp_extended["uplift_9c_to_11d_pct"].mean()
    median_uplift_9c = cmp_extended["uplift_9c_to_11d_pct"].median()
    avg_uplift_11b = cmp_extended["delta_11b_to_11d_pct"].mean()
    n_boundary = int(cmp_extended["11d_at_boundary"].sum())
    n_changed = sum(
        (r["11d_TP"] != r["11b_TP"]) or (r["11d_SL"] != r["11b_SL"])
        for _, r in cmp_extended.iterrows()
    )

    verdict = f"""# Phase 11 — Full Risk Geometry VERDICT (Final, Phase 11D included)

**Date:** 2026-04-26
**Engine:** lifecycle MC 5000 sims, full grid TP × SL × 8 prodotti
**Phases included:** 9C → 10A → 11A → 11B → **11D extended**

---

## ⚠️ CRITICAL FINDING — Boundary saturation persistente

**{n_boundary}/8 prodotti hanno best SL al boundary superiore (SL=400).**

Phase 11B aveva SL=200 come massimo. Phase 11D ha esteso a {{250, 300, 400}}.
**7/8 prodotti hanno scelto SL=400** → optimum è probabilmente ANCORA più largo (SL=500+, 800+).

Possibile spiegazione:
1. **MC underestimates tail risk** quando block bootstrap non sample worst-case days oltre lo storico
2. **Wide SL = max winner capture** — strategy guadagna da rare big wins, SL stretto kills questi
3. **DLL clamping (EOD products)**: per 25K_eod/50K_eod/150K_eod con DLL=$2K, SL=400pt = $8K viene CLAMPED dal broker a DLL → SL effettivo = 100pt. MC simula questo correttamente.

**Per Intraday products (no DLL): SL=400 è REALE = $8000 single trade loss su NQ.** Tail risk reale.

---

## TL;DR

Average E[net/cycle] uplift cumulato:
- **vs Phase 9C** (TP=200, SL=100 baseline): **+{avg_uplift_9c:.1f}%** (median +{median_uplift_9c:.1f}%)
- **vs Phase 11B** (best 5×5 grid): **+{avg_uplift_11b:.1f}%**

Top performer: **apex_50k_intraday TP=82 SL=400 → $8,894/cycle, $81K+/anno**

**MA:** boundary not solved. Phase 11E candidato per testare SL ∈ {{500, 600, 800}}.

---

## Comparazione full Phase 9C → 10A → 11A → 11B → 11D

```
{cmp_extended[['product', '9c_E_net', '10a_E_net', '11a_E_net', '11b_E_net', '11d_E_net', '11d_TP', '11d_SL', '11d_pass', 'uplift_9c_to_11d_pct', '11d_at_boundary']].to_string(index=False)}
```

---

## Updated final ranking (post-11D)

"""
    for i, r in cmp_extended.reset_index(drop=True).iterrows():
        medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f" {i+1}"
        boundary_warn = " ⚠️ BOUNDARY" if bool(r["11d_at_boundary"]) else ""
        verdict += (
            f"{medal} **{r['product']}** (TP={int(r['11d_TP'])}, SL={int(r['11d_SL'])}){boundary_warn}: "
            f"E[net]=${r['11d_E_net']:,.0f}/cycle, "
            f"pass={r['11d_pass']:.2%}, "
            f"ann=${r['11d_annualized']:,.0f}, "
            f"+{r['uplift_9c_to_11d_pct']:.1f}% vs 9C\n\n"
        )

    verdict += f"""
---

## Operational setup table (post-11D)

| Product | TP (pt) | SL (pt) | TP$ NQ | SL$ NQ | RR | Pass% | E[net]/cycle | Annualized | Boundary |
|---------|---------|---------|--------|--------|-----|-------|--------------|------------|----------|
"""
    for _, r in ops.iterrows():
        flag = "⚠️" if r["boundary_saturated"] else ""
        verdict += (
            f"| {r['product']:20s} | {r['TP_points']:3d} | {r['SL_points']:3d} | "
            f"${r['TP_dollars_NQ']:,} | ${r['SL_dollars_NQ']:,} | "
            f"{r['RR_ratio']:.2f} | {r['expected_pass_rate']:.2%} | "
            f"${r['expected_E_net_per_cycle']:,.0f} | "
            f"${r['expected_annualized_promo']:,.0f} | {flag} |\n"
        )

    verdict += f"""
---

## Risk operativo aggiornato (SL=400 reality check)

**Dollari risk per single trade:**
- SL=400pt × $20 NQ = **$8,000 single trade loss** (Intraday products)
- SL=400pt × $2 MNQ × 30c = $24,000 ma capped da position sizing (1% risk = 1 MNQ contract = $800/trade per 25K)

**Per Intraday products (NO DLL clamp):**
- 50K_intraday: SL=$8,000 = 16% capital → trailing $2,500 BLOWED da single bad trade
- 100K_intraday: SL=$8,000 = 8% capital → trailing $5,000 BLOWED se 1 trade
- 150K_intraday: SL=$8,000 = 5.3% capital → trailing $5,000 ancora vulnerabile

**Critical:** Una single bad trade può blow account istantaneamente. MC mostra alta pass rate
(54-72%) perché block bootstrap sui dati storici 2015-2025 NON ha visto trades così cattivi
da blow trailing in single day. **In real life potrebbe succedere** (cigno nero / news event).

**Per EOD products: DLL clamp protegge.** SL=400 in eod = SL effettivo = DLL value (≈$1,500-$2,000).
Lifecycle MC in 11D usa daily PnL clamped da DLL già incluso → numeri E[net] sono REALI per EOD.

**Per Intraday products: NO clamp.** Numeri E[net] potrebbero essere ottimistici. Phase 12 fat tails
o stress synthetic data per validare.

---

## Decisione operativa

**Conservative recommendation (RACCOMANDATA):**
- Per **EOD products** (DLL clamp protegge): usa Phase 11D best (SL=400 effective DLL=$1500-2000)
- Per **Intraday products** (no clamp): **STAY at Phase 11B SL=200pt** per controllo tail risk
  - SL=200 × $20 = $4000 single trade loss = 8% di 50K (rischio gestibile)
  - Lifecycle MC al SL=200 era già +60-90% vs 9C — già grande uplift
  - SL=400 promette +15-30% extra ma con tail risk REALE non modellato

**Aggressive recommendation (IF Phase 12 fat tail conferma):**
- Tutti SL=400 come da Phase 11D
- Outperformance teorica massima
- Richiede Phase 12 stress test prima di live deploy

---

## Confronto con video Columbia (final updated)

| Claim Columbia | Verifica framework |
|----------------|---------------------|
| Tight TP + wide SL = optimal prop firm | **Confermato 7/8 prodotti** (TP=82-115, SL=400 max tested) |
| Variance min in 2D | **Confermato** quando applicato jointly TP+SL |
| Asymmetric geometry estrema (TP < SL) | **Confermato extreme**: TP=82, SL=400 → ratio 0.21 |
| Boundary del SL ∞? | **Aperto**: 7/8 ancora boundary saturated, possibly Phase 11E needed |

---

## Cosa è cambiato Phase 11B → 11D

| Aspetto | 11B | **11D** |
|---------|-----|--------|
| SL grid | 5 valori (56-200) | **8 valori (30-400)** |
| Top E[net] | $7,347 | **$8,894** (+21%) |
| Avg uplift vs 9C | ~125% | **{avg_uplift_9c:.0f}%** |
| Best SL frequency | 200 (5/8) | **400 (7/8) — boundary!** |
| Operational risk | Manageable | **WARNING tail risk Intraday** |

---

## Next steps possibili

1. **Phase 11E — Even wider SL** (500, 600, 800): risolve boundary definitivamente
   - Compute: ~30 min
   - Risk: SL=800 = $16K single trade — operationally inaccettabile per accounts piccoli

2. **Phase 12 — Fat tails / EVT stress**: valida pass rate al SL=400 contro scenari estremi
   - Generalized Pareto fit sulla coda delle daily losses
   - Stress synthetic shock days (-$10K, -$15K injected)
   - Compute: ~1-2h

3. **Paper trading immediato** con setup Phase 11B (SL=200, conservative): riduce ambiguity, valida live execution
   - Outlay: $94 per primo cycle 50K_intra
   - Real-world feedback in 2-3 settimane

4. **Accept Phase 11D, conservative deploy**: usa SL=200 per Intraday, SL=400 per EOD (DLL-protected)

---

## File prodotti (Phase 11D incluso)

```
reports/
  phase11d_extended_sl/
    extended_backtest_summary.csv (38 rows)
    daily_pnl_tp{{TP}}_sl{{SL}}/ x 38 cells
    extended_mc_summary.csv (76 rows)
    lifecycle_mc_tp{{TP}}_sl{{SL}}/ x 38 cells
    frontier_2d_extended.csv
    heatmap_extended_{{product}}.png x 8
    VERDICT_11d.md
  phase11_summary/
    VERDICT.md (questo doc, post-11D)
    VERDICT_pre11d.md (backup pre-11D)
    comparison_phases.csv (esteso con 11D)
    operational_setup.csv (aggiornato)
```

## Reproducibility totale

```bash
# Phase 11A: SL sweep at fixed best-TP
python scripts/phase11a_export_trades.py
python scripts/phase11a_mae_analysis.py
python scripts/phase11a_sl_sweep.py
python scripts/phase11a_lifecycle_mc.py --sims 5000
python scripts/phase11a_frontier.py

# Phase 11B: Full 2D 5x5 grid
python scripts/phase11b_grid_backtest.py
python scripts/phase11b_grid_mc.py --sims 5000
python scripts/phase11b_frontier_2d.py

# Phase 11C: synthesis
python scripts/phase11c_synthesis.py

# Phase 11D: extended SL (250-400 + 30-40 tighter)
python scripts/phase11d_extended_backtest.py
python scripts/phase11d_extended_mc.py --sims 5000
python scripts/phase11d_updated_frontier.py
python scripts/phase11d_synthesis.py
```

Total wall time: ~30 min su M-series.
"""
    verdict_path.write_text(verdict)
    print(f"\n[Phase 11D.4] Updated VERDICT -> {verdict_path}")


if __name__ == "__main__":
    main()
