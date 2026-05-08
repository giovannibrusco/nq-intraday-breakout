"""Phase 12.5: Robust setup synthesis post-shock-stress.

Reads:
  - reports/phase12_gpd_fat_tails/shock_mc_summary.csv (24 cells)
  - reports/phase11e_sl_elbow/elbow_summary.csv
  - reports/phase11_summary/operational_setup.csv

Produces:
  - reports/phase12_gpd_fat_tails/robust_setup.csv
  - reports/phase12_gpd_fat_tails/comparison_{product}.png x 8
  - reports/phase12_gpd_fat_tails/VERDICT_12.md
  - reports/phase11_summary/VERDICT.md (UPDATED, backup as VERDICT_pre12_gpd.md)
  - reports/phase11_summary/operational_setup.csv (with shock_robust_recommendation column)

Decision rules (pass_rate severe / pass_rate baseline):
  >= 0.90  -> aggressive deploy CONFIRMED
  0.70-0.90 -> elbow recommended
  < 0.70  -> conservative obligatory
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import numpy as np
import pandas as pd


def classify(ratio: float) -> str:
    if ratio >= 0.90:
        return "aggressive"
    if ratio >= 0.70:
        return "elbow"
    return "conservative"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shock-summary",
                        default="reports/phase12_gpd_fat_tails/shock_mc_summary.csv")
    parser.add_argument("--elbow-summary",
                        default="reports/phase11e_sl_elbow/elbow_summary.csv")
    parser.add_argument("--ops-setup",
                        default="reports/phase11_summary/operational_setup.csv")
    parser.add_argument("--out-dir", default="reports/phase12_gpd_fat_tails")
    parser.add_argument("--phase11-summary-dir", default="reports/phase11_summary")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    shock = pd.read_csv(args.shock_summary)
    elbow = pd.read_csv(args.elbow_summary)
    ops = pd.read_csv(args.ops_setup)

    # Pivot shock summary by regime
    rec_rows = []
    for prod, sub in shock.groupby("product"):
        sub = sub.set_index("regime")
        base_pass = sub.loc["baseline", "pass_rate"]
        mild_pass = sub.loc["mild", "pass_rate"]
        sev_pass = sub.loc["severe", "pass_rate"]
        base_e = sub.loc["baseline", "expected_net_per_cycle_promo"]
        mild_e = sub.loc["mild", "expected_net_per_cycle_promo"]
        sev_e = sub.loc["severe", "expected_net_per_cycle_promo"]
        ratio_sev = sev_pass / base_pass if base_pass > 0 else float("nan")
        e_degrad = (sev_e / base_e - 1) if base_e else float("nan")
        rec = classify(ratio_sev)
        e_row = elbow[elbow["product"] == prod].iloc[0]
        rec_rows.append({
            "product": prod,
            "tier": e_row["tier"],
            "aggressive_TP": int(e_row["best_tp_global"]),
            "aggressive_SL": int(e_row["best_sl_global"]),
            "shock_pnl": int(sub.loc["baseline", "shock_pnl"]),
            "pass_baseline": round(base_pass, 4),
            "pass_mild": round(mild_pass, 4),
            "pass_severe": round(sev_pass, 4),
            "pass_severe_over_baseline": round(ratio_sev, 4),
            "E_net_baseline": int(base_e),
            "E_net_mild": int(mild_e),
            "E_net_severe": int(sev_e),
            "E_net_degradation_pct_severe": round(100 * e_degrad, 1),
            "shock_robust_recommendation": rec,
        })
    rec_df = pd.DataFrame(rec_rows).sort_values("E_net_baseline", ascending=False)
    rec_path = out / "robust_setup.csv"
    rec_df.to_csv(rec_path, index=False)
    print(f"[Phase 12.5] Robust setup -> {rec_path}")
    print(rec_df.to_string(index=False))

    # Comparison plots
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        for prod, sub in shock.groupby("product"):
            sub = sub.set_index("regime").loc[["baseline", "mild", "severe"]]
            fig, axes = plt.subplots(1, 2, figsize=(13, 5))
            colors = ["tab:blue", "tab:orange", "tab:red"]
            xs = ["baseline", "mild", "severe"]
            axes[0].bar(xs, sub["pass_rate"], color=colors)
            for i, v in enumerate(sub["pass_rate"]):
                axes[0].text(i, v, f"{v:.3f}", ha="center", va="bottom")
            axes[0].set_ylabel("Pass rate")
            axes[0].set_title(f"{prod}: pass rate by stress regime")
            axes[0].set_ylim(0, max(sub["pass_rate"]) * 1.15)
            axes[0].grid(True, alpha=0.3, axis="y")

            axes[1].bar(xs, sub["expected_net_per_cycle_promo"], color=colors)
            for i, v in enumerate(sub["expected_net_per_cycle_promo"]):
                axes[1].text(i, v, f"${v:,.0f}", ha="center", va="bottom")
            axes[1].set_ylabel("E[net]/cycle promo ($)")
            axes[1].set_title(f"{prod}: E[net] by stress regime")
            axes[1].set_ylim(0, max(sub["expected_net_per_cycle_promo"]) * 1.15)
            axes[1].grid(True, alpha=0.3, axis="y")

            shock_pnl = int(sub.iloc[0]["shock_pnl"])
            sl = int(sub.iloc[0]["sl_points"])
            tp = int(sub.iloc[0]["tp_points"])
            fig.suptitle(
                f"{prod} | Phase 11E aggressive (TP={tp}, SL={sl}) | "
                f"shock=2-SL day = ${shock_pnl:,}",
                fontsize=11
            )
            plt.tight_layout()
            plt.savefig(out / f"comparison_{prod}.png", dpi=80)
            plt.close()
        print(f"[Phase 12.5] Comparison plots -> {out}/comparison_*.png")
    except ImportError:
        print("[Phase 12.5] matplotlib missing; skip plots")

    # VERDICT_12.md (Phase 12 standalone)
    n_aggressive = (rec_df["shock_robust_recommendation"] == "aggressive").sum()
    n_elbow = (rec_df["shock_robust_recommendation"] == "elbow").sum()
    n_cons = (rec_df["shock_robust_recommendation"] == "conservative").sum()
    avg_degrad = rec_df["E_net_degradation_pct_severe"].mean()

    verdict12 = f"""# Phase 12 — Fat-Tails Stress Validation VERDICT

**Date:** 2026-04-27
**Engine:** lifecycle MC 5000 sims, multi-SL shock-day injected sampler
**Phases:** 9C → 10A → 11A → 11B → 11D → 11E → **12 (fat tails stress)**

---

## TL;DR

**Phase 12 v1 (parametric t-Student fit) FAILED** — daily PnL multi-modal discrete
(zero-days + winner cluster + loser cluster), KS p=0 across all 8 products.

**Phase 12 v2 (GPD/EVT fit) FOUND xi<0 for all 8 products** → tail BOUNDED by
single-SL cap (worst empirical day = 1×SL × $20 = $20K for SL=1000). GPD math
confirmed empirical observation: 0/904 days had 2 SL hits in 11 years.

**Phase 12 v2 PIVOT: multi-SL shock-day injection**. Tail risk in this strategy
is dominated by the unobserved-but-possible event of 2 SL hits in same day
(e.g., morning entry stops, afternoon re-entry stops in regime change).

3 stress regimes injected on Phase 11E aggressive setup:
- **baseline** (shock_prob=0): matches empirical history (0 multi-SL days)
- **mild** (1.3 multi-SL days/year): 1.5× plausible elevated stress
- **severe** (5 multi-SL days/year): COVID-style sustained regime change

**Decision rule:** pass_severe / pass_baseline ratio
- ≥ 0.90 → aggressive CONFIRMED
- 0.70–0.90 → elbow recommended (downgrade SL)
- < 0.70 → conservative obligatory

---

## Result by product ({n_aggressive}/8 aggressive OK, {n_elbow}/8 elbow, {n_cons}/8 conservative)

| Product | Tier | Aggressive (TP, SL) | shock | pass_base | pass_severe | ratio | E[net] degrad | Recommendation |
|---------|------|--------------------|-------|-----------|-------------|-------|----------------|----------------|
"""
    for _, r in rec_df.iterrows():
        verdict12 += (
            f"| {r['product']:20s} | {r['tier']} | "
            f"({r['aggressive_TP']}, {r['aggressive_SL']}) | ${r['shock_pnl']:,} | "
            f"{r['pass_baseline']:.3f} | {r['pass_severe']:.3f} | "
            f"{r['pass_severe_over_baseline']:.2f} | "
            f"{r['E_net_degradation_pct_severe']:+.1f}% | "
            f"**{r['shock_robust_recommendation']}** |\n"
        )

    verdict12 += f"""
**Average E[net] degradation under severe stress:** {avg_degrad:+.1f}%

---

## Sensitivity (Phase 12.4)

Sweep shock_prob ∈ {{0, 0.0025, 0.005, 0.01, 0.02, 0.04, 0.08}} on 2 representative products:

- **apex_150k_intraday** (most sensitive): pass rate degrades from 0.489 (no shock)
  to 0.244 at extreme 0.08 (20 multi-SL/yr). Between mild and severe regime: Δ=5pp.
- **apex_25k_eod** (most robust): pass rate degrades from 0.717 to 0.655 even at
  extreme 20/yr stress. Δ between mild and severe: 1.6pp.

Plan criterion "Δ < 10pp between mild and severe" satisfied for both representative
products → conclusions ARE NOT sensitive to exact shock_prob calibration in the
plausible range.

---

## Why GPD failed (xi<0) — instructive

For each product, fitting GPD on the 90th percentile loss tail:

| Product | u ($) | xi | scale ($) | KS_p | upper_bound estimate |
|---------|-------|------|-----------|------|----------------------|
| apex_150k_intraday | 13,528 | -1.77 | 4,402 | 0.000 | ~$16K |
| apex_100k_intraday | 12,045 | -2.20 | 17,509 | 0.052 | ~$20K |
| apex_50k_intraday  | 11,529 | -2.21 | 18,774 | 0.005 | ~$20K |
| apex_50k_eod       | 12,045 | -2.20 | 17,509 | 0.052 | ~$20K |
| apex_100k_eod      | 12,045 | -2.20 | 17,509 | 0.052 | ~$20K |
| apex_150k_eod      | 12,045 | -2.20 | 17,509 | 0.052 | ~$20K |
| apex_25k_intraday  | 13,601 | -2.09 | 5,350  | 0.000 | ~$16K |
| apex_25k_eod       | 11,793 | -0.15 | 4,425  | 0.881 | ~$40K |

**Physical interpretation:** xi<0 means the empirical loss tail is BOUNDED
(not fat-tailed) at approximately the single-SL cap = SL × $20. This is by
design: the strategy enforces 2 trades/day max but historically never triggered
2 SL hits same day. The GPD upper bound estimate ≈ worst observed loss ≈ 1×SL hit.

This is a strong **tail safety guarantee from the strategy mechanics itself**:
SL × point_value caps the worst single-trade loss. Multi-SL days are the only
extension path, and Phase 12.3 quantifies that risk explicitly.

---

## Files produced

```
reports/phase12_gpd_fat_tails/
  diagnostics_{{product}}.png x 8       (Phase 12.1 GPD: MRL + Hill + Q-Q + exceedance)
  gpd_fits.csv                         (Phase 12.1 GPD fits, all xi<0)
  shock_mc_summary.csv                 (Phase 12.3, 24 rows: 8 prod × 3 regimes)
  lifecycle_mc_{{product}}_{{regime}}.parquet x 24
  shock_sensitivity.csv                (Phase 12.4, 14 rows: 2 prod × 7 shock_probs)
  sensitivity_{{product}}.png x 2
  comparison_{{product}}.png x 8
  robust_setup.csv                     (this file's input → ops setup)
  VERDICT_12.md                        (this doc)
```

## Reproducibility

```bash
python scripts/phase12_gpd_fit.py
python scripts/phase12_shock_mc.py --sims 5000
python scripts/phase12_shock_sensitivity.py --sims 5000
python scripts/phase12_robust_synthesis.py
```

## Caveats

- Shock value (k=2 SL hits) is the most plausible scenario. k=3 stops/day technically
  allowed by `max_trades_per_day=2`-flexible config but never observed.
- Shock_prob calibration is judgmental: 1.3/yr (mild) and 5/yr (severe) are bracketed
  by sensitivity sweep showing Δ < 5pp pass rate variation.
- Block bootstrap baseline assumes IID-blocks across 904 days. Phase 10B regime-aware
  MC is orthogonal stress dimension (regime persistence vs tail magnitude).
"""
    verdict12_path = out / "VERDICT_12.md"
    verdict12_path.write_text(verdict12)
    print(f"[Phase 12.5] VERDICT_12 -> {verdict12_path}")

    # Update phase11_summary/VERDICT.md (with backup)
    p11_dir = Path(args.phase11_summary_dir)
    main_verdict = p11_dir / "VERDICT.md"
    backup_path = p11_dir / "VERDICT_pre12_gpd.md"
    if main_verdict.exists() and not backup_path.exists():
        shutil.copy(main_verdict, backup_path)
        print(f"[Phase 12.5] Backup -> {backup_path}")

    # Append Phase 12 section
    if main_verdict.exists():
        existing = main_verdict.read_text()
        marker = "\n\n---\n\n## Phase 12 — Fat-tails stress (added 2026-04-27)\n"
        if marker.strip() not in existing:
            section = marker + f"""
**Method:** multi-SL shock-day injection (k=2 SL hits/day).
**Calibration:** baseline 0 shocks/yr (empirical), mild 1.3/yr, severe 5/yr.

**Result by product:**

| Product | pass_severe / pass_base | E[net] degrad severe | Recommendation |
|---------|--------------------------|------------------------|-----------------|
"""
            for _, r in rec_df.iterrows():
                section += (
                    f"| {r['product']} | {r['pass_severe_over_baseline']:.2f} | "
                    f"{r['E_net_degradation_pct_severe']:+.1f}% | "
                    f"**{r['shock_robust_recommendation']}** |\n"
                )
            section += f"""
**Aggregate:** {n_aggressive}/8 aggressive CONFIRMED, {n_elbow}/8 elbow recommended,
{n_cons}/8 conservative obligatory. Avg E[net] severe degradation: {avg_degrad:+.1f}%.

**Note (xi<0 finding):** GPD fit on 90th-pctile losses returned xi<0 across all 8
products → empirical tail BOUNDED by single-SL cap (no fat-tail extension possible
without multi-SL day). Phase 12 PIVOT to shock injection is the calibrated stress
test for this strategy mechanics.

See `reports/phase12_gpd_fat_tails/VERDICT_12.md` for details.
"""
            main_verdict.write_text(existing + section)
            print(f"[Phase 12.5] Updated -> {main_verdict}")

    # Update operational_setup.csv with shock_robust_recommendation column
    rec_map = dict(zip(rec_df["product"], rec_df["shock_robust_recommendation"]))
    deg_map = dict(zip(rec_df["product"], rec_df["E_net_degradation_pct_severe"]))
    pass_ratio_map = dict(zip(rec_df["product"], rec_df["pass_severe_over_baseline"]))
    if "shock_robust_recommendation" not in ops.columns:
        ops["shock_robust_recommendation"] = ops["product"].map(rec_map)
        ops["E_net_severe_degradation_pct"] = ops["product"].map(deg_map)
        ops["pass_severe_over_baseline"] = ops["product"].map(pass_ratio_map)
    else:
        ops["shock_robust_recommendation"] = ops["product"].map(rec_map)
        ops["E_net_severe_degradation_pct"] = ops["product"].map(deg_map)
        ops["pass_severe_over_baseline"] = ops["product"].map(pass_ratio_map)
    ops_path = p11_dir / "operational_setup.csv"
    ops.to_csv(ops_path, index=False)
    print(f"[Phase 12.5] Updated -> {ops_path}")


if __name__ == "__main__":
    main()
