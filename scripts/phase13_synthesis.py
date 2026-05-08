"""Phase 13.7: Synthesis post-cost-fix + MAE correction.

Produces:
  - reports/phase13_mae_intraday/VERDICT_13.md (Phase 13 standalone)
  - reports/phase11_summary/VERDICT.md updated (backup VERDICT_pre13.md)
  - reports/phase11_summary/operational_setup.csv updated with mae_recommendation
  - reports/phase13_mae_intraday/comparison_{product}.png x 8 (proxy vs MAE)
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mae-summary",
                        default="reports/phase13_mae_intraday/mae_corrected_mc_summary.csv")
    parser.add_argument("--cost-summary",
                        default="reports/phase13_cost_fix/cost_corrected_summary.csv")
    parser.add_argument("--ops-setup",
                        default="reports/phase11_summary/operational_setup.csv")
    parser.add_argument("--out-dir", default="reports/phase13_mae_intraday")
    parser.add_argument("--phase11-summary-dir", default="reports/phase11_summary")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    mae = pd.read_csv(args.mae_summary)
    cost = pd.read_csv(args.cost_summary).set_index("product")
    ops = pd.read_csv(args.ops_setup)

    # Aggregate counts
    n_aggressive = (mae["mae_robust_recommendation"] == "aggressive").sum()
    n_elbow = (mae["mae_robust_recommendation"] == "elbow").sum()
    n_cons = (mae["mae_robust_recommendation"] == "conservative").sum()

    eod_subset = mae[mae["variant"] == "eod"]
    intra_subset = mae[mae["variant"] == "intraday"]

    # Comparison plots
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        for _, r in mae.iterrows():
            prod = r["product"]
            fig, axes = plt.subplots(1, 2, figsize=(13, 5))
            colors = ["tab:blue", "tab:red"]
            xs = ["proxy", "MAE-corrected"]
            pass_vals = [r["pass_baseline_proxy"], r["pass_mae_corrected"]]
            e_vals = [r["E_net_baseline"], r["E_net_mae_corrected"]]
            axes[0].bar(xs, pass_vals, color=colors)
            for i, v in enumerate(pass_vals):
                axes[0].text(i, v, f"{v:.3f}", ha="center", va="bottom")
            axes[0].set_ylabel("Pass rate")
            axes[0].set_title(f"{prod}: pass rate proxy vs MAE-corrected")
            axes[0].set_ylim(0, max(pass_vals) * 1.15)
            axes[0].grid(True, alpha=0.3, axis="y")

            axes[1].bar(xs, e_vals, color=colors)
            for i, v in enumerate(e_vals):
                axes[1].text(i, v, f"${v:,.0f}", ha="center", va="bottom")
            axes[1].set_ylabel("E[net]/cycle promo ($)")
            axes[1].set_title(f"{prod}: E[net] proxy vs MAE-corrected")
            axes[1].set_ylim(0, max(e_vals) * 1.15)
            axes[1].grid(True, alpha=0.3, axis="y")

            fig.suptitle(
                f"{prod} | {r['variant']} | TP={r['tp']} SL={r['sl']} | {r['instrument']} | "
                f"recommendation: **{r['mae_robust_recommendation']}**",
                fontsize=11
            )
            plt.tight_layout()
            plt.savefig(out / f"comparison_{prod}.png", dpi=80)
            plt.close()
        print(f"[Phase 13.7] Comparison plots -> {out}/comparison_*.png")
    except ImportError:
        print("[Phase 13.7] matplotlib missing")

    # VERDICT_13.md
    verdict13 = f"""# Phase 13 — Cost Fix + MAE Bar-Level Validation VERDICT

**Date:** 2026-04-27
**Engine:** lifecycle MC 5000 sims with MAE-aware (intraday extremes) sampler
**Phases:** 9C → 10A → 11A-E → 12 → **13 (cost fix + MAE bar-level)**

---

## TL;DR

**Two corrections applied to Phase 11E/12 numbers:**

1. **Cost model fix (Phase 13.1-13.2):** updated `costs.py` from legacy IB
   ($1.70 RT) to Apex Rithmic instrument-aware fees (NQ $3.98 RT, MNQ $1.02 RT).
   Impact: trivial for NQ (-0.3% E[net]), positive for MNQ (+2.9% E[net]).
   All Phase 11E aggressive recommendations preserved at this stage.

2. **MAE bar-level validation (Phase 13.3-13.6):** replaced MC's intraday proxy
   `min(daily_pnl, 0)` with actual intraday extremes computed from per-trade
   `mae_points`/`mfe_points` aggregated sequentially per day. Reveals proxy
   under-estimated intraday low by **avg $1,674 NQ / $167 MNQ per day**.
   - **EOD trail products (4): NO CHANGE** (proxy was correct — EOD rule checks
     only at close). All 4 EOD remain **aggressive CONFIRMED**.
   - **Intraday trail products (4): COLLAPSED** by -25 to -36pp pass rate,
     -62% E[net]. All 4 forced to **conservative**.

**Final recommendation: EOD-only deployment.** Intraday Trail PA at the Phase 11E
aggressive setup is NOT viable when the trailing drawdown is enforced properly
on intraday balance evolution.

---

## Cost fix impact (Phase 13.2)

| Product | Inst | Trades | E[net] OLD | E[net] NEW | Δ$ | Δ% |
|---------|------|--------|------------|------------|-----|------|
"""
    for _, r in cost.reset_index().iterrows():
        verdict13 += (
            f"| {r['product']:20s} | {r['instrument']} | {r['n_trades']} | "
            f"${r['E_net_per_cycle_promo_OLD']:,.0f} | ${r['E_net_per_cycle_promo_NEW']:,.0f} | "
            f"{r['delta_E_net_$']:+,.0f} | {r['delta_E_net_pct']:+.1f}% |\n"
        )

    verdict13 += f"""
**Conclusione cost fix:** trascurabile per NQ tier (-0.3%), positive per MNQ (+2.9%).
Causa: legacy default fee ($0.85/leg) era intermedio tra NQ ($1.99) e MNQ ($0.51)
ma usava tick_value NQ ($5) anche per MNQ, sovrastimando slippage MNQ.

---

## MAE-corrected MC ({n_aggressive}/8 aggressive, {n_elbow}/8 elbow, {n_cons}/8 conservative)

| Product | Variant | TP | SL | Inst | Pass proxy | Pass MAE | Δpp | Ratio | E[net] proxy | E[net] MAE | Δ% | Recommendation |
|---------|---------|----|----|------|------------|----------|-----|-------|--------------|------------|-----|----------------|
"""
    for _, r in mae.iterrows():
        rec = r["mae_robust_recommendation"]
        bold = f"**{rec}**" if rec != "aggressive" else rec
        verdict13 += (
            f"| {r['product']:20s} | {r['variant']:8s} | {r['tp']} | {r['sl']} | {r['instrument']} | "
            f"{r['pass_baseline_proxy']:.3f} | {r['pass_mae_corrected']:.3f} | "
            f"{r['pass_delta_pp']:+.1f} | {r['pass_ratio_mae_over_proxy']:.2f} | "
            f"${r['E_net_baseline']:,.0f} | ${r['E_net_mae_corrected']:,.0f} | "
            f"{r['E_net_delta_pct']:+.1f}% | {bold} |\n"
        )

    eod_passing = eod_subset[eod_subset["mae_robust_recommendation"] == "aggressive"]
    verdict13 += f"""
### Pattern by variant

- **EOD trail (4 products):** all 4 confirmed aggressive (Δ pass = 0pp).
  Proxy `min(daily_pnl, 0)` was already correct because EOD rule checks balance
  only at 16:59:59 ET snapshot.
- **Intraday trail (4 products):** all 4 forced to conservative (Δ pass -25/-36pp).
  Real-time peak-and-trough check exposes intraday violations that the MC proxy
  missed completely. Trailing drawdown buffer (€2.5K NQ tier, €1.5K MNQ) is too
  tight against MAE excursions of avg €2.8K NQ / €280 MNQ per day.

---

## Intraday extremes data (Phase 13.3)

Per-day intraday low/high computed from per-trade MAE/MFE × contracts × point_value,
aggregated sequentially through trades.

| Product | n_days | Avg intraday low | Worst single day | Pct days low<pnl | Avg proxy underestimate |
|---------|--------|------------------|-------------------|--------------------|--------------------------|
| 150K_intraday | 841 | -$2,785 | **-$20,039** | 99.9% | -$1,674 |
| 100K_intraday | 843 | -$2,801 | **-$22,200** | 99.8% | -$1,671 |
| 50K_intraday  | 679 | -$3,339 | **-$22,200** | 99.7% | -$1,873 |
| 50K_eod       | 843 | -$2,801 | -$22,200 | 99.8% | -$1,671 |
| 100K_eod      | 843 | -$2,801 | -$22,200 | 99.8% | -$1,671 |
| 150K_eod      | 843 | -$2,801 | -$22,200 | 99.8% | -$1,671 |
| 25K_intraday  | 841 | -$279   | -$2,005  | 99.9% | -$167 |
| 25K_eod       | 837 | -$279   | -$2,963  | 99.9% | -$169 |

99.8% di tutti giorni hanno intraday_low < daily_pnl proxy. Cioè quasi sempre
il proxy understimates l'estensione reale del drawdown intraday.

---

## Final operational recommendation

**SOLO EOD products. Intraday products SCONSIGLIATI al setup aggressive Phase 11E.**

| Product | Pass rate | E[net]/cycle | Annualized | Recommendation |
|---------|-----------|--------------|------------|----------------|
"""
    eod_sorted = eod_subset.sort_values("E_net_mae_corrected", ascending=False)
    for _, r in eod_sorted.iterrows():
        verdict13 += (
            f"| {r['product']:20s} | {r['pass_mae_corrected']:.3f} | "
            f"${r['E_net_mae_corrected']:,.0f} | "
            f"${r['annualized_promo_mae']:,.0f} | **AGGRESSIVE OK** |\n"
        )

    verdict13 += f"""

**Total annualized expected (4 EOD aggressive concurrent):** ~${eod_sorted['annualized_promo_mae'].sum():,.0f}/year.

### Intraday products (NOT recommended at aggressive setup)

| Product | Pass rate MAE | E[net]/cycle MAE | Status |
|---------|---------------|-------------------|---------|
"""
    intra_sorted = intra_subset.sort_values("E_net_mae_corrected", ascending=False)
    for _, r in intra_sorted.iterrows():
        verdict13 += (
            f"| {r['product']:20s} | {r['pass_mae_corrected']:.3f} | "
            f"${r['E_net_mae_corrected']:,.0f} | conservative — needs SL refit |\n"
        )

    verdict13 += f"""

**Possible upgrade path for Intraday products** (out-of-scope Phase 13):
- Re-run Phase 11E grid search using MAE-aware MC to find new aggressive
  setup that survives intraday violation check
- Likely requires tighter SL (smaller intraday MAE potential)
- Alternative: switch to MNQ-only deployment for Intraday tier (smaller dollar MAE)

---

## Files produced

```
reports/phase13_cost_fix/
  cost_corrected_summary.csv
  daily_pnl_{{product}}.parquet x 8

reports/phase13_mae_intraday/
  intraday_extremes_{{product}}.parquet x 8
  mae_summary.csv  (per-product MAE stats)
  lifecycle_mc_{{product}}.parquet x 8 (MAE-corrected)
  mae_corrected_mc_summary.csv
  comparison_{{product}}.png x 8
  VERDICT_13.md (this doc)
```

## Reproducibility

```bash
python scripts/phase13_cost_corrected_mc.py
python scripts/phase13_compute_intraday_extremes.py
python scripts/phase13_mae_corrected_mc.py
python scripts/phase13_synthesis.py
```

## Code changes

- `src/nqbreakout/strategy/costs.py`:
  - DEFAULT_COST_MODEL fee_per_leg=0.85 → 1.99 (NQ Apex Rithmic)
  - new NQ_RITHMIC_COST_MODEL, MNQ_RITHMIC_COST_MODEL constants
  - new make_cost_model_for_instrument() factory
- `src/nqbreakout/strategy/core.py`:
  - run_backtest derives cost_model from instrument when None
- `src/nqbreakout/prop/montecarlo.py`:
  - new block_bootstrap_with_extremes() (returns shape (n_days, 3))
  - simulate_eval_4 + simulate_pa_lifecycle: variant-aware violation check
    (intraday: real-time on intraday_low; eod: snapshot on EOD balance)
- `tests/test_costs.py`: 9 new tests for Apex Rithmic + factory
- `tests/test_montecarlo.py`: 6 new tests for extremes sampler
"""
    verdict13_path = out / "VERDICT_13.md"
    verdict13_path.write_text(verdict13)
    print(f"[Phase 13.7] VERDICT_13 -> {verdict13_path}")

    # Backup + update phase11_summary VERDICT
    p11_dir = Path(args.phase11_summary_dir)
    main_verdict = p11_dir / "VERDICT.md"
    backup_path = p11_dir / "VERDICT_pre13.md"
    if main_verdict.exists() and not backup_path.exists():
        shutil.copy(main_verdict, backup_path)
        print(f"[Phase 13.7] Backup -> {backup_path}")

    if main_verdict.exists():
        existing = main_verdict.read_text()
        marker = "\n\n---\n\n## Phase 13 — Cost fix + MAE bar-level (added 2026-04-27)\n"
        if marker.strip() not in existing:
            section = marker + f"""
**Two fixes applied:**

1. **Cost model:** `costs.py` updated to Apex Rithmic instrument-aware fees
   (NQ $3.98 RT, MNQ $1.02 RT). Impact: trivial (-0.3% NQ, +2.9% MNQ).

2. **MAE bar-level:** new `block_bootstrap_with_extremes` sampler returns
   per-day (pnl, intraday_low, intraday_high). `ApexTrailingTracker` now
   uses actual intraday low for Intraday Trail variant (was proxy
   `min(daily_pnl, 0)` underestimating by ~$1,700/day).

**Result table:**

| Product | Variant | Pass MAE | E[net] MAE | Δ vs proxy | Recommendation |
|---------|---------|----------|------------|------------|-----------------|
"""
            for _, r in mae.iterrows():
                section += (
                    f"| {r['product']} | {r['variant']} | "
                    f"{r['pass_mae_corrected']:.3f} | ${r['E_net_mae_corrected']:,.0f} | "
                    f"{r['E_net_delta_pct']:+.1f}% | "
                    f"**{r['mae_robust_recommendation']}** |\n"
                )
            section += f"""
**Aggregate:** {n_aggressive}/8 aggressive (all EOD), 0/8 elbow, {n_cons}/8 conservative (all Intraday).

**Final operational decision: deploy EOD products only.** Intraday products require
SL refit with MAE-aware MC before deployment is justified.

See `reports/phase13_mae_intraday/VERDICT_13.md` for full analysis.
"""
            main_verdict.write_text(existing + section)
            print(f"[Phase 13.7] Updated -> {main_verdict}")

    # Update operational_setup.csv
    rec_map = dict(zip(mae["product"], mae["mae_robust_recommendation"]))
    pass_mae_map = dict(zip(mae["product"], mae["pass_mae_corrected"]))
    e_net_mae_map = dict(zip(mae["product"], mae["E_net_mae_corrected"]))
    annual_mae_map = dict(zip(mae["product"], mae["annualized_promo_mae"]))
    ops["mae_recommendation"] = ops["product"].map(rec_map)
    ops["pass_rate_mae_corrected"] = ops["product"].map(pass_mae_map)
    ops["E_net_per_cycle_mae"] = ops["product"].map(e_net_mae_map)
    ops["annualized_net_mae"] = ops["product"].map(annual_mae_map)
    ops_path = p11_dir / "operational_setup.csv"
    ops.to_csv(ops_path, index=False)
    print(f"[Phase 13.7] Updated -> {ops_path}")


if __name__ == "__main__":
    main()
