"""Phase 5 - Rigorous OOS Edge Evaluation.

Esegue l'analisi statistica completa della strategia NQ breakout:
  1. Baseline metrics + bootstrap CI sull'intero storico
  2. Walk-forward 5y-train / 1y-test (no refit)
  3. Grid search su configs/grid.yaml (ridotto con --sample N)
  4. Stability score + top-5 config robuste
  5. Heatmap (stop_points x target_points) per Sharpe e win_rate

Output:
  reports/phase5_grid_results.parquet
  reports/phase5_robust_configs.csv
  reports/phase5_stat_rigor.md
  reports/phase5_heatmap_sharpe.png
  reports/phase5_heatmap_winrate.png

Flags:
  --sample N    Campiona N combinazioni random dal grid (evita 250-combo full run)
  --n-jobs N    Workers paralleli (default 1 per determinismo)
  --seed N      Seed globale (default 42)
"""

from __future__ import annotations

import argparse
import random
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from nqbreakout.data.loader import load_nq_1min, resample_to_5min
from nqbreakout.stats.bootstrap import bootstrap_all_metrics, bootstrap_statistic_ci
from nqbreakout.stats.gridsearch import (
    load_grid,
    pick_robust_config,
    run_grid,
    stability_score,
)
from nqbreakout.stats.metrics import profit_factor, sharpe, summary, win_rate
from nqbreakout.stats.walkforward import run_walkforward
from nqbreakout.strategy.config import load_instrument_spec, load_strategy_config


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 5: statistical rigor analysis")
    p.add_argument(
        "--sample",
        type=int,
        default=0,
        help="If > 0, randomly sample this many grid combinations instead of running all.",
    )
    p.add_argument(
        "--n-jobs",
        type=int,
        default=1,
        help="Parallel workers for grid search (default 1 = sequential, deterministic).",
    )
    p.add_argument("--seed", type=int, default=42, help="Global RNG seed (default 42).")
    p.add_argument(
        "--data", type=str, default="data/nq_1min.parquet", help="Path to NQ 1-min parquet."
    )
    p.add_argument(
        "--config",
        type=str,
        default="configs/strategy_default.yaml",
        help="Strategy config YAML.",
    )
    p.add_argument("--grid", type=str, default="configs/grid.yaml", help="Grid YAML.")
    p.add_argument(
        "--reports-dir", type=str, default="reports", help="Output directory for reports."
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _daily_pnl_from_result(result) -> pd.Series:
    """Reconstruct daily PnL from a BacktestResult's daily_equity."""
    if result.daily_equity.empty:
        return pd.Series(dtype=float)
    eq = result.daily_equity.sort_index()
    initial = float(result.config.initial_capital)
    vals = eq.values.astype(float)
    pnl_vals = []
    prev = initial
    for v in vals:
        pnl_vals.append(v - prev)
        prev = v
    return pd.Series(pnl_vals, dtype=float)


def _sample_grid(full_grid: dict[str, list], n: int, seed: int) -> dict[str, list]:
    """Randomly sample n unique combinations from full_grid, returned as filtered grid."""
    param_names = list(full_grid.keys())
    param_values = [full_grid[k] for k in param_names]
    all_combos = list(product(*param_values))
    rng = random.Random(seed)
    sampled = rng.sample(all_combos, min(n, len(all_combos)))
    # Rebuild a grid dict that only contains the sampled values per param.
    # We use a simpler approach: run_grid already supports direct combos via
    # the grid dict; easiest is to pass the sampled combos as the actual grid
    # by constructing single-value dicts per combo and running them iteratively.
    # Instead: return a combo list for direct iteration.
    return sampled, param_names


def _run_sampled_grid(df_5min, base_config, instrument, sampled_combos, param_names,
                      cost_model=None, filters=None):
    """Run a list of (param_tuple) combos and return a DataFrame."""
    import dataclasses
    from nqbreakout.stats.metrics import calmar, max_drawdown_pct, profit_factor, sharpe, sortino, win_rate
    from nqbreakout.strategy.core import run_backtest

    rows = []
    for combo in sampled_combos:
        kwargs = dict(zip(param_names, combo))
        cfg = dataclasses.replace(base_config, **kwargs)
        result = run_backtest(df_5min, cfg, instrument, filters=filters, cost_model=cost_model)
        row = dict(kwargs)
        n_trades = int(len(result.trades))
        if n_trades == 0 or result.daily_equity.empty:
            row.update({
                "total_pnl": 0.0, "n_trades": 0, "sharpe": float("nan"),
                "sortino": float("nan"), "calmar": float("nan"),
                "max_dd_pct": float("nan"), "profit_factor": float("nan"),
                "win_rate": float("nan"),
            })
        else:
            eq = result.daily_equity.sort_index()
            initial = float(cfg.initial_capital)
            eq_vals = eq.values.astype(float)
            pnl_vals = []
            prev = initial
            for v in eq_vals:
                pnl_vals.append(v - prev)
                prev = v
            daily_pnl = pd.Series(pnl_vals, dtype=float)
            equity_s = pd.Series(eq_vals, dtype=float)
            trade_pnl = result.trades["pnl"].astype(float) if "pnl" in result.trades.columns else pd.Series(dtype=float)
            row.update({
                "total_pnl": float(trade_pnl.sum()),
                "n_trades": n_trades,
                "sharpe": sharpe(daily_pnl),
                "sortino": sortino(daily_pnl),
                "calmar": calmar(daily_pnl, equity_s),
                "max_dd_pct": max_drawdown_pct(equity_s),
                "profit_factor": profit_factor(trade_pnl),
                "win_rate": win_rate(trade_pnl),
            })
        rows.append(row)
    return pd.DataFrame(rows)


def _heatmap(grid_results, x_param, y_param, metric, title, out_path: Path):
    """Save a heatmap of metric averaged over other grid dimensions."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print(f"  [skip heatmap: matplotlib not available]")
        return

    df = grid_results.copy()
    if x_param not in df.columns or y_param not in df.columns or metric not in df.columns:
        print(f"  [skip heatmap: required columns missing]")
        return

    pivot = (
        df.groupby([y_param, x_param])[metric]
        .mean()
        .unstack(x_param)
    )

    fig, ax = plt.subplots(figsize=(8, 6))
    import matplotlib.cm as cm
    im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn", origin="lower")
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([str(v) for v in pivot.columns])
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([str(v) for v in pivot.index])
    ax.set_xlabel(x_param)
    ax.set_ylabel(y_param)
    ax.set_title(title)
    plt.colorbar(im, ax=ax, label=metric)

    # Annotate cells
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            val = pivot.iloc[i, j]
            if not np.isnan(val):
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", fontsize=8)

    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()
    print(f"  Heatmap saved: {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    args = _parse_args()
    seed = args.seed
    reports_dir = REPO_ROOT / args.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("PHASE 5 -- RIGOROUS OOS EDGE EVALUATION")
    print("=" * 70)

    # -----------------------------------------------------------------------
    # Step 0: Load data and config
    # -----------------------------------------------------------------------
    print("\n[0] Loading data...")
    data_path = REPO_ROOT / args.data
    if not data_path.exists():
        print(f"ERROR: Data file not found: {data_path}")
        sys.exit(1)

    df_1min = load_nq_1min(data_path)
    df_5min = resample_to_5min(df_1min)
    date_min = df_5min["date"].min().date()
    date_max = df_5min["date"].max().date()
    print(f"  Data range: {date_min} to {date_max}")
    print(f"  Bars (5-min): {len(df_5min):,}")

    config = load_strategy_config(REPO_ROOT / args.config)
    instrument = load_instrument_spec("NQ")
    print(f"  Config: stop={config.stop_points}, target={config.target_points}")
    print(f"  Filters: gap={config.filter_spec.enable_overnight_gap}, "
          f"range={config.filter_spec.enable_prior_range}, "
          f"trend={config.filter_spec.enable_trend}, "
          f"liquidity={config.filter_spec.enable_liquidity}")

    # -----------------------------------------------------------------------
    # Step 1: Full-history baseline + bootstrap CI
    # -----------------------------------------------------------------------
    print("\n[1] Full-history baseline backtest...")
    from nqbreakout.strategy.core import run_backtest
    baseline = run_backtest(df_5min, config, instrument)
    daily_pnl_full = _daily_pnl_from_result(baseline)
    equity_full = baseline.daily_equity.sort_index()
    equity_series = pd.Series(equity_full.values.astype(float), dtype=float)
    trade_pnl_full = baseline.trades["pnl"].astype(float) if not baseline.trades.empty else pd.Series(dtype=float)

    metrics = summary(daily_pnl_full, equity_series, trade_pnl=trade_pnl_full)
    print(f"\n  Baseline metrics (full history):")
    print(f"  Sharpe:        {metrics['sharpe']:.4f}")
    print(f"  Sortino:       {metrics['sortino']:.4f}")
    print(f"  Calmar:        {metrics['calmar']:.4f}")
    print(f"  MaxDD%:        {metrics['max_dd_pct']:.4f}")
    print(f"  Profit Factor: {metrics['profit_factor']:.4f}")
    print(f"  Win Rate:      {metrics['win_rate']:.4f}")
    print(f"  Total PnL:     ${metrics['total_pnl']:,.0f}")
    print(f"  Trades:        {metrics['n_trades']}")

    print("\n  Computing bootstrap CIs (1000 resamples, block=10)...")
    boot_df = bootstrap_all_metrics(
        daily_pnl_full, equity_series, block_size=10, n_resamples=1000, alpha=0.05, seed=seed
    )
    print("\n  Bootstrap 95% CI:")
    print(f"  {'Metric':<12} {'Point':>8} {'Mean':>8} {'CI Lower':>10} {'CI Upper':>10}")
    print(f"  {'-'*52}")
    for _, row in boot_df.iterrows():
        print(f"  {row['metric']:<12} {row['point']:>8.4f} {row['mean']:>8.4f} "
              f"{row['ci_lower']:>10.4f} {row['ci_upper']:>10.4f}")

    # Profit factor CI (not in bootstrap_all_metrics -- computed separately)
    pf_ci = bootstrap_statistic_ci(
        trade_pnl_full,
        profit_factor,
        block_size=10,
        n_resamples=1000,
        alpha=0.05,
        seed=seed + 10,
    )
    wr_ci = bootstrap_statistic_ci(
        trade_pnl_full,
        win_rate,
        block_size=10,
        n_resamples=1000,
        alpha=0.05,
        seed=seed + 11,
    )
    print(f"  {'profit_factor':<12} {pf_ci['point']:>8.4f} {pf_ci['mean']:>8.4f} "
          f"{pf_ci['ci_lower']:>10.4f} {pf_ci['ci_upper']:>10.4f}")
    print(f"  {'win_rate':<12} {wr_ci['point']:>8.4f} {wr_ci['mean']:>8.4f} "
          f"{wr_ci['ci_lower']:>10.4f} {wr_ci['ci_upper']:>10.4f}")

    # -----------------------------------------------------------------------
    # Step 2: Walk-forward analysis (5y train / 1y test, no refit)
    # -----------------------------------------------------------------------
    print("\n[2] Walk-forward analysis (train=5y, test=1y, step=1y, no refit)...")
    try:
        wf = run_walkforward(
            df_5min,
            config,
            instrument,
            train_years=5,
            test_years=1,
            step_years=1,
        )
        print(f"\n  {len(wf.summary)} folds generated.")
        print(f"\n  {'Fold':>4} {'Train Period':<22} {'Test Period':<22} "
              f"{'Train Sharpe':>12} {'Test Sharpe':>11} {'OOS Degrad.':>11}")
        print(f"  {'-'*90}")
        for _, r in wf.summary.iterrows():
            ts = f"{r['train_start'].date()} - {r['train_end'].date()}"
            os_ = f"{r['test_start'].date()} - {r['test_end'].date()}"
            trsh = f"{r['train_sharpe']:.4f}" if not pd.isna(r["train_sharpe"]) else "   nan"
            tesh = f"{r['test_sharpe']:.4f}" if not pd.isna(r["test_sharpe"]) else "   nan"
            deg = f"{r['oos_degradation']:.4f}" if not pd.isna(r["oos_degradation"]) else "   nan"
            print(f"  {int(r['fold_id']):>4} {ts:<22} {os_:<22} {trsh:>12} {tesh:>11} {deg:>11}")

        valid_degs = wf.summary["oos_degradation"].dropna()
        if len(valid_degs) > 0:
            print(f"\n  Mean OOS degradation: {valid_degs.mean():.4f}")
            print(f"  Positive degradation folds (train > test): "
                  f"{(valid_degs > 0).sum()}/{len(valid_degs)}")
    except ValueError as e:
        print(f"  WARNING: Walk-forward could not run: {e}")
        wf = None

    # -----------------------------------------------------------------------
    # Step 3: Grid search
    # -----------------------------------------------------------------------
    print("\n[3] Grid search...")
    full_grid = load_grid(REPO_ROOT / args.grid)
    total_combos = 1
    for v in full_grid.values():
        total_combos *= len(v)
    print(f"  Full grid size: {total_combos} combinations")

    if args.sample > 0 and args.sample < total_combos:
        print(f"  --sample {args.sample}: running {args.sample} random combos (seed={seed}).")
        sampled_combos, param_names = _sample_grid(full_grid, args.sample, seed)
        print(f"  Running {len(sampled_combos)} sampled combinations...")
        grid_results = _run_sampled_grid(df_5min, config, instrument, sampled_combos, param_names)
    else:
        print(f"  Running all {total_combos} combinations (n_jobs={args.n_jobs})...")
        print(f"  NOTE: {total_combos} combos may take significant time. "
              f"Use --sample N for faster runs.")
        grid_results = run_grid(df_5min, config, instrument, full_grid, n_jobs=args.n_jobs)

    grid_out = reports_dir / "phase5_grid_results.parquet"
    grid_results.to_parquet(grid_out, index=False)
    print(f"  Grid results saved: {grid_out} ({len(grid_results)} rows)")

    valid_sharpe = grid_results["sharpe"].dropna()
    if len(valid_sharpe) > 0:
        print(f"  Sharpe range: [{valid_sharpe.min():.4f}, {valid_sharpe.max():.4f}]")
        print(f"  Combos with Sharpe > 0.5: {(valid_sharpe > 0.5).sum()}")

    # -----------------------------------------------------------------------
    # Step 4: Stability scoring and top-5 robust configs
    # -----------------------------------------------------------------------
    print("\n[4] Stability scoring...")
    scored = grid_results.copy()
    for param in ["stop_points", "target_points"]:
        if param in scored.columns and len(scored[param].unique()) >= 2:
            scored = stability_score(scored, metric="sharpe", param=param, neighbors=1)

    top5 = []
    for stab_col in [c for c in scored.columns if c.startswith("stability_sharpe_")]:
        for _ in range(5):
            best = pick_robust_config(scored, primary_metric="sharpe", stability_weight=0.5)
            top5.append(best)
            break  # just use one call per stability column

    # If no stability columns, just pick by sharpe
    if not top5:
        best = pick_robust_config(grid_results, primary_metric="sharpe", stability_weight=0.0)
        top5 = [best]

    # Build a top-5 by combined score
    if "stability_sharpe_stop_points" in scored.columns:
        stab_col = "stability_sharpe_stop_points"
    elif "stability_sharpe_target_points" in scored.columns:
        stab_col = "stability_sharpe_target_points"
    else:
        stab_col = None

    if stab_col is not None:
        import numpy as np
        s_metric = pd.to_numeric(scored["sharpe"], errors="coerce")
        s_stab = pd.to_numeric(scored[stab_col], errors="coerce")
        valid_mask = ~(s_metric.isna() | s_stab.isna())
        if valid_mask.sum() > 0:
            def zscore(s):
                std = s.std()
                return (s - s.mean()) / std if std > 0 else s - s.mean()
            combined = 0.5 * zscore(s_metric.fillna(s_metric.mean())) + \
                       0.5 * zscore(s_stab.fillna(s_stab.mean()))
            top5_idx = combined.nlargest(min(5, len(combined))).index
            top5_df = scored.loc[top5_idx]
        else:
            top5_df = scored.head(5)
    else:
        top5_df = scored.nlargest(min(5, len(scored)), "sharpe") if "sharpe" in scored.columns else scored.head(5)

    robust_out = reports_dir / "phase5_robust_configs.csv"
    top5_df.to_csv(robust_out, index=False)
    print(f"  Robust configs saved: {robust_out}")

    print("\n  Top-5 robust configurations:")
    display_cols = [c for c in ["stop_points", "target_points", "max_long_hold_days",
                                 "max_trades_per_day", "sharpe", "sortino", "win_rate"]
                    if c in top5_df.columns]
    if stab_col and stab_col in top5_df.columns:
        display_cols.append(stab_col)
    print(f"\n  {top5_df[display_cols].to_string(index=False)}")

    # -----------------------------------------------------------------------
    # Step 5: Heatmaps
    # -----------------------------------------------------------------------
    print("\n[5] Generating heatmaps...")
    if "stop_points" in grid_results.columns and "target_points" in grid_results.columns:
        _heatmap(
            grid_results, "stop_points", "target_points", "sharpe",
            "Sharpe by Stop x Target (avg over hold_days/max_trades)",
            reports_dir / "phase5_heatmap_sharpe.png",
        )
        _heatmap(
            grid_results, "stop_points", "target_points", "win_rate",
            "Win Rate by Stop x Target (avg over hold_days/max_trades)",
            reports_dir / "phase5_heatmap_winrate.png",
        )
    else:
        print("  [skip heatmaps: stop_points or target_points not in grid results]")

    # -----------------------------------------------------------------------
    # Step 6: Write markdown report
    # -----------------------------------------------------------------------
    print("\n[6] Writing report...")
    _write_report(
        reports_dir,
        metrics,
        boot_df,
        pf_ci,
        wr_ci,
        wf,
        grid_results,
        top5_df,
        display_cols,
        args,
        total_combos,
    )
    print(f"  Report saved: {reports_dir / 'phase5_stat_rigor.md'}")

    print("\n" + "=" * 70)
    print("Phase 5 complete.")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _write_report(reports_dir, metrics, boot_df, pf_ci, wr_ci, wf,
                  grid_results, top5_df, display_cols, args, total_combos):
    """Write the Phase 5 markdown report (italiano, tabelle in inglese)."""
    lines = []
    a = lines.append

    a("# Phase 5 - Rigorous OOS Edge Evaluation")
    a("")
    a("## Sommario Esecutivo")
    a("")
    a("Questo report documenta la valutazione statistica rigorosa della strategia")
    a("NQ intraday breakout sviluppata nelle Fasi 1-4. L'obiettivo e' determinare")
    a("se il vantaggio osservato nell'intera storia e' robusto fuori campione (OOS)")
    a("oppure e' un artefatto di overfitting. L'analisi comprende:")
    a("- Metriche baseline con intervalli di confidenza bootstrap (MBB, Kunsch)")
    a("- Walk-forward rolling 5y-train / 1y-test senza refit dei parametri")
    a("- Grid search parametrico con stability score")
    a("- Raccomandazione del set di parametri piu' stabile")
    a("")
    a("## 1. Metriche Baseline (Intero Storico)")
    a("")
    a("| Metric | Value |")
    a("|--------|-------|")
    a(f"| Sharpe (annualizzato) | {metrics['sharpe']:.4f} |")
    a(f"| Sortino | {metrics['sortino']:.4f} |")
    a(f"| Calmar | {metrics['calmar']:.4f} |")
    a(f"| Max Drawdown % | {metrics['max_dd_pct']:.4f} |")
    a(f"| Profit Factor | {metrics['profit_factor']:.4f} |")
    a(f"| Win Rate | {metrics['win_rate']:.4f} |")
    a(f"| Total PnL | ${metrics['total_pnl']:,.0f} |")
    a(f"| Trades | {metrics['n_trades']} |")
    a("")
    a("## 2. Bootstrap Confidence Intervals (95%, MBB block=10, n=1000)")
    a("")
    a("| Metric | Point | Mean | CI Lower | CI Upper |")
    a("|--------|-------|------|----------|----------|")
    for _, row in boot_df.iterrows():
        a(f"| {row['metric']} | {row['point']:.4f} | {row['mean']:.4f} | "
          f"{row['ci_lower']:.4f} | {row['ci_upper']:.4f} |")
    a(f"| profit_factor | {pf_ci['point']:.4f} | {pf_ci['mean']:.4f} | "
      f"{pf_ci['ci_lower']:.4f} | {pf_ci['ci_upper']:.4f} |")
    a(f"| win_rate | {wr_ci['point']:.4f} | {wr_ci['mean']:.4f} | "
      f"{wr_ci['ci_lower']:.4f} | {wr_ci['ci_upper']:.4f} |")
    a("")
    a("**Interpretazione:** Se il CI dello Sharpe include zero, il vantaggio non e'")
    a("statisticamente distinguibile da rumore al 95% di confidenza.")
    a("")

    a("## 3. Walk-Forward Analysis (5y train / 1y test, no refit)")
    a("")
    if wf is not None and len(wf.summary) > 0:
        a("| Fold | Train Period | Test Period | Train Sharpe | Test Sharpe | OOS Degradation |")
        a("|------|-------------|------------|-------------|------------|-----------------|")
        for _, r in wf.summary.iterrows():
            ts = f"{r['train_start'].date()} / {r['train_end'].date()}"
            os_ = f"{r['test_start'].date()} / {r['test_end'].date()}"
            trsh = f"{r['train_sharpe']:.4f}" if not pd.isna(r["train_sharpe"]) else "nan"
            tesh = f"{r['test_sharpe']:.4f}" if not pd.isna(r["test_sharpe"]) else "nan"
            deg = f"{r['oos_degradation']:.4f}" if not pd.isna(r["oos_degradation"]) else "nan"
            a(f"| {int(r['fold_id'])} | {ts} | {os_} | {trsh} | {tesh} | {deg} |")
        a("")
        valid_degs = wf.summary["oos_degradation"].dropna()
        if len(valid_degs) > 0:
            a(f"Media degradazione OOS: **{valid_degs.mean():.4f}**. "
              f"Fold con degradazione positiva (train > test): "
              f"**{(valid_degs > 0).sum()}/{len(valid_degs)}**.")
    else:
        a("Walk-forward non eseguito (dati insufficienti o errore).")
    a("")

    a("## 4. Grid Search - Top 5 Configurazioni Robuste")
    a("")
    a(f"Grid totale: {total_combos} combinazioni.")
    if args.sample > 0:
        a(f"Campionate {args.sample} combinazioni random (--sample {args.sample}, seed={args.seed}).")
    a("")
    if len(top5_df) > 0:
        # Header
        header = " | ".join(display_cols)
        sep = " | ".join(["---"] * len(display_cols))
        a(f"| {header} |")
        a(f"| {sep} |")
        for _, row in top5_df.iterrows():
            vals = []
            for c in display_cols:
                v = row.get(c, "")
                if isinstance(v, float):
                    vals.append(f"{v:.4f}")
                else:
                    vals.append(str(v))
            a(f"| {' | '.join(vals)} |")
    a("")

    a("## 5. Analisi della Stabilita' Parametrica")
    a("")
    a("Le heatmap (stop_points x target_points) mostrano la Sharpe ratio media")
    a("e il win rate medi aggregando sulle altre dimensioni del grid.")
    a("Una zona di colore uniforme indica che il vantaggio e' robusto rispetto")
    a("alla variazione di quei parametri. Picchi isolati suggeriscono overfitting.")
    a("")
    a("![Heatmap Sharpe](phase5_heatmap_sharpe.png)")
    a("![Heatmap Win Rate](phase5_heatmap_winrate.png)")
    a("")

    a("## 6. Conclusioni e Raccomandazione")
    a("")
    a("Basandosi sui risultati della Fase 5:")
    a("")
    a("1. **Baseline robustezza:** Sharpe > 0 su intero storico; il CI bootstrap")
    a("   indica se il vantaggio e' statisticamente significativo.")
    a("2. **OOS persistence:** La degradazione media walk-forward quantifica")
    a("   quanto il vantaggio si riduce su periodi non visti.")
    a("3. **Parametri stabili:** La configurazione raccomandata e' quella con")
    a("   il combined score piu' alto (performance + stability), riducendo")
    a("   il rischio di overfitting rispetto al semplice best-in-sample.")
    a("")
    a("**Avvertenza:** L'analisi non include costi di finanziamento, requisiti")
    a("di margine variabile, o impatto di mercato. La strategia e' progettata")
    a("per prop firm (Apex Trader Funding) con sizing fisso basato su stop risk.")
    a("")
    a("---")
    a("*Generato da scripts/phase5_stat_rigor.py (Phase 5)*")

    out_path = reports_dir / "phase5_stat_rigor.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
